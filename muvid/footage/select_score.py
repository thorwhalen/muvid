"""The score-driven ``weighted`` selection strategy: a beat-snapped semi-Markov Viterbi DP.

Given the fused score tensor ``S[clip, frame, metric]`` (from :mod:`muvid.footage.scoring.grid`)
and the master beat grid, choose which clip is on-air over each span of the song so that the
weighted composite reward is maximized, cuts land only on beat (or beat∪shot) boundaries,
shot lengths obey ``[L_min, L_max]``, and each switch pays a Potts penalty ``λ_switch``. The
same optimizer the Phase-2 editor steers (a manual pin becomes a hard constraint a re-solve
fills around).

The recurrence is the corrected form from the design's algorithm review
(``misc/docs/footage_scoring_design.md`` §4c′) — the load-bearing details:

- **Feasibility is CONTINUOUS containment** (``b_i ≥ offset_c − _EPS`` and
  ``b_j ≤ offset_c + duration_c + _EPS``), the *exact* check ``validate_edl`` runs — NOT a
  grid-frame mask (a sub-hop overhang would slip past a 10 Hz mask and make ``validate_edl``
  raise). So the emitted EDL passes ``validate_edl`` by construction.
- **Reward is a TIME integral of the normalized composite** ``ĝ = Σ_m w_m·S·M / W ∈ [0,1]``:
  ``R(c,i,j) = hop_s·(P[c][κ(b_j)] − P[c][κ(b_i)])`` (prefix-summed → O(1) per segment,
  hop/tempo-independent). A switch then costs ``λ_switch`` directly in those units
  ("a cut must earn ``λ_switch`` seconds of perfect-footage reward").
- **L_min is relaxed on the first & last segment**; an infeasible timeline is *classified*
  (coverage gap vs dwell-infeasible) and retried with ``L_min→0`` before any fallback —
  never a silent junk EDL.
- The ``allowed(i,j)`` clip-domain hook + the injectable boundary set make the Phase-2
  manual-pin re-solve a *pruning*, not a reformulation.

numpy only (no cv2/torch): registered LAZILY in :mod:`muvid.footage.strategy` so
``import muvid.footage`` never pulls numpy.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

import numpy as np

from muvid.footage.edl import (
    MAX_EDL_ENTRIES,
    EdlEntry,
    FootageAlignment,
    _EPS,
    validate_edl,
)

_NEG_INF = float("-inf")

#: Default per-metric weights. A metric absent from the tensor collapses to weight 0
#: (its column simply doesn't exist), so a project scored without the lip-sync tier still
#: selects cleanly on the metrics it has.
DEFAULT_WEIGHTS: dict[str, float] = {
    "lip_sync_lse_c": 1.0,
    "motion_beat_bas": 0.8,
    "motion_onset_xcorr": 0.5,
    "sharpness": 0.4,
    "exposure": 0.3,
    "face_framing": 0.4,
    "stability_shake": 0.3,
}


@dataclass(frozen=True)
class WeightedSelectionConfig:
    """The score-driven "strategy" as a pure config object (open-closed).

    Adding a metric = a tensor column + a weight; no new strategy code.

    ``l_max_s`` is a SOFT target: a shot longer than it pays ``l_max_overrun_penalty`` per
    second of overrun (in the same "reward-seconds" units as ``lambda_switch``), rather than
    being forbidden. So a lone/best clip rolls past ``l_max_s`` when the only alternative is
    materially worse (no forced cutaway to bad footage), yet comparable clips still cut around
    ``l_max_s`` for variety. Segments are always different-clip (each = one shot).
    """

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    lambda_switch: float = 0.35  # seconds of perfect-footage reward a cut must earn
    l_min_s: float = 1.2  # min shot length (relaxed on the first & last segment)
    l_max_s: float = 8.0  # soft max shot length (overrun is penalized, not forbidden)
    l_max_overrun_penalty: float = 0.15  # reward-seconds lost per second past l_max_s
    boundary_mode: str = "beats"  # "beats" | "beats+shots"
    beat_unit: str = "beat"  # "beat" | "downbeat"

    def to_dict(self) -> dict:
        return {
            "weights": dict(self.weights),
            "lambda_switch": self.lambda_switch,
            "l_min_s": self.l_min_s,
            "l_max_s": self.l_max_s,
            "l_max_overrun_penalty": self.l_max_overrun_penalty,
            "boundary_mode": self.boundary_mode,
            "beat_unit": self.beat_unit,
        }


#: Named presets — a filled-in config. "energetic" = many short cuts; "contemplative" = long
#: dwells, few cuts.
PRESETS: dict[str, WeightedSelectionConfig] = {
    "energetic": WeightedSelectionConfig(
        weights={**DEFAULT_WEIGHTS, "motion_beat_bas": 1.0, "motion_onset_xcorr": 0.8},
        lambda_switch=0.2,
        l_min_s=0.8,
        l_max_s=4.0,
        l_max_overrun_penalty=0.25,  # push cuts (short energetic shots)
    ),
    "contemplative": WeightedSelectionConfig(
        weights={**DEFAULT_WEIGHTS, "sharpness": 0.6, "face_framing": 0.6},
        lambda_switch=0.6,
        l_min_s=3.0,
        l_max_s=12.0,
        l_max_overrun_penalty=0.05,  # tolerate long dwells
    ),
}


def resolve_config(
    *,
    preset: str | None = None,
    weights: Mapping[str, float] | None = None,
    config: Mapping | None = None,
) -> WeightedSelectionConfig:
    """Build a :class:`WeightedSelectionConfig` from an optional preset + overrides.

    Precedence: preset (or the default) < ``config`` dict fields < explicit ``weights``.
    """
    base = PRESETS.get(preset) if preset else None
    cfg = base or WeightedSelectionConfig()
    if config:
        known = {
            k: config[k]
            for k in (
                "lambda_switch",
                "l_min_s",
                "l_max_s",
                "l_max_overrun_penalty",
                "boundary_mode",
                "beat_unit",
            )
            if k in config
        }
        if "weights" in config and config["weights"]:
            known["weights"] = {**cfg.weights, **dict(config["weights"])}
        cfg = replace(cfg, **known)
    if weights:
        cfg = replace(cfg, weights={**cfg.weights, **dict(weights)})
    return cfg


@dataclass(frozen=True)
class SelectionContext:
    """Everything the score-driven strategy needs beyond ``(alignments, song_duration)``.

    ``tensor`` is the fused :class:`~muvid.footage.scoring.grid.ScoreTensor` (or ``None`` → the
    strategy raises a clear "run scoring first"). ``shot_boundaries`` (clip_id → song-times)
    is consumed only when ``config.boundary_mode == "beats+shots"``. ``pins`` (Phase 2) is a
    list of ``(song_start, song_end, clip_id)`` hard constraints.
    """

    tensor: object | None  # ScoreTensor (typed loosely to avoid importing grid eagerly here)
    beat_times: Sequence[float] = ()
    downbeat_times: Sequence[float] = ()
    shot_boundaries: Mapping[str, Sequence[float]] | None = None
    pins: Sequence[tuple] | None = None
    config: WeightedSelectionConfig = field(default_factory=WeightedSelectionConfig)


# ---------------------------------------------------------------------------
# The registered strategy
# ---------------------------------------------------------------------------


def weighted_selection(
    alignments: Sequence[FootageAlignment],
    song_duration: float,
    *,
    context: SelectionContext | None = None,
) -> list[EdlEntry]:
    """The ``weighted`` :data:`~muvid.footage.strategy.SelectionStrategy` (score-driven DP).

    Requires a :class:`SelectionContext` carrying a score tensor. Raises a clear error if
    scores are absent so the MCP layer can say "run muvid_score_footage first".
    """
    if context is None or context.tensor is None:
        raise ValueError(
            "the 'weighted' strategy needs score tracks — run scoring first "
            "(muvid_score_footage), then assemble with strategy='weighted'"
        )
    entries, _meta = run_weighted(alignments, song_duration, context)
    return entries


def run_weighted(
    alignments: Sequence[FootageAlignment],
    song_duration: float,
    context: SelectionContext,
) -> tuple[list[EdlEntry], dict]:
    """Run the DP and return ``(edl, meta)``; ``meta`` carries any fallback + its cause."""
    tensor = context.tensor
    cfg = context.config
    aligns = list(alignments)
    if not aligns:
        return [], {"fallback": "empty", "cause": "no_alignments"}
    # v1 grids are t0=0 (the persistence + _kappa handle t0, but the boundary set and
    # margins assume it); assert rather than silently mis-map a non-zero-origin grid.
    assert tensor.t0 == 0.0, (
        f"weighted selector assumes a t0=0 grid; got t0={tensor.t0}"
    )

    boundaries = _boundary_set(aligns, tensor, context, cfg)
    composite, W = _composite(tensor, cfg.weights, aligns)
    if W <= 0:
        # No weighted metric present in the tensor → nothing to optimize; defer to the
        # alignment-only default rather than emit an arbitrary path.
        return _fallback(aligns, song_duration, cause="no_weighted_metrics")

    entries, cause = _viterbi(aligns, boundaries, composite, tensor, cfg)
    solved_cfg = cfg  # the config that actually produced `entries` (escalate on THIS)
    if entries is None:
        # Infeasible: classify, retry with the L_min floor dropped, then fall back.
        if cause == "coverage_gap":
            # best_confidence will surface the same gap via validate_edl — keep the UX identical.
            return _fallback(aligns, song_duration, cause="coverage_gap")
        # Drop L_min (a required mid-song short segment can be < L_min). L_max is already
        # soft, so this is the only shot-length relaxation needed. Guaranteed feasible for a
        # gapless timeline, so this is the real safety net — best_confidence is last resort.
        relaxed = replace(cfg, l_min_s=0.0)
        entries, cause2 = _viterbi(aligns, boundaries, composite, tensor, relaxed)
        if entries is None:
            return _fallback(aligns, song_duration, cause=cause2 or "infeasible")
        solved_cfg = relaxed
        cause = "dwell_relaxed"

    # Escalate lambda_switch (on the config that solved) if the cut count blows the cap.
    lam = solved_cfg.lambda_switch
    tries = 0
    while len(entries) > MAX_EDL_ENTRIES and tries < 6:
        lam *= 2.0
        escalated, _ = _viterbi(
            aligns, boundaries, composite, tensor, replace(solved_cfg, lambda_switch=lam)
        )
        if escalated is None:  # feasibility is λ-independent, so this shouldn't happen
            break  # keep the last good `entries`
        entries = escalated
        tries += 1
    if len(entries) > MAX_EDL_ENTRIES:
        raise ValueError(
            f"the weighted edit needs {len(entries)} cuts (> the {MAX_EDL_ENTRIES} cap); "
            "raise lambda_switch or l_min_s"
        )

    validated = validate_edl(entries, aligns, song_duration)  # tautology by construction
    meta = {
        "strategy": "weighted",
        "cuts": len(validated),
        "cause": cause,
        "config": cfg.to_dict(),
        "lambda_effective": lam,
    }
    return validated, meta


# ---------------------------------------------------------------------------
# Boundaries, composite, prefix sums
# ---------------------------------------------------------------------------


def _cover_span(aligns: Sequence[FootageAlignment]) -> tuple[float, float]:
    return (
        min(a.coverage[0] for a in aligns),
        max(a.coverage[1] for a in aligns),
    )


def _boundary_set(aligns, tensor, context: SelectionContext, cfg) -> list[float]:
    cover_start, cover_end = _cover_span(aligns)
    beats = context.beat_times
    if cfg.beat_unit == "downbeat" and len(context.downbeat_times):
        beats = context.downbeat_times
    pts = {round(cover_start, 6), round(cover_end, 6)}
    for b in beats:
        if cover_start - _EPS <= b <= cover_end + _EPS:
            pts.add(round(float(b), 6))
    if cfg.boundary_mode == "beats+shots" and context.shot_boundaries:
        for _cid, shots in context.shot_boundaries.items():
            for s in shots:
                if cover_start - _EPS <= s <= cover_end + _EPS:
                    pts.add(round(float(s), 6))
    if context.pins:
        for ps, pe, _cid in context.pins:
            for p in (ps, pe):
                if cover_start - _EPS <= p <= cover_end + _EPS:
                    pts.add(round(float(p), 6))
    hop = tensor.hop_s
    B = _dedup(sorted(pts), tol=hop / 2.0)
    # Pin the exact endpoints (invariant: b_0=cover_start, b_m=cover_end). _dedup keeps the
    # rightmost of a near-tie, so a beat just after cover_start would otherwise replace it and
    # drop the head span; a beat just before cover_end would drop the tail.
    if B:
        B[0] = round(cover_start, 6)
        B[-1] = round(cover_end, 6)
    return B


def _dedup(xs: Sequence[float], *, tol: float) -> list[float]:
    out: list[float] = []
    for x in xs:
        if not out or x - out[-1] > tol:
            out.append(x)
        else:
            out[-1] = x  # keep the later (rightmost) of a near-tie
    return out


def _composite(
    tensor, weights: Mapping[str, float], aligns
) -> tuple[np.ndarray, float]:
    """``ĝ[clip_in_align_order, frame]`` = Σ_m w_m·S·M / W ∈ [0,1]; also returns W.

    Rows are ordered to match ``aligns`` (the selector indexes clips by alignment order).
    Metrics in ``weights`` but absent from the tensor contribute nothing (weight → 0).
    """
    metrics = tensor.metrics
    w = np.array(
        [max(0.0, float(weights.get(m, 0.0))) for m in metrics], dtype=np.float64
    )
    W = float(w.sum())
    n = tensor.n
    g = np.zeros((len(aligns), n), dtype=np.float64)
    if W <= 0:
        return g, 0.0
    S = np.nan_to_num(np.asarray(tensor.S, dtype=np.float64), nan=0.0)
    M = np.asarray(tensor.M, dtype=np.float64)
    contrib = (S * M) @ w  # [n_clips_tensor, n]
    contrib /= W
    for ai, a in enumerate(aligns):
        if a.clip_id in tensor.clip_ids:
            g[ai] = contrib[tensor.clip_ids.index(a.clip_id)]
    return g, W


def _prefix(g_row: np.ndarray) -> np.ndarray:
    """P[k] = Σ_{k'<k} g_row[k'] with a leading 0 (length n+1)."""
    p = np.empty(g_row.shape[0] + 1, dtype=np.float64)
    p[0] = 0.0
    np.cumsum(g_row, out=p[1:])
    return p


def _kappa(b: float, hop_s: float, n: int, t0: float = 0.0) -> int:
    """First grid index ≥ (b − t0)/hop_s, clamped to [0, n]."""
    return int(min(max(0, int(np.ceil((b - t0) / hop_s - 1e-9))), n))


# ---------------------------------------------------------------------------
# The DP
# ---------------------------------------------------------------------------


def _viterbi(aligns, boundaries, composite, tensor, cfg):
    """Return ``(edl_entries | None, cause)``. ``None`` ⇒ infeasible (cause classifies why).

    A *segment* is one shot on one clip spanning boundaries ``[B_i, B_j)``, length in
    ``[L_min_eff, L_max]``. **Consecutive segments must be DIFFERENT clips** (same-clip
    adjacency would just be one longer shot), so ``L_max`` is a true max-shot-length and
    every segment boundary is a real cut costing ``λ_switch``. ``max_{c'≠c}`` is O(1) via a
    per-boundary top-2 of ``best[i][·]``.
    """
    B = boundaries
    m = len(B) - 1  # intervals [B_0,B_1) .. [B_{m-1},B_m)
    if m < 1:
        return None, "empty_span"
    K = len(aligns)
    hop, n = tensor.hop_s, tensor.n
    l_min, l_max = cfg.l_min_s, cfg.l_max_s
    lam = cfg.lambda_switch
    overrun = cfg.l_max_overrun_penalty
    # Bound the transition window for performance; the base case (i=0) is uncapped so a
    # lone clip can still cover the whole span as one (overrun-penalized) segment.
    max_seg_s = max(4.0 * l_max, l_max + 8.0)

    prefixes = [_prefix(composite[c]) for c in range(K)]
    kap = [_kappa(b, hop, n, tensor.t0) for b in B]

    def seg_reward(c, i, j):
        # Time-integral of the composite MINUS a soft penalty for exceeding l_max.
        r = hop * float(prefixes[c][kap[j]] - prefixes[c][kap[i]])
        return r - overrun * max(0.0, (B[j] - B[i]) - l_max)

    def contains(c, i, j):
        a = aligns[c]
        return B[i] >= a.offset_s - _EPS and B[j] <= a.offset_s + a.duration_s + _EPS

    def feasible(c, i, j):
        length = B[j] - B[i]
        if length <= _EPS:  # l_max is SOFT (penalized in seg_reward), not a hard cap
            return False
        l_min_eff = 0.0 if (i == 0 or j == m) else l_min
        if length < l_min_eff - _EPS:
            return False
        return contains(c, i, j)

    # coverage-gap classification: an interval no clip continuously contains.
    for p in range(m):
        if not any(contains(c, p, p + 1) for c in range(K)):
            return None, "coverage_gap"

    best = [[_NEG_INF] * K for _ in range(m + 1)]
    back = [[None] * K for _ in range(m + 1)]
    # Per-boundary top-2 of best[i][·]: (v1, a1, v2) → max over clips ≠ c is O(1).
    top = [(_NEG_INF, -1, _NEG_INF)] * (m + 1)

    def best_other(i, c):
        v1, a1, v2 = top[i]
        return v2 if a1 == c else v1

    for j in range(1, m + 1):
        # Base case: the FIRST segment [B_0, B_j) — no cut, L_min relaxed via i==0.
        for c in range(K):
            if feasible(c, 0, j):
                r = seg_reward(c, 0, j)
                if r > best[j][c]:
                    best[j][c] = r
                    back[j][c] = (0, None)
        # Transitions: a cut from a DIFFERENT clip's segment ending at boundary i (i≥1).
        i = j - 1
        while i >= 1 and (B[j] - B[i]) <= max_seg_s + _EPS:
            for c in range(K):
                if not feasible(c, i, j):
                    continue
                prev = best_other(i, c)  # best segment ending at i on a clip ≠ c
                if prev == _NEG_INF:
                    continue
                cand = prev + seg_reward(c, i, j) - lam
                if cand > best[j][c] + 1e-12:
                    best[j][c] = cand
                    back[j][c] = (i, _arg_other(best[i], c))
            i -= 1
        top[j] = _top2(best[j])

    v1, a1, _ = top[m]
    if v1 == _NEG_INF:
        return None, "dwell_infeasible"

    # Reconstruct segments (last → first) via back-pointers.
    segments: list[tuple[float, float, str]] = []
    j, c = m, a1
    while j > 0:
        bp = back[j][c]
        if bp is None:
            return None, "reconstruct_failed"
        i, prev_c = bp
        segments.append((B[i], B[j], aligns[c].clip_id))
        if prev_c is None:
            break
        j, c = i, prev_c
    segments.reverse()
    entries = _coalesce([EdlEntry(s, e, cid) for s, e, cid in segments])
    return entries, "optimal"


def _top2(row):
    """(max, argmax_lowest_index, second_max) of a list (−inf-safe)."""
    v1, a1, v2 = _NEG_INF, -1, _NEG_INF
    for c, v in enumerate(row):
        if v > v1:
            v2, v1, a1 = v1, v, c
        elif v > v2:
            v2 = v
    return v1, a1, v2


def _arg_other(row, c):
    """Lowest-index argmax of ``row`` over clips != c (the predecessor of a cut into c)."""
    best_c, best_v = -1, _NEG_INF
    for cc, v in enumerate(row):
        if cc != c and v > best_v:
            best_v, best_c = v, cc
    return best_c


def _coalesce(entries: list[EdlEntry]) -> list[EdlEntry]:
    out: list[EdlEntry] = []
    for e in entries:
        if out and out[-1].clip_id == e.clip_id and abs(out[-1].song_end - e.song_start) <= 1e-3:
            out[-1] = EdlEntry(out[-1].song_start, e.song_end, e.clip_id)
        else:
            out.append(e)
    return out


def _fallback(aligns, song_duration, *, cause: str):
    """Defer to the alignment-only default, tagging the reason (never silent)."""
    from muvid.footage.strategy import best_confidence

    entries = best_confidence(aligns, song_duration)
    meta = {"strategy": "weighted", "fallback": "best_confidence", "cause": cause}
    return entries, meta


# ---------------------------------------------------------------------------
# selection_margin — a separate O(n·K) per-frame diagnostic (NOT the DP path margin)
# ---------------------------------------------------------------------------


def selection_margin(
    alignments: Sequence[FootageAlignment],
    tensor,
    *,
    weights: Mapping[str, float] | None = None,
) -> np.ndarray:
    """Per-frame ``best_composite − 2nd_best_composite`` over clips COVERING that frame.

    A LOCAL "where a human should decide" proxy (small margin = a toss-up), distinct from the
    DP's global path optimality. ``NaN`` where fewer than 2 clips cover the frame.
    """
    aligns = list(alignments)
    g, W = _composite(tensor, weights or DEFAULT_WEIGHTS, aligns)
    n = tensor.n
    hop = tensor.hop_s
    out = np.full(n, np.nan, dtype=np.float32)
    if W <= 0 or not aligns:
        return out
    times = np.arange(n) * hop
    covered = np.zeros((len(aligns), n), dtype=bool)
    for ai, a in enumerate(aligns):
        covered[ai] = (times >= a.coverage[0] - _EPS) & (times < a.coverage[1] + _EPS)
    for k in range(n):
        vals = g[covered[:, k], k]
        if vals.size >= 2:
            top2 = np.partition(vals, -2)[-2:]
            out[k] = float(np.max(top2) - np.min(top2))
    return out
