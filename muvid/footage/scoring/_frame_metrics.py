"""Pure per-frame / per-frame-pair OpenCV kernels for footage scoring.

Small, stateless functions on numpy frames — the cheap-CPU quality tier + the optical-flow
motion primitive. They know nothing about the song grid, clips, or the selector: given a
frame (or a pair) they return a scalar / small tuple. That makes them **promotion candidates
for ``mixing.video``** the moment a second consumer appears (they'd slot into mixing.video's
existing OpenCV footprint with no new dependency) — kept in muvid for v1 to hold the
cross-repo surface to one clean audio PR (see the design doc's federation rule).

cv2 is lazy-imported (``muvid[scoring]`` extra). Frames are BGR or grayscale numpy arrays
(as ``mixing.video.VideoFrames`` yields).
"""

from __future__ import annotations

import numpy as np

#: Target luma standard deviation (0-255) a well-exposed, contrasty frame reaches.
_EXPOSURE_CONTRAST_TARGET = 64.0


def _cv2():
    import cv2  # lazy: heavy

    return cv2


def to_gray(frame: np.ndarray) -> np.ndarray:
    """BGR (or already-gray) frame → uint8 grayscale."""
    if frame.ndim == 2:
        return frame
    return _cv2().cvtColor(frame, _cv2().COLOR_BGR2GRAY)


def sharpness(gray: np.ndarray) -> float:
    """Focus / motion-blur proxy = variance of the Laplacian (higher = sharper)."""
    lap = _cv2().Laplacian(gray, _cv2().CV_64F)
    return float(lap.var())


def exposure_quality(gray: np.ndarray) -> float:
    """Exposure health in [0,1] (higher = better): clipping-free AND contrasty.

    Two failure modes a luma histogram reveals: (a) crushed shadows (< 16) / blown highlights
    (> 239) — the ``clip_ok`` term; (b) a flat, low-contrast frame (a wall of mid-gray) — the
    ``contrast`` term (normalized luma std). A well-exposed, contrasty frame → ~1.0; a flat
    mid-gray frame → ~0.0 even though it clips nothing.
    """
    g = gray.reshape(-1).astype(np.float64)
    n = g.size
    if n == 0:
        return 0.0
    dark = float(np.count_nonzero(g < 16))
    bright = float(np.count_nonzero(g > 239))
    clip_ok = 1.0 - (dark + bright) / n
    contrast = min(1.0, float(g.std()) / _EXPOSURE_CONTRAST_TARGET)
    return clip_ok * contrast


def flow_residual_and_global(
    prev_gray: np.ndarray, gray: np.ndarray, *, downscale: int = 4
) -> tuple[float, float, float]:
    """Dense Farneback flow → ``(residual_motion, global_dx, global_dy)``.

    ``global_(dx,dy)`` is the median flow vector — the camera pan/tilt estimate.
    ``residual_motion`` is the mean magnitude of the flow AFTER subtracting that global
    motion — i.e. camera-compensated SUBJECT motion (what the motion-to-beat envelope wants).
    ``|global|`` doubles as the shake signal (the quality tier). Frames are downscaled first
    (``downscale``×) to bound cost — a hard per-frame budget on the memory-fragile box.
    """
    cv2 = _cv2()
    if downscale > 1:
        h, w = gray.shape[:2]
        size = (max(1, w // downscale), max(1, h // downscale))
        prev_gray = cv2.resize(prev_gray, size)
        gray = cv2.resize(gray, size)
    flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 2, 15, 3, 5, 1.2, 0)
    fx, fy = flow[..., 0], flow[..., 1]
    gdx, gdy = float(np.median(fx)), float(np.median(fy))
    residual = float(np.mean(np.hypot(fx - gdx, fy - gdy)))
    return residual, gdx, gdy
