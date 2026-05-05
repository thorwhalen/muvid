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

    def by_kind(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for ln in self.lines:
            out[ln.kind] = out.get(ln.kind, 0.0) + ln.amount
        return out


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
    except ImportError:
        return _Rollup(total_amount=0.0, currency="USD")

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
            "shot.image", shot.id, "image", quality,
            pick_model, estimate_call_cost, skipped,
            seconds=None, note="still",
        )
        return

    if strategy == "image_to_video":
        yield from _price_one(
            "shot.image", shot.id, "image", quality,
            pick_model, estimate_call_cost, skipped,
            seconds=None, note="storyboard still",
        )
        yield from _price_one(
            "shot.image_to_video", shot.id, "image_to_video", quality,
            pick_model, estimate_call_cost, skipped,
            seconds=duration, note=f"i2v × {duration:.1f}s",
        )
        return

    if strategy == "text_to_video":
        yield from _price_one(
            "shot.text_to_video", shot.id, "text_to_video", quality,
            pick_model, estimate_call_cost, skipped,
            seconds=duration, note=f"t2v × {duration:.1f}s",
        )
        return

    if strategy == "lipsync":
        # animate_face uses category="avatar" in falaw.
        yield from _price_one(
            "shot.lipsync", shot.id, "avatar", quality,
            pick_model, estimate_call_cost, skipped,
            seconds=duration, note=f"avatar × {duration:.1f}s",
        )
        return

    if strategy == "animation":
        # Local cutout render, no fal calls.
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
        skipped.append(
            f"shot {shot_id} {kind}: no cost_estimate on {record.id!r}"
        )
        return
    currency = (
        record.cost_estimate.currency
        if record.cost_estimate is not None
        else "USD"
    )
    yield _RolledLine(
        kind=kind, item_id=shot_id,
        model_id=record.id, amount=cost,
        currency=currency, note=note,
    )


# Public alias for callers who don't want the underscore-prefixed name.
CostRollup = _Rollup
CostLine = _RolledLine


__all__ = [
    "CostLine",
    "CostRollup",
    "estimate_render_cost",
]
