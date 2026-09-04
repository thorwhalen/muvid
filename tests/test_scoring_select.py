"""Pure tests for the score grid + the beat-snapped semi-Markov Viterbi selector.

No heavy deps — synthetic score tensors only. Covers the correctness properties the design
review flagged as load-bearing: continuous containment (validate_edl by construction),
beat-only switching, L_min/L_max, the λ_switch scaling, missing-metric collapse, the
infeasible→fallback classification, selection_margin, determinism, and the context dispatch.
"""

from __future__ import annotations

import numpy as np
import pytest

from muvid.footage.edl import EdlEntry, FootageAlignment, validate_edl
from muvid.footage import strategy as S


def _align(clip_id, offset, duration, song=None, conf=0.9):
    end = offset + duration if song is None else min(song, offset + duration)
    return FootageAlignment(clip_id, offset, conf, duration, (max(0.0, offset), end))


def make_tensor(aligns, comp_by_clip, *, hop=0.1, n=None, metric="m"):
    """Build a single-metric ScoreTensor whose composite ĝ equals ``comp_by_clip`` directly.

    ``comp_by_clip[clip_id]`` is a length-n array (NaN where not covered/scored). With one
    metric of weight 1, the selector's ĝ = S·M, so setting S=comp and M=~isnan(comp) makes
    ĝ exactly the array we supply — precise control for the DP tests.
    """
    from muvid.footage.scoring.grid import ScoreTensor

    clip_ids = [a.clip_id for a in aligns]
    if n is None:
        n = len(next(iter(comp_by_clip.values())))
    nc, nm = len(clip_ids), 1
    Sarr = np.full((nc, n, nm), np.nan, dtype=np.float32)
    M = np.zeros((nc, n, nm), dtype=bool)
    raw = np.full((nc, n, nm), np.nan, dtype=np.float32)
    for ci, cid in enumerate(clip_ids):
        comp = np.asarray(comp_by_clip[cid], dtype=np.float32)
        Sarr[ci, :, 0] = comp
        raw[ci, :, 0] = comp
        M[ci, :, 0] = np.isfinite(comp)
    return ScoreTensor(
        clip_ids=clip_ids,
        metrics=[metric],
        t0=0.0,
        hop_s=hop,
        n=n,
        S=Sarr,
        M=M,
        raw=raw,
        norms={
            metric: {
                "median": 0.5,
                "iqr": 1.0,
                "p5": 0.0,
                "p95": 1.0,
                "direction": "higher_better",
            }
        },
    )


# ---------------------------------------------------------------------------
# λ_switch scaling — the review's exact "switch iff δ·(T/2) > λ" test
# ---------------------------------------------------------------------------


def _lambda_fixture(hop=0.1):
    """A: density 1 on [0,2), 0 on [2,4); B: mirror. δ=1, T/2=2 → switch iff 2 > λ."""
    from muvid.footage.select_score import SelectionContext, WeightedSelectionConfig

    song = 4.0
    n = int(np.ceil(song / hop)) + 1
    t = np.arange(n) * hop
    a = np.where(t < 2.0, 1.0, 0.0)
    b = np.where(t < 2.0, 0.0, 1.0)
    aligns = [_align("A", 0.0, 4.0, song), _align("B", 0.0, 4.0, song)]
    tensor = make_tensor(aligns, {"A": a, "B": b}, hop=hop, n=n)
    beats = [1.0, 2.0, 3.0]

    def ctx(lam):
        return SelectionContext(
            tensor=tensor,
            beat_times=beats,
            config=WeightedSelectionConfig(
                weights={"m": 1.0}, lambda_switch=lam, l_min_s=0.0, l_max_s=4.0
            ),
        )

    return aligns, song, ctx


def test_lambda_switch_scaling_boundary_is_delta_times_halfT():
    from muvid.footage.select_score import weighted_selection

    aligns, song, ctx = _lambda_fixture()
    # δ·(T/2) = 1·2 = 2. λ=1 (< 2) → switch (2 entries A then B). λ=3 (> 2) → no switch.
    edl_switch = weighted_selection(aligns, song, context=ctx(1.0))
    edl_stay = weighted_selection(aligns, song, context=ctx(3.0))
    assert [e.clip_id for e in edl_switch] == ["A", "B"]
    assert edl_switch[0].song_end == pytest.approx(2.0)  # cut on the beat at T/2
    assert len(edl_stay) == 1 and edl_stay[0].clip_id == "A"  # tie → deterministic A


def test_more_lambda_never_increases_cuts():
    from muvid.footage.select_score import weighted_selection

    aligns, song, ctx = _lambda_fixture()
    cuts = [
        len(weighted_selection(aligns, song, context=ctx(lam)))
        for lam in (0.1, 1.0, 3.0, 10.0)
    ]
    assert cuts == sorted(cuts, reverse=True) or all(
        cuts[i] >= cuts[i + 1] for i in range(len(cuts) - 1)
    )


# ---------------------------------------------------------------------------
# Valid-by-construction, gapless, beat-snapped
# ---------------------------------------------------------------------------


def test_output_is_gapless_and_passes_validate_edl():
    from muvid.footage.select_score import (
        SelectionContext,
        WeightedSelectionConfig,
        weighted_selection,
    )

    hop = 0.1
    song = 6.0
    n = int(np.ceil(song / hop)) + 1
    t = np.arange(n) * hop
    aligns = [_align("A", 0.0, 6.0, song), _align("B", 0.0, 6.0, song)]
    tensor = make_tensor(
        aligns,
        {"A": np.where(t < 3, 1.0, 0.2), "B": np.where(t < 3, 0.2, 1.0)},
        hop=hop,
        n=n,
    )
    ctx = SelectionContext(
        tensor=tensor,
        beat_times=[1, 2, 3, 4, 5],
        config=WeightedSelectionConfig(
            weights={"m": 1.0}, lambda_switch=0.2, l_min_s=0.5, l_max_s=6.0
        ),
    )
    edl = weighted_selection(aligns, song, context=ctx)
    validate_edl(edl, aligns, song)  # must not raise
    assert edl[0].song_start == pytest.approx(0.0)
    assert edl[-1].song_end == pytest.approx(6.0)
    for a, b in zip(edl, edl[1:]):
        assert a.song_end == pytest.approx(b.song_start)  # gapless
        assert a.song_end in (1, 2, 3, 4, 5)  # cut on a beat


def test_by_construction_fractional_offset_off_grid_boundary():
    """offset_s=2.05, a beat at 2.00 → a grid mask would call A covered; the CONTINUOUS
    containment must reject A there, so the emitted EDL still survives validate_edl."""
    from muvid.footage.select_score import (
        SelectionContext,
        WeightedSelectionConfig,
        weighted_selection,
    )

    hop = 0.1
    song = 5.0
    n = int(np.ceil(song / hop)) + 1
    t = np.arange(n) * hop
    # A starts at 2.05 (covers [2.05, 5]); B covers [0, 5]. Beats include 2.00.
    aligns = [_align("A", 2.05, 2.95, song), _align("B", 0.0, 5.0, song)]
    a = np.where(t >= 2.05, 1.0, np.nan)  # A only scored where it exists
    b = np.full(n, 0.3)
    tensor = make_tensor(aligns, {"A": a, "B": b}, hop=hop, n=n)
    ctx = SelectionContext(
        tensor=tensor,
        beat_times=[1.0, 2.0, 3.0, 4.0],
        config=WeightedSelectionConfig(
            weights={"m": 1.0}, lambda_switch=0.1, l_min_s=0.0, l_max_s=5.0
        ),
    )
    edl = weighted_selection(aligns, song, context=ctx)
    validate_edl(edl, aligns, song)  # the real assertion: no "does not contain" raise
    # A must never be shown before 2.05 (a cut to A can only land at a beat ≥ 2.05 → 3.0).
    for e in edl:
        if e.clip_id == "A":
            assert e.song_start >= 2.05 - 1e-9


# ---------------------------------------------------------------------------
# L_min / L_max
# ---------------------------------------------------------------------------


def test_soft_l_max_does_not_force_a_cutaway_to_worse_footage():
    """A best everywhere, B much worse; L_max small. The DP must NOT alternate to B just to
    honor L_max (soft-L_max fix) — it stays on the strictly-better clip."""
    from muvid.footage.select_score import (
        SelectionContext,
        WeightedSelectionConfig,
        weighted_selection,
    )

    hop = 0.1
    song = 20.0
    n = int(np.ceil(song / hop)) + 1
    aligns = [_align("A", 0.0, 20.0, song), _align("B", 0.0, 20.0, song)]
    tensor = make_tensor(
        aligns, {"A": np.full(n, 0.9), "B": np.full(n, 0.1)}, hop=hop, n=n
    )
    ctx = SelectionContext(
        tensor=tensor,
        beat_times=[float(i) for i in range(2, 20, 2)],
        config=WeightedSelectionConfig(
            weights={"m": 1.0},
            lambda_switch=0.35,
            l_min_s=0.0,
            l_max_s=4.0,
            l_max_overrun_penalty=0.15,
        ),
    )
    edl = weighted_selection(aligns, song, context=ctx)
    validate_edl(edl, aligns, song)
    assert [e.clip_id for e in edl] == ["A"]  # one shot on A, no forced cutaway to B


def test_l_max_forces_a_cut_when_a_choice_exists():
    from muvid.footage.select_score import (
        SelectionContext,
        WeightedSelectionConfig,
        weighted_selection,
    )

    hop = 0.1
    song = 10.0
    n = int(np.ceil(song / hop)) + 1
    aligns = [_align("A", 0.0, 10.0, song), _align("B", 0.0, 10.0, song)]
    # Both equally good everywhere → the ONLY reason to cut is L_max; with a choice available
    # the DP must not exceed L_max=3 on a single shot.
    tensor = make_tensor(
        aligns, {"A": np.full(n, 0.5), "B": np.full(n, 0.5)}, hop=hop, n=n
    )
    beats = [float(i) for i in range(1, 10)]
    ctx = SelectionContext(
        tensor=tensor,
        beat_times=beats,
        config=WeightedSelectionConfig(
            weights={"m": 1.0}, lambda_switch=0.0, l_min_s=0.0, l_max_s=3.0
        ),
    )
    edl = weighted_selection(aligns, song, context=ctx)
    validate_edl(edl, aligns, song)
    assert all(e.song_end - e.song_start <= 3.0 + 1e-6 for e in edl)


# ---------------------------------------------------------------------------
# Missing-metric collapse, fallback classification, margin, determinism
# ---------------------------------------------------------------------------


def test_missing_metric_collapses_to_zero_weight():
    from muvid.footage.select_score import (
        SelectionContext,
        WeightedSelectionConfig,
        weighted_selection,
    )

    hop = 0.1
    song = 4.0
    n = int(np.ceil(song / hop)) + 1
    t = np.arange(n) * hop
    aligns = [_align("A", 0.0, 4.0, song), _align("B", 0.0, 4.0, song)]
    tensor = make_tensor(
        aligns,
        {"A": np.where(t < 2, 1.0, 0.0), "B": np.where(t < 2, 0.0, 1.0)},
        hop=hop,
        n=n,
    )
    # weights reference a metric 'lip_sync_lse_c' that isn't in the tensor → it must collapse,
    # and 'm' (weight 1) still drives a valid selection.
    ctx = SelectionContext(
        tensor=tensor,
        beat_times=[1, 2, 3],
        config=WeightedSelectionConfig(
            weights={"m": 1.0, "lip_sync_lse_c": 5.0},
            lambda_switch=0.1,
            l_min_s=0.0,
            l_max_s=4.0,
        ),
    )
    edl = weighted_selection(aligns, song, context=ctx)
    validate_edl(edl, aligns, song)
    assert [e.clip_id for e in edl] == ["A", "B"]


def test_coverage_gap_falls_back_and_surfaces_the_gap():
    from muvid.footage.select_score import (
        SelectionContext,
        WeightedSelectionConfig,
        run_weighted,
    )

    hop = 0.1
    song = 10.0
    n = int(np.ceil(song / hop)) + 1
    # A covers [0,4], B covers [6,10] → [4,6] is an interior coverage gap.
    aligns = [_align("A", 0.0, 4.0, song), _align("B", 6.0, 4.0, song)]
    tensor = make_tensor(
        aligns, {"A": np.full(n, 0.8), "B": np.full(n, 0.8)}, hop=hop, n=n
    )
    ctx = SelectionContext(
        tensor=tensor,
        beat_times=[1, 2, 3, 7, 8, 9],
        config=WeightedSelectionConfig(
            weights={"m": 1.0}, lambda_switch=0.2, l_min_s=1.0, l_max_s=8.0
        ),
    )
    entries, meta = run_weighted(aligns, song, ctx)
    assert (
        meta.get("fallback") == "best_confidence"
        and meta.get("cause") == "coverage_gap"
    )


def test_selection_margin_shape_and_nan_where_single_cover():
    from muvid.footage.select_score import selection_margin

    hop = 0.1
    song = 4.0
    n = int(np.ceil(song / hop)) + 1
    t = np.arange(n) * hop
    # A covers [0,4]; B covers [2,4] → frames < 2s have a single cover → NaN margin.
    aligns = [_align("A", 0.0, 4.0, song), _align("B", 2.0, 2.0, song)]
    tensor = make_tensor(
        aligns, {"A": np.full(n, 0.7), "B": np.where(t >= 2, 0.4, np.nan)}, hop=hop, n=n
    )
    margin = selection_margin(aligns, tensor, weights={"m": 1.0})
    assert margin.shape == (n,)
    assert np.isnan(margin[5])  # ~0.5s: only A covers
    assert not np.isnan(margin[35])  # ~3.5s: both cover → a real margin ≈ 0.3


def test_determinism_same_input_same_output():
    from muvid.footage.select_score import weighted_selection

    aligns, song, ctx = _lambda_fixture()
    e1 = weighted_selection(aligns, song, context=ctx(1.0))
    e2 = weighted_selection(aligns, song, context=ctx(1.0))
    assert [(e.song_start, e.song_end, e.clip_id) for e in e1] == [
        (e.song_start, e.song_end, e.clip_id) for e in e2
    ]


# ---------------------------------------------------------------------------
# Registry dispatch: context passed only when declared; built-ins untouched
# ---------------------------------------------------------------------------


def test_weighted_is_listed_and_resolvable_lazily():
    assert "weighted" in S.list_strategies()
    fn = S.resolve_strategy("weighted")
    assert callable(fn)


def test_select_edl_passes_context_only_when_declared():
    seen = {}

    def ctx_strategy(alignments, song_duration, *, context=None):
        seen["got_context"] = context
        return [EdlEntry(0, 1, alignments[0].clip_id)]

    def plain_strategy(alignments, song_duration):
        seen["plain"] = True
        return [EdlEntry(0, 1, alignments[0].clip_id)]

    aligns = [_align("A", 0.0, 5.0, 5.0)]
    S.select_edl(ctx_strategy, aligns, 5.0, context="SENTINEL")
    assert seen["got_context"] == "SENTINEL"
    S.select_edl(plain_strategy, aligns, 5.0, context="SENTINEL")  # must not raise
    assert seen.get("plain") is True


def test_kwargs_strategy_receives_context():
    seen = {}

    def kw_strategy(alignments, song_duration, **kwargs):
        seen["context"] = kwargs.get("context")
        return [EdlEntry(0, 1, alignments[0].clip_id)]

    aligns = [_align("A", 0.0, 5.0, 5.0)]
    S.select_edl(kw_strategy, aligns, 5.0, context="X")
    assert seen["context"] == "X"


# -- composite renormalisation over AVAILABLE weight (muvid#19) ----------------


def _two_metric_tensor(*, available):
    """One clip, one frame, two metrics of equal weight; ``available`` masks metric 2.

    Metric 1 scores a perfect 1.0. Metric 2 is either measured-and-perfect, or not
    measured at all. The composite must read 1.0 in BOTH cases: an unmeasured metric
    is not evidence of badness.
    """
    from muvid.footage.scoring.grid import ScoreTensor

    S_arr = np.array([[[1.0, 1.0]]], dtype=np.float32)
    M = np.array([[[True, available]]])
    return ScoreTensor(
        clip_ids=["A"],
        metrics=["m1", "m2"],
        t0=0.0,
        hop_s=0.1,
        n=1,
        S=S_arr,
        M=M,
        raw=S_arr,
        norms={"m1": None, "m2": None},
    )


def test_composite_does_not_count_an_unavailable_metric_as_zero():
    # muvid#19: dividing by the FIXED total weight scores "could not measure this"
    # identically to "measured it, it is the worst possible" — which silently drags a
    # clip down for a metric that never ran. The denominator must be the weight
    # actually available at that frame.
    from muvid.footage.select_score import _composite

    aligns = [_align("A", 0.0, 1.0)]
    weights = {"m1": 1.0, "m2": 1.0}

    g_both, _ = _composite(_two_metric_tensor(available=True), weights, aligns)
    g_one, _ = _composite(_two_metric_tensor(available=False), weights, aligns)

    assert g_both[0][0] == pytest.approx(1.0)
    # Before the fix this was 0.5 — a perfect clip reading as half-bad because a
    # metric was unavailable.
    assert g_one[0][0] == pytest.approx(1.0)


def test_composite_is_zero_when_nothing_is_available():
    # A frame with no metric measured at all is genuinely unusable, and must be 0
    # rather than NaN: _prefix takes a cumulative sum, so one NaN would poison every
    # downstream segment reward.
    from muvid.footage.select_score import _composite

    aligns = [_align("A", 0.0, 1.0)]
    tensor = _two_metric_tensor(available=False)
    tensor.M[0, 0, 0] = False  # mask the other one too

    g, _ = _composite(tensor, {"m1": 1.0, "m2": 1.0}, aligns)
    assert g[0][0] == 0.0
    assert np.isfinite(g).all()


def test_composite_weights_the_available_metrics_proportionally():
    # Two metrics, unequal weights, one unavailable → the answer is the surviving
    # metric's own score, not a fraction of it.
    from muvid.footage.scoring.grid import ScoreTensor
    from muvid.footage.select_score import _composite

    S_arr = np.array([[[0.8, 0.2]]], dtype=np.float32)
    M = np.array([[[True, False]]])
    tensor = ScoreTensor(
        clip_ids=["A"],
        metrics=["m1", "m2"],
        t0=0.0,
        hop_s=0.1,
        n=1,
        S=S_arr,
        M=M,
        raw=S_arr,
        norms={"m1": None, "m2": None},
    )
    g, _ = _composite(tensor, {"m1": 0.4, "m2": 1.6}, [_align("A", 0.0, 1.0)])
    assert g[0][0] == pytest.approx(0.8)


# -- NA rather than 0.0 at the face-scoring boundary (muvid#19) ----------------


def test_face_sample_is_na_when_nothing_was_measured():
    # Three ways to have not measured a face; all must be NaN, none 0.0. Scoring them
    # 0.0 made them indistinguishable from a face that IS present and framed as badly
    # as the metric allows.
    from muvid.footage.scoring.frames import _face_sample

    def raises(_frame):
        raise RuntimeError("detector blew up")

    assert np.isnan(_face_sample(None, object()))  # no detector installed
    assert np.isnan(_face_sample(lambda _f: None, object()))  # detector found no face
    assert np.isnan(_face_sample(raises, object()))  # detector failed

    assert _face_sample(lambda _f: 0.75, object()) == pytest.approx(0.75)
    # A real, measured zero is still a zero — only *absence* becomes NA.
    assert _face_sample(lambda _f: 0.0, object()) == 0.0
