"""The scene compiler: events + sections -> a list of drawable OBJECTS.

An :class:`Obj` is one thing on screen with a life: born at ``t_born``, dead at
``t_die``, a shape ``kind``, a position, a size, a colour and a ``motion`` that
says how it moves in between. Every number is computed here, in Python, from
the event list and the treatment; the renderer only draws.

Each **archetype** is a function ``(events, section, ...) -> [Obj]`` in a
registry (``ARCHETYPE_FNS``), the same seam :mod:`muvid.lyricvid.scene` uses:
adding a look is adding a function and a vocabulary entry. The four shipped
ones are the closed set in :data:`muvid.choreo.spec.ARCHETYPES`.

**Determinism is a design rule, not a hope.** The only "randomness" is
:func:`unit_hash`, a closed-form integer hash of ``(seed, index, ...)`` — so
the same audio and seed produce the same objects on any machine, a re-render
after a palette change moves nothing, and a test can pin a coordinate.

Positions are normalised (``x`` in fractions of width, ``y`` of height, origin
top-left) and sizes are fractions of canvas **height**, so one scene renders at
any resolution. Velocities are in the same fractions per second.
"""

from __future__ import annotations

import colorsys
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Sequence

from muvid.choreo import spec as spec_mod
from muvid.choreo.analysis import Analysis, Event, Section

__all__ = [
    "ARCHETYPE_FNS",
    "Backdrop",
    "Canvas",
    "ChoreoScene",
    "KINDS",
    "MOTIONS",
    "Obj",
    "compile_scene",
    "register_archetype",
    "unit_hash",
]

#: Shape classes the renderer knows how to draw.
KINDS = ("circle", "rect", "triangle", "line", "diamond", "ring")
#: How an object moves between birth and death.
MOTIONS = ("hold", "scroll", "pulse", "drift", "flicker")

#: Fischinger arrangements, cycled by section index.
ARRANGEMENTS = ("grid", "ring", "rows", "diagonal")

#: Bounds on what one compile may produce, so a dense treatment on a long
#: song cannot make the renderer's per-frame work unbounded.
MAX_OBJECTS = 200_000


# --------------------------------------------------------------------------
# the one source of variation
# --------------------------------------------------------------------------

_MASK = (1 << 64) - 1


def unit_hash(seed: int, *keys: int) -> float:
    """A float in ``[0, 1)`` that is a pure function of its integer arguments.

    splitmix64-style mixing; no state, no platform dependence, so it is the
    only "random" this package allows itself.

    >>> unit_hash(0, 1) == unit_hash(0, 1), 0.0 <= unit_hash(3, 4, 5) < 1.0
    (True, True)
    >>> unit_hash(0, 1) != unit_hash(1, 1)
    True
    """
    h = (int(seed) * 0x9E3779B97F4A7C15 + 0x632BE59BD9B4E019) & _MASK
    for k in keys:
        h = ((h ^ (int(k) & _MASK)) * 0xBF58476D1CE4E5B9) & _MASK
        h ^= h >> 31
    h = (h * 0x94D049BB133111EB) & _MASK
    h ^= h >> 29
    return (h >> 11) / float(1 << 53)


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Canvas:
    """Output geometry."""

    width: int = 1920
    height: int = 1080
    fps: int = 30

    @property
    def aspect(self) -> float:
        return self.width / self.height


@dataclass(frozen=True, slots=True, kw_only=True)
class Obj:
    """One drawable thing with a life.

    :param x, y: centre, normalised (x of width, y of height), at birth.
    :param size: the shape's main dimension as a fraction of canvas height —
        diameter for ``circle``/``ring``/``diamond``, height for ``rect``/
        ``triangle``, length for ``line``.
    :param aspect: width/height for ``rect``/``triangle``; length/thickness
        for ``line``.
    :param angle: degrees, for ``line``.
    :param vx, vy: velocity (fractions per second) for ``scroll``/``drift``.
    :param attack_s, release_s: alpha ramps at the start and end of life.
    :param seed: per-object key for ``flicker``'s per-frame jitter.
    """

    t_born: float
    t_die: float
    kind: str
    x: float
    y: float
    size: float
    colour: str
    motion: str = "hold"
    band: str = ""
    strength: float = 1.0
    aspect: float = 1.0
    angle: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    attack_s: float = 0.0
    release_s: float = 0.0
    layer: int = 0
    seed: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class Backdrop:
    """What is behind the objects during ``[start, end)``."""

    start: float
    end: float
    kind: str  # solid | gradient | vignette
    top: str
    bottom: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ChoreoScene:
    """Everything the renderer needs, and nothing it has to interpret."""

    canvas: Canvas
    duration: float
    backdrops: tuple[Backdrop, ...]
    objects: tuple[Obj, ...]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

ArchetypeFn = Callable[..., list[Obj]]

ARCHETYPE_FNS: dict[str, ArchetypeFn] = {}


def register_archetype(name: str) -> Callable[[ArchetypeFn], ArchetypeFn]:
    """Register an archetype under ``name``.

    >>> @register_archetype('doctest-demo')
    ... def _demo(events, **kw): return []
    >>> 'doctest-demo' in ARCHETYPE_FNS
    True
    >>> del ARCHETYPE_FNS['doctest-demo']
    """

    def deco(fn: ArchetypeFn) -> ArchetypeFn:
        ARCHETYPE_FNS[name] = fn
        return fn

    return deco


def _param(params: Mapping[str, Any], archetype: str, key: str) -> float:
    """An archetype parameter, defaulted and clamped from :data:`spec.ARCHETYPE_PARAMS`."""
    default, lo, hi, _ = spec_mod.ARCHETYPE_PARAMS[archetype][key]
    try:
        v = float(params.get(key, default))
    except (TypeError, ValueError):
        v = float(default)
    return min(hi, max(lo, v))


def _band_colour(direction: spec_mod.Direction, band: str) -> str:
    return getattr(direction.palette, band, direction.palette.fg)


def _kept(events: Sequence[Event], direction: spec_mod.Direction) -> list[Event]:
    gate = spec_mod.DENSITY_GATE.get(direction.density, 0.15)
    return [e for e in events if e.strength >= gate]


_BAND_ORDER = {"low": 0, "mid": 1, "high": 2}


# --------------------------------------------------------------------------
# archetypes
# --------------------------------------------------------------------------


@register_archetype("fischinger")
def fischinger(
    events: Sequence[Event],
    *,
    section: Section,
    analysis: Analysis,
    direction: spec_mod.Direction,
    params: Mapping[str, Any],
    canvas: Canvas,
    seed: int,
) -> list[Obj]:
    """Shapes ignite per onset on a grid; band -> shape, strength -> size, section -> arrangement.

    ``low`` events are discs on the lower rows, ``mid`` squares in the middle,
    ``high`` triangles on top; each holds for ``hold_beats`` and pulses down.
    The arrangement cycles with the section index so a chorus is visibly a
    different picture from a verse.
    """
    cols = int(_param(params, "fischinger", "columns"))
    rows = int(_param(params, "fischinger", "rows"))
    hold = _param(params, "fischinger", "hold_beats") * analysis.tempo.period
    arrangement = ARRANGEMENTS[section.index % len(ARRANGEMENTS)]
    cell_w = canvas.aspect / cols  # in height units
    cell_h = 0.9 / rows
    base = min(cell_w, cell_h) * 0.85
    shape = {"low": "circle", "mid": "rect", "high": "triangle"}
    out: list[Obj] = []
    for i, e in enumerate(_kept(events, direction)):
        band_i = _BAND_ORDER.get(e.band, 1)
        j = int(unit_hash(seed, section.index, i, 1) * cols)
        # a band owns a third of the rows: low at the bottom, high at the top
        band_rows = max(1, rows // 3) if rows >= 3 else 1
        row0 = (2 - band_i) * (rows // 3) if rows >= 3 else 0
        r = row0 + int(unit_hash(seed, section.index, i, 2) * band_rows)
        r = min(rows - 1, r)
        if arrangement == "grid":
            x = (j + 0.5) / cols
            y = 0.05 + (r + 0.5) * cell_h
        elif arrangement == "ring":
            ang = 2 * math.pi * (i % cols) / cols
            rad = (0.18 + 0.1 * band_i)
            x = 0.5 + rad * math.cos(ang) / canvas.aspect
            y = 0.5 + rad * math.sin(ang)
        elif arrangement == "rows":
            x = ((i % cols) + 0.5) / cols
            y = 0.05 + (row0 + band_rows / 2) * cell_h
        else:  # diagonal
            x = (j + 0.5) / cols
            y = 0.1 + 0.8 * x if band_i != 2 else 0.9 - 0.8 * x
        size = base * (0.35 + 0.65 * e.strength)
        life = hold * (0.5 + 0.5 * e.strength)
        out.append(
            Obj(
                t_born=e.t, t_die=e.t + life, kind=shape.get(e.band, "diamond"),
                x=x, y=y, size=size, colour=_band_colour(direction, e.band),
                motion="pulse", band=e.band, strength=e.strength,
                release_s=0.35 * life, layer=band_i,
                seed=int(unit_hash(seed, i, 3) * 1e9),
            )
        )
    return out


@register_archetype("star_guitar")
def star_guitar(
    events: Sequence[Event],
    *,
    section: Section,
    analysis: Analysis,
    direction: spec_mod.Direction,
    params: Mapping[str, Any],
    canvas: Canvas,
    seed: int,
) -> list[Obj]:
    """A side-scrolling landscape whose object spacing IS the rhythm.

    Every object enters at the right edge on its event and scrolls left at
    ``speed`` canvas-widths per second, so two objects born a beat apart sit
    exactly ``speed * period`` widths apart on screen. ``low`` -> poles,
    ``mid`` -> buildings, ``high`` -> wires. The ground is one object per
    section; the sky is the section's backdrop (see :func:`_sky`).
    """
    speed = _param(params, "star_guitar", "speed")
    horizon = _param(params, "star_guitar", "horizon")
    pal = direction.palette
    ground_h = 1.0 - horizon
    out: list[Obj] = [
        Obj(  # the ground: a rect covering everything under the horizon
            t_born=section.start, t_die=section.end, kind="rect",
            x=0.5, y=horizon + ground_h / 2, size=ground_h,
            aspect=canvas.aspect / ground_h, colour=_darken(pal.bg2, 0.55),
            motion="hold", layer=-2,
        ),
        Obj(  # the horizon line
            t_born=section.start, t_die=section.end, kind="line",
            x=0.5, y=horizon, size=canvas.aspect, aspect=canvas.aspect / 0.004,
            colour=pal.fg, motion="hold", layer=-1,
        ),
    ]
    for i, e in enumerate(_kept(events, direction)):
        h1 = unit_hash(seed, section.index, i, 1)
        if e.band == "low":
            height = horizon * (0.25 + 0.5 * e.strength)
            width_h = 0.012 + 0.01 * e.strength  # in height units
            obj = dict(kind="rect", y=horizon - height / 2, size=height,
                       aspect=width_h / height, layer=2)
        elif e.band == "mid":
            height = horizon * (0.12 + 0.38 * e.strength)
            width_h = 0.06 + 0.14 * h1
            obj = dict(kind="rect", y=horizon - height / 2, size=height,
                       aspect=width_h / height, layer=1)
        else:
            length = 0.10 + 0.18 * e.strength
            obj = dict(kind="line", y=horizon - 0.18 - 0.35 * h1 * horizon,
                       size=length, aspect=length / 0.004, angle=(h1 - 0.5) * 10.0,
                       layer=3)
        width_frac = (obj["size"] * obj["aspect"]) / canvas.aspect  # in width fractions
        x0 = 1.0 + width_frac / 2
        out.append(
            Obj(
                t_born=e.t, t_die=e.t + (1.0 + width_frac) / speed,
                x=x0, colour=_band_colour(direction, e.band), motion="scroll",
                vx=-speed, band=e.band, strength=e.strength, **obj,
            )
        )
    return out


@register_archetype("mclaren")
def mclaren(
    events: Sequence[Event],
    *,
    section: Section,
    analysis: Analysis,
    direction: spec_mod.Direction,
    params: Mapping[str, Any],
    canvas: Canvas,
    seed: int,
) -> list[Obj]:
    """Scratches on film: one mark per onset, jittered, gone within a fraction of a beat.

    ``low`` -> a thick short dash, ``mid`` -> a medium stroke at any angle,
    ``high`` -> a long thin near-vertical scratch. All in ``palette.fg``.
    """
    life = _param(params, "mclaren", "life_beats") * analysis.tempo.period
    out: list[Obj] = []
    for i, e in enumerate(_kept(events, direction)):
        h1 = unit_hash(seed, section.index, i, 1)
        h2 = unit_hash(seed, section.index, i, 2)
        h3 = unit_hash(seed, section.index, i, 3)
        if e.band == "low":
            length, thick, angle = 0.08 + 0.12 * e.strength, 0.02 + 0.02 * e.strength, h3 * 180
        elif e.band == "mid":
            length, thick, angle = 0.12 + 0.22 * e.strength, 0.008, h3 * 180
        else:
            length, thick, angle = 0.25 + 0.4 * e.strength, 0.003, 90 + (h3 - 0.5) * 30
        out.append(
            Obj(
                t_born=e.t, t_die=e.t + life * (0.5 + 0.5 * e.strength), kind="line",
                x=0.08 + 0.84 * h1, y=0.08 + 0.84 * h2, size=length, aspect=length / thick,
                angle=angle, colour=direction.palette.fg, motion="flicker",
                band=e.band, strength=e.strength, seed=int(h1 * 1e9),
            )
        )
    return out


@register_archetype("swarm")
def swarm(
    events: Sequence[Event],
    *,
    section: Section,
    analysis: Analysis,
    direction: spec_mod.Direction,
    params: Mapping[str, Any],
    canvas: Canvas,
    seed: int,
) -> list[Obj]:
    """Particles whose count and speed follow band energy, with persistence.

    Each event throws ``particles * density * strength`` discs from a band
    origin (``low`` from the bottom upward, ``high`` from the top downward,
    ``mid`` from the centre outward); each decelerates to rest over its life.
    """
    per_event = _param(params, "swarm", "particles") * spec_mod.DENSITY_FACTOR.get(direction.density, 1.0)
    life_s = _param(params, "swarm", "life_s")
    origins = {"low": (0.5, 0.88, -math.pi, 0.0), "high": (0.5, 0.12, 0.0, math.pi),
               "mid": (0.5, 0.5, 0.0, 2 * math.pi)}
    out: list[Obj] = []
    for i, e in enumerate(_kept(events, direction)):
        ox, oy, a0, a1 = origins.get(e.band, origins["mid"])
        n = max(1, int(round(per_event * (0.3 + 0.7 * e.strength))))
        for j in range(n):
            ang = a0 + (a1 - a0) * unit_hash(seed, section.index, i, j, 1)
            spd = (0.12 + 0.5 * e.strength) * (0.5 + unit_hash(seed, section.index, i, j, 2))
            hs = unit_hash(seed, section.index, i, j, 3)
            life = life_s * (0.6 + 0.4 * hs)
            out.append(
                Obj(
                    t_born=e.t, t_die=e.t + life, kind="circle",
                    x=ox + (unit_hash(seed, section.index, i, j, 4) - 0.5) * 0.2,
                    y=oy, size=(0.008 + 0.03 * e.strength) * (0.5 + hs),
                    colour=_band_colour(direction, e.band), motion="drift",
                    vx=spd * math.cos(ang) / canvas.aspect, vy=spd * math.sin(ang),
                    band=e.band, strength=e.strength,
                    attack_s=0.05, release_s=0.5 * life,
                )
            )
    return out


# --------------------------------------------------------------------------
# colours and backdrops
# --------------------------------------------------------------------------


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{int(round(min(1.0, max(0.0, c)) * 255)):02x}" for c in rgb)


def _darken(hex_colour: str, factor: float) -> str:
    return _hex(tuple(c * factor for c in _rgb(hex_colour)))  # type: ignore[arg-type]


def _rotate_hue(hex_colour: str, turns: float, *, sat: float | None = None) -> str:
    """Rotate a colour's hue by ``turns`` (1.0 = full circle).

    >>> _rotate_hue('#ff0000', 1/3)
    '#00ff00'
    """
    h, s, v = colorsys.rgb_to_hsv(*_rgb(hex_colour))
    return _hex(colorsys.hsv_to_rgb((h + turns) % 1.0, s if sat is None else sat, v))


def _sky(direction: spec_mod.Direction, section: Section, n_sections: int) -> Backdrop:
    """Star Guitar's sky: one hue per section, walking round the wheel."""
    turns = section.index / max(1, n_sections)
    top = _rotate_hue(direction.palette.bg2, turns, sat=0.55)
    bottom = _rotate_hue(direction.palette.bg2, turns + 0.08, sat=0.35)
    return Backdrop(start=section.start, end=section.end, kind="gradient", top=top, bottom=bottom)


def _backdrop(direction: spec_mod.Direction, section: Section) -> Backdrop:
    pal = direction.palette
    return Backdrop(start=section.start, end=section.end, kind=direction.background,
                    top=pal.bg, bottom=pal.bg2 if direction.background == "gradient" else pal.bg)


# --------------------------------------------------------------------------
# compile
# --------------------------------------------------------------------------


def _scene_for(section: Section, scenes: Sequence[spec_mod.Scene]) -> tuple[spec_mod.Scene, bool]:
    """The scene that claims ``section``; ``(scene, uncovered)``."""
    for sc in scenes:
        if section.label in sc.applies_to or str(section.index) in sc.applies_to:
            return sc, False
    for sc in scenes:
        if "*" in sc.applies_to:
            return sc, False
    return scenes[0], True


def compile_scene(
    treatment: spec_mod.TreatmentSpec,
    analysis: Analysis,
    *,
    canvas: Canvas | None = None,
    seed: int = 0,
) -> ChoreoScene:
    """Every object and backdrop for the whole song. Deterministic given ``seed``.

    >>> from muvid.choreo.analysis import Analysis, Event, Section, Tempo
    >>> a = Analysis(duration=4.0, tempo=Tempo(bpm=120, beats=(0, .5, 1, 1.5), source='numpy'),
    ...              events=(Event(t=1.0, band='low', strength=0.9),
    ...                      Event(t=2.0, band='high', strength=0.5)),
    ...              sections=(Section(index=0, label='mid', start=0.0, end=4.0, energy_db=-10),))
    >>> s = compile_scene(spec_mod.default_treatment('fischinger'), a, seed=1)
    >>> [(o.t_born, o.kind, o.band) for o in s.objects]
    [(1.0, 'circle', 'low'), (2.0, 'triangle', 'high')]
    >>> compile_scene(spec_mod.default_treatment('fischinger'), a, seed=1) == s
    True
    """
    canvas = canvas or Canvas()
    errors = spec_mod.validate(treatment)
    if errors:
        raise ValueError("treatment is not renderable: " + "; ".join(errors))
    sections = analysis.sections or (
        Section(index=0, label="mid", start=0.0, end=analysis.duration, energy_db=0.0),
    )
    objects: list[Obj] = []
    backdrops: list[Backdrop] = []
    uncovered: list[str] = []
    used: list[str] = []
    for section in sections:
        sc, fell_back = _scene_for(section, treatment.scenes)
        if fell_back:
            uncovered.append(f"{section.index}:{section.label}")
        fn = ARCHETYPE_FNS[sc.archetype]
        used.append(sc.archetype)
        events = analysis.events_in(section.start, section.end)
        objects.extend(
            fn(events, section=section, analysis=analysis, direction=treatment.direction,
               params=sc.params, canvas=canvas, seed=seed)
        )
        backdrops.append(
            _sky(treatment.direction, section, len(sections))
            if sc.archetype == "star_guitar" else _backdrop(treatment.direction, section)
        )
        if len(objects) > MAX_OBJECTS:
            raise ValueError(
                f"treatment produces more than {MAX_OBJECTS} objects; lower the density "
                "or the swarm's `particles`"
            )
    objects.sort(key=lambda o: (o.t_born, o.layer))
    meta: dict[str, Any] = {
        "seed": seed,
        "archetypes": sorted(set(used)),
        "n_objects": len(objects),
        "n_events": len(analysis.events),
        "n_sections": len(sections),
    }
    if uncovered:
        meta["uncovered_sections"] = uncovered
    return ChoreoScene(
        canvas=canvas, duration=analysis.duration,
        backdrops=tuple(backdrops), objects=tuple(objects), meta=meta,
    )
