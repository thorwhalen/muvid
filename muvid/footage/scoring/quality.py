"""Quality tier: sharpness / exposure / stability_shake / face_framing → song-grid tracks.

Pure given a :class:`~muvid.footage.scoring.frames.FramePass` (one decode pass, shared with
:mod:`~muvid.footage.scoring.motionbeat`). Each per-frame signal is mapped from clip time to
song time (``song_t = clip_t + offset_s``) and resampled onto the shared grid. All cheap-CPU
(OpenCV/MediaPipe), commercial-clean (Apache-2.0).

Gating: a per-frame ``quality_ok`` mask (sharpness/exposure above env-tunable FLOORS) is
computed so the orchestrator can AND it into coverage; the FLOORS default to disabled
(0.0) — an absolute blur/exposure threshold is fragile across cameras, so v1 prefers the
soft signal (sharpness/exposure as weighted metrics) and only hard-gates when the owner sets
a floor. See ``misc/docs/footage_scoring_design.md`` §3a.
"""

from __future__ import annotations

import os

import numpy as np

from muvid.footage.scoring.frames import FramePass
from muvid.footage.scoring.grid import ScoreTrack, resample_to_grid

#: Env-tunable hard gate floors (default disabled → no hard gate; soft metric only).
SHARPNESS_FLOOR = float(os.environ.get("MUVID_SCORING_SHARPNESS_FLOOR", "0.0"))
EXPOSURE_FLOOR = float(os.environ.get("MUVID_SCORING_EXPOSURE_FLOOR", "0.0"))

QUALITY_METRICS = ("sharpness", "exposure", "stability_shake", "face_framing")


def quality_tracks(
    frame_pass: FramePass,
    *,
    clip_id: str,
    offset_s: float,
    t0: float,
    hop_s: float,
    n: int,
) -> list[ScoreTrack]:
    """Sharpness / exposure / stability_shake / face_framing tracks for one clip."""
    song_t = frame_pass.clip_times + offset_s
    tracks: list[ScoreTrack] = []

    def _track(metric, samples, direction):
        vals, mask = resample_to_grid(song_t, samples, t0=t0, hop_s=hop_s, n=n)
        return ScoreTrack(clip_id, metric, t0, hop_s, vals, mask, direction)

    tracks.append(_track("sharpness", frame_pass.sharpness, "higher_better"))
    tracks.append(_track("exposure", frame_pass.exposure, "higher_better"))
    # Shake = frame-to-frame JITTER of the camera-motion vector (a steady pan is stable, so
    # its magnitude must not read as shake). Less jitter = steadier = better → lower_better.
    gdx, gdy = frame_pass.global_dx, frame_pass.global_dy
    jitter = np.full(gdx.shape, np.nan, dtype=np.float64)
    if gdx.size >= 2:
        jitter[1:] = np.hypot(np.diff(gdx), np.diff(gdy))  # NaN where either end is NaN
    tracks.append(_track("stability_shake", jitter, "lower_better"))
    tracks.append(_track("face_framing", frame_pass.face, "higher_better"))
    return tracks


def quality_gate_mask(
    frame_pass: FramePass,
    *,
    offset_s: float,
    t0: float,
    hop_s: float,
    n: int,
) -> np.ndarray:
    """A ``quality_ok`` grid mask (True = usable), from the env FLOORS (default: all True).

    The orchestrator ANDs this into the composite mask so a truly-unusable (black / blown /
    frozen) frame is excluded from selection. Disabled by default to avoid over-masking.
    """
    if SHARPNESS_FLOOR <= 0 and EXPOSURE_FLOOR <= 0:
        return np.ones(n, dtype=bool)  # no hard gate configured
    ok = (frame_pass.sharpness >= SHARPNESS_FLOOR) & (
        frame_pass.exposure >= EXPOSURE_FLOOR
    )
    song_t = frame_pass.clip_times + offset_s
    vals, mask = resample_to_grid(song_t, ok.astype(float), t0=t0, hop_s=hop_s, n=n)
    # A frame is gated OUT where it is covered but the (interpolated) ok-signal < 0.5.
    return ~(mask & (np.nan_to_num(vals, nan=1.0) < 0.5))
