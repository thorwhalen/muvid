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

    Uses ``find_spec`` rather than an import: no module executes, and this runs on
    the estimate path. It cannot see an ``an`` that is installed but broken — that
    also degrades to a still, so this under-reports rather than over-reports.
    """
    from importlib.util import find_spec

    try:
        return find_spec("an") is not None
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
        # NOT a free project — an unpriceable one, and the worst shape of it:
        # returning a bare $0.00 with an EMPTY `skipped` leaves a gate with
        # nothing to fail on, so the caller cannot even tell that nothing was
        # priced. Naming it here is what lets `has_unknown_costs` be true.
        return _Rollup(
            total_amount=0.0,
            currency="USD",
            skipped=(f"nothing could be priced: falaw is not installed ({e})",),
        )

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
    except Exception:
        skipped.append(f"shot {shot_id} {kind}: no model in category {category!r}")
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
