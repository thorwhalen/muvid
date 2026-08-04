"""One decode pass per clip → the shared per-frame artifacts the quality + motion extractors
both consume.

The design review flagged that quality.py and motionbeat.py must NOT each decode the clip and
each estimate camera motion — that doubles IO/CPU on the memory-fragile box and makes the
"camera-motion computed once" claim false. So a single sequential :func:`sample_clip_frames`
pass computes, per sampled frame: sharpness, exposure, an (injected) face score, the
camera-compensated motion residual, and the global-motion (shake) magnitude — in clip time.
The orchestrator maps clip time → song time via the clip's offset and hands this
:class:`FramePass` to both extractors.

cv2 is lazy-imported (``muvid[scoring]`` extra). Hard caps (``max_frames``) bound the work;
``should_cancel`` is polled so a cancel lands within a few frames.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

import numpy as np

from muvid.footage.scoring import _frame_metrics as fm

#: Default frame sample rate (Hz) — plenty for quality + a motion envelope onto a 10 Hz grid.
DEFAULT_SAMPLE_FPS = 5.0
#: Default hard cap on sampled frames per clip (bounds CPU + memory regardless of duration).
DEFAULT_MAX_FRAMES = 1200


@dataclass(frozen=True)
class FramePass:
    """Per-sampled-frame metrics for one clip, in CLIP time (seconds from the clip start)."""

    clip_times: np.ndarray  # float[k]
    sharpness: np.ndarray  # float[k]
    exposure: np.ndarray  # float[k] in [0,1]
    face: np.ndarray  # float[k] (0 where no face / no detector)
    motion_residual: (
        np.ndarray
    )  # float[k], camera-compensated subject motion (NaN @ frame 0)
    global_dx: np.ndarray  # float[k], camera-motion vector x (NaN @ frame 0)
    global_dy: np.ndarray  # float[k], camera-motion vector y (NaN @ frame 0)
    fps: float
    n_sampled: int


def sample_clip_frames(
    clip_path: str,
    *,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    max_frames: int = DEFAULT_MAX_FRAMES,
    face_fn: Callable[[np.ndarray], float] | None = None,
    flow_downscale: int = 4,
    should_cancel: Callable[[], bool] | None = None,
) -> FramePass:
    """Decode ``clip_path`` ONCE, sampling ~``sample_fps`` frames → a :class:`FramePass`.

    Args:
        clip_path: the video file.
        sample_fps: target frames/second to analyze (strided over the native fps).
        max_frames: hard cap on analyzed frames (bounds cost).
        face_fn: optional ``bgr_frame -> face_score`` (mediapipe, injected by the caller so
            this module stays cv2-only). ``None`` → face score is 0 everywhere.
        flow_downscale: downscale factor for the Farneback flow (cost bound).
        should_cancel: polled every frame; returns early (a partial pass) when it goes True.
    """
    import cv2  # lazy

    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise ValueError(f"cannot open clip: {clip_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    stride = max(1, int(round(fps / max(0.1, sample_fps))))

    times, sharp, expo, faces, motion, gxs, gys = [], [], [], [], [], [], []
    prev_gray = None
    frame_idx = 0
    kept = 0
    try:
        while kept < max_frames:
            ok = cap.grab()
            if not ok:
                break
            if frame_idx % stride == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                gray = fm.to_gray(frame)
                times.append(frame_idx / fps)
                sharp.append(fm.sharpness(gray))
                expo.append(fm.exposure_quality(gray))
                faces.append(float(face_fn(frame)) if face_fn is not None else 0.0)
                if prev_gray is not None:
                    res, gdx, gdy = fm.flow_residual_and_global(
                        prev_gray, gray, downscale=flow_downscale
                    )
                    motion.append(res)
                    gxs.append(
                        gdx
                    )  # keep the camera-motion VECTOR so shake = its jitter,
                    gys.append(
                        gdy
                    )  # not its magnitude (a smooth pan has large mag, low jitter)
                else:
                    motion.append(np.nan)
                    gxs.append(np.nan)
                    gys.append(np.nan)
                prev_gray = gray
                kept += 1
                if should_cancel is not None and should_cancel():
                    break
            frame_idx += 1
    finally:
        cap.release()

    return FramePass(
        clip_times=np.asarray(times, dtype=np.float64),
        sharpness=np.asarray(sharp, dtype=np.float64),
        exposure=np.asarray(expo, dtype=np.float64),
        face=np.asarray(faces, dtype=np.float64),
        motion_residual=np.asarray(motion, dtype=np.float64),
        global_dx=np.asarray(gxs, dtype=np.float64),
        global_dy=np.asarray(gys, dtype=np.float64),
        fps=float(fps),
        n_sampled=kept,
    )


def _framing_score(width: float, height: float, xmin: float, ymin: float) -> float:
    """area_fraction (capped at 0.5) × centering — from a relative bbox."""
    area = max(0.0, width) * max(0.0, height)
    cx, cy = xmin + width / 2.0, ymin + height / 2.0
    centering = 1.0 - min(1.0, abs(cx - 0.5) + abs(cy - 0.5))
    return float(min(area, 0.5) / 0.5 * max(0.0, centering))


def make_face_scorer():
    """Build a ``bgr_frame -> face_framing_score`` (or ``None`` if unavailable).

    Best-effort and never fatal: face_framing is a soft metric (not one of the two named
    signals), so ANY failure → ``None`` and the orchestrator scores 0 (neutral). Tries the
    classic ``mp.solutions.face_detection`` (mediapipe 0.10 full build); falls back to the
    Tasks ``FaceDetector`` ONLY when the operator supplies a model via
    ``MUVID_MEDIAPIPE_FACE_MODEL`` (no auto-download). Score = best face's area×centering.
    """
    try:
        import cv2  # lazy
        import mediapipe as mp  # lazy
    except Exception:
        return None

    # 1) Classic solutions API (bundled model, no download).
    if hasattr(mp, "solutions") and hasattr(mp.solutions, "face_detection"):
        try:
            detector = mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=0.5
            )

            def score_solutions(bgr: np.ndarray) -> float:
                res = detector.process(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                if not res.detections:
                    return 0.0
                return max(
                    _framing_score(
                        d.location_data.relative_bounding_box.width,
                        d.location_data.relative_bounding_box.height,
                        d.location_data.relative_bounding_box.xmin,
                        d.location_data.relative_bounding_box.ymin,
                    )
                    for d in res.detections
                )

            return score_solutions
        except Exception:
            pass

    # 2) Tasks API — only with an operator-provided model file (no runtime download).
    model_path = os.environ.get("MUVID_MEDIAPIPE_FACE_MODEL")
    if model_path and os.path.exists(model_path):
        try:
            base = mp.tasks.BaseOptions(model_asset_path=model_path)
            opts = mp.tasks.vision.FaceDetectorOptions(base_options=base)
            detector = mp.tasks.vision.FaceDetector.create_from_options(opts)

            def score_tasks(bgr: np.ndarray) -> float:
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                res = detector.detect(image)
                h, w = bgr.shape[:2]
                best = 0.0
                for d in res.detections or []:
                    bb = d.bounding_box
                    best = max(
                        best,
                        _framing_score(
                            bb.width / w,
                            bb.height / h,
                            bb.origin_x / w,
                            bb.origin_y / h,
                        ),
                    )
                return best

            return score_tasks
        except Exception:
            return None
    return None
