"""Fitting a cut's timing to the beat — the pure numerics behind ``service.fit_to_beat``.

A cut shows its clip through an affine map of song time (``edl.clip_time_at``):
``clip_in + rate * (t - song_start)`` with ``clip_in = song_start - offset + slip``. So
a movement accent recorded at clip time ``tau`` lands at song time
``song_start + (tau - clip_in) / rate``. Fitting a cut means choosing ``(slip, rate)``
so the clip's accents (``beats.MOTION_STOPS``) land on the song's beat grid.

**The score** of a candidate is the accents' weighted mean alignment with the grid,
``sum(w * cos(2 pi (t - phase) / period)) / sum(w)``: +1 when every accent is on a beat,
-1 when every one is half a beat off, 0 when they ignore the beat. Weights are the
accent strengths above their median, so the steady floor of a moving crowd does not
vote.

**The confidence** of the best fit is a z-score against a null that keeps what the
signal is made of and destroys only its relation to the beat: the samples are cut into
blocks of about two beats and each block is rotated by its own random amount, and the
SAME search is run on every such null signal. The search's own freedom (it will always
find some slip that looks good) is therefore in the null too. A whole-signal rotation
would not do: it only turns every phase by the same angle, which the slip search undoes.

**The window** is wider than a short cut: a 1.5 s cut holds three beats, too few to judge
by. The fit is judged over at least ``window_s`` of the clip around the cut, read
through the same map — the same dancer, a few seconds either side.

Pure numpy; nothing here reads a file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

#: The slips searched (s), either way, and their step: one video frame at 60 fps.
FIT_SLIP_MAX_S = 0.25
FIT_SLIP_STEP_S = 1.0 / 60.0
#: The speeds searched, and their step.
FIT_RATE_MIN = 0.92
FIT_RATE_MAX = 1.08
FIT_RATE_STEP = 0.01
#: Seconds of clip the fit is judged over, at least (centred on the cut).
FIT_WINDOW_S = 8.0
#: Null signals drawn for the confidence, and each null block's length in beats.
FIT_NULL_DRAWS = 200
FIT_NULL_BLOCK_BEATS = 2.0
#: A cut shorter than this many beats is not fitted.
FIT_MIN_BEATS = 3.0


@dataclass(frozen=True)
class BeatGrid:
    """A steady beat: beat ``k`` falls at ``phase + k * period`` seconds of the song."""

    period: float
    phase: float

    @classmethod
    def fitted(cls, beats: Sequence[float], *, min_beats: int = 8) -> "Optional[BeatGrid]":
        """The least-squares grid through tracked beat instants (each beat numbered by
        ``beats._beat_numbers``, so a skipped or spurious beat does not bend it), or
        ``None`` when the beats are too few or not steady (``beats.fitted_tempo``)."""
        from muvid.footage.beats import _beat_numbers, fitted_tempo

        bpm = fitted_tempo(beats, min_beats=min_beats)
        if bpm is None:
            return None
        b = np.asarray(beats, dtype=np.float64)
        period = 60.0 / bpm
        k, own = _beat_numbers(b, period)
        slope, icept = np.polyfit(k[own], b[own], 1)
        return cls(period=float(slope), phase=float(icept))


@dataclass(frozen=True)
class Fit:
    """The best ``(slip, rate)`` for one cut, its score, the current setting's score,
    and the z of the best score against the null."""

    slip_s: float
    rate: float
    score: float
    current_score: float
    z: float
    n_accents: int


def _weights(values: np.ndarray) -> np.ndarray:
    v = np.where(np.isfinite(values), values, np.nan)
    floor = np.nanmedian(v) if np.isfinite(v).any() else 0.0
    return np.nan_to_num(np.clip(v - floor, 0.0, None))


def _scores(tau, w, *, song_start, offset, slips, rates, grid, window):
    """Score of every ``(slip, rate)`` pair: a ``[len(slips), len(rates)]`` array, NaN
    where no accent lands in ``window`` (song time)."""
    slips = np.asarray(slips)[:, None, None]
    rates = np.asarray(rates)[None, :, None]
    clip_in = song_start - offset + slips
    t = song_start + (tau[None, None, :] - clip_in) / rates
    inside = (t >= window[0]) & (t <= window[1])
    ww = np.where(inside, w[None, None, :], 0.0)
    total = ww.sum(axis=2)
    cos = np.cos(2 * np.pi * (t - grid.phase) / grid.period)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(total > 0, (ww * cos).sum(axis=2) / total, np.nan)


def _block_rotated(w: np.ndarray, block: int, rng) -> np.ndarray:
    out = np.empty_like(w)
    for a in range(0, w.size, block):
        seg = w[a : a + block]
        out[a : a + block] = np.roll(seg, int(rng.integers(0, max(1, seg.size))))
    return out


def fit_cut(
    tau: np.ndarray,
    values: np.ndarray,
    *,
    song_start: float,
    song_end: float,
    offset: float,
    current_slip: float = 0.0,
    current_rate: float = 1.0,
    grid: BeatGrid,
    feasible=None,
    window_s: float = FIT_WINDOW_S,
    slips: "Sequence[float] | None" = None,
    rates: "Sequence[float] | None" = None,
    null_draws: int = FIT_NULL_DRAWS,
    seed: int = 0,
) -> Optional[Fit]:
    """The best timing for a cut over song time ``[song_start, song_end]`` of a clip at
    ``offset``, from the clip's accent signal ``values`` sampled at clip times ``tau``.

    ``feasible(slip, rate) -> bool`` rules out settings the clip cannot hold. ``None``
    when no accent lands in the window under any setting.
    """
    tau = np.asarray(tau, dtype=np.float64)
    w = _weights(np.asarray(values, dtype=np.float64))
    keep = w > 0
    if not keep.any():
        return None
    slips = np.asarray(
        slips
        if slips is not None
        else np.round(np.arange(-FIT_SLIP_MAX_S, FIT_SLIP_MAX_S + 1e-9, FIT_SLIP_STEP_S), 6)
    )
    rates = np.asarray(
        rates
        if rates is not None
        else np.round(np.arange(FIT_RATE_MIN, FIT_RATE_MAX + 1e-9, FIT_RATE_STEP), 4)
    )
    mid = (song_start + song_end) / 2.0
    half = max(window_s, song_end - song_start) / 2.0
    window = (mid - half, mid + half)
    # Only the samples any candidate could put in the window: the search then costs
    # the window's length, not the clip's.
    reach = FIT_SLIP_MAX_S + (FIT_RATE_MAX - 1.0) * half * 2 + 0.5
    near = (tau >= window[0] - offset - reach) & (tau <= window[1] - offset + reach)
    tau, w = tau[near], w[near]
    keep = w > 0
    if not keep.any():
        return None
    kw = dict(song_start=song_start, offset=offset, grid=grid, window=window)
    mask = np.ones((slips.size, rates.size), dtype=bool)
    if feasible is not None:
        mask = np.array([[bool(feasible(float(s), float(r))) for r in rates] for s in slips])
    if not mask.any():
        return None

    def best(weights) -> float:
        sc = _scores(tau, weights, slips=slips, rates=rates, **kw)
        sc = np.where(mask, sc, np.nan)
        return float(np.nanmax(sc)) if np.isfinite(sc).any() else np.nan

    sc = np.where(mask, _scores(tau, w, slips=slips, rates=rates, **kw), np.nan)
    if not np.isfinite(sc).any():
        return None
    i, j = np.unravel_index(np.nanargmax(sc), sc.shape)
    top = float(sc[i, j])
    cur = _scores(tau, w, slips=[current_slip], rates=[current_rate], **kw)[0, 0]
    # The null: the same search on block-rotated signals (block ~ two beats).
    hop = float(np.median(np.diff(tau))) if tau.size > 1 else grid.period
    block = max(2, int(round(FIT_NULL_BLOCK_BEATS * grid.period / max(hop, 1e-6))))
    rng = np.random.default_rng(seed)
    null = np.array([best(_block_rotated(w, block, rng)) for _ in range(null_draws)])
    null = null[np.isfinite(null)]
    sd = float(null.std()) if null.size > 1 else 0.0
    z = (top - float(null.mean())) / sd if sd > 0 else 0.0
    t_in = song_start + (tau - (song_start - offset + slips[i])) / rates[j]
    n = int(((t_in >= window[0]) & (t_in <= window[1]) & keep).sum())
    return Fit(
        slip_s=float(slips[i]),
        rate=float(rates[j]),
        score=top,
        current_score=float(cur) if np.isfinite(cur) else float("nan"),
        z=float(z),
        n_accents=n,
    )


__all__ = ["BeatGrid", "Fit", "fit_cut", "FIT_MIN_BEATS", "FIT_WINDOW_S"]
