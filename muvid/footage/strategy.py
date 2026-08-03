"""The pluggable ``SelectionStrategy`` registry — alignments → an EDL.

The user-extensible strategy pattern behind full-auto assembly: a strategy turns a set of
:class:`~muvid.footage.edl.FootageAlignment`\\ s (which clip covers which span of the
song, with a confidence) into an EDL (which clip to SHOW for each span). Strategies emit
only ``{song_start, song_end, clip_id}`` — the ``clip_in`` sign convention lives in
:func:`muvid.footage.edl.derive_cuts` (SSOT), so a third-party strategy can't desync the
cut. Every strategy's output still passes :func:`~muvid.footage.edl.validate_edl` before
any cutting, so a strategy that leaves a gap fails loudly with the exact uncovered span.

Registry idiom mirrors ``mixing.audio.segmentation`` (a ``strategy: str | callable`` param
+ a ``_STRATEGIES`` dict + :func:`resolve_strategy`) — the federation's established shape.
Register your own with :func:`register_selection_strategy`.
"""

from __future__ import annotations

from typing import Callable, Sequence

from muvid.footage.edl import EdlEntry, FootageAlignment

#: A strategy: alignments + song duration → an EDL (built-ins ignore song_duration but it
#: is passed so a strategy MAY reason about the full timeline).
SelectionStrategy = Callable[[Sequence[FootageAlignment], float], "list[EdlEntry]"]

_STRATEGIES: dict[str, SelectionStrategy] = {}
_EPS = 1e-6


def register_selection_strategy(slug: str, fn: SelectionStrategy) -> SelectionStrategy:
    """Register a selection strategy under ``slug`` (returns it, for inline use)."""
    if not isinstance(slug, str) or not slug.strip() or any(c.isspace() for c in slug):
        raise ValueError(
            f"strategy slug must be a non-empty whitespace-free string: {slug!r}"
        )
    if not callable(fn):
        raise TypeError("a selection strategy must be callable")
    _STRATEGIES[slug] = fn
    return fn


def list_strategies() -> list[str]:
    """The registered strategy slugs, sorted."""
    return sorted(_STRATEGIES)


def resolve_strategy(strategy: "str | SelectionStrategy") -> SelectionStrategy:
    """Resolve a strategy name OR a bare callable to a :data:`SelectionStrategy`."""
    if callable(strategy):
        return strategy
    if strategy not in _STRATEGIES:
        raise KeyError(
            f"unknown strategy {strategy!r}; registered: {list_strategies()}"
        )
    return _STRATEGIES[strategy]


def select_edl(
    strategy: "str | SelectionStrategy",
    alignments: Sequence[FootageAlignment],
    song_duration: float,
) -> list[EdlEntry]:
    """Run ``strategy`` (name or callable) to produce an EDL from ``alignments``."""
    return list(resolve_strategy(strategy)(alignments, song_duration))


# --------------------------------------------------------------------------
# The built-in strategies — all deterministic, non-AI, over the covered timeline.
# --------------------------------------------------------------------------


def _breakpoints(alignments: Sequence[FootageAlignment]) -> list[float]:
    """Sorted unique coverage boundaries — the elementary-interval grid."""
    bs = set()
    for a in alignments:
        bs.add(round(a.coverage[0], 6))
        bs.add(round(a.coverage[1], 6))
    return sorted(bs)


def _covering(
    alignments: Sequence[FootageAlignment], mid: float
) -> list[FootageAlignment]:
    """Alignments whose coverage contains ``mid``."""
    return [a for a in alignments if a.coverage[0] - _EPS <= mid < a.coverage[1] + _EPS]


def _coalesce(spans: list[tuple[float, float, str]]) -> list[EdlEntry]:
    """Merge consecutive same-clip elementary spans into one EDL entry."""
    out: list[EdlEntry] = []
    for start, end, clip_id in spans:
        if out and out[-1].clip_id == clip_id and abs(out[-1].song_end - start) <= 1e-3:
            out[-1] = EdlEntry(out[-1].song_start, end, clip_id)
        else:
            out.append(EdlEntry(start, end, clip_id))
    return out


def _build(alignments, pick) -> list[EdlEntry]:
    """Walk the elementary intervals, ``pick`` a clip for each covered one, coalesce.

    ``pick(covering, prev_clip_id) -> clip_id``. Intervals covered by no clip are left
    out — the resulting gap is surfaced by validate_edl with the exact uncovered span.
    """
    if not alignments:
        return []
    bs = _breakpoints(alignments)
    spans: list[tuple[float, float, str]] = []
    prev_clip_id = None
    for lo, hi in zip(bs, bs[1:]):
        if hi - lo <= 1e-3:
            continue
        covering = _covering(alignments, (lo + hi) / 2)
        if not covering:
            prev_clip_id = None  # a gap breaks continuity
            continue
        clip_id = pick(covering, prev_clip_id)
        spans.append((lo, hi, clip_id))
        prev_clip_id = clip_id
    return _coalesce(spans)


def best_confidence(alignments, song_duration) -> list[EdlEntry]:
    """For each covered span, show the highest-confidence clip (ties: longest coverage)."""

    def pick(covering, _prev):
        return max(
            covering, key=lambda a: (a.confidence, a.coverage[1] - a.coverage[0])
        ).clip_id

    return _build(alignments, pick)


def longest_take(alignments, song_duration) -> list[EdlEntry]:
    """Prefer the clip that keeps rolling longest — pick the one whose coverage extends
    furthest forward (ties: higher confidence). Yields long, continuous takes."""

    def pick(covering, _prev):
        return max(covering, key=lambda a: (a.coverage[1], a.confidence)).clip_id

    return _build(alignments, pick)


def fewest_cuts(alignments, song_duration) -> list[EdlEntry]:
    """Stay on the current clip as long as it covers; only switch when it runs out
    (then to the clip extending furthest). Minimizes the number of cuts."""

    def pick(covering, prev):
        if prev is not None and any(a.clip_id == prev for a in covering):
            return prev
        return max(covering, key=lambda a: (a.coverage[1], a.confidence)).clip_id

    return _build(alignments, pick)


for _slug, _fn in [
    ("best_confidence", best_confidence),
    ("longest_take", longest_take),
    ("fewest_cuts", fewest_cuts),
]:
    register_selection_strategy(_slug, _fn)

#: The default auto-strategy when none is chosen.
DEFAULT_STRATEGY = "best_confidence"
