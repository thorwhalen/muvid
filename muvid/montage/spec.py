"""The montage treatment spec — what a director (human or model) decides, as data.

Two layers, the same split as :mod:`muvid.lyricvid.spec` and for the same
reason: the half a model is good at is separated from the half it is not.

``direction``
    WHAT and WHY — a mood, the accent colour and grade, how the cutting should
    *feel* (a multiplier over the archetype's own pacing), and the reuse policy
    that decides how a small pool carries a long song.

``scenes``
    HOW — an **archetype** from a closed set applied to named sections, plus a
    small closed set of parameters. Never a cut time, never a crop rectangle.

Three rules follow:

* **No timings from the model.** Every cut sits on a beat, a bar or a
  subdivision of the song's measured grid; the spec names a density, Python
  applies it (:mod:`muvid.montage.plan`).
* **No geometry from the model.** Crops and moves come from a closed set of
  variants, chosen by the planner's reuse policy so a revisited photo shows a
  different framing. A model that emitted rectangles would emit wrong ones.
* **Closed vocabularies everywhere**, so an invalid spec can be *projected*
  onto the valid space (:func:`repair`) rather than bounced back for a retry.

This module is stdlib-only and import-safe on purpose: it is on the listing
path of the plugin surface.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Mapping

SPEC_VERSION = "1.0"

# --------------------------------------------------------------------------
# Closed vocabularies. Each entry carries a one-line description that goes into
# the prompt, the JSON Schema and the CLI's `vocabulary` verb.
# --------------------------------------------------------------------------

#: How the pool is cut to the song. The planner owns the timing and geometry;
#: the spec only names the family and its knobs.
ARCHETYPES: dict[str, str] = {
    "ballad_dissolve": (
        "Cut every 2-4 bars on a downbeat, one-beat crossfades, a slow Ken "
        "Burns drift on each still. For slow songs and quiet sections."
    ),
    "beat_cut": (
        "Hard cuts on the grid — every beat or half-bar in a chorus, every bar "
        "in a verse — with a punch-zoom on each cut. The 'photo beat sync' look."
    ),
    "grid": (
        "A 2x2 grid of tiles; one tile swaps on every beat. Dense and busy; "
        "wants a pool of eight or more."
    ),
    "stop_motion": (
        "Stills held for a beat subdivision with no motion at all — a "
        "flip-book. Mechanical, playful."
    ),
}

#: The parameters each archetype accepts, as a small JSON Schema per key. A key
#: outside this table is a validation error and is dropped by :func:`repair`;
#: a value outside its range is clamped. Closed, so a plugin UI can render it.
ARCHETYPE_PARAMS: dict[str, dict[str, dict[str, Any]]] = {
    "ballad_dissolve": {
        "bars_per_cut": {
            "type": "number", "minimum": 1, "maximum": 16, "default": 4,
            "description": "Bars between cuts in a verse; a chorus halves it.",
        },
        "fade_beats": {
            "type": "number", "minimum": 0, "maximum": 4, "default": 1,
            "description": "Crossfade length in beats. 0 is a hard cut.",
        },
        "drift": {
            "type": "number", "minimum": 0, "maximum": 0.3, "default": 0.08,
            "description": "Ken Burns amplitude as a fraction of the frame.",
        },
    },
    "beat_cut": {
        "beats_per_cut": {
            "type": "number", "minimum": 1, "maximum": 16, "default": 4,
            "description": "Beats between cuts in a verse; a chorus halves it.",
        },
        "punch": {
            "type": "number", "minimum": 0, "maximum": 0.3, "default": 0.12,
            "description": "Punch-zoom amount on each cut (0 disables).",
        },
    },
    "grid": {
        "beats_per_swap": {
            "type": "number", "minimum": 1, "maximum": 16, "default": 2,
            "description": "Beats between tile swaps in a verse; a chorus halves it.",
        },
    },
    "stop_motion": {
        "subdivision": {
            "type": "integer", "minimum": 1, "maximum": 4, "default": 2,
            "description": "Holds per beat (2 = eighth notes). A chorus doubles it, "
            "up to 4.",
        },
    },
}

#: How the cutting should feel. A MULTIPLIER over each archetype's own pacing,
#: so the same treatment reads "slow" on a ballad and "driving" on a banger
#: without the model naming a number of beats.
CUT_FEELS: dict[str, str] = {
    "slow": "Half as many cuts as the archetype's default. Contemplative.",
    "steady": "The archetype's own pacing.",
    "driving": "Twice as many cuts. Pushes forward.",
    "frantic": "Four times as many cuts, floored at the archetype's minimum.",
}

#: Multiplier on beats-per-cut per cut feel. Smaller is more cuts.
CUT_FEEL_FACTORS: dict[str, float] = {
    "slow": 2.0, "steady": 1.0, "driving": 0.5, "frantic": 0.25,
}

#: A colour grade applied to every frame. Closed because the grade is
#: EXECUTABLE ffmpeg; the palette's accent parameterises it, never a filter string.
GRADES: dict[str, str] = {
    "none": "The pool as shot.",
    "tint": "Blend the palette's accent colour into the frame (a duotone-ish wash).",
    "mono": "Desaturate to black and white.",
}

#: How a still moves within a slot. Assigned by the ARCHETYPE, never by the
#: spec — listed here so the plan's vocabulary is closed and documented.
MOTIONS: dict[str, str] = {
    "none": "Held still.",
    "zoom_in": "A slow push in.",
    "zoom_out": "A slow pull out.",
    "pan_left": "A slow drift leftward.",
    "pan_right": "A slow drift rightward.",
    "punch": "Arrives zoomed in and relaxes to rest within half a beat.",
}

#: How a slot arrives. A curated subset of ffmpeg's ``xfade`` transitions, the
#: same posture as ``muvid.footage.edl.TRANSITION_CURVES``: a name outside it is
#: refused here rather than discovered as an ffmpeg error three stages later.
TRANSITIONS: dict[str, str] = {
    "cut": "A hard cut.",
    "fade": "A crossfade.",
    "dissolve": "A noisy dissolve.",
    "fadeblack": "Dip to black.",
}

#: Section labels the planner knows how to pace. Others are paced as a verse.
SECTION_LABELS: dict[str, str] = {
    "intro": "Sparse cuts.",
    "verse": "The archetype's verse pacing.",
    "chorus": "Dense cuts; the last chorus gets the strongest images.",
    "bridge": "Chorus density.",
    "outro": "Sparse cuts.",
}


def _enum(vocab: Mapping[str, Any]) -> list[str]:
    return list(vocab)


# --------------------------------------------------------------------------
# Coercion helpers — what makes from_dict total over hostile input
# --------------------------------------------------------------------------


def _mapping(v: Any) -> dict[str, Any]:
    """A dict, or an empty one. Never raises."""
    return dict(v) if isinstance(v, Mapping) else {}


def _seq(v: Any) -> tuple:
    """A tuple; a bare string is ONE element, not its characters."""
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
    """Instantiate a frozen record from a mapping, coercing each field's type.

    Unknown keys are dropped; a value that will not coerce falls to the
    field's default.
    """
    defaults = asdict(cls())
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        v = raw[f.name]
        want = type(defaults[f.name])
        if v is None:
            continue
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
# The spec
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Palette:
    """Colours, as ``#rrggbb``. ``accent`` parameterises the ``tint`` grade;
    ``bg`` is the letterbox/pad colour when a tile does not fill its region."""

    bg: str = "#000000"
    accent: str = "#e0533d"


@dataclass(frozen=True, slots=True, kw_only=True)
class Reuse:
    """The reuse POLICY — how a small pool carries a long song.

    ``min_gap`` is how many cuts must pass before an image may return (it
    shrinks automatically to ``pool - 1`` for a small pool). Every return uses
    a different crop/move variant. ``reserve_for_finale`` holds the strongest
    quarter of the pool back for the last chorus.
    """

    min_gap: int = 6
    reserve_for_finale: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class Direction:
    """The song-level creative decision."""

    mood: str = ""
    palette: Palette = field(default_factory=Palette)
    cut_feel: str = "steady"
    grade: str = "none"
    reuse: Reuse = field(default_factory=Reuse)
    rationale: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Scene:
    """One archetype, applied to part of the song.

    ``applies_to`` names sections by label, or ``"*"`` for the whole song. A
    named scene wins its section; sections no scene names fall back to the
    first ``"*"`` scene, so a one-scene spec is valid and complete.
    """

    applies_to: tuple[str, ...] = ("*",)
    archetype: str = "beat_cut"
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class TreatmentSpec:
    """A complete, plannable treatment.

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
        """A JSON-native dict: tuples become lists, so what this emits is
        exactly what :func:`json_schema` validates and what a file round-trips."""
        return json.loads(json.dumps(asdict(self)))

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "TreatmentSpec":
        """Build from a plain mapping — TOTAL over what a model or a remote caller sends.

        Missing keys default; wrong-shaped values are coerced toward the
        field's type rather than raised on; unknown keys are dropped. What
        cannot be coerced falls to the default and :func:`repair` reports the
        vocabulary-level substitutions afterwards.

        >>> s = TreatmentSpec.from_dict({'scenes': [None, {'applies_to': 'chorus',
        ...     'archetype': 'grid', 'params': 'fast'}],
        ...     'direction': {'palette': {'foo': 1, 'accent': '#00ff00'},
        ...                   'reuse': {'min_gap': '3'}}})
        >>> s.scenes[1].applies_to, s.scenes[1].archetype, dict(s.scenes[1].params)
        (('chorus',), 'grid', {})
        >>> s.direction.palette.accent, s.direction.reuse.min_gap
        ('#00ff00', 3)
        """
        d = _mapping(d)
        direction = _mapping(d.get("direction"))
        direction_obj = Direction(
            mood=_text(direction.get("mood")),
            rationale=_text(direction.get("rationale")),
            palette=_build(Palette, _mapping(direction.get("palette"))),
            cut_feel=_text(direction.get("cut_feel"), "steady"),
            grade=_text(direction.get("grade"), "none"),
            reuse=_build(Reuse, _mapping(direction.get("reuse"))),
        )
        raw_scenes = d.get("scenes")
        raw_scenes = list(raw_scenes) if isinstance(raw_scenes, (list, tuple)) else [{}]
        scenes = []
        for raw in raw_scenes or [{}]:
            raw = _mapping(raw)
            scenes.append(
                Scene(
                    applies_to=tuple(str(a) for a in _seq(raw.get("applies_to"))) or ("*",),
                    archetype=_text(raw.get("archetype"), "beat_cut"),
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


# --------------------------------------------------------------------------
# Validation and repair
# --------------------------------------------------------------------------

_HEX = "0123456789abcdefABCDEF"


def _bad_colour(value: Any) -> bool:
    return not (
        isinstance(value, str)
        and len(value) == 7
        and value[0] == "#"
        and all(c in _HEX for c in value[1:])
    )


def _number(v: Any) -> float | None:
    """A finite number, or None. Bools are not numbers here."""
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate(spec: TreatmentSpec) -> list[str]:
    """Return a list of human-readable problems. Empty means plannable.

    >>> validate(TreatmentSpec(scenes=(Scene(archetype='nope'),)))
    ["scenes[0].archetype 'nope' is not one of the known archetypes"]
    >>> validate(TreatmentSpec(scenes=(Scene(archetype='grid', params={'punch': 1}),)))
    ["scenes[0].params 'punch' is not a parameter of 'grid' (allowed: ['beats_per_swap'])"]
    """
    errs: list[str] = []
    if spec.spec_version != SPEC_VERSION:
        errs.append(f"spec_version {spec.spec_version!r} != supported {SPEC_VERSION!r}")
    d = spec.direction
    for name, value in asdict(d.palette).items():
        if _bad_colour(value):
            errs.append(f"direction.palette.{name} {value!r} is not a #rrggbb colour")
    if d.cut_feel not in CUT_FEELS:
        errs.append(f"direction.cut_feel {d.cut_feel!r} is not one of {_enum(CUT_FEELS)}")
    if d.grade not in GRADES:
        errs.append(f"direction.grade {d.grade!r} is not one of {_enum(GRADES)}")
    if d.reuse.min_gap < 0:
        errs.append("direction.reuse.min_gap must be >= 0")
    if not spec.scenes:
        errs.append("scenes is empty — at least one scene is required")
    for i, sc in enumerate(spec.scenes):
        if sc.archetype not in ARCHETYPES:
            errs.append(
                f"scenes[{i}].archetype {sc.archetype!r} is not one of the known archetypes"
            )
            continue
        allowed = ARCHETYPE_PARAMS[sc.archetype]
        for key, value in dict(sc.params).items():
            if key not in allowed:
                errs.append(
                    f"scenes[{i}].params {key!r} is not a parameter of "
                    f"{sc.archetype!r} (allowed: {sorted(allowed)})"
                )
                continue
            rule = allowed[key]
            num = _number(value)
            if num is None:
                errs.append(f"scenes[{i}].params.{key} {value!r} is not a number")
            elif not rule["minimum"] <= num <= rule["maximum"]:
                errs.append(
                    f"scenes[{i}].params.{key} {num} is outside "
                    f"{rule['minimum']}..{rule['maximum']}"
                )
    return errs


def repair(spec: TreatmentSpec) -> tuple[TreatmentSpec, list[str]]:
    """Project ``spec`` onto the valid space. Returns ``(spec, notes)``.

    A model that names a grade that does not exist has made a small, mechanical
    mistake; projecting it onto the default renders something good now, where a
    retry costs a round trip. Every substitution is reported.

    >>> fixed, notes = repair(TreatmentSpec(scenes=(Scene(archetype='swirl'),)))
    >>> fixed.scenes[0].archetype
    'beat_cut'
    >>> notes
    ["scenes[0].archetype 'swirl' -> 'beat_cut'"]
    >>> fixed, notes = repair(TreatmentSpec(scenes=(
    ...     Scene(archetype='beat_cut', params={'punch': 9, 'nope': 1}),)))
    >>> dict(fixed.scenes[0].params), notes
    ({'punch': 0.3}, ['scenes[0].params.punch 9 -> 0.3', "scenes[0].params dropped unknown ['nope']"])
    """
    notes: list[str] = []

    def pick(value: str, vocab: Mapping[str, Any], default: str, where: str) -> str:
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
    palette = replace(d.palette, **fixes) if fixes else d.palette
    reuse = d.reuse
    if reuse.min_gap < 0:
        notes.append(f"direction.reuse.min_gap {reuse.min_gap} -> 0")
        reuse = replace(reuse, min_gap=0)
    direction = replace(
        d,
        palette=palette,
        cut_feel=pick(d.cut_feel, CUT_FEELS, "steady", "direction.cut_feel"),
        grade=pick(d.grade, GRADES, "none", "direction.grade"),
        reuse=reuse,
    )

    scenes = []
    for i, sc in enumerate(spec.scenes or (Scene(),)):
        archetype = pick(sc.archetype, ARCHETYPES, "beat_cut", f"scenes[{i}].archetype")
        allowed = ARCHETYPE_PARAMS[archetype]
        params: dict[str, Any] = {}
        dropped = []
        for key, value in dict(sc.params).items():
            if key not in allowed:
                dropped.append(key)
                continue
            rule = allowed[key]
            num = _number(value)
            if num is None:
                dropped.append(key)
                continue
            clamped = min(rule["maximum"], max(rule["minimum"], num))
            if rule["type"] == "integer":
                clamped = int(round(clamped))
            if clamped != num:
                notes.append(f"scenes[{i}].params.{key} {value!r} -> {clamped!r}")
            params[key] = clamped
        if dropped:
            notes.append(f"scenes[{i}].params dropped unknown {dropped}")
        scenes.append(replace(sc, archetype=archetype, params=params))

    return (
        TreatmentSpec(
            spec_version=SPEC_VERSION,
            title=spec.title,
            direction=direction,
            scenes=tuple(scenes),
        ),
        notes,
    )


def coerce(obj: Mapping[str, Any] | TreatmentSpec | str) -> tuple[TreatmentSpec, list[str]]:
    """Take whatever a caller or a model produced and return a plannable spec.

    Accepts a :class:`TreatmentSpec`, a mapping, or a JSON string — and repairs
    it. This is the one entry point production code should use.

    >>> spec, notes = coerce('{"scenes": [{"archetype": "grid"}]}')
    >>> spec.scenes[0].archetype, notes
    ('grid', [])
    """
    if isinstance(obj, TreatmentSpec):
        spec = obj
    elif isinstance(obj, str):
        spec = TreatmentSpec.from_json(obj)
    else:
        spec = TreatmentSpec.from_dict(obj)
    return repair(spec)


def one_scene(archetype: str = "beat_cut", **direction: Any) -> TreatmentSpec:
    """The treatment a caller gets from ``archetype=`` alone.

    >>> one_scene('grid', cut_feel='driving').scenes[0].archetype
    'grid'
    """
    spec, _ = coerce({"direction": direction, "scenes": [{"archetype": archetype}]})
    return spec


# --------------------------------------------------------------------------
# JSON Schema — one artefact serving the UI form, CLI validation, the MCP tool
# definition and a model's structured-output constraint.
# --------------------------------------------------------------------------


def json_schema() -> dict[str, Any]:
    """The JSON Schema for a :class:`TreatmentSpec`.

    >>> s = json_schema()
    >>> s['properties']['scenes']['items']['properties']['archetype']['enum'][0]
    'ballad_dissolve'
    """
    colour = {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}
    params_props: dict[str, Any] = {}
    for table in ARCHETYPE_PARAMS.values():
        for key, rule in table.items():
            params_props.setdefault(key, dict(rule))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "muvid montage treatment",
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
                    "mood": {
                        "type": "string",
                        "description": "One sentence: the feeling the cutting should carry.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this treatment suits this song and pool.",
                    },
                    "palette": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"bg": colour, "accent": colour},
                    },
                    "cut_feel": {
                        "type": "string",
                        "enum": _enum(CUT_FEELS),
                        "description": "; ".join(f"{k}: {v}" for k, v in CUT_FEELS.items()),
                    },
                    "grade": {
                        "type": "string",
                        "enum": _enum(GRADES),
                        "description": "; ".join(f"{k}: {v}" for k, v in GRADES.items()),
                    },
                    "reuse": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "min_gap": {"type": "integer", "minimum": 0},
                            "reserve_for_finale": {"type": "boolean"},
                        },
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
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Section labels this scene applies to, or "
                            "['*'] for the whole song.",
                        },
                        "archetype": {
                            "type": "string",
                            "enum": _enum(ARCHETYPES),
                            "description": "; ".join(
                                f"{k}: {v}" for k, v in ARCHETYPES.items()
                            ),
                        },
                        "params": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": params_props,
                            "description": "Per-archetype knobs: "
                            + "; ".join(
                                f"{a}: {sorted(t)}" for a, t in ARCHETYPE_PARAMS.items()
                            ),
                        },
                    },
                },
            },
        },
    }


def vocabulary() -> dict[str, dict[str, Any]]:
    """Every closed vocabulary, for prompts and UI. One copy, several readers."""
    return {
        "archetypes": dict(ARCHETYPES),
        "archetype_params": {a: dict(t) for a, t in ARCHETYPE_PARAMS.items()},
        "cut_feels": dict(CUT_FEELS),
        "grades": dict(GRADES),
        "motions": dict(MOTIONS),
        "transitions": dict(TRANSITIONS),
        "section_labels": dict(SECTION_LABELS),
    }
