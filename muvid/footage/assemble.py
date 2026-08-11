"""Assemble validated cuts into a music video — ONE bounded ffmpeg pass.

Deliberately NOT moviepy (its ``write_videofile`` runs in-process and would escape the
``$MUVID_FFMPEG_TIMEOUT_S`` worker guard, and its dimension normalization is a per-frame
Python blur). Instead a single ``ffmpeg -filter_complex`` invocation, run through
:func:`muvid.visualize.ffmpeg.run_ffmpeg` (wall-clock bounded): each cut is trimmed at its
derived in-point, scaled+padded onto a FIXED canvas (from the genre, not clip-0), and
concatenated; the **clean song** audio for the covered span is mapped as the sole audio
track. ffmpeg auto-applies each clip's rotation metadata on decode (default ``-autorotate``
1), so mixed-orientation phone clips land upright without extra handling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from muvid.footage.edl import AssemblyCut

#: Default output frame rate for the assembled video.
DEFAULT_FPS = 30
#: Default canvas (16:9 1080p) when the caller/genre gives none.
DEFAULT_CANVAS = (1920, 1080)


def _even(n: int) -> int:
    return n - (n % 2)  # H.264/yuv420p needs even dimensions


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
    """Render ``cuts`` (a validated, contiguous EDL) into ``out_path``.

    One ffmpeg pass: trim+scale+pad each cut onto ``canvas`` → concat → mux the clean
    song audio for ``[cuts[0].song_start, cuts[-1].song_end]``. Returns ``out_path``.
    """
    from muvid.visualize.ffmpeg import require_ffmpeg, run_ffmpeg  # lazy + bounded

    if not cuts:
        raise ValueError("no cuts to assemble")
    require_ffmpeg("ffmpeg")
    w, h = _even(canvas[0]), _even(canvas[1])
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    song_start = cuts[0].song_start
    total = cuts[-1].song_end - song_start

    args: list[str] = []
    for c in cuts:  # one input per cut (same file may repeat — ffmpeg allows it)
        args += ["-i", str(c.clip_path)]
    args += ["-i", str(song_path)]
    song_idx = len(cuts)

    # Per-cut video: trim to the derived in-point, reset PTS, scale to fit + pad to canvas,
    # square pixels, fixed fps. Then concat all into one stream.
    parts = []
    labels = []
    for i, c in enumerate(cuts):
        parts.append(
            f"[{i}:v]trim=start={c.clip_in:.3f}:duration={c.duration:.3f},"
            f"setpts=PTS-STARTPTS,"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]"
        )
        labels.append(f"[v{i}]")
    concat = f"{''.join(labels)}concat=n={len(cuts)}:v=1:a=0[vout]"
    # Audio: the clean song for the covered span.
    aud = (
        f"[{song_idx}:a]atrim=start={song_start:.3f}:duration={total:.3f},"
        f"asetpts=PTS-STARTPTS[aout]"
    )
    filter_complex = ";".join(parts + [concat, aud])

    args += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        # verify_video's delivery contract (2ch @ 48 kHz, no edit lists) must be a property
        # of the renderer, not of the source: without these, the aac encoder inherits the
        # master's rate/channels, so a 44.1 kHz or mono song fails every verify with nothing
        # in the pipeline explaining why (muvid#24 B3).
        "-ar",
        "48000",
        "-ac",
        "2",
        "-use_editlist",
        "0",
        "-movflags",
        "+faststart",
        "-shortest",
        str(out),
    ]
    run_ffmpeg(args)
    return out
