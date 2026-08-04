"""Pure tests for muvid.footage.scoring.grid — resample, robust normalization, tensor
assembly, and crash-consistent / NaN-safe persistence. No heavy deps (numpy only)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from muvid.footage.scoring.grid import (
    ScoreTrack,
    apply_norm,
    build_tensor,
    compute_norm,
    grid_len,
    load_tensor,
    resample_to_grid,
    save_scores,
)


# -- resample_to_grid --------------------------------------------------------


def test_resample_mean_bins_and_masks_outside_span():
    # Samples at 0.05,0.15,0.25s onto a 0.1s grid over n=10 frames (0..0.9s).
    vals, mask = resample_to_grid(
        [0.05, 0.15, 0.25], [1.0, 2.0, 3.0], t0=0.0, hop_s=0.1, n=10, max_gap_s=0.4
    )
    assert vals.shape == (10,) and mask.shape == (10,)
    # Frames within [first, last] sample are valid; beyond are masked (no extrapolation).
    assert mask[0] and mask[2]
    assert not mask[9]  # 0.9s is past the last sample (0.25s) → masked
    assert np.isnan(vals[9])


def test_resample_bridges_small_gaps_not_large_ones():
    # Two samples 1.0s apart; max_gap 0.4s → the WHOLE interior gap stays masked (not just the
    # midpoint) — a large gap must never be invented on its edges (finding: whole-gap masking).
    vals, mask = resample_to_grid(
        [0.0, 1.0], [0.0, 1.0], t0=0.0, hop_s=0.1, n=11, max_gap_s=0.4
    )
    assert mask[0] and mask[10]  # the two real samples are kept
    assert not mask[1:10].any()  # the entire oversized gap is masked end-to-end


def test_align_fingerprint_order_independent_and_offset_sensitive():
    from muvid.footage.edl import FootageAlignment
    from muvid.footage.scoring.grid import align_fingerprint

    a = FootageAlignment("A", 1.0, 0.9, 5.0, (1.0, 6.0))
    b = FootageAlignment("B", 2.0, 0.8, 4.0, (2.0, 6.0))
    assert align_fingerprint([a, b]) == align_fingerprint([b, a])  # order-independent
    a2 = FootageAlignment("A", 1.5, 0.9, 5.0, (1.5, 6.5))  # offset moved
    assert align_fingerprint([a, b]) != align_fingerprint([a2, b])


def test_manifest_is_current_guards_song_and_alignment():
    from muvid.footage.scoring.grid import manifest_is_current

    m = {"song_hash": "h1", "align_fingerprint": "f1"}
    assert manifest_is_current(m, song_hash="h1", align_fingerprint="f1")
    assert not manifest_is_current(m, song_hash="h2", align_fingerprint="f1")  # new song
    assert not manifest_is_current(m, song_hash="h1", align_fingerprint="f2")  # re-aligned
    assert not manifest_is_current(None, song_hash="h1", align_fingerprint="f1")


def test_resample_empty_and_all_nan():
    vals, mask = resample_to_grid([], [], t0=0.0, hop_s=0.1, n=5)
    assert not mask.any() and np.isnan(vals).all()
    vals, mask = resample_to_grid([0.1], [np.nan], t0=0.0, hop_s=0.1, n=5)
    assert not mask.any()


# -- robust normalization ----------------------------------------------------


def test_compute_and_apply_norm_maps_to_unit_and_inverts_distance():
    raw = np.array([0, 1, 2, 3, 4, 100], dtype=float)  # 100 is an outlier
    m = np.ones_like(raw, dtype=bool)
    norm = compute_norm([raw], [m], direction="higher_better")
    out = apply_norm(raw, norm, direction="higher_better")
    assert np.nanmin(out) == pytest.approx(0.0)
    assert np.nanmax(out) == pytest.approx(1.0)
    # lower_better inverts: the smallest raw becomes the highest score.
    out_lo = apply_norm(raw, norm, direction="lower_better")
    assert out_lo[0] > out_lo[4]


def test_norm_iqr_zero_is_neutral_half():
    raw = np.full(6, 7.0)
    m = np.ones(6, dtype=bool)
    norm = compute_norm([raw], [m], direction="higher_better")
    out = apply_norm(raw, norm, direction="higher_better")
    assert np.allclose(out, 0.5)  # constant metric → neutral, never div-by-zero


def test_norm_excludes_masked_and_all_masked_is_none():
    raw = np.array([1.0, 2.0, 1000.0])
    m = np.array([True, True, False])  # the 1000 is masked → must not skew the scale
    norm = compute_norm([raw], [m], direction="higher_better")
    assert norm["p95"] < 100
    # all masked → None (→ null in the manifest, all-NaN scores)
    assert compute_norm([raw], [np.zeros(3, dtype=bool)], direction="higher_better") is None
    out = apply_norm(raw, None, direction="higher_better")
    assert np.isnan(out).all()


# -- tensor assembly ---------------------------------------------------------


def _track(cid, metric, arr, hop=0.1):
    a = np.asarray(arr, dtype=np.float32)
    return ScoreTrack(cid, metric, 0.0, hop, a, np.isfinite(a), "higher_better")


def test_build_tensor_missing_metric_is_all_masked_column():
    n = 5
    tracks = {
        "A": [_track("A", "sharp", [1, 2, 3, 4, 5]), _track("A", "motion", [5, 4, 3, 2, 1])],
        "B": [_track("B", "sharp", [2, 2, 2, 2, 2])],  # B has no 'motion' track
    }
    tensor = build_tensor(tracks, t0=0.0, hop_s=0.1, n=n, metrics=["sharp", "motion"])
    mi = tensor.metric_index("motion")
    bi = tensor.clip_index("B")
    assert not tensor.M[bi, :, mi].any()  # B/motion is an all-masked column
    assert np.isnan(tensor.S[bi, :, mi]).all()


def test_build_tensor_geometry_mismatch_raises():
    bad = ScoreTrack("A", "x", 0.0, 0.05, np.zeros(5, np.float32), np.ones(5, bool), "higher_better")
    with pytest.raises(ValueError, match="geometry"):
        build_tensor({"A": [bad]}, t0=0.0, hop_s=0.1, n=5, metrics=["x"])


# -- persistence: atomic, NaN-safe, round-trip -------------------------------


def test_save_load_roundtrip_and_nan_safe_manifest(tmp_path):
    n = grid_len(2.0, 0.1)
    a = np.linspace(0, 1, n).astype(np.float32)
    tracks = {
        "A": [_track("A", "sharp", a)],
        "B": [_track("B", "sharp", np.full(n, np.nan, np.float32))],  # all-masked metric
    }
    save_scores(
        tmp_path,
        tracks,
        t0=0.0,
        hop_s=0.1,
        n=n,
        metrics=["sharp"],
        song_hash="deadbeef",
        beat_times=[0.5, 1.0, 1.5],
    )
    # The manifest must be STRICT JSON (no NaN token) even with an all-masked clip.
    manifest_text = (tmp_path / "scores" / "manifest.json").read_text()
    assert "NaN" not in manifest_text
    manifest = json.loads(manifest_text)  # strict parse must not raise
    assert manifest["song_hash"] == "deadbeef"
    assert manifest["beats"]["beat_times"] == [0.5, 1.0, 1.5]

    tensor = load_tensor(tmp_path)
    assert tensor is not None
    assert tensor.clip_ids == ["A", "B"] and tensor.metrics == ["sharp"]
    ai = tensor.clip_index("A")
    assert tensor.M[ai, :, 0].any()  # A scored
    bi = tensor.clip_index("B")
    assert not tensor.M[bi, :, 0].any()  # B all-masked survived the round trip


def test_manifest_written_last_signals_completeness(tmp_path):
    n = grid_len(1.0, 0.1)
    save_scores(
        tmp_path,
        {"A": [_track("A", "sharp", np.ones(n, np.float32))]},
        t0=0.0,
        hop_s=0.1,
        n=n,
        metrics=["sharp"],
        song_hash="x",
    )
    from muvid.footage.scoring.grid import scores_present

    assert scores_present(tmp_path)
