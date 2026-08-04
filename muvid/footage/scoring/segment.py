"""Shot boundaries (PySceneDetect) + the per-clip coverage mask.

- ``coverage_mask`` is dependency-free: the boolean of song-grid frames a clip actually
  covers (from its offset + duration). It is the base mask every metric ANDs with, and the
  authority for "does this clip exist at song time t".
- ``shot_boundaries`` returns song-times of within-clip scene cuts (PySceneDetect, BSD-3).
  Consumed by the selector ONLY when ``boundary_mode="beats+shots"`` (they become *inter-clip*
  cut candidates at shot-boundary times — a within-clip jump cut is unrepresentable in the
  single-offset EDL model). Optional dep: absent → ``[]`` and the mode silently degrades to
  beats-only.
"""

from __future__ import annotations

import numpy as np

from muvid.footage.edl import _EPS


def coverage_mask(
    *,
    offset_s: float,
    duration_s: float,
    coverage: tuple,
    t0: float,
    hop_s: float,
    n: int,
) -> np.ndarray:
    """Grid frames (bool[n]) the clip covers — using its clamped ``coverage`` span."""
    lo, hi = coverage
    times = t0 + np.arange(n) * hop_s
    return (times >= lo - _EPS) & (times < hi + _EPS)


def shot_boundaries(clip_path: str, *, offset_s: float) -> list[float]:
    """Song-times of within-clip scene cuts (PySceneDetect ContentDetector), or ``[]``.

    Returns ``[]`` (never raises) if PySceneDetect is not installed, so the selector's
    ``beats+shots`` mode degrades cleanly to beats-only.
    """
    try:
        from scenedetect import ContentDetector, SceneManager, open_video  # lazy
    except Exception:
        return []
    try:
        video = open_video(str(clip_path))
        sm = SceneManager()
        sm.add_detector(ContentDetector())
        sm.detect_scenes(video)
        scenes = sm.get_scene_list()
    except Exception:
        return []
    # Each scene is (start, end) timecodes in CLIP time → song time via the offset. The cut
    # points are the scene starts (drop the very first, which is the clip start).
    cuts = []
    for i, (start, _end) in enumerate(scenes):
        if i == 0:
            continue
        cuts.append(float(start.get_seconds()) + offset_s)
    return cuts
