"""Align a set of footage clips to the song — a thin wrapper over ``mixing.audio``.

Each clip is a video file; its audio track (the different-device capture of the song) is
what we align. ``mixing.audio.align_clips_to_reference`` loads each clip's audio via
pydub/ffmpeg (so a video path works directly — no separate extraction, no moviepy), and
returns offsets + a scale-invariant confidence + coverage clamped to the song timeline.
"""

from __future__ import annotations

from typing import Sequence

from muvid.footage.edl import FootageAlignment

#: Analysis sample rate for alignment (mono). 16 kHz is plenty for offset precision.
ALIGN_SAMPLE_RATE = 16000


def align_footage(
    song_path: str,
    clips: Sequence[tuple],
    *,
    song_duration: float | None = None,
    sample_rate: int = ALIGN_SAMPLE_RATE,
) -> list[FootageAlignment]:
    """Align ``clips`` (``[(clip_id, clip_path), ...]``) to the song at ``song_path``.

    Returns a :class:`~muvid.footage.edl.FootageAlignment` per clip that overlaps the song
    (non-overlapping clips are dropped by the underlying primitive), keyed by ``clip_id``.
    Heavy deps (mixing.audio → numpy/scipy/pydub) are imported lazily here so importing the
    genre stays light.
    """
    from mixing.audio import align_clips_to_reference  # lazy: heavy

    clip_ids = [cid for cid, _ in clips]
    clip_paths = [str(p) for _, p in clips]
    aligned = align_clips_to_reference(
        song_path,
        clip_paths,
        reference_duration=song_duration,
        sample_rate=sample_rate,
    )
    return [
        FootageAlignment(
            clip_id=clip_ids[a.index],
            offset_s=a.offset_s,
            confidence=a.confidence,
            duration_s=a.duration_s,
            coverage=a.coverage,
        )
        for a in aligned
    ]
