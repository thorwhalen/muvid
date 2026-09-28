"""Beat signals — continuous envelopes of where the beat is, in the song and in each video.

``beat_grid`` answers *when* the song's beats fall (instants). An editor lining a dancer's
hits up with the music wants more than instants: a **continuous** signal it can look at,
threshold anywhere between its minimum and maximum, and bend towards binary. That is what
this module measures, per piece of media and in that media's OWN time (a clip reaches song
time through its offset, exactly as filmstrips do):

- ``audio_onset`` — the onset-strength envelope ``mixing.audio.beat_grid`` estimates beats
  from (the same estimator ``beat_grid`` uses, so the two never disagree about the song),
  for the song and for every clip that has a soundtrack. The estimator's beat instants and
  tempo come along.
- ``motion`` — subject-motion energy: the mean camera-compensated optical-flow magnitude
  (the scoring layer's ``flow_residual_and_global`` kernel), in frame-heights per second so
  its scale depends little on resolution or sampling rate (not at all is not claimed: the
  flow's window and noise floor are in downscaled pixels).
- ``visual_impact`` — the visual BEAT envelope: how much motion, direction by direction,
  STOPS from one sample to the next — the half-wave-rectified decrease of a
  magnitude-weighted **directogram** (a histogram of flow directions), after Davis &
  Agrawala, *Visual Rhythm and Beat* (SIGGRAPH 2018), whose visual beats are sudden
  decelerations. A hit stopping dead and a change of direction (motion leaving one
  direction bin) both register; motion energy alone misses the turn, which is most of
  what a dance beat looks like. Measured on phone footage of dancers, this deceleration
  flux locked to the song's beat about twice as strongly as the increase or the total
  change did.

Every signal is **unnormalised** and carries its own grid (``t0``, ``hop_s``) and its
``min``, ``max`` and ``p99`` (a robust top a display can scale by), because thresholding is a
view the caller chooses, not something the data should have decided.

Cached per media content hash and parameters at ``footage/beats/<hash>-<kind>-<key>.json``,
beside ``peaks/`` — derived from media the project holds, expensive to make, cheap to keep.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Callable, Optional

import numpy as np

#: Visual signals' rate: samples per second of clip time. A dance beat at ~130 BPM is
#: ~0.46 s, so 15 puts ~7 samples on each beat.
VISUAL_SAMPLE_FPS = float(os.environ.get("MUVID_BEAT_VISUAL_FPS", "15"))
#: Hard cap on the clip time measured (bounds CPU): the first 15 minutes.
VISUAL_MAX_SECONDS = float(os.environ.get("MUVID_BEAT_VISUAL_MAX_SECONDS", "900"))
#: At most this many frame pairs a second are measured (a 120 fps file is thinned).
_MAX_PAIR_RATE = 32.0
#: Downscale factor before the Farneback flow — the same cost bound the scorer uses.
FLOW_DOWNSCALE = 4
#: Direction bins of the directogram (45 degrees each).
DIRECTOGRAM_BINS = 8
#: Flow below this many pixels (downscaled) is sensor noise, not a direction.
_FLOW_NOISE_PX = 0.05
#: The robust top reported beside min/max.
_TOP_PERCENTILE = 99.0
#: Decimals kept on the wire and on disk.
_DECIMALS = 5

#: The record FORMAT's version, part of the cache key: bump it when the same parameters
#: start producing different signals, so an old cache is not served as the new one.
BEAT_SIGNALS_FORMAT = 2
_DIRNAME = "beats"
_HASH_PREFIX = 16

AUDIO_ONSET = "audio_onset"
MOTION = "motion"
VISUAL_IMPACT = "visual_impact"

#: What each signal is, in the words a screen can use.
SIGNAL_LABELS = {
    AUDIO_ONSET: "Sound hits",
    MOTION: "Movement",
    VISUAL_IMPACT: "Moves that land",
}


# -- the record ---------------------------------------------------------------------


def signal_record(
    values, *, t0: float, hop_s: float, name: str, domain: str
) -> dict:
    """One signal on a regular grid: sample ``i`` is at ``t0 + i * hop_s`` seconds of the
    media's own time. Non-finite samples become ``None`` (not measured, never zero)."""
    arr = np.asarray(values, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    stats = (
        (float(finite.min()), float(finite.max()), float(np.percentile(finite, _TOP_PERCENTILE)))
        if finite.size
        else (None, None, None)
    )
    lo, hi, top = (None if s is None else round(s, _DECIMALS) for s in stats)
    return {
        "name": name,
        "label": SIGNAL_LABELS.get(name, name),
        "domain": domain,
        "t0": round(float(t0), 6),
        "hop_s": round(float(hop_s), 6),
        "n": int(arr.size),
        "min": lo,
        "max": hi,
        "p99": top,
        "values": [round(float(v), _DECIMALS) if math.isfinite(v) else None for v in arr],
    }


def decimated(record: dict, max_points: Optional[int]) -> dict:
    """A signal with at most ``max_points`` samples: each kept sample is the MAX of the
    ``k`` it stands for (a beat is a peak; averaging would erase it), on a grid whose
    hop grows by ``k`` and whose ``t0`` moves to the centre of the first block — so a
    pooled peak stays where it was. ``min`` / ``max`` are the full-resolution ones;
    ``p99`` is the POOLED one (max-pooling raises the typical value, and a display
    scaled by the full-resolution p99 would saturate)."""
    n = record["n"]
    if not max_points or n <= max_points:
        return record
    k = math.ceil(n / int(max_points))
    vals = np.array(
        [np.nan if v is None else v for v in record["values"]], dtype=np.float64
    )
    pad = (-n) % k
    if pad:
        vals = np.concatenate([vals, np.full(pad, np.nan)])
    blocks = vals.reshape(-1, k)
    with np.errstate(all="ignore"):
        pooled = np.where(
            np.all(np.isnan(blocks), axis=1), np.nan, np.nanmax(blocks, axis=1)
        )
    finite = pooled[np.isfinite(pooled)]
    hop = record["hop_s"]
    return {
        **record,
        "t0": round(record["t0"] + (k - 1) * hop / 2.0, 6),
        "hop_s": round(hop * k, 6),
        "n": int(pooled.size),
        "p99": (
            round(float(np.percentile(finite, _TOP_PERCENTILE)), _DECIMALS)
            if finite.size
            else None
        ),
        "values": [round(float(v), _DECIMALS) if math.isfinite(v) else None for v in pooled],
    }


# -- audio --------------------------------------------------------------------------


def audio_signals(path) -> dict:
    """``{signals: {audio_onset}, beats, tempo_bpm}`` for a media file's soundtrack, from
    ``mixing.audio.beat_grid``. Raises ``ImportError`` without librosa (the caller names
    the install)."""
    from mixing.audio import beat_grid as estimate

    grid = estimate(str(path))
    hop = float(grid.onset_hop_s)
    env = np.asarray(grid.onset_env, dtype=np.float64)
    tempo = float(grid.tempo_bpm)
    return {
        "signals": {
            AUDIO_ONSET: signal_record(
                env, t0=0.0, hop_s=hop, name=AUDIO_ONSET, domain="audio"
            )
        },
        "beats": [round(float(t), 4) for t in grid.beat_times],
        "tempo_bpm": round(tempo, 3) if math.isfinite(tempo) and tempo > 0 else None,
    }


def has_audio(path) -> bool:
    """Whether a media file carries an audio stream (an unprobeable file: no)."""
    from muvid.visualize.ffmpeg import probe

    try:
        streams = probe(Path(path)).get("streams", [])
    except Exception:  # noqa: BLE001 — unprobeable: treat as silent, never crash a read
        return False
    return any(s.get("codec_type") == "audio" for s in streams)


# -- visual -------------------------------------------------------------------------


def directogram(fx: np.ndarray, fy: np.ndarray, *, bins: int = DIRECTOGRAM_BINS) -> np.ndarray:
    """Flow magnitude summed per direction bin, divided by the pixel count: how much of
    the picture moves which way. Flow under the noise floor votes for no direction."""
    mag = np.hypot(fx, fy).ravel()
    ang = np.arctan2(fy, fx).ravel()
    keep = mag > _FLOW_NOISE_PX
    idx = ((ang[keep] + np.pi) / (2 * np.pi) * bins).astype(int) % bins
    hist = np.bincount(idx, weights=mag[keep], minlength=bins)
    return hist / max(1, mag.size)


def impact_from_directograms(hists: np.ndarray) -> np.ndarray:
    """The visual-beat envelope: per sample, the motion that stopped since the previous
    one, summed over directions (``sum(max(0, h[t-1] - h[t]))``). ``hists`` is
    ``[k, bins]`` with NaN rows where nothing was measured; the first sample, and any
    sample next to a NaN row, is NaN."""
    hists = np.asarray(hists, dtype=np.float64)
    out = np.full(hists.shape[0], np.nan)
    if hists.shape[0] > 1:
        out[1:] = np.clip(-np.diff(hists, axis=0), 0.0, None).sum(axis=1)
    return out


def visual_signals(
    path,
    *,
    sample_fps: float = VISUAL_SAMPLE_FPS,
    max_seconds: float = VISUAL_MAX_SECONDS,
    downscale: int = FLOW_DOWNSCALE,
    bins: int = DIRECTOGRAM_BINS,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    """``{signals: {motion, visual_impact}}`` for a video, in one decode pass.

    Flow is measured between CONSECUTIVE frames (at most ``_MAX_PAIR_RATE`` pairs a
    second) and each pair's rate is averaged into bins of ``1 / sample_fps`` s. That
    average is the low-pass a decimation needs: measuring on every other frame instead
    aliases whatever moves faster than half the sampling rate into a fake slow rhythm
    (on 30 fps phone footage, a steady 3 Hz pulse that is not in the picture).

    Pairs are timed by the frames' TIMESTAMPS, not by index over the container's frame
    rate — a phone file can declare 120 fps and carry 24. Both signals are per SECOND
    and per frame HEIGHT, so the frame rate does not change their scale and the
    resolution changes it little. Bin ``i`` covers ``[i, i + 1) / sample_fps`` s; a bin
    no pair fell in is ``None``.
    """
    import cv2  # lazy: the 'scoring' extra

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")
    hop = 1.0 / max(0.1, float(sample_fps))
    min_gap = 1.0 / _MAX_PAIR_RATE
    mids, motion, hists = [], [], []
    prev_small, prev_t = None, None
    try:
        while cap.grab():
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if t > max_seconds:
                break
            if prev_t is not None and t - prev_t < min_gap:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            small = _small_gray(frame, downscale=downscale)
            if prev_small is not None and t > prev_t:
                fx, fy = _compensated_flow(prev_small, small)
                per_s = 1.0 / ((t - prev_t) * small.shape[0])  # frame-heights per second
                mids.append((t + prev_t) / 2.0)
                motion.append(float(np.mean(np.hypot(fx, fy))) * per_s)
                hists.append(directogram(fx, fy, bins=bins) * per_s)
            prev_small, prev_t = small, t
            if should_cancel is not None and should_cancel():
                break
    finally:
        cap.release()
    return binned_visual_signals(
        np.asarray(mids), np.asarray(motion), np.asarray(hists).reshape(-1, bins), hop
    )


def _small_gray(frame, *, downscale: int):
    import cv2  # lazy

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    if downscale > 1:
        h, w = gray.shape[:2]
        gray = cv2.resize(gray, (max(1, w // downscale), max(1, h // downscale)))
    return gray


def _compensated_flow(prev_gray, gray):
    """Farneback flow with the camera's own move (the median vector) taken out, so a pan
    does not read as the dancer moving — the compensation ``flow_residual_and_global``
    applies for the scorer."""
    import cv2  # lazy

    flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 2, 15, 3, 5, 1.2, 0)
    fx, fy = flow[..., 0], flow[..., 1]
    return fx - float(np.median(fx)), fy - float(np.median(fy))


def binned_visual_signals(mids, motion, hists, hop: float) -> dict:
    """Per-pair rates (at pair midpoints ``mids``) averaged into ``hop``-second bins.

    ``motion`` is each bin's mean, reported at the bin's CENTRE (``t0 = hop / 2``).
    ``visual_impact`` is the deceleration flux between consecutive bin-mean
    directograms, so it belongs to the BOUNDARY between two bins and is reported there
    (``t0 = 0``: sample ``i`` at ``i * hop``, sample 0 unmeasured) — half a hop earlier
    than a centre would put it, which matters once it drives a time-warp. Pairs before
    the clip's first frame (a negative container timestamp) are dropped. Pure numpy —
    the part of the visual pass a test can reach."""
    keep = mids >= 0
    mids, motion, hists = mids[keep], motion[keep], hists[keep]
    if mids.size == 0:
        return {
            "signals": {
                MOTION: signal_record([], t0=hop / 2.0, hop_s=hop, name=MOTION, domain="visual"),
                VISUAL_IMPACT: signal_record([], t0=0.0, hop_s=hop, name=VISUAL_IMPACT, domain="visual"),
            }
        }
    idx = np.floor(mids / hop).astype(int)
    n = int(idx.max()) + 1
    counts = np.bincount(idx, minlength=n).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_motion = np.bincount(idx, weights=motion, minlength=n) / counts
        mean_hists = np.stack(
            [
                np.bincount(idx, weights=hists[:, b], minlength=n) / counts
                for b in range(hists.shape[1])
            ],
            axis=1,
        )
    return {
        "signals": {
            MOTION: signal_record(
                mean_motion, t0=hop / 2.0, hop_s=hop, name=MOTION, domain="visual"
            ),
            VISUAL_IMPACT: signal_record(
                impact_from_directograms(mean_hists),
                t0=0.0,
                hop_s=hop,
                name=VISUAL_IMPACT,
                domain="visual",
            ),
        }
    }


# -- cached, per piece of media -----------------------------------------------------


def _package_version(name: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return "0"


def cache_key(kind: str) -> str:
    """Every parameter that changes the bytes, so a new setting is a new file: the
    record format, the audio estimator's versions (``mixing`` and ``librosa`` — a
    record must never disagree with a fresh ``beat_grid``), and every constant of the
    visual pass."""
    if kind == "audio":
        return (
            f"a{BEAT_SIGNALS_FORMAT}-mix{_package_version('mixing')}"
            f"-lr{_package_version('librosa')}"
        )
    return (
        f"v{BEAT_SIGNALS_FORMAT}-{VISUAL_SAMPLE_FPS:g}fps-d{FLOW_DOWNSCALE}"
        f"-b{DIRECTOGRAM_BINS}-m{VISUAL_MAX_SECONDS:g}s-p{_MAX_PAIR_RATE:g}"
        f"-n{_FLOW_NOISE_PX:g}"
    )


def has_signal(record: dict) -> bool:
    """Whether a measured record carries at least one sample of anything."""
    return any(sig.get("n") for sig in (record.get("signals") or {}).values())


def cached_signals(root: Path, media_hash: str, kind: str, compute: Callable[[], dict]) -> dict:
    """The record for ``(media, kind)``: a file read when it was made before, else
    ``compute()`` written atomically under ``<root>/beats/``.

    One computation per record at a time: a second request for the same record waits
    on a lock and then reads what the first wrote, instead of starting a second
    minutes-long pass (an editor opening every video's channel at once, twice, would
    otherwise run each pass twice). A record with nothing in it is NOT kept — a
    truncated file or an unreadable stream must be measured again next time, not be
    remembered as silence — and ``compute`` should refuse rather than return one."""
    from muvid.footage.media_views import read_json, write_json
    from muvid.footage.workspace import file_lock

    name = f"{media_hash[:_HASH_PREFIX]}-{kind}-{cache_key(kind)}"
    path = Path(root) / _DIRNAME / f"{name}.json"
    cached = read_json(path)
    if cached is not None:
        return cached
    with file_lock(path.with_name(f".{name}.lock")):
        cached = read_json(path)
        if cached is not None:
            return cached
        record = {"media_hash": media_hash, "kind": kind, **compute()}
        if has_signal(record):
            write_json(path, record)
    return record


__all__ = [
    "AUDIO_ONSET",
    "MOTION",
    "VISUAL_IMPACT",
    "SIGNAL_LABELS",
    "audio_signals",
    "visual_signals",
    "has_audio",
    "directogram",
    "impact_from_directograms",
    "signal_record",
    "decimated",
    "cached_signals",
    "cache_key",
    "has_signal",
    "binned_visual_signals",
]
