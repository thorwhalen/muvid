"""The choreo treatment spec — what a director decides, as data; stdlib-only.

The same two-layer split as :mod:`muvid.lyricvid.spec`, for the same reason:

``direction``
    WHAT and WHY — palette, background, density, mood, a rationale. The half a
    model (or a person in a hurry) is genuinely good at.

``scenes``
    HOW — an **archetype** from a closed set, which sections it applies to,
    and a small bag of archetype parameters. Never geometry, never timings:
    every coordinate and every time is computed in Python from the event list.

Closed vocabularies everywhere (archetypes, backgrounds, densities), and a
``repair`` that projects a nearly-right spec onto the valid space rather than
handing it back — a mechanical substitution renders something good now.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Mapping

SPEC_VERSION = "1.0"

#: How events become objects. Each is a function in :mod:`muvid.choreo.scene`.
ARCHETYPES: dict[str, str] = {
    "fischinger": (
        "Geometric shapes ignite per onset on a grid — band picks the shape "
        "class (low: discs, mid: squares, high: triangles), strength the size, "
        "and each section a different arrangement. The Study No. 7 look."
    ),
    "star_guitar": (
        "A side-scrolling landscape: bass onsets are poles, mids are buildings, "
        "highs are wires. Everything scrolls left at one constant speed, so the "
        "spacing of the objects IS the rhythm. Sections change the sky."
    ),
    "mclaren": (
        "White scratches and marks on black, one per onset, jittered, gone within "
        "a fraction of a beat. Hand-scratched film."
    ),
    "swarm": (
        "Particles whose count and speed follow band energy, thrown from a "
        "band-specific origin and persisting as they slow. Continuous, organic."
    ),
}

#: What is behind the objects.
BACKGROUNDS: dict[str, str] = {
    "solid": "One flat colour (palette.bg).",
    "gradient": "Vertical gradient from palette.bg (top) to palette.bg2 (bottom).",
    "vignette": "palette.bg, darkened toward the corners.",
}

#: How many events become objects, and how many objects an event makes.
DENSITIES: dict[str, str] = {
    "sparse": "Only strong onsets mark; few objects at a time.",
    "normal": "Most onsets mark.",
    "dense": "Every onset marks, and each makes more.",
}
#: Strength gate per density: an event below it makes no object.
DENSITY_GATE: dict[str, float] = {"sparse": 0.45, "normal": 0.15, "dense": 0.0}
#: Count multiplier per density, for the archetypes that spawn several per event.
DENSITY_FACTOR: dict[str, float] = {"sparse": 0.5, "normal": 1.0, "dense": 2.0}

#: Archetype parameters, documented once for the schema and the prompt. Each is
#: read by its archetype with the default given here and clamped to the range.
ARCHETYPE_PARAMS: dict[str, dict[str, tuple[float, float, float, str]]] = {
    "fischinger": {
        "columns": (6, 2, 24, "grid columns"),
        "rows": (3, 1, 12, "grid rows"),
        "hold_beats": (1.0, 0.1, 8.0, "how long a shape lives, in beats"),
    },
    "star_guitar": {
        "speed": (0.35, 0.05, 2.0, "scroll speed, canvas widths per second"),
        "horizon": (0.72, 0.3, 0.95, "ground line, fraction of height from the top"),
    },
    "mclaren": {
        "life_beats": (0.25, 0.05, 2.0, "how long a scratch lives, in beats"),
    },
    "swarm": {
        "particles": (6, 1, 40, "particles per event at normal density"),
        "life_s": (1.5, 0.2, 6.0, "particle lifetime, seconds"),
    },
}


def _enum(vocab: Mapping[str, str]) -> list[str]:
    return list(vocab)


# --------------------------------------------------------------------------
# coercion helpers — what makes from_dict total over hostile input
# --------------------------------------------------------------------------


def _mapping(v: Any) -> dict[str, Any]:
    return dict(v) if isinstance(v, Mapping) else {}


def _seq(v: Any) -> tuple:
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,) if v.strip() else ()
    if isinstance(v, (list, tuple, set, frozenset)):
        return tuple(x for x in v if x is not None)
    return (v,)


def _text(v: Any, default: str = "") -> str:
    return default if v is None else str(v)


def _build(cls, raw: Mapping[str, Any]):
    """A frozen record from a mapping, each field coerced toward its type.

    Unknown keys are dropped; a value that will not coerce keeps the default.
    """
    from dataclasses import fields

    defaults = asdict(cls())
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw or raw[f.name] is None:
            continue
        v = raw[f.name]
        want = type(defaults[f.name])
        try:
            if want is bool:
                kwargs[f.name] = bool(v)
            elif want is int:
                kwargs[f.name] = int(float(v))
            elif want is float:
                kwargs[f.name] = float(v)
            elif want is str:
                kwargs[f.name] = str(v)
            else:
                kwargs[f.name] = v
        except (TypeError, ValueError):
            continue
    return cls(**kwargs)


# --------------------------------------------------------------------------
# the spec
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Palette:
    """Colours as ``#rrggbb``. ``low``/``mid``/``high`` are the band colours."""

    bg: str = "#0b0b12"
    bg2: str = "#1b1b2e"
    fg: str = "#f4f1e8"
    low: str = "#e4572e"
    mid: str = "#f3c623"
    high: str = "#4cc9f0"


@dataclass(frozen=True, slots=True, kw_only=True)
class Direction:
    """The song-level creative decision."""

    mood: str = ""
    palette: Palette = field(default_factory=Palette)
    background: str = "solid"
    density: str = "normal"
    rationale: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Scene:
    """One archetype applied to part of the song.

    ``applies_to`` names section tiers (``low``/``mid``/``high``), section
    indices as strings (``"2"``), or ``"*"`` for everything. A named scene wins
    its sections over a ``"*"`` one; sections nobody names fall back to the
    first ``"*"`` scene, and to the first scene if there is none.
    """

    applies_to: tuple[str, ...] = ("*",)
    archetype: str = "fischinger"
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class TreatmentSpec:
    """A complete, renderable treatment.

    >>> s = TreatmentSpec()
    >>> validate(s)
    []
    >>> TreatmentSpec.from_dict(s.to_dict()) == s
    True
    """

    spec_version: str = SPEC_VERSION
    title: str = ""
    direction: Direction = field(default_factory=Direction)
    scenes: tuple[Scene, ...] = field(default_factory=lambda: (Scene(),))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "TreatmentSpec":
        """Build from a plain mapping — TOTAL over what a model or caller sends.

        >>> s = TreatmentSpec.from_dict({'scenes': [None, {'applies_to': 'high',
        ...     'archetype': 'swarm', 'params': 'nope'}],
        ...     'direction': {'palette': {'foo': 1, 'bg': '#000000'}, 'density': None}})
        >>> s.scenes[1].applies_to, s.scenes[1].params, s.direction.palette.bg
        (('high',), {}, '#000000')
        """
        d = _mapping(d)
        direction = _mapping(d.get("direction"))
        direction_obj = Direction(
            mood=_text(direction.get("mood")),
            rationale=_text(direction.get("rationale")),
            palette=_build(Palette, _mapping(direction.get("palette"))),
            background=_text(direction.get("background"), "solid"),
            density=_text(direction.get("density"), "normal"),
        )
        raw_scenes = d.get("scenes")
        raw_scenes = list(raw_scenes) if isinstance(raw_scenes, (list, tuple)) else [{}]
        scenes = []
        for raw in raw_scenes or [{}]:
            raw = _mapping(raw)
            scenes.append(
                Scene(
                    applies_to=tuple(str(a) for a in _seq(raw.get("applies_to"))) or ("*",),
                    archetype=_text(raw.get("archetype"), "fischinger"),
                    params=_mapping(raw.get("params")),
                )
            )
        return cls(
            spec_version=_text(d.get("spec_version"), SPEC_VERSION),
            title=_text(d.get("title")),
            direction=direction_obj,
            scenes=tuple(scenes),
        )

    @classmethod
    def from_json(cls, text: str) -> "TreatmentSpec":
        return cls.from_dict(json.loads(text))


def default_treatment(archetype: str = "fischinger", **direction: Any) -> TreatmentSpec:
    """A one-scene treatment for ``archetype``; the CLI's ``--archetype`` shortcut.

    >>> default_treatment('mclaren').scenes[0].archetype
    'mclaren'
    """
    return TreatmentSpec(
        direction=Direction(**direction) if direction else Direction(),
        scenes=(Scene(archetype=archetype),),
    )


# --------------------------------------------------------------------------
# validation and repair
# --------------------------------------------------------------------------

_HEX = "0123456789abcdefABCDEF"


def _bad_colour(value: Any) -> bool:
    return not (
        isinstance(value, str) and len(value) == 7 and value[0] == "#"
        and all(c in _HEX for c in value[1:])
    )


def validate(spec: TreatmentSpec) -> list[str]:
    """Human-readable problems; empty means renderable.

    >>> validate(TreatmentSpec(scenes=(Scene(archetype='nope'),)))
    ["scenes[0].archetype 'nope' is not one of the known archetypes"]
    """
    errs: list[str] = []
    if spec.spec_version != SPEC_VERSION:
        errs.append(f"spec_version {spec.spec_version!r} != supported {SPEC_VERSION!r}")
    d = spec.direction
    for name, value in asdict(d.palette).items():
        if _bad_colour(value):
            errs.append(f"direction.palette.{name} {value!r} is not a #rrggbb colour")
    if d.background not in BACKGROUNDS:
        errs.append(f"direction.background {d.background!r} is not one of {_enum(BACKGROUNDS)}")
    if d.density not in DENSITIES:
        errs.append(f"direction.density {d.density!r} is not one of {_enum(DENSITIES)}")
    if not spec.scenes:
        errs.append("scenes is empty — at least one scene is required")
    for i, sc in enumerate(spec.scenes):
        if sc.archetype not in ARCHETYPES:
            errs.append(f"scenes[{i}].archetype {sc.archetype!r} is not one of the known archetypes")
        elif not isinstance(sc.params, Mapping):
            errs.append(f"scenes[{i}].params must be an object")
        else:
            known = ARCHETYPE_PARAMS[sc.archetype]
            for k, v in sc.params.items():
                if k not in known:
                    errs.append(f"scenes[{i}].params.{k} is not a {sc.archetype} parameter "
                                f"(known: {sorted(known)})")
                elif not isinstance(v, (int, float)) or isinstance(v, bool):
                    errs.append(f"scenes[{i}].params.{k} must be a number")
    return errs


def repair(spec: TreatmentSpec) -> tuple[TreatmentSpec, list[str]]:
    """Project ``spec`` onto the valid space; return ``(spec, notes)``.

    >>> fixed, notes = repair(TreatmentSpec(scenes=(Scene(archetype='swirl',
    ...     params={'columns': 99, 'bogus': 1}),)))
    >>> fixed.scenes[0].archetype, dict(fixed.scenes[0].params)
    ('fischinger', {'columns': 24})
    >>> notes
    ["scenes[0].archetype 'swirl' -> 'fischinger'", 'scenes[0].params.columns 99 -> 24', "scenes[0].params dropped unknown ['bogus']"]
    """
    notes: list[str] = []

    def pick(value: str, vocab: Mapping[str, str], default: str, where: str) -> str:
        if value in vocab:
            return value
        notes.append(f"{where} {value!r} -> {default!r}")
        return default

    d = spec.direction
    fixes = {}
    for name, value in asdict(d.palette).items():
        if _bad_colour(value):
            fallback = getattr(Palette(), name)
            notes.append(f"direction.palette.{name} {value!r} -> {fallback!r}")
            fixes[name] = fallback
    direction = replace(
        d,
        palette=replace(d.palette, **fixes) if fixes else d.palette,
        background=pick(d.background, BACKGROUNDS, "solid", "direction.background"),
        density=pick(d.density, DENSITIES, "normal", "direction.density"),
    )

    scenes = []
    for i, sc in enumerate(spec.scenes or (Scene(),)):
        archetype = pick(sc.archetype, ARCHETYPES, "fischinger", f"scenes[{i}].archetype")
        known = ARCHETYPE_PARAMS[archetype]
        params: dict[str, float] = {}
        dropped = []
        for k, v in (sc.params.items() if isinstance(sc.params, Mapping) else ()):
            if k not in known:
                dropped.append(k)
                continue
            try:
                num = float(v) if not isinstance(v, bool) else float("nan")
            except (TypeError, ValueError):
                num = float("nan")
            default, lo, hi, _ = known[k]
            if num != num:  # NaN: unusable
                notes.append(f"scenes[{i}].params.{k} {v!r} -> {default!r}")
                num = default
            clamped = min(hi, max(lo, num))
            if clamped != num:
                notes.append(f"scenes[{i}].params.{k} {v!r} -> {clamped!r}")
            params[k] = int(clamped) if float(clamped).is_integer() else clamped
        if dropped:
            notes.append(f"scenes[{i}].params dropped unknown {dropped}")
        scenes.append(replace(sc, archetype=archetype, params=params))

    return (
        TreatmentSpec(spec_version=SPEC_VERSION, title=spec.title,
                      direction=direction, scenes=tuple(scenes)),
        notes,
    )


def coerce(obj: Mapping[str, Any] | TreatmentSpec | str) -> tuple[TreatmentSpec, list[str]]:
    """Whatever a caller produced -> a renderable spec plus the repair notes."""
    if isinstance(obj, TreatmentSpec):
        spec = obj
    elif isinstance(obj, str):
        spec = TreatmentSpec.from_json(obj)
    else:
        spec = TreatmentSpec.from_dict(obj)
    return repair(spec)


# --------------------------------------------------------------------------
# JSON Schema and vocabulary — one artefact, several readers
# --------------------------------------------------------------------------


def json_schema() -> dict[str, Any]:
    """The JSON Schema for a :class:`TreatmentSpec`.

    >>> json_schema()['properties']['scenes']['items']['properties']['archetype']['enum']
    ['fischinger', 'star_guitar', 'mclaren', 'swarm']
    """
    colour = {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "muvid choreo treatment",
        "type": "object",
        "additionalProperties": False,
        "required": ["direction", "scenes"],
        "properties": {
            "spec_version": {"type": "string", "const": SPEC_VERSION},
            "title": {"type": "string"},
            "direction": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mood": {"type": "string"},
                    "rationale": {"type": "string"},
                    "palette": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {k: colour for k in asdict(Palette())},
                    },
                    "background": {
                        "type": "string", "enum": _enum(BACKGROUNDS),
                        "description": "; ".join(f"{k}: {v}" for k, v in BACKGROUNDS.items()),
                    },
                    "density": {
                        "type": "string", "enum": _enum(DENSITIES),
                        "description": "; ".join(f"{k}: {v}" for k, v in DENSITIES.items()),
                    },
                },
            },
            "scenes": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["archetype"],
                    "properties": {
                        "applies_to": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Section tiers (low/mid/high), section "
                            "indices as strings, or ['*'] for the whole song.",
                        },
                        "archetype": {
                            "type": "string", "enum": _enum(ARCHETYPES),
                            "description": "; ".join(f"{k}: {v}" for k, v in ARCHETYPES.items()),
                        },
                        "params": {
                            "type": "object",
                            "description": "; ".join(
                                f"{a}: " + ", ".join(
                                    f"{k} ({d}; default {dflt}, {lo}..{hi})"
                                    for k, (dflt, lo, hi, d) in ps.items()
                                )
                                for a, ps in ARCHETYPE_PARAMS.items()
                            ),
                        },
                    },
                },
            },
        },
    }


def vocabulary() -> dict[str, Any]:
    """Every closed vocabulary, for prompts and UI. One copy, several readers."""
    return {
        "archetypes": dict(ARCHETYPES),
        "backgrounds": dict(BACKGROUNDS),
        "densities": dict(DENSITIES),
        "archetype_params": {
            a: {k: {"default": d, "min": lo, "max": hi, "description": desc}
                for k, (d, lo, hi, desc) in ps.items()}
            for a, ps in ARCHETYPE_PARAMS.items()
        },
    }
