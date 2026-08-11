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
   count derived from the shared song-time grid (``round(end*fps) - round(start*fps)``);
   sub-frame cuts (0 grid frames) are dropped, an exhausted source clones its last frame
   up to the count (``tpad``), and a cut whose source yields no frames at all falls back
   to black — so total frames equals ``round(song_duration*fps)`` by construction,
   whatever the cut count, source rates, or a clip whose audio outlives its video.
2. **Concat by stream copy** (concat demuxer) — no re-encode, no filtergraph.
3. **Mux the clean song** in the same final pass. When the master already IS the delivery
   contract (aac, 48 kHz stereo), its packets are stream-copied bit-identically; anything
   else is encoded to the contract (aac 192k, 48 kHz stereo — verify_video's audio check
   must be a property of the renderer, not of which song the user brought: muvid#24 B3).

ffmpeg auto-applies each clip's rotation metadata on decode (default ``-autorotate 1``),
so a display-matrix portrait clip lands upright and pillarboxed, never stretched.

Deliberately NOT moviepy (its ``write_videofile`` runs in-process and would escape the
``$MUVID_FFMPEG_TIMEOUT_S`` worker guard). Every stage runs through
:func:`muvid.visualize.ffmpeg.run_ffmpeg`; note the guard is **per invocation** now, so a
render's wall-clock bound is ``(cuts + 1) * MUVID_FFMPEG_TIMEOUT_S`` — the env var bounds
a hang, not the total render.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import replace
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
    regardless of how many cuts there are. A cut that rounds to ZERO grid frames (the
    EDL's 1 ms span floor is finer than a frame) gets count 0 and is skipped by the
    renderer — flooring it at 1 would add a frame per tiny cut, and 500 hostile 1 ms
    spans would push everything after them ~15 s late. Dropping keeps the sum exact.
    """
    return [round(c.song_end * fps) - round(c.song_start * fps) for c in cuts]


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

    # tpad at the tail: -frames:v alone is a CAP, not a guarantee — a clip whose audio
    # outlives its video (alignment durations come from the AUDIO length) legitimately
    # validates a span past the last video frame, and without tpad that part comes up
    # short, silently desyncing every later cut. Cloning the last frame makes the frame
    # count exact whenever the source yields at least one frame.
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
        f"tpad=stop=-1:stop_mode=clone"
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
        vf = f"setsar=1,fps={fps}"
        args = [
            "-f",
            "lavfi",
            "-i",
            f"color=black:size={w}x{h}:rate={fps}:duration={cut.duration + 1.0 / fps:.6f}",
            "-vf",
            vf,
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


def _has_video_frames(part: Path) -> bool:
    """Whether a rendered part actually carries a video stream.

    ffmpeg exits 0 even when an input-side ``-ss`` lands wholly past the source's last
    video frame — the result is a streamless few-hundred-byte mp4 the concat demuxer
    silently swallows, shortening the video and desyncing every later cut. This is the
    check that turns that silence into the black-gap fallback.
    """
    from muvid.visualize.ffmpeg import probe

    try:
        streams = probe(part).get("streams", [])
    except Exception:  # noqa: BLE001 — unreadable part: treat as frameless, fall back
        return False
    return any(s.get("codec_type") == "video" for s in streams)


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

    # A unique, per-call directory: a fixed name next to out_path would rmtree
    # whatever already lived there, and two concurrent renders into one directory
    # would interleave part files and delete each other's work.
    parts_dir = Path(tempfile.mkdtemp(dir=out.parent, prefix=".parts-"))
    try:
        counts = _frame_counts(cuts, fps)
        names = []
        for i, (cut, n) in enumerate(zip(cuts, counts)):
            if n == 0:  # a sub-frame cut owns no frame on the grid — see _frame_counts
                continue
            name = f"part{i:04d}.mp4"
            part = parts_dir / name
            render_kwargs = dict(w=w, h=h, fps=fps, n_frames=n, crf=crf, preset=preset)
            _render_part(cut, part, **render_kwargs)
            if cut.clip_path and part.exists() and not _has_video_frames(part):
                # The source yielded no frames at all for this span (audio outlived the
                # video). The span still exists in song time — fill it black rather
                # than silently shortening the render.
                part.unlink()
                _render_part(
                    replace(cut, clip_path="", clip_in=0.0), part, **render_kwargs
                )
            names.append(name)
        if not names:
            raise ValueError("no renderable cuts — every span rounds to zero frames")
        # Concat-demuxer entries resolve relative to the LIST file, and the names are
        # ours (partNNNN.mp4) — plain relative names, so the demuxer's default "safe"
        # mode is fine and no quoting/escaping surface exists here.
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
                "-i",
                str(concat_list),
                *song_input,
                "-map",
                "0:v",
                # :a:0, not :a — a multi-stream master would otherwise carry extra audio
                # tracks past the contract decision, which probes only the first.
                "-map",
                "1:a:0",
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
