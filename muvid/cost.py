"""Cost estimation for a muvid project.

Walks the project's ShotSpecs and returns the same CostRollup shape
``falaw.estimate_scene_cost`` returns, but priced against muvid's
render strategies (lipsync / image_to_video / text_to_video / animation
/ still) instead of falaw's Scene/Beat IR. The pricing pulls
``falaw.pick_model`` per category + ``falaw.estimate_call_cost`` per
ModelRecord, so any improvements to ``falaw.cost`` flow through.

Used by:

- :func:`muvid.facade.estimate_render_cost(root, *, quality)`
- ``muvid status`` shows the rollup as a summary line.
- ``muvid render --budget=$X`` aborts before any fal call when the
  estimate exceeds X.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from muvid.project import MusicVideoProject
from muvid.schema import ShotSpec


@dataclass(frozen=True, slots=True, kw_only=True)
class _RolledLine:
    kind: str
    item_id: str
    model_id: str
    amount: float
    currency: str
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Rollup:
    total_amount: float
    currency: str
    lines: tuple[_RolledLine, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def has_unknown_costs(self) -> bool:
        """Whether anything could NOT be priced — the flag every gate must read.

        ``total_amount`` alone is not a decision. A price this module could not
        determine is routed into :attr:`skipped` and contributes **nothing** to
        the total, which is correct arithmetic and a trap for any caller that
        compares only the number: an unpriceable shot reads as free and clears
        any budget. Same encoding as reelee's
        ``OperationEstimate(estimated_cost_usd, has_unknown_costs)``, and the
        same rule — *unknown is not zero, and unknown must force approval*.
        """
        return bool(self.skipped)

    def by_kind(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for ln in self.lines:
            out[ln.kind] = out.get(ln.kind, 0.0) + ln.amount
        return out


def an_available() -> bool:
    """Whether the ``an`` package is importable — i.e. whether ``animation`` is free.

    A seam, not a detail. The estimate genuinely depends on the environment here,
    because the RENDER does: ``an`` is declared in no muvid extra and carries no
    version floor, so a machine without it renders every ``animation`` shot as a
    ``still`` instead. Making the probe a named function keeps that dependence
    visible and lets a test pin both branches, rather than the answer quietly
    changing with the runner.

    **Probes ``an.orchestrate``, not ``an``** — the exact module
    ``muvid.renderers.animation`` imports, and matching it is the whole point. Probing
    the parent answers a DIFFERENT question: a package that is findable but whose
    submodule cannot be imported (a half-removed install, a broken transitive dep) makes
    the renderer degrade to a paid ``still`` while the estimate prices it at nothing —
    the same $0.00-then-bill this function exists to close, one level down.

    Catches what the renderer catches. ``find_spec`` on a submodule imports the parent,
    so a package whose ``__init__`` raises ``ImportError`` reports unavailable here and
    degrades there; one that raises something else propagates in BOTH places, so the
    render fails rather than billing and "free" is the right estimate for it.
    """
    from importlib.util import find_spec

    try:
        return find_spec("an.orchestrate") is not None
    except (ImportError, ValueError):  # a broken or partially-removed install
        return False


def estimate_render_cost(
    project: MusicVideoProject,
    *,
    quality: str = "balanced",
) -> _Rollup:
    """Estimate USD cost of running ``muvid render`` for the whole project.

    Returns a structured rollup. Per-shot pricing depends on
    ``shot.render_strategy``: ``image_to_video`` and ``text_to_video``
    cost 1 image-gen + 1 video-gen × duration; ``lipsync`` is 1
    avatar × duration; ``still`` is 1 image-gen; ``animation`` is
    free of fal calls (rendered locally via ``an``).
    """
    try:
        from falaw import estimate_call_cost
        from falaw.registry import pick_model
    except ImportError as e:
        # NOT a free project — an unpriceable one, and the worst shape of it: a bare
        # $0.00 with an EMPTY `skipped` leaves a gate nothing to fail on, so the caller
        # cannot even tell that nothing was priced.
        #
        # But it is not unconditionally unpriceable either, and returning early here
        # said it was: a project whose shots are all already rendered, or which has no
        # shots, or which is all `animation` on a machine that has `an`, needs falaw for
        # NOTHING — and was refused anyway. So instead of short-circuiting, fall through
        # to the normal walk with a `pick_model` that explains itself. `_price_one` then
        # records a skip per shot that actually WOULD have reached falaw, and a project
        # that needs none is priced cleanly at $0.00 with an empty `skipped`.
        # Bound to a local FIRST: Python deletes the `except ... as e` name at the end
        # of the block, so a closure over `e` raises NameError at call time — and the
        # `except Exception` in `_price_one` would swallow that and report the
        # NameError as the pricing failure.
        reason = f"falaw is not installed ({e})"

        def pick_model(**_kw):
            raise RuntimeError(reason)

        estimate_call_cost = None  # unreachable: pick_model always raises first

    spec = project.read_spec()
    lines: list[_RolledLine] = []
    skipped: list[str] = []

    for sh in spec.shots:
        # Skip already-rendered shots — render() will hash-cache them.
        out = project.shot_dir(sh.id) / "output.mp4"
        if out.exists():
            continue
        for line in _shot_lines(sh, quality, pick_model, estimate_call_cost, skipped):
            lines.append(line)

    total = sum(ln.amount for ln in lines)
    currency = lines[0].currency if lines else "USD"
    return _Rollup(
        total_amount=total,
        currency=currency,
        lines=tuple(lines),
        skipped=tuple(skipped),
    )


def _shot_lines(
    shot: ShotSpec,
    quality: str,
    pick_model,
    estimate_call_cost,
    skipped: list[str],
):
    """Per-strategy pricing breakdown."""
    duration = float(shot.duration_s or 0.0)
    strategy = shot.render_strategy

    if strategy == "still":
        yield from _price_one(
            "shot.image",
            shot.id,
            "image",
            quality,
            pick_model,
            estimate_call_cost,
            skipped,
            seconds=None,
            note="still",
        )
        return

    if strategy == "image_to_video":
        yield from _price_one(
            "shot.image",
            shot.id,
            "image",
            quality,
            pick_model,
            estimate_call_cost,
            skipped,
            seconds=None,
            note="storyboard still",
        )
        yield from _price_one(
            "shot.image_to_video",
            shot.id,
            "image_to_video",
            quality,
            pick_model,
            estimate_call_cost,
            skipped,
            seconds=duration,
            note=f"i2v × {duration:.1f}s",
        )
        return

    if strategy == "text_to_video":
        yield from _price_one(
            "shot.text_to_video",
            shot.id,
            "text_to_video",
            quality,
            pick_model,
            estimate_call_cost,
            skipped,
            seconds=duration,
            note=f"t2v × {duration:.1f}s",
        )
        return

    if strategy == "lipsync":
        # animate_face uses category="avatar" in falaw.
        yield from _price_one(
            "shot.lipsync",
            shot.id,
            "avatar",
            quality,
            pick_model,
            estimate_call_cost,
            skipped,
            seconds=duration,
            note=f"avatar × {duration:.1f}s",
        )
        return

    if strategy == "animation":
        if an_available():
            # Local cutout render, no fal calls.
            return
        # `an` is not installed, so the renderer will degrade this shot to a
        # `still` (muvid#46) — and `still` reaches ``falaw.generate_image``.
        # Pricing it at nothing is how a $0.00 estimate cleared a budget and
        # then billed. Price it as exactly what it will render as; that this
        # over-estimates when an environment anchor already exists is not a new
        # imprecision, it is the same one every `still` shot already carries.
        yield from _price_one(
            "shot.image",
            shot.id,
            "image",
            quality,
            pick_model,
            estimate_call_cost,
            skipped,
            seconds=None,
            note="animation degraded to still (`an` not installed)",
        )
        return


def _price_one(
    kind: str,
    shot_id: str,
    category: str,
    quality: str,
    pick_model,
    estimate_call_cost,
    skipped: list[str],
    *,
    seconds: float | None,
    note: str = "",
):
    try:
        record = pick_model(category=category, quality_tier=quality)
    except Exception as e:
        # The reason is appended rather than replacing the message: "no model in
        # category" is what a missing REGISTRY ENTRY looks like, and "falaw is not
        # installed" is what a missing registry looks like. A caller reading the abort
        # needs to tell those apart — one is a catalogue gap, the other is an install.
        skipped.append(
            f"shot {shot_id} {kind}: no model in category {category!r} ({e})"
        )
        return
    cost = estimate_call_cost(record, seconds=seconds)
    if cost is None:
        skipped.append(f"shot {shot_id} {kind}: no cost_estimate on {record.id!r}")
        return
    currency = (
        record.cost_estimate.currency if record.cost_estimate is not None else "USD"
    )
    yield _RolledLine(
        kind=kind,
        item_id=shot_id,
        model_id=record.id,
        amount=cost,
        currency=currency,
        note=note,
    )


# Public alias for callers who don't want the underscore-prefixed name.
CostRollup = _Rollup
CostLine = _RolledLine


__all__ = [
    "CostLine",
    "CostRollup",
    "estimate_render_cost",
]
