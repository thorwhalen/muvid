"""The renderer-neutral scene — every number computed, no renderer opinions.

A :class:`Scene` is a flat list of :class:`Cue`\\ s: a piece of text, where it
sits, how big it is, what colour, and its time envelope. That is all. It says
nothing about ASS tags or CSS, which is exactly what lets ``render_ass`` and
``render_web`` be two backends over one compiler instead of two half-products.

Positions are **normalised** (``0..1`` of the canvas, origin top-left, anchor at
the text's centre) and sizes are a **fraction of canvas height**, so one scene
renders correctly at 1080p, at 4K and in portrait without recomputation.

The compiler is where the treatment spec stops being advice and becomes
geometry. Each archetype is a small function ``(spec, scene_spec, timed_text,
canvas) -> list[Cue]``; adding one is adding a function and a vocabulary entry,
which is the seam a plugin author or a future muvid uses to grow the vocabulary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from muvid.lyricvid import spec as spec_mod
from muvid.lyricvid.timed_text import Line, TimedText, Word

__all__ = [
    "Canvas",
    "Cue",
    "Scene",
    "compile_scene",
    "register_archetype",
    "ARCHETYPE_FNS",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class Canvas:
    """Output geometry. Sizes in the scene are relative to :attr:`height`."""

    width: int = 1920
    height: int = 1080
    fps: int = 30

    @property
    def aspect(self) -> float:
        return self.width / self.height


@dataclass(frozen=True, slots=True, kw_only=True)
class Cue:
    """One piece of text, placed and timed.

    :param x, y: centre of the text, normalised to the canvas (0..1).
    :param size: cap height as a fraction of canvas height.
    :param t_in: when it starts arriving.
    :param t_full: when it is fully arrived. ``t_in == t_full`` is a hard cut.
    :param t_out: when it starts leaving; ``None`` means it never leaves.
    :param t_gone: when it has fully left.
    :param dim_from: when it recedes to ``dim_colour`` (``persistence='dim'``).
    """

    text: str
    x: float
    y: float
    size: float
    t_in: float
    t_full: float
    t_out: float | None = None
    t_gone: float | None = None
    colour: str = "#ffffff"
    dim_colour: str | None = None
    dim_from: float | None = None
    motion: str = "fade"
    layer: int = 0
    #: Free-form, for a renderer that can use it (e.g. per-word wipe fraction).
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class Scene:
    """Everything a renderer needs, and nothing it has to interpret."""

    canvas: Canvas
    duration: float
    background: str
    cues: tuple[Cue, ...]
    typography: spec_mod.Typography
    #: Provenance, for reporting and for tests.
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------


def _apply_case(text: str, case: str) -> str:
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "title":
        return text.title()
    return text


#: Rough advance width of one character as a fraction of cap height. Used only
#: to lay text out; the renderer does real shaping. Deliberately generous, so
#: layout errs toward too much room rather than overlap.
_CHAR_W = 0.62


def _text_width(text: str, size: float, tracking: float = 0.0) -> float:
    """Approximate rendered width, in the same normalised units as ``size``."""
    return len(text) * size * (_CHAR_W + tracking)


def _fit_size(text: str, *, max_frac: float, canvas: Canvas, base: float) -> float:
    """Shrink ``base`` until ``text`` fits inside ``max_frac`` of the width."""
    limit = max_frac * canvas.aspect  # width budget, in height-relative units
    w = _text_width(text, base)
    return base if w <= limit else max(0.02, base * limit / w)


def _envelope(
    word: Word, sc: spec_mod.Scene, *, line: Line, tt: TimedText
) -> tuple[float, float]:
    """When a cue starts arriving and when it is fully there.

    The model chose a quantisation policy; this applies it to measured times.
    ``line`` quantisation is also the automatic fallback when the word times
    were interpolated rather than measured, because pretending to word-accuracy
    we do not have is the thing that makes a lyric video look broken.
    """
    q = sc.timing.quantize_to
    if q in {"line", "downbeat"} or not word.measured:
        t = line.start
    else:
        t = word.start
    t = max(0.0, t - sc.timing.lead_s)
    return t, t + max(0.0, sc.timing.attack_s)


def _persistence_times(
    word: Word, line: Line, tt: TimedText, sc: spec_mod.Scene
) -> tuple[float | None, float | None, float | None]:
    """``(t_out, t_gone, dim_from)`` for one word under a persistence policy."""
    fade = 0.25
    if sc.persistence == "hold":
        return None, None, None
    if sc.persistence == "dim":
        return None, None, word.end
    if sc.persistence == "clear_on_line":
        return line.end, line.end + fade, None
    section = tt.section_for(line.start)
    end = section.end if section else line.end
    return end, end + fade, None


# --------------------------------------------------------------------------
# archetypes
# --------------------------------------------------------------------------

ArchetypeFn = Callable[..., list[Cue]]
ARCHETYPE_FNS: dict[str, ArchetypeFn] = {}


def register_archetype(name: str) -> Callable[[ArchetypeFn], ArchetypeFn]:
    """Register a layout archetype.

    Follows muvid's house registry idiom (``register_visual``,
    ``register_aligner``, ``register_selection_strategy``). A new archetype is a
    function plus an entry in :data:`muvid.lyricvid.spec.ARCHETYPES` so the
    model knows it exists.

    >>> @register_archetype('doctest-demo')
    ... def _demo(**kw): return []
    >>> 'doctest-demo' in ARCHETYPE_FNS
    True
    >>> del ARCHETYPE_FNS['doctest-demo']
    """

    def deco(fn: ArchetypeFn) -> ArchetypeFn:
        ARCHETYPE_FNS[name] = fn
        return fn

    return deco


@register_archetype("one_word_centred")
def _one_word_centred(*, sc, direction, lines, tt, canvas, **_) -> list[Cue]:
    """One word at a time, as large as it can be without touching the edges."""
    cues: list[Cue] = []
    base = float(sc.params.get("size", 0.20))
    for line in lines:
        for w in line.words:
            text = _apply_case(w.text, direction.typography.case)
            t_in, t_full = _envelope(w, sc, line=line, tt=tt)
            cues.append(
                Cue(
                    text=text,
                    x=0.5,
                    y=0.5,
                    size=_fit_size(text, max_frac=0.82, canvas=canvas, base=base),
                    t_in=t_in,
                    t_full=t_full,
                    t_out=w.end,
                    t_gone=w.end + 0.08,
                    colour=direction.palette.fg,
                    motion=sc.motion,
                )
            )
    return cues


@register_archetype("stacked_lines")
def _stacked_lines(*, sc, direction, lines, tt, canvas, **_) -> list[Cue]:
    """Lines accumulate down the frame; the current one is accented."""
    cues: list[Cue] = []
    size = float(sc.params.get("size", 0.075))
    # Never reserve more slots than there are lines to put in them: a
    # three-line song laid out on a six-line grid renders in the top third of
    # the frame with dead space below it.
    per_screen = min(int(sc.params.get("lines_on_screen", 6)), max(1, len(lines)))
    gap = size * 1.55
    for i, line in enumerate(lines):
        slot = i % per_screen
        y = 0.5 + (slot - (per_screen - 1) / 2) * gap
        text = _apply_case(line.text, direction.typography.case)
        first = line.words[0] if line.words else None
        if first is None:
            continue
        t_in, t_full = _envelope(first, sc, line=line, tt=tt)
        cycle_end = (
            lines[i + per_screen - slot].start
            if i + per_screen - slot < len(lines)
            else tt.duration
        )
        cues.append(
            Cue(
                text=text,
                x=0.5,
                y=y,
                size=_fit_size(text, max_frac=0.86, canvas=canvas, base=size),
                t_in=t_in,
                t_full=t_full,
                t_out=cycle_end,
                t_gone=cycle_end + 0.3,
                colour=direction.palette.fg,
                dim_colour=direction.palette.dim,
                dim_from=line.end,
                motion=sc.motion,
            )
        )
    return cues


@register_archetype("karaoke_wipe")
def _karaoke_wipe(*, sc, direction, lines, tt, canvas, **_) -> list[Cue]:
    """Two lines low in the frame, the live one wiped word by word.

    Emits one cue per WORD carrying its own wipe window, plus a dim cue for the
    whole line underneath — which is how ASS karaoke and a CSS clip animation
    both want it.
    """
    cues: list[Cue] = []
    size = float(sc.params.get("size", 0.070))
    y_live = float(sc.params.get("y", 0.80))
    y_next = y_live + size * 1.6
    for i, line in enumerate(lines):
        text = _apply_case(line.text, direction.typography.case)
        fitted = _fit_size(text, max_frac=0.88, canvas=canvas, base=size)
        lead = float(sc.params.get("preroll_s", 1.0))
        show_from = max(0.0, line.start - lead)
        cues.append(
            Cue(
                text=text,
                x=0.5,
                y=y_live,
                size=fitted,
                t_in=show_from,
                t_full=show_from + 0.2,
                t_out=line.end,
                t_gone=line.end + 0.25,
                colour=direction.palette.dim,
                motion="fade",
                layer=0,
            )
        )
        # the wipe: each word lights the accent colour across its own span
        width = _text_width(text, fitted, direction.typography.tracking)
        x0 = 0.5 - width / 2
        cursor = x0
        for w in line.words:
            wt = _apply_case(w.text, direction.typography.case)
            ww = _text_width(wt + " ", fitted, direction.typography.tracking)
            cues.append(
                Cue(
                    text=wt,
                    x=cursor + ww / 2,
                    y=y_live,
                    size=fitted,
                    t_in=w.start,
                    t_full=w.start + max(0.01, sc.timing.attack_s),
                    t_out=line.end,
                    t_gone=line.end + 0.25,
                    colour=direction.palette.accent,
                    motion="wipe",
                    layer=1,
                    extra={"wipe_end": w.end},
                )
            )
            cursor += ww
        if i + 1 < len(lines):
            nxt = _apply_case(lines[i + 1].text, direction.typography.case)
            cues.append(
                Cue(
                    text=nxt,
                    x=0.5,
                    y=y_next,
                    size=_fit_size(nxt, max_frac=0.88, canvas=canvas, base=size * 0.86),
                    t_in=show_from,
                    t_full=show_from + 0.2,
                    t_out=line.end,
                    t_gone=line.end + 0.2,
                    colour=direction.palette.dim,
                    motion="fade",
                    layer=0,
                )
            )
    return cues


@register_archetype("concrete_page")
def _concrete_page(*, sc, direction, lines, tt, canvas, **_) -> list[Cue]:
    """The whole lyric typeset as one fixed page; words ignite in reading order.

    The page never reflows — every word's position is computed once — so the
    shape the text makes is stable for the whole song, which is the entire
    point of the treatment. Words that have not been sung yet are either absent
    (``persistence`` other than ``dim``) or present in the dim colour.
    """
    cues: list[Cue] = []
    rows = list(lines)
    if not rows:
        return cues
    top = float(sc.params.get("top", 0.08))
    bottom = float(sc.params.get("bottom", 0.94))
    leading = float(sc.params.get("leading", 1.18))
    widest = max((len(l.text) for l in rows), default=1)

    # one size that makes both the tallest column and the widest row fit
    size_h = (bottom - top) / max(1, len(rows)) / leading
    size_w = (0.90 * canvas.aspect) / max(1, widest * _CHAR_W)
    size = min(size_h, size_w)
    total_h = len(rows) * size * leading
    y0 = (top + bottom) / 2 - total_h / 2 + size * leading / 2

    show_all = sc.persistence == "dim"
    for r, line in enumerate(rows):
        y = y0 + r * size * leading
        row_w = _text_width(
            _apply_case(line.text, direction.typography.case),
            size,
            direction.typography.tracking,
        )
        cursor = 0.5 - row_w / 2
        for w in line.words:
            wt = _apply_case(w.text, direction.typography.case)
            ww = _text_width(wt + " ", size, direction.typography.tracking)
            t_in, t_full = _envelope(w, sc, line=line, tt=tt)
            cues.append(
                Cue(
                    text=wt,
                    x=cursor + (ww - _text_width(" ", size)) / 2,
                    y=y,
                    size=size,
                    t_in=0.0 if show_all else t_in,
                    t_full=t_full,
                    t_out=None,
                    t_gone=None,
                    colour=direction.palette.fg,
                    dim_colour=direction.palette.dim if show_all else None,
                    dim_from=None,
                    motion=sc.motion,
                    extra={"ignite_at": t_in},
                )
            )
            cursor += ww
    return cues


@register_archetype("text_on_path")
def _text_on_path(*, sc, direction, lines, tt, canvas, **_) -> list[Cue]:
    """Words ride a gentle arc across the frame, one line per sweep."""
    cues: list[Cue] = []
    size = float(sc.params.get("size", 0.085))
    amp = float(sc.params.get("amplitude", 0.14))
    for line in lines:
        n = max(1, len(line.words))
        for i, w in enumerate(line.words):
            frac = (i + 0.5) / n
            t_in, t_full = _envelope(w, sc, line=line, tt=tt)
            t_out, t_gone, dim_from = _persistence_times(w, line, tt, sc)
            cues.append(
                Cue(
                    text=_apply_case(w.text, direction.typography.case),
                    x=0.10 + 0.80 * frac,
                    y=0.5 - amp * math.sin(math.pi * frac),
                    size=size,
                    t_in=t_in,
                    t_full=t_full,
                    t_out=t_out,
                    t_gone=t_gone,
                    colour=direction.palette.fg,
                    dim_colour=direction.palette.dim,
                    dim_from=dim_from,
                    motion=sc.motion,
                )
            )
    return cues


@register_archetype("scatter")
def _scatter(*, sc, direction, lines, tt, canvas, **_) -> list[Cue]:
    """Words placed away from centre on a deterministic phyllotaxis spiral.

    Deterministic on purpose: the same song must produce the same video, so the
    placement is a closed-form function of the word's index, never ``random``.
    """
    cues: list[Cue] = []
    size = float(sc.params.get("size", 0.075))
    spread = float(sc.params.get("spread", 0.36))
    golden = math.pi * (3 - math.sqrt(5))
    words = [(l, w) for l in lines for w in l.words]
    n = max(1, len(words))
    for i, (line, w) in enumerate(words):
        r = spread * math.sqrt((i + 0.5) / n)
        a = i * golden
        t_in, t_full = _envelope(w, sc, line=line, tt=tt)
        t_out, t_gone, dim_from = _persistence_times(w, line, tt, sc)
        cues.append(
            Cue(
                text=_apply_case(w.text, direction.typography.case),
                x=0.5 + r * math.cos(a) / canvas.aspect * canvas.aspect * 0.9,
                y=0.5 + r * math.sin(a),
                size=size,
                t_in=t_in,
                t_full=t_full,
                t_out=t_out,
                t_gone=t_gone,
                colour=direction.palette.fg,
                dim_colour=direction.palette.dim,
                dim_from=dim_from,
                motion=sc.motion,
            )
        )
    return cues


@register_archetype("shape_fill")
def _shape_fill(*, sc, direction, lines, tt, canvas, **_) -> list[Cue]:
    """Words packed inside an outline, filling it as the song proceeds.

    Placement is delegated to :mod:`muvid.lyricvid.shape`, which grows an
    Archimedean spiral inside the shape's distance field — the classic
    shape-word-cloud construction. It is deterministic and it is Python's job,
    never the model's.
    """
    from muvid.lyricvid.shape import pack_words_into_shape

    words = [(l, w) for l in lines for w in l.words]
    shape = sc.shape or spec_mod.ShapeRef()
    placements = pack_words_into_shape(
        [_apply_case(w.text, direction.typography.case) for _, w in words],
        shape_kind=shape.kind,
        shape_value=shape.value,
        aspect=canvas.aspect,
        base_size=float(sc.params.get("size", 0.06)),
    )
    cues: list[Cue] = []
    for (line, w), place in zip(words, placements):
        if place is None:  # did not fit; dropping is better than overlapping
            continue
        t_in, t_full = _envelope(w, sc, line=line, tt=tt)
        t_out, t_gone, dim_from = _persistence_times(w, line, tt, sc)
        cues.append(
            Cue(
                text=place.text,
                x=place.x,
                y=place.y,
                size=place.size,
                t_in=t_in,
                t_full=t_full,
                t_out=t_out,
                t_gone=t_gone,
                colour=direction.palette.fg,
                dim_colour=direction.palette.dim,
                dim_from=dim_from,
                motion=sc.motion,
            )
        )
    return cues


# --------------------------------------------------------------------------
# the compiler
# --------------------------------------------------------------------------


def _lines_for_scene(sc: spec_mod.Scene, tt: TimedText) -> list[Line]:
    if "*" in sc.applies_to:
        return list(tt.lines())
    wanted = {a.lower() for a in sc.applies_to}
    return [
        l
        for section in tt.sections
        if section.label.lower() in wanted
        for l in section.lines
    ]


def compile_scene(
    treatment: spec_mod.TreatmentSpec,
    timed_text: TimedText,
    *,
    canvas: Canvas | None = None,
) -> Scene:
    """Turn a treatment plus timed text into a fully-resolved :class:`Scene`.

    Every number in the result was computed here, from measurement. Nothing the
    model wrote reaches a renderer as a coordinate or a time.

    >>> from muvid.lyricvid.timed_text import from_words
    >>> tt = from_words([('one', 0.0, .5), ('two', .5, 1.0)], duration=1.0)
    >>> s = compile_scene(spec_mod.TreatmentSpec(), tt)
    >>> len(s.cues), s.cues[0].text
    (2, 'one')
    """
    canvas = canvas or Canvas()
    treatment, _notes = spec_mod.repair(treatment)
    direction = treatment.direction

    covered: set[int] = set()
    cues: list[Cue] = []
    fallback: spec_mod.Scene | None = None
    for sc in treatment.scenes:
        if "*" in sc.applies_to and fallback is None:
            fallback = sc
        lines = _lines_for_scene(sc, timed_text)
        if not lines:
            continue
        fn = ARCHETYPE_FNS.get(sc.archetype)
        if fn is None:  # repair() guarantees this, but stay total
            fn = ARCHETYPE_FNS["one_word_centred"]
        cues.extend(
            fn(sc=sc, direction=direction, lines=lines, tt=timed_text, canvas=canvas)
        )
        covered.update(id(l) for l in lines)

    return Scene(
        canvas=canvas,
        duration=timed_text.duration,
        background=direction.palette.bg,
        cues=tuple(sorted(cues, key=lambda c: (c.t_in, c.layer))),
        typography=direction.typography,
        meta={
            "source": timed_text.source,
            "measured": timed_text.measured,
            "archetypes": sorted({s.archetype for s in treatment.scenes}),
            "n_cues": len(cues),
        },
    )
