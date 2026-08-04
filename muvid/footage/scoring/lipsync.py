"""Lip-sync tier (OPT-IN, OFF BY DEFAULT): SyncNet LSE-C vs the master's Demucs vocal stem.

**Not on the prod path.** The design's LOCKED decision: this tier is behind the
``muvid[scoring-lipsync]`` extra and disabled by default because (a) Demucs + SyncNet on CPU
peak ~2–3 GB and would OOM the memory-fragile connector, and (b) the htdemucs weights are
**CC-BY-NC (research-only)** — not commercial-clean. So it runs only on a local/worker box the
operator opts into, and it requires the operator to POINT AT weights via env vars (rather than
this package downloading questionable weights at runtime):

- ``MUVID_SYNCNET_S3FD_WEIGHTS`` — the S3FD face-detector weights.
- ``MUVID_SYNCNET_WEIGHTS`` — the SyncNet model weights.

If either is unset, or ``demucs``/``syncnet-python`` is not installed, the extractor is
**skipped** (returns ``[]`` + a reason) — never a crash, never a silent 0.

Pipeline (design §3c): separate the master vocal stem ONCE (orchestrator), then per clip run
SyncNet's face-detect → track → mouth-crop → per-window LSE-C **against the master vocal stem
at the known offset** (a validation, not a search). Emit ``lip_sync_lse_c`` + ``lse_d_offset``,
gated to NA where no singing face is present (never 0).

⚠ This module's heavy glue (Demucs + SyncNetPipeline) cannot run in CI or on the dev box
(deps absent) — it needs a live validation pass on a machine with the extra + weights before
first real use. It is structured to fail safe (skip) everywhere else.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

from muvid.footage.scoring.grid import ScoreTrack, resample_to_grid

logger = logging.getLogger(__name__)

LIPSYNC_METRICS = ("lip_sync_lse_c", "lse_d_offset")


def lipsync_available() -> tuple[bool, str]:
    """``(ok, reason)`` — whether the opt-in lip-sync tier can run here."""
    try:
        import demucs  # noqa: F401
    except Exception:
        return False, "demucs not installed (muvid[scoring-lipsync])"
    try:
        import syncnet_python  # noqa: F401
    except Exception:
        return False, "syncnet-python not installed (muvid[scoring-lipsync])"
    if not os.environ.get("MUVID_SYNCNET_S3FD_WEIGHTS") or not os.environ.get(
        "MUVID_SYNCNET_WEIGHTS"
    ):
        return False, "SyncNet weights not configured (MUVID_SYNCNET_*_WEIGHTS)"
    return True, "ok"


def separate_master_vocals(song_path: str, *, out_dir: str) -> Path | None:
    """Demucs → the master vocal stem as a wav (``out_dir/vocals.wav``), or ``None``.

    Computed ONCE on the clean master; the resulting stem is passed to every clip's SyncNet
    call as the co-temporal reference audio. Returns ``None`` (never raises) if Demucs is
    absent — the caller then skips lip-sync.
    """
    try:
        from demucs.api import Separator, save_audio  # lazy, heavy (torch)
    except Exception:
        return None
    try:
        model = os.environ.get("MUVID_DEMUCS_MODEL", "htdemucs")
        separator = Separator(model=model, segment=int(os.environ.get("MUVID_DEMUCS_SEGMENT", "7")))
        _origin, stems = separator.separate_audio_file(str(song_path))
        vocals = stems.get("vocals")
        if vocals is None:
            return None
        out = Path(out_dir) / "vocals.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        save_audio(vocals, str(out), samplerate=separator.samplerate)
        return out
    except Exception:
        return None


def _slice_stem(vocal_stem_path: str, *, start_s: float, dur_s: float, out_dir: str) -> str:
    """Write the ``[start_s, start_s+dur_s]`` slice of the master vocal stem to a temp wav.

    So SyncNet's audio-frame-0 lines up with the clip's video-frame-0 (the clip is aligned to
    the master at ``offset_s``). Falls back to the whole stem if soundfile is unavailable.
    """
    try:
        import soundfile as sf  # lazy
    except Exception:
        return vocal_stem_path
    try:
        info = sf.info(vocal_stem_path)
        sr = info.samplerate
        a = max(0, int(round(start_s * sr)))
        b = max(a + 1, int(round((start_s + dur_s) * sr)))
        data, _ = sf.read(vocal_stem_path, start=a, stop=b, dtype="float32")
        out = Path(out_dir) / "vocals_slice.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), data, sr)
        return str(out)
    except Exception:
        return vocal_stem_path


def lipsync_tracks(
    clip_path: str,
    *,
    clip_id: str,
    offset_s: float,
    duration_s: float,
    coverage: tuple,
    vocal_stem_path: str,
    t0: float,
    hop_s: float,
    n: int,
    device: str = "cpu",
) -> list[ScoreTrack]:
    """Per-clip LSE-C / LSE-D tracks vs the OFFSET-ALIGNED master vocal stem, or ``[]``.

    Feeds SyncNet the co-temporal slice of the master vocals (``[offset_s, offset_s+dur]``) so
    the score is meaningful for clips whose offset exceeds SyncNet's tiny internal search;
    holds the per-face-track LSE-C over that track's span, clamped to the clip's coverage
    (never fabricates lip-sync beyond the clip); spans with no detected face are NA.
    """
    ok, _reason = lipsync_available()
    if not ok:
        return []
    try:
        from syncnet_python import SyncNetPipeline  # lazy, heavy (torch)
    except Exception:
        return []
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = _slice_stem(
            vocal_stem_path, start_s=max(0.0, offset_s), dur_s=duration_s, out_dir=tmp
        )
        try:
            pipeline = SyncNetPipeline(
                s3fd_weights=os.environ["MUVID_SYNCNET_S3FD_WEIGHTS"],
                syncnet_weights=os.environ["MUVID_SYNCNET_WEIGHTS"],
                device=device,
            )
            (
                _offset_list,
                confidence_list,
                min_dist_list,
                _best_conf,
                _best_dist,
                detections_json,
                success,
            ) = pipeline.inference(video_path=str(clip_path), audio_path=str(audio_path))
        except Exception:
            # Fail safe (skip lip-sync) BUT log — a misconfigured worker must be
            # distinguishable from a genuine "no singing face" skip.
            logger.warning("SyncNet lip-sync failed for clip %s", clip_id, exc_info=True)
            return []
    if not success or not confidence_list:
        return []

    lo, hi = coverage
    lse_c_times, lse_c_vals = [], []
    lse_d_times, lse_d_vals = [], []
    spans = _detection_spans(detections_json, duration_s=duration_s)
    fps = _detections_fps(detections_json) or 25.0
    for ti, (f0, f1) in enumerate(spans):
        conf = float(confidence_list[ti]) if ti < len(confidence_list) else float(confidence_list[0])
        dist = float(min_dist_list[ti]) if ti < len(min_dist_list) else float(min_dist_list[0])
        for f in range(f0, f1):
            st = f / fps + offset_s
            if st < lo - hop_s or st > hi + hop_s:  # never beyond the clip's coverage
                continue
            lse_c_times.append(st)
            lse_c_vals.append(conf)
            lse_d_times.append(st)
            lse_d_vals.append(dist)

    if not lse_c_times:
        return []
    c_vals, c_mask = resample_to_grid(lse_c_times, lse_c_vals, t0=t0, hop_s=hop_s, n=n)
    d_vals, d_mask = resample_to_grid(lse_d_times, lse_d_vals, t0=t0, hop_s=hop_s, n=n)
    return [
        ScoreTrack(clip_id, "lip_sync_lse_c", t0, hop_s, c_vals, c_mask, "higher_better"),
        ScoreTrack(clip_id, "lse_d_offset", t0, hop_s, d_vals, d_mask, "lower_better"),
    ]


def _detection_spans(detections_json, *, duration_s: float, fps: float = 25.0) -> list[tuple[int, int]]:
    """Best-effort (start_frame, end_frame) per face track from SyncNet's detections JSON.

    Defensive: SyncNet's detections shape varies by version; on a parse miss return ONE span
    bounded by the clip's own duration (never the old fabricated 10_000 frames, which invented
    ~400 s of "valid" lip-sync far beyond the clip). The caller also clamps to coverage.
    This glue needs live validation (see the module docstring).
    """
    try:
        tracks = detections_json.get("tracks") if isinstance(detections_json, dict) else detections_json
        spans = []
        for tr in tracks or []:
            frames = tr.get("frame") if isinstance(tr, dict) else None
            if frames:
                spans.append((int(min(frames)), int(max(frames)) + 1))
        if spans:
            return spans
    except Exception:
        pass
    return [(0, max(1, int(round(duration_s * fps))))]


def _detections_fps(detections_json) -> float | None:
    try:
        if isinstance(detections_json, dict):
            return float(detections_json.get("fps")) if detections_json.get("fps") else None
    except Exception:
        return None
    return None
