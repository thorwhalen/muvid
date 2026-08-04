"""Motion-to-beat tier: ``motion_beat_bas`` + ``motion_onset_xcorr`` → song-grid tracks.

Pure given a :class:`~muvid.footage.scoring.frames.FramePass` (the camera-compensated motion
envelope) + the master :class:`mixing.audio.BeatGrid` (computed ONCE on the clean song). No
per-clip beat/onset recomputation — everything maps to song time via the clip's offset.

- ``motion_beat_bas`` — a per-beat Beat Alignment Score (AIST++ [11] idea, localized): each
  audio beat scores how close its nearest MOTION peak is (``exp(−(Δt/σ)²/2)``); the score is
  held over that beat's interval. NA where the clip has no motion (static / no person) — never
  0 (a 0 would penalize valid instrumental footage). Person-present signal.
- ``motion_onset_xcorr`` — the clip-level normalized cross-correlation of the motion envelope
  vs the master onset envelope at the best lag within a bounded A/V-latency window (the lag
  refines per-clip capture latency). Content-agnostic (covers no-person clips). Held over the
  clip's coverage.

Commercial-clean (librosa beats via ``mixing[beats]``, numpy motion). See the design §3b.
"""

from __future__ import annotations

import numpy as np

from muvid.footage.scoring.frames import FramePass
from muvid.footage.scoring.grid import ScoreTrack, resample_to_grid

#: Gaussian width (s) for beat↔motion-peak proximity in the BAS.
_BAS_SIGMA_S = 0.12
#: Bounded lag search for the onset xcorr (±s) — also the plausible A/V capture-latency range.
_XCORR_MAX_LAG_S = 1.0

MOTIONBEAT_METRICS = ("motion_beat_bas", "motion_onset_xcorr")


def motionbeat_tracks(
    frame_pass: FramePass,
    *,
    clip_id: str,
    offset_s: float,
    beat_times: np.ndarray,
    onset_env: np.ndarray,
    onset_hop_s: float,
    t0: float,
    hop_s: float,
    n: int,
) -> list[ScoreTrack]:
    """The two motion-to-beat tracks for one clip."""
    # A motion sample measured between frames k-1 and k represents the INTERVAL midpoint, so
    # shift the motion sample times back half a frame before gridding (else the envelope — and
    # thus the on-beat BAS — reads ~half a sample late). Quality metrics keep clip_times as-is.
    half = (
        0.5 * float(np.median(np.diff(frame_pass.clip_times)))
        if frame_pass.clip_times.size >= 2
        else 0.0
    )
    song_t = frame_pass.clip_times + offset_s - half
    # Motion envelope on the song grid (camera-compensated residual; NaN @ frame 0 dropped).
    motion, mmask = resample_to_grid(
        song_t, frame_pass.motion_residual, t0=t0, hop_s=hop_s, n=n
    )
    cover = (song_t[0] if song_t.size else 0.0, song_t[-1] if song_t.size else 0.0)

    bas_vals, bas_mask = _bas_track(
        motion, mmask, np.asarray(beat_times, dtype=float), t0, hop_s, n, cover
    )  # cover uses the (shifted) song_t span; the shift is sub-frame so coverage is unaffected
    xcorr_vals, xcorr_mask = _onset_xcorr_track(
        motion, mmask, onset_env, onset_hop_s, t0, hop_s, n, cover
    )
    return [
        ScoreTrack(clip_id, "motion_beat_bas", t0, hop_s, bas_vals, bas_mask, "higher_better"),
        ScoreTrack(
            clip_id, "motion_onset_xcorr", t0, hop_s, xcorr_vals, xcorr_mask, "higher_better"
        ),
    ]


def _motion_peaks(motion: np.ndarray, mask: np.ndarray, t0: float, hop_s: float) -> np.ndarray:
    """Song-times of local maxima of the motion envelope above a robust threshold."""
    m = np.where(mask, motion, np.nan)
    finite = m[np.isfinite(m)]
    if finite.size < 3:
        return np.asarray([], dtype=float)
    med = np.median(finite)
    mad = np.median(np.abs(finite - med)) or (finite.std() or 1.0)
    thr = med + 1.0 * mad
    peaks = []
    for k in range(1, len(m) - 1):
        if not (np.isfinite(m[k]) and np.isfinite(m[k - 1]) and np.isfinite(m[k + 1])):
            continue
        if m[k] >= m[k - 1] and m[k] > m[k + 1] and m[k] >= thr:
            peaks.append(t0 + k * hop_s)  # absolute song time (matches _bas_track's beats)
    return np.asarray(peaks, dtype=float)


def _bas_track(motion, mmask, beat_times, t0, hop_s, n, cover):
    vals = np.full(n, np.nan, dtype=np.float32)
    mask = np.zeros(n, dtype=bool)
    peaks = _motion_peaks(motion, mmask, t0, hop_s)
    lo, hi = cover
    beats = beat_times[(beat_times >= lo - _EPS_T) & (beat_times <= hi + _EPS_T)]
    if peaks.size == 0 or beats.size == 0:
        return vals, mask  # no motion peaks / no beats in coverage → NA (never 0)
    beats_sorted = np.sort(beats)
    for bi, b in enumerate(beats_sorted):
        # proximity of the nearest motion peak to this beat
        dt = np.min(np.abs(peaks - b))
        score = float(np.exp(-0.5 * (dt / _BAS_SIGMA_S) ** 2))
        end = beats_sorted[bi + 1] if bi + 1 < len(beats_sorted) else hi
        k0 = max(0, int(np.floor((b - t0) / hop_s)))
        k1 = min(n, int(np.ceil((end - t0) / hop_s)))
        # only over the motion-covered part of the interval
        for k in range(k0, k1):
            if mmask[k]:
                vals[k] = score
                mask[k] = True
    return vals, mask


def _onset_xcorr_track(motion, mmask, onset_env, onset_hop_s, t0, hop_s, n, cover):
    """Clip-level normalized xcorr strength (best lag within ±_XCORR_MAX_LAG_S), held over
    the motion-covered span."""
    vals = np.full(n, np.nan, dtype=np.float32)
    mask = np.zeros(n, dtype=bool)
    valid = np.flatnonzero(mmask)
    if valid.size < 4 or onset_env is None or len(onset_env) < 4:
        return vals, mask
    lo_k, hi_k = valid[0], valid[-1]
    # Resample the master onset envelope onto the SAME song grid over the covered span.
    onset_times = np.arange(len(onset_env)) * onset_hop_s
    onset_on_grid, on_mask = resample_to_grid(
        onset_times, np.asarray(onset_env, float), t0=t0, hop_s=hop_s, n=n
    )
    a = np.where(mmask, motion, np.nan)[lo_k : hi_k + 1]
    b = np.where(on_mask, onset_on_grid, np.nan)[lo_k : hi_k + 1]
    both = np.isfinite(a) & np.isfinite(b)
    if np.count_nonzero(both) < 4:
        return vals, mask
    a = np.nan_to_num(a - np.nanmean(a[both]))
    b = np.nan_to_num(b - np.nanmean(b[both]))
    max_lag = int(round(_XCORR_MAX_LAG_S / hop_s))
    best = 0.0
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    for lag in range(-max_lag, max_lag + 1):
        r = float(np.dot(a, np.roll(b, lag))) / denom
        best = max(best, abs(r))
    vals[lo_k : hi_k + 1] = np.where(mmask[lo_k : hi_k + 1], best, np.nan)
    mask[lo_k : hi_k + 1] = mmask[lo_k : hi_k + 1]
    return vals, mask


_EPS_T = 1e-6
