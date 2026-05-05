"""``muvid.cost.estimate_render_cost`` + ``--budget`` gating."""

from __future__ import annotations

import pytest


def _force_falaw_costs(monkeypatch):
    """Inject deterministic per-category model records into the cost calculator."""
    from falaw import CostEstimate, ModelRecord
    import muvid.cost as mcost

    fake_records = {
        "image": ModelRecord(
            id="img-fast",
            category="image",
            cost_estimate=CostEstimate(kind="per_image", amount=0.04),
        ),
        "image_to_video": ModelRecord(
            id="i2v",
            category="image_to_video",
            cost_estimate=CostEstimate(kind="per_second", amount=0.50),
        ),
        "text_to_video": ModelRecord(
            id="t2v",
            category="text_to_video",
            cost_estimate=CostEstimate(kind="per_second", amount=0.40),
        ),
        "avatar": ModelRecord(
            id="ai-avatar",
            category="avatar",
            cost_estimate=CostEstimate(kind="per_second", amount=0.30),
        ),
    }

    def fake_pick_model(*, category, quality_tier="balanced"):
        return fake_records[category]

    # Patch where muvid.cost looks them up.
    import falaw.registry as freg

    monkeypatch.setattr(freg, "pick_model", fake_pick_model)
    return fake_records


def test_estimate_render_cost_aggregates_strategies(tmp_path, monkeypatch):
    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.schema import ShotSpec

    _force_falaw_costs(monkeypatch)
    facade.init_project(tmp_path / "p")
    p = MusicVideoProject(tmp_path / "p")
    # 2s still + 4s i2v + 3s lipsync + 5s t2v + 2s animation (free).
    p.upsert_shot(
        ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still")
    )
    p.upsert_shot(
        ShotSpec(id="s02", start_s=2.0, end_s=6.0, render_strategy="image_to_video")
    )
    p.upsert_shot(
        ShotSpec(id="s03", start_s=6.0, end_s=9.0, render_strategy="lipsync")
    )
    p.upsert_shot(
        ShotSpec(id="s04", start_s=9.0, end_s=14.0, render_strategy="text_to_video")
    )
    p.upsert_shot(
        ShotSpec(id="s05", start_s=14.0, end_s=16.0, render_strategy="animation")
    )

    rollup = facade.estimate_render_cost(tmp_path / "p")
    by_kind = rollup.by_kind()
    # Still: 1 image @ 0.04. i2v: image (0.04) + 4s × 0.50 = 0.04 + 2.0.
    # Lipsync: 3s × 0.30 = 0.90. t2v: 5s × 0.40 = 2.00. Animation: free.
    expected = 0.04 + (0.04 + 2.00) + 0.90 + 2.00
    assert rollup.total_amount == pytest.approx(expected, abs=0.01)
    assert "shot.image" in by_kind
    assert "shot.image_to_video" in by_kind
    assert "shot.lipsync" in by_kind
    assert "shot.text_to_video" in by_kind


def test_estimate_render_cost_skips_already_rendered(tmp_path, monkeypatch):
    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.schema import ShotSpec

    _force_falaw_costs(monkeypatch)
    facade.init_project(tmp_path / "p")
    p = MusicVideoProject(tmp_path / "p")
    p.upsert_shot(
        ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still")
    )
    # Pretend it's already rendered.
    out = p.shot_dir("s01") / "output.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"fake")

    rollup = facade.estimate_render_cost(tmp_path / "p")
    assert rollup.total_amount == 0.0


def test_render_with_budget_aborts_when_estimate_exceeds(tmp_path, monkeypatch):
    """`render(budget=0.01)` should refuse a project that costs more."""
    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.schema import ShotSpec

    _force_falaw_costs(monkeypatch)
    facade.init_project(tmp_path / "p")
    p = MusicVideoProject(tmp_path / "p")
    # 5 seconds of i2v at $0.50/s = $2.50, way over $0.01.
    p.upsert_shot(
        ShotSpec(id="s01", start_s=0.0, end_s=5.0, render_strategy="image_to_video")
    )

    with pytest.raises(RuntimeError, match="exceeds budget"):
        facade.render(tmp_path / "p", budget=0.01)


def test_render_with_budget_under_does_not_raise(tmp_path, monkeypatch):
    """`render(budget=large)` must NOT raise during the gate.

    We don't actually run the renders here (that's covered by
    test_smoke_pipeline). Instead we confirm the budget gate accepts a
    large budget by stubbing _render_all to a no-op.
    """
    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.schema import ShotSpec

    _force_falaw_costs(monkeypatch)
    facade.init_project(tmp_path / "p")
    p = MusicVideoProject(tmp_path / "p")
    p.upsert_shot(
        ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still")
    )

    monkeypatch.setattr(facade, "_render_all", lambda *_a, **_k: [])
    outputs = facade.render(tmp_path / "p", budget=1000.0)
    assert outputs == []


def test_status_includes_estimated_render_cost(tmp_path, monkeypatch):
    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.schema import ShotSpec

    _force_falaw_costs(monkeypatch)
    facade.init_project(tmp_path / "p")
    p = MusicVideoProject(tmp_path / "p")
    p.upsert_shot(
        ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still")
    )

    s = facade.status(tmp_path / "p")
    assert "estimated_render_cost" in s
    assert s["estimated_render_cost"] is not None
    assert s["estimated_render_cost"]["total_amount"] == pytest.approx(0.04)


def test_format_status_shows_cost_line(tmp_path, monkeypatch):
    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.schema import ShotSpec

    _force_falaw_costs(monkeypatch)
    facade.init_project(tmp_path / "p", title="cost demo")
    p = MusicVideoProject(tmp_path / "p")
    p.upsert_shot(
        ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still")
    )

    text = facade.format_status(facade.status(tmp_path / "p"))
    assert "Estimated remaining render cost" in text
