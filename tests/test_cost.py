"""``muvid.cost.estimate_render_cost`` + ``--budget`` gating.

The gate is TWO conditions, and muvid#47 is about the second. A price this code
could not determine is routed into ``_Rollup.skipped`` and contributes **nothing**
to ``total_amount`` — correct arithmetic, and a trap for a caller that compares
only the number, because an unpriceable shot then reads as free and clears **any**
budget. *Unknown is not zero, and unknown must force approval.*

Three separate ways the total under-reported, all guarded below: a model with no
`cost_estimate` (or no model in the category), `falaw` not installed at all (the
worst shape — a bare $0.00 with an EMPTY `skipped`, so a gate had nothing to fail
on), and an ``animation`` shot on a machine without ``an``, which the renderer
degrades to a ``still`` that reaches ``falaw.generate_image``.
"""

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
    # `animation` is free only where `an` is importable, and CI has no `an`.
    # Pinning the probe keeps this test measuring pricing rather than the runner.
    monkeypatch.setattr("muvid.cost.an_available", lambda: True)
    facade.init_project(tmp_path / "p")
    p = MusicVideoProject(tmp_path / "p")
    # 2s still + 4s i2v + 3s lipsync + 5s t2v + 2s animation (free).
    p.upsert_shot(ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still"))
    p.upsert_shot(
        ShotSpec(id="s02", start_s=2.0, end_s=6.0, render_strategy="image_to_video")
    )
    p.upsert_shot(ShotSpec(id="s03", start_s=6.0, end_s=9.0, render_strategy="lipsync"))
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
    p.upsert_shot(ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still"))
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
    p.upsert_shot(ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still"))

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
    p.upsert_shot(ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still"))

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
    p.upsert_shot(ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still"))

    text = facade.format_status(facade.status(tmp_path / "p"))
    assert "Estimated remaining render cost" in text


# --------------------------------------------------------------------------
# muvid#47 — an unpriceable item must not read as a free one
# --------------------------------------------------------------------------


def _unpriceable(monkeypatch, category="image"):
    """Make one category price to `None` — a real `_price_one` skip, not a stub."""
    from falaw import ModelRecord
    import falaw.registry as freg

    monkeypatch.setattr(
        freg,
        "pick_model",
        lambda *, category, quality_tier="balanced": ModelRecord(
            id=f"{category}-unpriced", category=category, cost_estimate=None
        ),
    )


def _project(tmp_path, monkeypatch, *, strategy, seconds=2.0):
    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.schema import ShotSpec

    facade.init_project(tmp_path / "p")
    p = MusicVideoProject(tmp_path / "p")
    p.upsert_shot(
        ShotSpec(id="s01", start_s=0.0, end_s=seconds, render_strategy=strategy)
    )
    return p


def test_an_unpriceable_shot_is_recorded_rather_than_counted_as_zero(
    tmp_path, monkeypatch
):
    from muvid import facade

    _unpriceable(monkeypatch)
    _project(tmp_path, monkeypatch, strategy="still")

    rollup = facade.estimate_render_cost(tmp_path / "p")
    assert rollup.total_amount == 0.0
    assert rollup.has_unknown_costs
    assert any("no cost_estimate" in r for r in rollup.skipped)


def test_the_budget_gate_refuses_an_unpriceable_project_under_budget(
    tmp_path, monkeypatch
):
    """The headline. The total is $0.00 and the budget is $1000 — and it REFUSES.

    Comparing the number alone passes here, which is exactly how an unpriceable
    shot cleared any cap.
    """
    from muvid import facade

    _unpriceable(monkeypatch)
    _project(tmp_path, monkeypatch, strategy="still")
    monkeypatch.setattr(facade, "_render_all", lambda *_a, **_k: [])

    with pytest.raises(RuntimeError, match="could not be priced"):
        facade.render(tmp_path / "p", budget=1000.0)


def test_the_abort_names_what_it_could_not_price(tmp_path, monkeypatch):
    """A refusal a caller cannot act on is a wall, not a gate."""
    from muvid import facade

    _unpriceable(monkeypatch)
    _project(tmp_path, monkeypatch, strategy="still")

    with pytest.raises(RuntimeError) as e:
        facade.render(tmp_path / "p", budget=1000.0)
    msg = str(e.value)
    assert "s01" in msg and "LOWER BOUND" in msg
    assert "--allow-unpriced" in msg


def test_allow_unpriced_proceeds_after_the_names_have_been_read(tmp_path, monkeypatch):
    from muvid import facade

    _unpriceable(monkeypatch)
    _project(tmp_path, monkeypatch, strategy="still")
    monkeypatch.setattr(facade, "_render_all", lambda *_a, **_k: [])

    assert facade.render(tmp_path / "p", budget=1000.0, allow_unpriced=True) == []


def test_no_budget_means_no_gate_at_all_even_with_unknowns(tmp_path, monkeypatch):
    """`budget=None` opts out of the gate; it does not opt into a silent one."""
    from muvid import facade

    _unpriceable(monkeypatch)
    _project(tmp_path, monkeypatch, strategy="still")
    monkeypatch.setattr(facade, "_render_all", lambda *_a, **_k: [])

    assert facade.render(tmp_path / "p", budget=None) == []


def test_a_fully_priced_project_still_passes_the_gate(tmp_path, monkeypatch):
    """The control — fail-closed must not mean fail-always."""
    from muvid import facade

    _force_falaw_costs(monkeypatch)
    _project(tmp_path, monkeypatch, strategy="still")
    monkeypatch.setattr(facade, "_render_all", lambda *_a, **_k: [])

    rollup = facade.estimate_render_cost(tmp_path / "p")
    assert not rollup.has_unknown_costs
    assert facade.render(tmp_path / "p", budget=1000.0) == []


def test_falaw_missing_is_unpriceable_not_free(tmp_path, monkeypatch):
    """The worst shape: a bare $0.00 with an EMPTY `skipped` had nothing to fail on."""
    import builtins

    from muvid import facade

    _project(tmp_path, monkeypatch, strategy="still")
    real_import = builtins.__import__

    def _no_falaw(name, *a, **k):
        if name == "falaw" or name.startswith("falaw."):
            raise ImportError("No module named 'falaw'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_falaw)
    rollup = facade.estimate_render_cost(tmp_path / "p")

    assert rollup.total_amount == 0.0
    assert rollup.has_unknown_costs, (
        "with no falaw NOTHING was priced, and a gate reading only the total "
        "cannot tell that apart from a genuinely free project"
    )
    assert any("falaw is not installed" in r for r in rollup.skipped)


def test_animation_is_free_only_where_an_is_installed(tmp_path, monkeypatch):
    from muvid import facade

    _force_falaw_costs(monkeypatch)
    _project(tmp_path, monkeypatch, strategy="animation")

    monkeypatch.setattr("muvid.cost.an_available", lambda: True)
    assert facade.estimate_render_cost(tmp_path / "p").total_amount == 0.0


def test_animation_without_an_is_priced_as_the_still_it_will_become(
    tmp_path, monkeypatch
):
    """muvid#46 degrades it to a `still`, and `still` reaches falaw.generate_image.

    Pricing it at nothing is how a $0.00 estimate cleared a budget and then billed.
    """
    from muvid import facade

    _force_falaw_costs(monkeypatch)
    _project(tmp_path, monkeypatch, strategy="animation")
    monkeypatch.setattr("muvid.cost.an_available", lambda: False)

    rollup = facade.estimate_render_cost(tmp_path / "p")
    assert rollup.total_amount == pytest.approx(0.04, abs=0.001)
    assert any("degraded to still" in ln.note for ln in rollup.lines)


def test_the_over_budget_abort_no_longer_advises_the_strictest_possible_cap(
    tmp_path, monkeypatch
):
    """`--budget=0` is a $0 cap, so the old advice made the abort repeat forever."""
    from muvid import facade

    _force_falaw_costs(monkeypatch)
    _project(tmp_path, monkeypatch, strategy="image_to_video", seconds=5.0)

    with pytest.raises(RuntimeError, match="exceeds budget") as e:
        facade.render(tmp_path / "p", budget=0.01)
    assert "--budget=-1" in str(e.value)
    assert "--budget=0 to disable" not in str(e.value)


# --------------------------------------------------------------------------
# The probe itself, and the CLI flag — the two things patching hides
# --------------------------------------------------------------------------


def test_an_available_answers_the_real_import_question():
    """Every test above patches this seam, so nothing exercised the real one.

    Written to hold in BOTH environments — `an` is present on a dev machine and
    absent in CI — because asserting a fixed answer would just pin the runner.
    """
    from muvid.cost import an_available

    try:
        import an  # noqa: F401

        present = True
    except ImportError:  # pragma: no cover - depends on the environment
        present = False
    assert an_available() is present


def test_an_available_is_false_when_the_package_cannot_be_found(monkeypatch):
    """The branch that decides whether an animation shot gets priced at all."""
    import importlib.util

    from muvid.cost import an_available

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert an_available() is False


def test_an_available_survives_a_broken_install(monkeypatch):
    """`find_spec` raises on a partially-removed package; that is 'not usable'."""
    import importlib.util

    from muvid.cost import an_available

    def _boom(name):
        raise ValueError("__spec__ is None")

    monkeypatch.setattr(importlib.util, "find_spec", _boom)
    assert an_available() is False


def test_the_cli_forwards_allow_unpriced(monkeypatch, tmp_path):
    """The flag is only useful if it reaches the gate.

    `__main__.render` is a thin dispatcher, and a thin dispatcher that drops a
    keyword looks exactly like one that forwards it.
    """
    from muvid import __main__ as cli
    from muvid import facade

    seen = {}
    monkeypatch.setattr(facade, "render", lambda root, **kw: seen.update(kw) or [])
    cli.render(str(tmp_path), budget=5.0, allow_unpriced=True)
    assert seen["allow_unpriced"] is True
    assert seen["budget"] == 5.0

    seen.clear()
    cli.render(str(tmp_path), budget=5.0)
    assert seen["allow_unpriced"] is False


def test_the_cli_treats_budget_zero_as_a_cap_and_minus_one_as_off(
    monkeypatch, tmp_path
):
    """The distinction the old abort message got backwards."""
    from muvid import __main__ as cli
    from muvid import facade

    seen = {}
    monkeypatch.setattr(facade, "render", lambda root, **kw: seen.update(kw) or [])

    cli.render(str(tmp_path), budget=0.0)
    assert seen["budget"] == 0.0, "--budget=0 is a $0 cap, not an off switch"

    cli.render(str(tmp_path), budget=-1.0)
    assert seen["budget"] is None, "--budget=-1 disables the gate"


# --------------------------------------------------------------------------
# Found by adversarial review of the first pass at this fix
# --------------------------------------------------------------------------


def test_nothing_to_price_is_not_refused_when_falaw_is_missing(tmp_path, monkeypatch):
    """A project that provably cannot spend must not be refused.

    The first version of this fix seeded the `falaw is not installed` skip BEFORE
    reading the project, so it could not tell "could not price things" from "there was
    nothing to price". A project with no pending shots needs falaw for nothing, and was
    refused anyway — which makes `--budget` unusable on a whole install class, and the
    only remedy is to pass `--allow-unpriced` on every invocation. That habituation is
    exactly what the escape must never become.
    """
    import builtins

    from muvid import facade
    from muvid.project import MusicVideoProject

    facade.init_project(tmp_path / "p")
    MusicVideoProject(tmp_path / "p")  # a project with no shots at all
    real_import = builtins.__import__

    def _no_falaw(name, *a, **k):
        if name == "falaw" or name.startswith("falaw."):
            raise ImportError("No module named 'falaw'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_falaw)
    rollup = facade.estimate_render_cost(tmp_path / "p")
    assert not rollup.has_unknown_costs, rollup.skipped
    assert rollup.skipped == ()


def test_an_animation_only_project_needs_no_falaw_and_is_not_refused(
    tmp_path, monkeypatch
):
    """The same defect, in the case a user would actually hit.

    `an`-only animation work on a machine without the `ai` extra is a supported state,
    and it reaches falaw for nothing.
    """
    import builtins

    from muvid import facade

    _project(tmp_path, monkeypatch, strategy="animation")
    monkeypatch.setattr("muvid.cost.an_available", lambda: True)
    real_import = builtins.__import__

    def _no_falaw(name, *a, **k):
        if name == "falaw" or name.startswith("falaw."):
            raise ImportError("No module named 'falaw'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_falaw)
    rollup = facade.estimate_render_cost(tmp_path / "p")
    assert not rollup.has_unknown_costs, rollup.skipped


def test_the_probe_asks_the_same_question_the_renderer_does(monkeypatch):
    """`an` findable but `an.orchestrate` not is a PAID still priced at nothing.

    The renderer does `from an.orchestrate import orchestrate`; probing the parent
    package answers a different question, so a half-removed install degraded to a
    billable `still` while the estimate called it free — the same $0.00-then-bill this
    whole change closes, one level down.
    """
    import importlib.util

    from muvid.cost import an_available

    asked = []

    def _spec(name):
        asked.append(name)
        return None

    monkeypatch.setattr(importlib.util, "find_spec", _spec)
    assert an_available() is False
    assert asked == ["an.orchestrate"], (
        f"probed {asked}, but the renderer imports `an.orchestrate` — probing the "
        "parent lets a findable-but-unusable `an` price a paid still at $0.00"
    )


def test_the_cli_refuses_budget_flags_with_a_single_shot(monkeypatch, tmp_path):
    """Silently ignoring them is worse than not having the gate.

    `facade.render_shot` takes no budget, so accepting `--budget` there would let a
    caller believe a cap applied to a render that is not capped.
    """
    from muvid import __main__ as cli
    from muvid import facade

    monkeypatch.setattr(facade, "render_shot", lambda *a, **k: "out.mp4")

    with pytest.raises(SystemExit, match="whole project"):
        cli.render(str(tmp_path), shot="s01", budget=5.0)
    with pytest.raises(SystemExit, match="whole project"):
        cli.render(str(tmp_path), shot="s01", allow_unpriced=True)
    # ...and an ungated single shot still works.
    cli.render(str(tmp_path), shot="s01")
