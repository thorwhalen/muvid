"""The planner: a pool of media + a beat grid + sections -> an edit list of slots.

This is the core of the subgenre. Everything creative that CapCut's "photo beat
sync" templates, Animoto and Rotor sell is a planning decision, and it lives
here as pure functions over measured data: no file is opened, no ffmpeg runs,
and the same inputs always yield the same plan.

Three decisions, each in its own place:

**Where the cuts fall** — an *archetype* (a function in :data:`ARCHETYPE_FNS`,
registered with :func:`register_archetype` the way ``muvid.lyricvid.scene``
registers its layouts) walks a section's beats and returns
:class:`SlotDraft`\\ s: a span, how many tile regions it has, which of them are
fresh, a motion cycle and a transition. Every cut is a beat, a downbeat or a
subdivision of the measured grid; the archetype only chooses the density, and
the section label and the treatment's ``cut_feel`` scale it (a chorus is twice
as dense as a verse, an intro half).

**Which image fills each slot** — the reuse policy (:func:`assign_media`),
which is what lets twelve photos carry a three-minute song:

* **never the same image twice within ``min_gap`` cuts** (the gap shrinks to
  ``pool - 1`` for a small pool, so a two-photo pool alternates);
* among what is allowed, the **least-used** image wins, and ties are broken by
  a golden-ratio stride over the slot index — a closed-form permutation that
  keeps the order from reading as a loop, with no random anywhere;
* every **return uses a different crop/move variant** (the k-th use of an
  image takes the k-th entry of :data:`CROP_VARIANTS`; a clip's k-th use trims
  a different stretch of it);
* the **strongest quarter of the pool is reserved for the final chorus** and
  leads it, strongest first;
* a **cover**, when given, opens and closes the montage and appears nowhere else.

**How each tile moves** — :func:`tile_path`, a piecewise-linear window path
(normalised to the fitted frame) that the renderer compiles to ``zoompan``. A
blended boundary samples the same path from both sides, so a Ken Burns move
never restarts on a crossfade (the muvid#73 lesson, honoured by construction).

The output, :class:`Plan`, is JSON-able and is written next to every render as
``plan.json`` — the inspectable, hand-editable artifact the subgenre contract
asks for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from muvid.montage import spec as spec_mod
from muvid.montage.analysis import Analysis, Media, Section

__all__ = [
    "ARCHETYPE_FNS",
    "CROP_VARIANTS",
    "Keyframe",
    "Plan",
    "PlanContext",
    "Slot",
    "SlotDraft",
    "Tile",
    "Window",
    "assign_media",
    "cut_times",
    "plan_montage",
    "register_archetype",
    "tile_path",
]

PLAN_VERSION = "1.0"

#: The closed set of framings a still may be shown in, as ``(cx, cy, size)``
#: of the frame fitted to the canvas (``size`` is the visible fraction; the
#: aspect always follows the canvas). The k-th use of an image takes the k-th
#: variant, so a revisit is a different picture.
CROP_VARIANTS: dict[str, tuple[float, float, float]] = {
    "full": (0.5, 0.5, 1.0),
    "left": (0.32, 0.5, 0.78),
    "right": (0.68, 0.5, 0.78),
    "top": (0.5, 0.32, 0.78),
    "bottom": (0.5, 0.68, 0.78),
    "centre": (0.5, 0.5, 0.7),
}
_VARIANTS = tuple(CROP_VARIANTS)

#: The irrational stride behind every "which of the tied candidates" choice.
#: ``frac(i * phi)`` visits ``[0, 1)`` evenly and never cycles, which is exactly
#: what keeps a round-robin from reading as one.
GOLDEN = 0.6180339887498949
#: A slot shorter than this (seconds) is merged into its neighbour.
MIN_SLOT_S = 0.1
#: A punch-zoom relaxes to rest over this many beats.
PUNCH_BEATS = 0.5
#: A crossfade may take at most this fraction of the shorter neighbour.
MAX_TRANSITION_FRACTION = 0.45
#: A quarter of the pool is held back for the finale.
FINALE_RESERVE_DIVISOR = 4
GRID_REGIONS = 4
#: Which grid tile swaps on successive beats. Not 0,1,2,3: a diagonal order
#: reads less like a scan.
_GRID_SWAP_ORDER = (0, 3, 1, 2)
_BALLAD_CYCLE = ("zoom_in", "pan_right", "zoom_out", "pan_left")

#: Density multipliers by section label, on top of the archetype's verse pacing.
_SECTION_DENSITY = {"chorus": 0.5, "bridge": 0.5, "intro": 2.0, "outro": 2.0}
#: No still is ever held longer than this, whatever the pacing says: a ballad
#: intro at ``slow`` would otherwise hold one photo for half a minute. In
#: seconds, not beats, because the complaint is about wall-clock stillness.
MAX_HOLD_S = 10.0


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Window:
    """A framing: top-left ``(x, y)`` and visible fraction ``size`` of the
    fitted frame, all in ``0..1``. The aspect is the canvas's."""

    x: float
    y: float
    size: float


@dataclass(frozen=True, slots=True)
class Keyframe:
    """``window`` at slot-relative time ``t`` (seconds)."""

    t: float
    window: Window


@dataclass(frozen=True, slots=True, kw_only=True)
class Tile:
    """One region of a slot: which media, framed and moved how."""

    region: int
    media: int
    motion: str
    variant: str
    path: tuple[Keyframe, ...]
    #: In-point into a clip (seconds). Ignored for stills.
    source_in: float = 0.0
    #: The ordinal of this use of the media, 0-based (drives variant/in-point).
    use: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class Slot:
    """One span of the montage, in song seconds."""

    index: int
    start: float
    end: float
    section: str
    archetype: str
    regions: int
    tiles: tuple[Tile, ...]
    #: How this slot ARRIVES. The first slot's is always a cut.
    transition: str = "cut"
    transition_s: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True, kw_only=True)
class SlotDraft:
    """What an archetype emits: a span with regions, before media assignment."""

    start: float
    end: float
    section: str
    archetype: str
    regions: int = 1
    fresh: tuple[int, ...] = (0,)
    motions: tuple[str, ...] = ("none",)
    amplitude: float = 0.0
    transition: str = "cut"
    transition_s: float = 0.0


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanContext:
    """What every archetype gets: the measurements and the direction."""

    analysis: Analysis
    direction: spec_mod.Direction


@dataclass(frozen=True, slots=True, kw_only=True)
class Plan:
    """The edit list. JSON-able, canvas-independent (windows are normalised)."""

    duration: float
    tempo_bpm: float
    beats_per_bar: int
    beat_source: str
    section_source: str
    sections: tuple[Section, ...]
    media: tuple[Media, ...]
    slots: tuple[Slot, ...]
    reuse: Mapping[str, Any] = field(default_factory=dict)
    treatment: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    plan_version: str = PLAN_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version,
            "duration": round(self.duration, 4),
            "tempo_bpm": round(self.tempo_bpm, 3),
            "beats_per_bar": self.beats_per_bar,
            "beat_source": self.beat_source,
            "section_source": self.section_source,
            "sections": [
                {"label": s.label, "start": round(s.start, 4), "end": round(s.end, 4)}
                for s in self.sections
            ],
            "media": [m.to_dict() for m in self.media],
            "slots": [
                {
                    "index": s.index,
                    "start": round(s.start, 4),
                    "end": round(s.end, 4),
                    "section": s.section,
                    "archetype": s.archetype,
                    "regions": s.regions,
                    "transition": s.transition,
                    "transition_s": round(s.transition_s, 4),
                    "tiles": [
                        {
                            "region": t.region,
                            "media": t.media,
                            "motion": t.motion,
                            "variant": t.variant,
                            "use": t.use,
                            "source_in": round(t.source_in, 4),
                            "path": [
                                {
                                    "t": round(k.t, 4),
                                    "x": round(k.window.x, 4),
                                    "y": round(k.window.y, 4),
                                    "size": round(k.window.size, 4),
                                }
                                for k in t.path
                            ],
                        }
                        for t in s.tiles
                    ],
                }
                for s in self.slots
            ],
            "reuse": dict(self.reuse),
            "treatment": dict(self.treatment),
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Plan":
        """Read a plan back — a hand-edited ``plan.json`` renders the same way."""
        media = tuple(
            Media(
                index=int(m["index"]),
                path=str(m["path"]),
                kind=str(m["kind"]),
                width=int(m["width"]),
                height=int(m["height"]),
                duration=m.get("duration"),
                strength=float(m.get("strength", 0.0)),
            )
            for m in d.get("media", ())
        )
        slots = tuple(
            Slot(
                index=int(s["index"]),
                start=float(s["start"]),
                end=float(s["end"]),
                section=str(s.get("section", "*")),
                archetype=str(s.get("archetype", "")),
                regions=int(s.get("regions", 1)),
                transition=str(s.get("transition", "cut")),
                transition_s=float(s.get("transition_s", 0.0)),
                tiles=tuple(
                    Tile(
                        region=int(t["region"]),
                        media=int(t["media"]),
                        motion=str(t.get("motion", "none")),
                        variant=str(t.get("variant", "full")),
                        use=int(t.get("use", 0)),
                        source_in=float(t.get("source_in", 0.0)),
                        path=tuple(
                            Keyframe(
                                float(k["t"]),
                                Window(float(k["x"]), float(k["y"]), float(k["size"])),
                            )
                            for k in t.get("path", ())
                        )
                        or (Keyframe(0.0, Window(0.0, 0.0, 1.0)),),
                    )
                    for t in s.get("tiles", ())
                ),
            )
            for s in d.get("slots", ())
        )
        return cls(
            duration=float(d["duration"]),
            tempo_bpm=float(d.get("tempo_bpm", 0.0)),
            beats_per_bar=int(d.get("beats_per_bar", 4)),
            beat_source=str(d.get("beat_source", "")),
            section_source=str(d.get("section_source", "")),
            sections=tuple(
                Section(
                    label=str(s["label"]), start=float(s["start"]), end=float(s["end"])
                )
                for s in d.get("sections", ())
            ),
            media=media,
            slots=slots,
            reuse=dict(d.get("reuse", {})),
            treatment=dict(d.get("treatment", {})),
            notes=tuple(d.get("notes", ())),
            plan_version=str(d.get("plan_version", PLAN_VERSION)),
        )


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def window(cx: float, cy: float, size: float) -> Window:
    """A window centred at ``(cx, cy)``, clamped into the frame.

    >>> window(0.5, 0.5, 1.0)
    Window(x=0.0, y=0.0, size=1.0)
    >>> window(0.9, 0.5, 0.5)
    Window(x=0.5, y=0.25, size=0.5)
    """
    size = min(1.0, max(0.05, size))
    x = min(1.0 - size, max(0.0, cx - size / 2))
    y = min(1.0 - size, max(0.0, cy - size / 2))
    return Window(round(x, 6), round(y, 6), round(size, 6))


def tile_path(
    variant: str,
    motion: str,
    length_s: float,
    *,
    amplitude: float = 0.08,
    punch_s: float = 0.25,
) -> tuple[Keyframe, ...]:
    """The window path for one still: piecewise linear, slot-relative seconds.

    >>> tile_path('full', 'none', 2.0)
    (Keyframe(t=0.0, window=Window(x=0.0, y=0.0, size=1.0)),)
    >>> [(k.t, k.window.size) for k in tile_path('full', 'zoom_in', 2.0, amplitude=0.1)]
    [(0.0, 1.0), (2.0, 0.9)]
    >>> [(k.t, k.window.size) for k in tile_path('centre', 'punch', 2.0, amplitude=0.1, punch_s=0.25)]
    [(0.0, 0.63), (0.25, 0.7), (2.0, 0.7)]
    """
    cx, cy, size = CROP_VARIANTS.get(variant, CROP_VARIANTS["full"])
    length_s = max(0.0, float(length_s))
    amp = max(0.0, float(amplitude))
    if motion == "zoom_in":
        return (
            Keyframe(0.0, window(cx, cy, size)),
            Keyframe(length_s, window(cx, cy, size * (1 - amp))),
        )
    if motion == "zoom_out":
        return (
            Keyframe(0.0, window(cx, cy, size * (1 - amp))),
            Keyframe(length_s, window(cx, cy, size)),
        )
    if motion in {"pan_left", "pan_right"}:
        s = min(size, 1.0 - amp)  # a full frame cannot pan; zoom in just enough
        sign = -1.0 if motion == "pan_left" else 1.0
        return (
            Keyframe(0.0, window(cx - sign * amp / 2, cy, s)),
            Keyframe(length_s, window(cx + sign * amp / 2, cy, s)),
        )
    if motion == "punch":
        rest = window(cx, cy, size)
        hit = window(cx, cy, size * (1 - amp))
        settle = min(max(0.0, punch_s), length_s)
        if settle <= 0.0 or settle >= length_s:
            return (Keyframe(0.0, hit), Keyframe(length_s, rest))
        return (Keyframe(0.0, hit), Keyframe(settle, rest), Keyframe(length_s, rest))
    return (Keyframe(0.0, window(cx, cy, size)),)


def window_at(path: Sequence[Keyframe], t: float) -> Window:
    """The window at slot-relative time ``t`` — clamped at both ends.

    >>> p = tile_path('full', 'zoom_in', 2.0, amplitude=0.2)
    >>> window_at(p, 1.0).size, window_at(p, -5).size, window_at(p, 9).size
    (0.9, 1.0, 0.8)
    """
    if not path:
        return Window(0.0, 0.0, 1.0)
    if t <= path[0].t:
        return path[0].window
    for a, b in zip(path, path[1:]):
        if t <= b.t:
            span = b.t - a.t
            p = 0.0 if span <= 0 else (t - a.t) / span
            wa, wb = a.window, b.window
            return Window(
                round(wa.x + (wb.x - wa.x) * p, 6),
                round(wa.y + (wb.y - wa.y) * p, 6),
                round(wa.size + (wb.size - wa.size) * p, 6),
            )
    return path[-1].window


# --------------------------------------------------------------------------
# beat walking
# --------------------------------------------------------------------------


def _beats_per_cut(
    label: str, base: float, *, feel: str, minimum: float, beat_s: float
) -> float:
    """The archetype's verse pacing, scaled by the section and the cut feel,
    floored at the archetype's minimum and capped at :data:`MAX_HOLD_S`.

    >>> _beats_per_cut('chorus', 4, feel='steady', minimum=1, beat_s=0.5)
    2.0
    >>> _beats_per_cut('intro', 16, feel='slow', minimum=4, beat_s=0.5)   # 64 beats -> 10 s cap
    20.0
    """
    factor = _SECTION_DENSITY.get(label, 1.0) * spec_mod.CUT_FEEL_FACTORS.get(feel, 1.0)
    cap = MAX_HOLD_S / max(beat_s, 1e-6)
    return max(minimum, min(base * factor, cap))


def cut_times(
    section: Section, analysis: Analysis, beats_per_cut: float
) -> list[float]:
    """Slot boundaries inside ``section``: ``[start, cut, cut, ..., end]``.

    Cuts are beats of the measured grid every ``round(beats_per_cut)`` beats
    (anchored on the section's first downbeat when that keeps them on the bar),
    or, below one beat, evenly spaced subdivisions between consecutive beats.
    A leading or trailing slot shorter than half the cut interval is folded into
    its neighbour — a half-beat pickup is not a slot — and a cut within
    :data:`MIN_SLOT_S` of a boundary is dropped.

    >>> from muvid.montage.analysis import Analysis, Section
    >>> a = Analysis(duration=8.0, tempo_bpm=120.0,
    ...              beats=tuple(i * 0.5 for i in range(16)),
    ...              downbeats=tuple(i * 2.0 for i in range(4)))
    >>> cut_times(Section(label='verse', start=0.0, end=8.0), a, 4)
    [0.0, 2.0, 4.0, 6.0, 8.0]
    >>> cut_times(Section(label='chorus', start=1.0, end=4.0), a, 2)
    [1.0, 2.0, 3.0, 4.0]
    >>> cut_times(Section(label='chorus', start=0.0, end=2.0), a, 0.5)
    [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
    >>> cut_times(Section(label='verse', start=1.5, end=6.8), a, 4)   # pickup + tail folded
    [1.5, 4.0, 6.8]
    """
    start, end = section.start, section.end
    beats = [t for t in analysis.beats]
    inside = [i for i, t in enumerate(beats) if start <= t < end]
    if not inside:
        return [start, end]
    k0 = inside[0]
    bpb = max(1, analysis.beats_per_bar)
    cuts: list[float] = []
    if beats_per_cut >= 1:
        step = max(1, int(round(beats_per_cut)))
        if bpb % step == 0 or step % bpb == 0:
            for i in inside:
                if beats[i] in analysis.downbeats:
                    k0 = i
                    break
        cuts = beats[k0::step]
        interval_s = step * analysis.beat_s
    else:
        sub = max(1, int(round(1 / beats_per_cut)))
        for i in range(k0, len(beats)):
            b0 = beats[i]
            b1 = beats[i + 1] if i + 1 < len(beats) else b0 + analysis.beat_s
            cuts += [b0 + j * (b1 - b0) / sub for j in range(sub)]
        interval_s = analysis.beat_s / sub
    cuts = [c for c in cuts if start + MIN_SLOT_S < c < end - MIN_SLOT_S]
    if cuts and cuts[0] - start < FOLD_FRACTION * interval_s:
        cuts = cuts[1:]
    if cuts and end - cuts[-1] < FOLD_FRACTION * interval_s:
        cuts = cuts[:-1]
    bounds = [start] + [round(c, 6) for c in cuts] + [end]
    return _split_long(bounds, beats, MAX_HOLD_S)


#: A leading/trailing slot shorter than this fraction of the cut interval is
#: folded into its neighbour. Below one half so a tail of exactly half an
#: interval (common: a 2-bar tail on 4-bar cuts) keeps its own cut.
FOLD_FRACTION = 0.4


def _split_long(
    bounds: list[float], beats: Sequence[float], max_hold: float
) -> list[float]:
    """Split any span longer than ``max_hold`` at the beat nearest its middle.

    Folding and capping are both applied per section, and a section a little
    longer than one cut interval can come out as a single hold that breaks
    :data:`MAX_HOLD_S`; this restores the bound on the grid, recursively.

    >>> _split_long([0.0, 24.0], [i * 0.5 for i in range(48)], 10.0)
    [0.0, 6.0, 12.0, 18.0, 24.0]
    >>> _split_long([0.0, 24.0], [], 10.0)
    [0.0, 24.0]
    """
    out = [bounds[0]]
    for a, b in zip(bounds, bounds[1:]):
        out += _split_span(a, b, beats, max_hold)[1:]
    return out


def _split_span(
    a: float, b: float, beats: Sequence[float], max_hold: float
) -> list[float]:
    if b - a <= max_hold + 1e-6:
        return [a, b]
    inside = [t for t in beats if a + MIN_SLOT_S < t < b - MIN_SLOT_S]
    if not inside:
        return [a, b]
    mid = (a + b) / 2
    cut = min(inside, key=lambda t: (abs(t - mid), t))
    return _split_span(a, cut, beats, max_hold)[:-1] + _split_span(
        cut, b, beats, max_hold
    )


def _spans(bounds: Sequence[float]) -> list[tuple[float, float]]:
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]


# --------------------------------------------------------------------------
# archetypes — a registry, like muvid.lyricvid.scene
# --------------------------------------------------------------------------

ArchetypeFn = Callable[[Section, PlanContext, Mapping[str, Any]], list[SlotDraft]]
ARCHETYPE_FNS: dict[str, ArchetypeFn] = {}


def register_archetype(name: str) -> Callable[[ArchetypeFn], ArchetypeFn]:
    """Register an archetype planner under ``name``.

    >>> @register_archetype('doctest-demo')
    ... def demo(section, ctx, params): return []
    >>> 'doctest-demo' in ARCHETYPE_FNS
    True
    >>> del ARCHETYPE_FNS['doctest-demo']
    """

    def deco(fn: ArchetypeFn) -> ArchetypeFn:
        ARCHETYPE_FNS[name] = fn
        return fn

    return deco


def _param(params: Mapping[str, Any], archetype: str, key: str) -> float:
    rule = spec_mod.ARCHETYPE_PARAMS[archetype][key]
    v = params.get(key, rule["default"])
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(rule["default"])


@register_archetype("ballad_dissolve")
def ballad_dissolve(
    section: Section, ctx: PlanContext, params: Mapping[str, Any]
) -> list[SlotDraft]:
    """Cut every 2-4 bars on a downbeat, one-beat crossfades, slow Ken Burns."""
    a = ctx.analysis
    bars = _param(params, "ballad_dissolve", "bars_per_cut")
    bpc = _beats_per_cut(
        section.label,
        bars * a.beats_per_bar,
        feel=ctx.direction.cut_feel,
        minimum=a.beats_per_bar,
        beat_s=a.beat_s,
    )
    fade_s = _param(params, "ballad_dissolve", "fade_beats") * a.beat_s
    drift = _param(params, "ballad_dissolve", "drift")
    return [
        SlotDraft(
            start=s,
            end=e,
            section=section.label,
            archetype="ballad_dissolve",
            motions=_BALLAD_CYCLE,
            amplitude=drift,
            transition="fade" if fade_s > 0 else "cut",
            transition_s=fade_s,
        )
        for s, e in _spans(cut_times(section, a, bpc))
    ]


@register_archetype("beat_cut")
def beat_cut(
    section: Section, ctx: PlanContext, params: Mapping[str, Any]
) -> list[SlotDraft]:
    """Hard cuts every bar in a verse, every half-bar in a chorus, punch on the cut."""
    a = ctx.analysis
    bpc = _beats_per_cut(
        section.label,
        _param(params, "beat_cut", "beats_per_cut"),
        feel=ctx.direction.cut_feel,
        minimum=1.0,
        beat_s=a.beat_s,
    )
    punch = _param(params, "beat_cut", "punch")
    return [
        SlotDraft(
            start=s,
            end=e,
            section=section.label,
            archetype="beat_cut",
            motions=("punch",) if punch > 0 else ("none",),
            amplitude=punch,
        )
        for s, e in _spans(cut_times(section, a, bpc))
    ]


@register_archetype("grid")
def grid(
    section: Section, ctx: PlanContext, params: Mapping[str, Any]
) -> list[SlotDraft]:
    """A 2x2 grid; one tile swaps per beat.

    All four swap on a section's first slot — a section change is the one
    place a full reset reads as an accent rather than a glitch.
    """
    a = ctx.analysis
    bpc = _beats_per_cut(
        section.label,
        _param(params, "grid", "beats_per_swap"),
        feel=ctx.direction.cut_feel,
        minimum=1.0,
        beat_s=a.beat_s,
    )
    drafts = []
    for k, (s, e) in enumerate(_spans(cut_times(section, a, bpc))):
        fresh = (
            tuple(range(GRID_REGIONS))
            if k == 0
            else (_GRID_SWAP_ORDER[(k - 1) % GRID_REGIONS],)
        )
        drafts.append(
            SlotDraft(
                start=s,
                end=e,
                section=section.label,
                archetype="grid",
                regions=GRID_REGIONS,
                fresh=fresh,
                motions=("none",),
            )
        )
    return drafts


@register_archetype("stop_motion")
def stop_motion(
    section: Section, ctx: PlanContext, params: Mapping[str, Any]
) -> list[SlotDraft]:
    """Stills held for a beat subdivision, no motion."""
    a = ctx.analysis
    sub = max(1.0, _param(params, "stop_motion", "subdivision"))
    bpc = _beats_per_cut(
        section.label,
        1.0 / sub,
        feel=ctx.direction.cut_feel,
        minimum=0.25,
        beat_s=a.beat_s,
    )
    return [
        SlotDraft(
            start=s,
            end=e,
            section=section.label,
            archetype="stop_motion",
            motions=("none",),
        )
        for s, e in _spans(cut_times(section, a, bpc))
    ]


# --------------------------------------------------------------------------
# the reuse policy
# --------------------------------------------------------------------------


def _stride_pick(candidates: Sequence[int], ordinal: int) -> int:
    """The golden-ratio pick among tied candidates — a permutation, not a loop."""
    j = int(((ordinal * GOLDEN) % 1.0) * len(candidates))
    return candidates[min(j, len(candidates) - 1)]


def assign_media(
    drafts: Sequence[SlotDraft],
    media: Sequence[Media],
    reuse: spec_mod.Reuse,
    *,
    finale_start: float | None = None,
) -> tuple[list[dict[int, tuple[int, int]]], dict[str, Any]]:
    """Fill every region of every draft with ``(media_index, use_ordinal)``.

    Returns one ``{region: (media, use)}`` per draft, plus the policy's own
    account of what it did (reserved images, uses, the smallest gap it
    actually produced), which goes into the plan for inspection.

    >>> from muvid.montage.analysis import Media
    >>> pool = [Media(index=i, path=f'{i}.jpg', kind='photo', width=100, height=100,
    ...               strength=float(i)) for i in range(3)]
    >>> drafts = [SlotDraft(start=i, end=i + 1, section='verse', archetype='beat_cut')
    ...           for i in range(7)]
    >>> picks, stats = assign_media(drafts, pool, spec_mod.Reuse(min_gap=6))
    >>> [p[0][0] for p in picks]      # gap shrinks to pool-1 = 2: a forced cycle
    [0, 2, 1, 0, 2, 1, 0]
    >>> stats['min_gap'], stats['min_observed_gap']
    (2, 3)
    """
    cover = next((m.index for m in media if m.kind == "cover"), None)
    pool = [m for m in media if m.kind != "cover"]
    pool_idx = [m.index for m in pool]
    if not pool_idx:
        if cover is None:
            raise ValueError("the media pool is empty")
        pool_idx = [cover]
        cover = None
    gap = max(0, min(int(reuse.min_gap), len(pool_idx) - 1))

    n_finale = sum(
        len(d.fresh)
        for d in drafts
        if finale_start is not None and d.start >= finale_start
    )
    n_reserve = 0
    if reuse.reserve_for_finale and finale_start is not None and len(pool_idx) >= 2:
        n_reserve = min(len(pool_idx) // FINALE_RESERVE_DIVISOR, n_finale)
        if len(pool_idx) - n_reserve < 2:
            n_reserve = 0
    by_strength = sorted(pool, key=lambda m: (-m.strength, m.index))
    reserved = [m.index for m in by_strength[:n_reserve]]
    reserved_queue = list(reserved)

    uses: dict[int, int] = {i: 0 for i in pool_idx}
    last_at: dict[int, int] = {}
    recent: list[int] = []
    shown: dict[int, tuple[int, int]] = {}
    out: list[dict[int, tuple[int, int]]] = []
    ordinal = 0
    min_observed_gap: int | None = None

    def take(m: int) -> tuple[int, int]:
        nonlocal ordinal, min_observed_gap
        use = uses.get(m, 0)
        uses[m] = use + 1
        if m in last_at:
            g = ordinal - last_at[m]
            min_observed_gap = (
                g if min_observed_gap is None else min(min_observed_gap, g)
            )
        last_at[m] = ordinal
        ordinal += 1
        recent.append(m)
        if gap:
            del recent[:-gap]
        else:
            recent.clear()
        return m, use

    def pick(hard: set[int], soft: set[int], in_finale: bool) -> tuple[int, int]:
        """``hard``: what this tile must not be (its own previous media, and
        what this slot already assigned). ``soft``: what the other tiles show.
        Relaxed in tiers when the pool is too small: the gap goes first, then
        the other tiles' media — a photo moving to another tile is a lesser
        evil than a tile that does not change, or two tiles alike."""
        if in_finale and reserved_queue:
            m = reserved_queue.pop(0)
            if m not in hard and m not in soft:
                return take(m)
        candidates = [m for m in pool_idx if in_finale or m not in reserved]
        # The gap is against the candidates actually in play: with the reserve
        # held back a smaller pool is being cycled, and a gap sized for the
        # whole pool would leave nothing available and fall through to "anything".
        g = min(gap, len(candidates) - 1)
        blocked = set(recent[-g:]) if g > 0 else set()
        avail: list[int] = []
        for exclude in (
            hard | soft | blocked,
            hard | soft,
            hard | blocked,
            hard,
            set(),
        ):
            avail = [m for m in candidates if m not in exclude]
            if avail:
                break
        least = min(uses[m] for m in avail)
        tied = [m for m in avail if uses[m] == least]
        return take(_stride_pick(tied, ordinal))

    last = len(drafts) - 1
    for k, d in enumerate(drafts):
        in_finale = finale_start is not None and d.start >= finale_start
        if d.regions != len(shown):
            shown = {}
        fresh = set(d.fresh) | {r for r in range(d.regions) if r not in shown}
        assigned: dict[int, tuple[int, int]] = {}
        for r in range(d.regions):
            if r in fresh:
                pin = cover is not None and (
                    (k == 0 and r == min(fresh)) or (k == last and r == min(fresh))
                )
                if pin:
                    assigned[r] = (cover, 0 if k == 0 else 1)
                    continue
                # The replaced tile's own media is a hard exclusion — a fresh
                # tile that re-picks it is a swap nobody sees — as is anything
                # this slot has already placed; the other tiles' media is soft.
                hard = {v[0] for v in assigned.values()}
                if r in shown:
                    hard.add(shown[r][0])
                soft = {v[0] for rr, v in shown.items() if rr != r}
                assigned[r] = pick(hard, soft, in_finale)
            else:
                assigned[r] = shown[r]
        shown = dict(assigned)
        out.append(assigned)

    stats = {
        "pool": len(pool_idx),
        "cover": cover,
        "min_gap": gap,
        "reserved_for_finale": reserved,
        "finale_start": None if finale_start is None else round(finale_start, 4),
        "uses": {str(m): uses[m] for m in pool_idx},
        "n_assignments": ordinal,
        "min_observed_gap": min_observed_gap,
    }
    return out, stats


# --------------------------------------------------------------------------
# the planner
# --------------------------------------------------------------------------


def _scene_for(
    section: Section, scenes: Sequence[spec_mod.Scene]
) -> tuple[spec_mod.Scene, bool]:
    """The scene that claims ``section``: a named one wins, else the first ``*``,
    else the first scene (reported as uncovered)."""
    for sc in scenes:
        if section.label in sc.applies_to:
            return sc, True
    for sc in scenes:
        if "*" in sc.applies_to:
            return sc, True
    return scenes[0], False


def _merge_tiny(drafts: list[SlotDraft]) -> list[SlotDraft]:
    """Absorb slots shorter than MIN_SLOT_S into a neighbour, keeping coverage exact."""
    from dataclasses import replace

    out: list[SlotDraft] = []
    for d in drafts:
        if d.end - d.start < MIN_SLOT_S and out:
            out[-1] = replace(out[-1], end=d.end)
        elif d.end - d.start < MIN_SLOT_S and not out:
            out.append(d)  # merged into the next one below
        else:
            if out and out[-1].end - out[-1].start < MIN_SLOT_S:
                out[-1] = replace(d, start=out[-1].start)
            else:
                out.append(d)
    return out


def finale_of(sections: Sequence[Section]) -> Section | None:
    """The last chorus, else the last section, else None.

    >>> finale_of([Section(label='verse', start=0, end=4), Section(label='chorus', start=4, end=8),
    ...            Section(label='outro', start=8, end=10)]).label
    'chorus'
    """
    for s in reversed(list(sections)):
        if s.label == "chorus":
            return s
    return list(sections)[-1] if sections else None


def plan_montage(
    analysis: Analysis, media: Sequence[Media], treatment: spec_mod.TreatmentSpec
) -> Plan:
    """Pool + measurements + treatment -> a :class:`Plan`. Pure and deterministic."""
    if not analysis.sections:
        raise ValueError("analysis has no sections")
    if not media:
        raise ValueError("no media to plan with")
    ctx = PlanContext(analysis=analysis, direction=treatment.direction)
    notes: list[str] = list(analysis.notes)
    drafts: list[SlotDraft] = []
    uncovered: list[str] = []
    for section in analysis.sections:
        scene, covered = _scene_for(section, treatment.scenes)
        if not covered:
            uncovered.append(section.label)
        fn = ARCHETYPE_FNS.get(scene.archetype)
        if fn is None:
            raise KeyError(
                f"archetype {scene.archetype!r} has no planner; known: {sorted(ARCHETYPE_FNS)}"
            )
        drafts += fn(section, ctx, dict(scene.params))
    if uncovered:
        notes.append(f"sections with no scene fell back to the first: {uncovered}")
    drafts = _merge_tiny(drafts)
    if not drafts:
        raise ValueError("the plan has no slots — is the song empty?")

    finale = finale_of(analysis.sections)
    picks, stats = assign_media(
        drafts,
        media,
        treatment.direction.reuse,
        finale_start=finale.start if finale else None,
    )
    by_index = {m.index: m for m in media}
    punch_s = PUNCH_BEATS * analysis.beat_s
    slots: list[Slot] = []
    prev_len: float | None = None
    for i, (d, assigned) in enumerate(zip(drafts, picks)):
        length = d.end - d.start
        tiles = []
        for region in range(d.regions):
            m_index, use = assigned[region]
            m = by_index[m_index]
            variant = _VARIANTS[use % len(_VARIANTS)]
            motion = d.motions[i % len(d.motions)] if m.kind != "clip" else "none"
            if m.kind == "clip":
                variant = "full"
                room = max(0.0, (m.duration or 0.0) - length)
                source_in = round(((use * GOLDEN) % 1.0) * room, 4)
            else:
                source_in = 0.0
            tiles.append(
                Tile(
                    region=region,
                    media=m_index,
                    motion=motion,
                    variant=variant,
                    path=tile_path(
                        variant, motion, length, amplitude=d.amplitude, punch_s=punch_s
                    ),
                    source_in=source_in,
                    use=use,
                )
            )
        transition, t_s = d.transition, d.transition_s
        if i == 0 or transition == "cut" or t_s <= 0:
            transition, t_s = "cut", 0.0
        else:
            t_s = min(t_s, MAX_TRANSITION_FRACTION * min(length, prev_len or length))
        slots.append(
            Slot(
                index=i,
                start=d.start,
                end=d.end,
                section=d.section,
                archetype=d.archetype,
                regions=d.regions,
                tiles=tuple(tiles),
                transition=transition,
                transition_s=round(t_s, 6),
            )
        )
        prev_len = length

    stats["finale"] = (
        None
        if finale is None
        else {
            "label": finale.label,
            "start": round(finale.start, 4),
            "end": round(finale.end, 4),
        }
    )
    stats["n_slots"] = len(slots)
    stats["slots_per_section"] = {
        s.label: sum(1 for sl in slots if sl.section == s.label)
        for s in analysis.sections
    }
    return Plan(
        duration=analysis.duration,
        tempo_bpm=analysis.tempo_bpm,
        beats_per_bar=analysis.beats_per_bar,
        beat_source=analysis.beat_source,
        section_source=analysis.section_source,
        sections=tuple(analysis.sections),
        media=tuple(media),
        slots=tuple(slots),
        reuse=stats,
        treatment=treatment.to_dict(),
        notes=tuple(notes),
    )
