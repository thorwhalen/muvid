"""The scoring orchestrator: a project (song + aligned clips) → the persisted score tensor.

Owns the shared, compute-once artifacts and the single decode pass per clip (the design
review's fix for the double-decode / camera-motion-sharing problem):

1. **Master beat grid** (``mixing.audio.beat_grid``) — computed ONCE; every clip maps to it
   via its offset.
2. (opt-in) **Master vocal stem** (Demucs) — ONCE, for the lip-sync tier.
3. **Per clip: ONE decode pass** (:func:`~muvid.footage.scoring.frames.sample_clip_frames`)
   feeds BOTH quality and motion-beat (sharpness/exposure/face + the camera-compensated motion
   envelope come out of the same loop). Plus shot boundaries (PySceneDetect) and, when enabled,
   the lip-sync tracks.
4. Assemble + **persist** the tensor (per-metric-global normalization, atomic writes).

Resource safety (LOCKED decisions): a **process-wide concurrency=1 semaphore**
(``MUVID_SCORING_MAX_CONCURRENT``) serializes scoring runs; the lip-sync tier is **off by
default** (``MUVID_SCORING_ENABLE_LIPSYNC``); ``should_cancel`` is polled between every clip
and stage (cancel latency ≈ one clip); progress is emitted as ``{'kind':'progress',
'stage_index','stage_count','current_transform'}`` dicts (the nw.jobs mirror contract).

Import-safe: every heavy import (cv2/mediapipe/librosa/torch) is inside a function body.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Sequence

from muvid.footage.scoring.grid import DEFAULT_HOP_S, grid_len, save_scores

#: The torch-free CORE metric set (prod-safe). Lip-sync metrics are added only when the
#: opt-in tier is enabled.
DEFAULT_METRICS = (
    "sharpness",
    "exposure",
    "stability_shake",
    "face_framing",
    "motion_beat_bas",
    "motion_onset_xcorr",
)
_LIPSYNC_METRICS = ("lip_sync_lse_c", "lse_d_offset")

#: Process-wide serialization of scoring runs (bounds peak memory on the fragile box).
_MAX_CONCURRENT = max(1, int(os.environ.get("MUVID_SCORING_MAX_CONCURRENT", "1")))
_SCORING_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT)


def _lipsync_enabled(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return os.environ.get("MUVID_SCORING_ENABLE_LIPSYNC", "0") not in ("0", "", "false", "False")


def list_available_extractors() -> dict:
    """Which tiers can run here (import + weight availability) — for diagnostics / the tool."""
    def _has(mod):
        import importlib.util

        return importlib.util.find_spec(mod) is not None

    from muvid.footage.scoring.lipsync import lipsync_available

    ok, reason = lipsync_available()
    return {
        "quality": _has("cv2"),
        "face_framing": _has("mediapipe"),
        "motion_beat": _has("cv2") and _has("librosa"),
        "segment": _has("scenedetect"),
        "lipsync": {"available": ok, "reason": reason},
    }


def score_project(
    project,
    *,
    metrics: Sequence[str] | None = None,
    hop_s: float = DEFAULT_HOP_S,
    sample_fps: float | None = None,
    enable_lipsync: bool | None = None,
    progress_cb: Callable[[dict], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """Score every aligned clip of ``project`` → persist the tensor; return a summary dict.

    Args:
        project: a ``MusicVideoFootageProject`` (needs ``song_path``/``song_duration``/
            ``load_alignments``/``clip_paths``/``root``/``song_hash``).
        metrics: restrict to these metric names (default: the core set, + lip-sync if enabled).
        hop_s: grid step (default 10 Hz).
        sample_fps: frame analysis rate (default from ``frames.DEFAULT_SAMPLE_FPS``).
        enable_lipsync: force the opt-in tier on/off (default: the env flag).
        progress_cb: sink for ``{'kind':'progress', ...}`` dict events (nw.jobs mirror shape).
        should_cancel: polled between clips/stages; a True short-circuits to a clean cancel.
    """
    with _SCORING_SEMAPHORE:
        return _score(
            project,
            metrics=metrics,
            hop_s=hop_s,
            sample_fps=sample_fps,
            enable_lipsync=enable_lipsync,
            progress_cb=progress_cb,
            should_cancel=should_cancel,
        )


def _emit(progress_cb, idx, total, name):
    if progress_cb is not None:
        progress_cb(
            {
                "kind": "progress",
                "stage_index": idx,
                "stage_count": total,
                "current_transform": name,
            }
        )


def _cancelled(should_cancel):
    return should_cancel is not None and should_cancel()


def _score(project, *, metrics, hop_s, sample_fps, enable_lipsync, progress_cb, should_cancel):
    from muvid.footage.scoring import frames as F
    from muvid.footage.scoring.motionbeat import motionbeat_tracks
    from muvid.footage.scoring.quality import quality_gate_mask, quality_tracks
    from muvid.footage.scoring.segment import shot_boundaries

    aligns = project.load_alignments()
    if not aligns:
        raise ValueError("no alignments — run align_footage first")
    song_dur = project.song_duration()
    n = grid_len(song_dur, hop_s)
    t0 = 0.0
    clip_paths = project.clip_paths()

    want_lipsync = _lipsync_enabled(enable_lipsync)
    # If the caller explicitly asks for lip-sync metrics, requesting them must ENABLE the tier
    # (honoring availability) rather than silently returning an empty result.
    requested_lipsync = set(metrics) & set(_LIPSYNC_METRICS) if metrics else set()
    if requested_lipsync and not want_lipsync:
        from muvid.footage.scoring.lipsync import lipsync_available

        ok, reason = lipsync_available()
        if not ok:
            raise ValueError(
                f"lip-sync metrics {sorted(requested_lipsync)} requested but the tier is "
                f"unavailable: {reason} (install muvid[scoring-lipsync] + set weights)"
            )
        want_lipsync = True
    wanted = set(metrics) if metrics else set(DEFAULT_METRICS) | (
        set(_LIPSYNC_METRICS) if want_lipsync else set()
    )

    total_stages = 1 + (1 if want_lipsync else 0) + len(aligns)
    stage = 0

    # 1. Master beat grid (once).
    _emit(progress_cb, stage, total_stages, "beat_grid")
    from mixing.audio import beat_grid

    bg = beat_grid(str(project.song_path()))
    stage += 1
    if _cancelled(should_cancel):
        return {"status": "cancelled", "stage": "beat_grid"}

    # 2. (opt-in) master vocal stem (once).
    vocal_stem = None
    if want_lipsync:
        _emit(progress_cb, stage, total_stages, "separate_vocals")
        from muvid.footage.scoring.lipsync import lipsync_available, separate_master_vocals

        ok, _reason = lipsync_available()
        if ok:
            vocal_stem = separate_master_vocals(
                str(project.song_path()), out_dir=str(project.root / "scores" / "_stems")
            )
        stage += 1
        if _cancelled(should_cancel):
            return {"status": "cancelled", "stage": "separate_vocals"}

    face_fn = F.make_face_scorer()
    fps_kw = {} if sample_fps is None else {"sample_fps": sample_fps}

    tracks_by_clip: dict[str, list] = {}
    shots_by_clip: dict[str, list] = {}
    skipped: dict[str, str] = {}

    for a in aligns:
        _emit(progress_cb, stage, total_stages, f"clip:{a.clip_id}")
        stage += 1
        if _cancelled(should_cancel):
            return {"status": "cancelled", "stage": f"clip:{a.clip_id}"}
        path = clip_paths.get(a.clip_id)
        if not path:
            skipped[a.clip_id] = "no file"
            continue
        clip_tracks = []
        try:
            fp = F.sample_clip_frames(
                str(path), face_fn=face_fn, should_cancel=should_cancel, **fps_kw
            )
        except Exception as e:  # a bad clip must not sink the whole job
            skipped[a.clip_id] = f"decode failed: {e}"
            continue

        qt = quality_tracks(fp, clip_id=a.clip_id, offset_s=a.offset_s, t0=t0, hop_s=hop_s, n=n)
        mt = motionbeat_tracks(
            fp,
            clip_id=a.clip_id,
            offset_s=a.offset_s,
            beat_times=bg.beat_times,
            onset_env=bg.onset_env,
            onset_hop_s=bg.onset_hop_s,
            t0=t0,
            hop_s=hop_s,
            n=n,
        )
        clip_tracks.extend(qt)
        clip_tracks.extend(mt)

        # Optional hard quality gate → AND into every metric's mask for this clip.
        gate = quality_gate_mask(fp, offset_s=a.offset_s, t0=t0, hop_s=hop_s, n=n)
        if not gate.all():
            clip_tracks = [
                _and_mask(tr, gate) for tr in clip_tracks
            ]

        # Shot boundaries (for the selector's beats+shots mode).
        shots_by_clip[a.clip_id] = shot_boundaries(str(path), offset_s=a.offset_s)

        # Opt-in lip-sync tier.
        if want_lipsync and vocal_stem is not None:
            from muvid.footage.scoring.lipsync import lipsync_tracks

            clip_tracks.extend(
                lipsync_tracks(
                    str(path),
                    clip_id=a.clip_id,
                    offset_s=a.offset_s,
                    duration_s=a.duration_s,
                    coverage=a.coverage,
                    vocal_stem_path=str(vocal_stem),
                    t0=t0,
                    hop_s=hop_s,
                    n=n,
                )
            )

        tracks_by_clip[a.clip_id] = [tr for tr in clip_tracks if tr.metric in wanted]

    # Metric axis = the wanted metrics actually produced by ≥1 clip.
    produced = {tr.metric for trs in tracks_by_clip.values() for tr in trs}
    metric_axis = [m for m in _ordered_metrics(want_lipsync) if m in produced and m in wanted]

    import math

    from muvid.footage.scoring.grid import align_fingerprint

    tempo = float(bg.tempo_bpm)
    save_scores(
        project.root,
        tracks_by_clip,
        t0=t0,
        hop_s=hop_s,
        n=n,
        metrics=metric_axis,
        song_hash=project.song_hash(),
        align_fingerprint=align_fingerprint(aligns),
        beat_times=bg.beat_times,
        downbeat_times=bg.downbeat_times,
        extra={
            "shot_boundaries": shots_by_clip,
            "tempo_bpm": round(tempo, 3) if math.isfinite(tempo) else None,
            "sample_fps": sample_fps or F.DEFAULT_SAMPLE_FPS,
            "lipsync_enabled": want_lipsync,
            "skipped": skipped,
        },
    )
    return {
        "status": "ok",
        "metrics": metric_axis,
        "clips_scored": list(tracks_by_clip.keys()),
        "skipped": skipped,
        "n_frames": n,
        "hop_s": hop_s,
        "beats": int(len(bg.beat_times)),
        "lipsync_enabled": want_lipsync,
    }


def _ordered_metrics(want_lipsync: bool) -> list[str]:
    ms = list(DEFAULT_METRICS)
    if want_lipsync:
        ms += list(_LIPSYNC_METRICS)
    return ms


def _and_mask(track, gate):
    import numpy as np

    from muvid.footage.scoring.grid import ScoreTrack

    new_mask = track.mask & gate
    new_vals = np.where(new_mask, track.raw_values, np.nan).astype("float32")
    return ScoreTrack(
        track.clip_id, track.metric, track.t0, track.hop_s, new_vals, new_mask, track.direction
    )
