"""Assemble validated cuts into a music video, in BOUNDED memory.

The previous shape — one ffmpeg ``-filter_complex`` pass with one input per cut — held a
decoder context and inter-filter queues for EVERY cut at once, so peak RSS grew roughly
linearly with cut count: a real 30-cut weighted edit needed >2.3 GB and was OOM-killed on
the 3.7 GB production box, while 15 cuts only just fit (muvid#21/#24). Score-driven edits
routinely produce 30-70 cuts, so that shape caps exactly the edits the scoring layer
exists to make.

Now three bounded stages, memory O(1) in cut count:

1. **One intermediate per cut** — a single-input ffmpeg run per cut (input-side ``-ss``,
   so a late cut no longer decodes the whole head of its clip), scaled+padded onto the
   fixed canvas at a constant ``fps``. A gap entry (``clip_path == ""`` — a span of the
   song with no footage) renders as black from a ``color`` source, which is what makes
   partial-coverage edits renderable at all. Each intermediate is cut to an EXACT frame
   count derived from the shared song-time grid (``round(end*fps) - round(start*fps)``),
   so per-cut frame quantization cannot accumulate into drift: total frames equals
   ``round(song_duration*fps)`` by construction, whatever the cut count or source rates.
2. **Concat by stream copy** (concat demuxer) — no re-encode, no filtergraph.
3. **Mux the clean song** in the same final pass. When the master already IS the delivery
   contract (aac, 48 kHz stereo), its packets are stream-copied bit-identically; anything
   else is encoded to the contract (aac 192k, 48 kHz stereo — verify_video's audio check
   must be a property of the renderer, not of which song the user brought: muvid#24 B3).

ffmpeg auto-applies each clip's rotation metadata on decode (default ``-autorotate 1``),
so a display-matrix portrait clip lands upright and pillarboxed, never stretched.

Deliberately NOT moviepy (its ``write_videofile`` runs in-process and would escape the
``$MUVID_FFMPEG_TIMEOUT_S`` worker guard). Every stage runs through
:func:`muvid.visualize.ffmpeg.run_ffmpeg` (wall-clock bounded per invocation).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

from muvid.footage.edl import AssemblyCut

#: Default output frame rate for the assembled video.
DEFAULT_FPS = 30
#: Default canvas (16:9 1080p) when the caller/genre gives none.
DEFAULT_CANVAS = (1920, 1080)


def _even(n: int) -> int:
    return n - (n % 2)  # H.264/yuv420p needs even dimensions


def _frame_counts(cuts: Sequence[AssemblyCut], fps: int) -> list[int]:
    """Exact frames per cut on the shared song-time grid — drift-proof by construction.

    Quantizing each cut's *boundaries* (not its duration) to the frame grid means the
    counts telescope: their sum is ``round(last_end*fps) - round(first_start*fps)``
    regardless of how many cuts there are. A sub-frame span still gets one frame (the
    EDL's 1 ms span floor is finer than a 30 fps frame), which can over-run the total by
    a frame in pathological many-tiny-cuts edits — accepted, it cannot compound.
    """
    counts = []
    for c in cuts:
        n = round(c.song_end * fps) - round(c.song_start * fps)
        counts.append(max(1, n))
    return counts


def _video_codec_args(crf: int, preset: str) -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(crf),
        "-preset",
        preset,
    ]


def _render_part(
    cut: AssemblyCut,
    part: Path,
    *,
    w: int,
    h: int,
    fps: int,
    n_frames: int,
    crf: int,
    preset: str,
) -> None:
    """Render ONE cut (footage or gap) to an intermediate — the single-decoder stage."""
    from muvid.visualize.ffmpeg import run_ffmpeg

    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
    )
    if cut.clip_path:
        args = [
            # Input-side seek: lands on the keyframe before clip_in and decodes forward
            # to it, so a cut 3 minutes into a clip does not decode 3 minutes of video.
            "-ss",
            f"{cut.clip_in:.6f}",
            # One spare frame of input beyond the target; -frames:v is the exact cap.
            "-t",
            f"{cut.duration + 1.0 / fps:.6f}",
            "-i",
            str(cut.clip_path),
            "-vf",
            vf,
        ]
    else:  # a gap entry: no footage for this span — fill black on the same grid
        args = [
            "-f",
            "lavfi",
            "-i",
            f"color=black:size={w}x{h}:rate={fps}:duration={cut.duration + 1.0 / fps:.6f}",
        ]
    args += [
        "-frames:v",
        str(n_frames),
        "-an",
        *_video_codec_args(crf, preset),
        str(part),
    ]
    run_ffmpeg(args)


def _audio_args(song_path: str) -> list[str]:
    """Stream-copy the master when it already is the delivery contract; else encode to it.

    Bit-identical audio (muvid#21 item 5) and the fixed 2ch@48kHz delivery contract
    (muvid#24 B3) are only simultaneously satisfiable when the master is already
    aac/48000/2ch — so that is exactly the copy condition, decided by probe, not hope.
    """
    from muvid.visualize.ffmpeg import probe

    try:
        astreams = [
            s
            for s in probe(song_path).get("streams", [])
            if s.get("codec_type") == "audio"
        ]
        a = astreams[0] if astreams else {}
    except Exception:  # noqa: BLE001 — un-probeable song: encode, never crash the render
        a = {}
    if (
        a.get("codec_name") == "aac"
        and str(a.get("sample_rate")) == "48000"
        and a.get("channels") == 2
    ):
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def assemble_music_video(
    cuts: Sequence[AssemblyCut],
    song_path: str,
    out_path: str,
    *,
    canvas: tuple[int, int] = DEFAULT_CANVAS,
    fps: int = DEFAULT_FPS,
    crf: int = 20,
    preset: str = "veryfast",
) -> Path:
    """Render ``cuts`` (a validated, contiguous, gap-filled EDL) into ``out_path``.

    Bounded stages: one single-input ffmpeg run per cut (gaps render black), a stream-copy
    concat, and a final mux of the clean song for ``[cuts[0].song_start,
    cuts[-1].song_end]`` — which, for EDLs produced by ``fill_gaps``, is the whole song.
    Returns ``out_path``.
    """
    from muvid.visualize.ffmpeg import require_ffmpeg, run_ffmpeg

    if not cuts:
        raise ValueError("no cuts to assemble")
    require_ffmpeg("ffmpeg")
    require_ffmpeg("ffprobe")
    w, h = _even(canvas[0]), _even(canvas[1])
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    parts_dir = out.parent / "_parts"
    parts_dir.mkdir(exist_ok=True)
    try:
        counts = _frame_counts(cuts, fps)
        names = []
        for i, (cut, n) in enumerate(zip(cuts, counts)):
            name = f"part{i:04d}.mp4"
            _render_part(
                cut,
                parts_dir / name,
                w=w,
                h=h,
                fps=fps,
                n_frames=n,
                crf=crf,
                preset=preset,
            )
            names.append(name)
        # Concat-demuxer entries resolve relative to the LIST file, and the names are
        # ours (partNNNN.mp4), so no quoting/escaping surface exists here.
        concat_list = parts_dir / "parts.txt"
        concat_list.write_text("".join(f"file {n}\n" for n in names))
        song_start = cuts[0].song_start
        total = cuts[-1].song_end - song_start
        # The song is trimmed to the covered span. For a fill_gaps EDL that span IS the
        # whole song, so the -ss is omitted and -t admits every packet — which is what
        # keeps the stream-copy path bit-identical to the master.
        song_input = ["-i", str(song_path)]
        if song_start > 1e-3:
            song_input = ["-ss", f"{song_start:.6f}", *song_input]
        run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                *song_input,
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-t",
                f"{total:.6f}",
                "-c:v",
                "copy",
                *_audio_args(song_path),
                # Delivery contract (muvid#24 B3): no edit lists, moov up front.
                "-use_editlist",
                "0",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
    finally:
        shutil.rmtree(parts_dir, ignore_errors=True)
    return out
