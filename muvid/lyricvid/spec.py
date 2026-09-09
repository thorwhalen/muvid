"""The treatment spec — what a director (human or model) decides, as data.

This is the SSOT both runtimes share: the Claude Code skill and the production
LLM call produce *this*, and every renderer consumes *this*. It is stdlib-only
and import-safe on purpose.

The shape is two layers, and the split is the single most important decision in
the whole subgenre:

``direction``
    WHAT and WHY — mood, palette, typography, motion vocabulary, and a
    rationale a human can read when choosing between options. This is the part
    a model is genuinely good at.

``scenes``
    HOW — an **archetype** from a closed set, plus parameters. Never raw
    geometry, never raw timings.

Three rules follow, and they are not stylistic:

* **No coordinates from the model.** Every archetype computes its own geometry
  in Python. The *Visual Lyrics* authors reached this the hard way and reported
  that LLM-generated bounding boxes "often result in layouts with misalignment
  and overlap issues"; the archetype removes the need to generate one at all.
* **No timings from the model.** Word onsets, the beat grid and section bounds
  come from muvid's aligner. The model picks a *quantisation policy*
  (:class:`Quantize`) and Python applies it. This kills the entire class of
  "the words drift out of sync" bugs by construction.
* **Closed vocabularies everywhere.** Archetypes, motion families, easings and
  cut styles are enums. Closed sets are what make the schema constrainable, two
  options diffable, and a renderer total rather than best-effort.

An invalid spec is not an error to hand back to a model when it can be
*projected* onto the valid space instead — see :func:`repair`. Deterministic
repair is cheaper, faster and more predictable than a retry, and it means a
slightly-wrong model output still renders.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable, Mapping

SPEC_VERSION = "1.0"

# --------------------------------------------------------------------------
# Closed vocabularies. Every one of these is an LLM-facing enum, so each entry
# carries a one-line description that goes into the prompt and the JSON Schema.
# --------------------------------------------------------------------------

#: How words are placed on screen. The renderer owns the geometry; the spec
#: only names the family and its knobs.
ARCHETYPES: dict[str, str] = {
    "one_word_centred": (
        "One word at a time, large, centred. The default lyric-video look: "
        "unmissable, works at any aspect ratio, reads on a phone."
    ),
    "stacked_lines": (
        "Lines accumulate down the frame and hold, so the viewer can read back "
        "what has already been sung. Good for narrative or dense lyrics."
    ),
    "karaoke_wipe": (
        "Two lines at the bottom, the current one wiped syllable by syllable as "
        "it is sung. The classic karaoke treatment; the most legible option."
    ),
    "concrete_page": (
        "The whole lyric is typeset as a fixed page — one CENTRED HORIZONTAL ROW "
        "per line — and each word ignites in reading order as it is sung. The "
        "page never reflows. Use when the poem is lines on a page. It cannot "
        "slant, indent or shape anything: for a calligram or a concrete poem "
        "whose picture is made by the run of the text, use 'calligram'; for "
        "words poured into an outline, use 'shape_fill'."
    ),
    "calligram": (
        "Each line becomes a slanting streak of UPRIGHT letters, one letter per "
        "slot, the streaks fanning open as they descend — the Apollinaire "
        "'Il pleut' construction. Use for a calligram or concrete poem whose "
        "shape is made by the run of the text itself rather than by an outline; "
        "prefer 'shape_fill' when the shape is a picture the words pour into, "
        "and 'concrete_page' when the layout is simply lines on a page."
    ),
    "shape_fill": (
        "Words packed into the outline of a shape, filling it as the song "
        "proceeds. Use when the song has one strong concrete image."
    ),
    "text_on_path": (
        "Words follow a curve across the frame. Cheap, distinctive, and good "
        "for a single repeated hook."
    ),
    "scatter": (
        "Words appear away from centre and drift, density rising with energy. "
        "Use for chaos, crowds, or an instrumental-heavy chorus."
    ),
}

#: How a single word arrives. Composable with the archetype rather than part of it.
MOTIONS: dict[str, str] = {
    "cut": "Appears instantly. Hardest, most rhythmic.",
    "fade": "Fades up over a fraction of a beat.",
    "pop": "Fades up with a slight overshoot in scale, then settles.",
    "rise": "Fades up while moving a short distance upward.",
    "wipe": "Revealed left-to-right, like a karaoke wipe.",
    "typewriter": "Letters appear one at a time across the word's duration.",
}

#: What a word does when it is no longer current.
PERSISTENCE: dict[str, str] = {
    "hold": "Stays exactly as it arrived, forever. The page fills up.",
    "dim": "Stays but recedes, so the current word leads. Keeps context readable.",
    "clear_on_line": "Cleared when its line ends.",
    "clear_on_section": "Cleared when its section ends.",
}

#: What the animation clock is quantised to. The MODEL picks one of these; the
#: numbers behind them always come from measurement, never from the model.
QUANTIZE: dict[str, str] = {
    "word": "Each word ignites at its own measured onset. Tightest sync.",
    "syllable": "Sub-word timing, where the aligner provides it.",
    "line": "The whole line arrives together, at the line's start.",
    "beat": "Snapped to the nearest beat. Rhythmic, forgiving of alignment error.",
    "downbeat": "Snapped to the nearest bar start. Slow, deliberate.",
}

CUT_STYLES: dict[str, str] = {
    "hard": "Instant change between scenes.",
    "crossfade": "Brief dissolve between scenes.",
    "hold": "The previous scene stays until the next has fully arrived.",
}

CASES: dict[str, str] = {
    "as_written": "Exactly as the lyrics are written.",
    "upper": "ALL CAPS.",
    "lower": "all lowercase.",
    "title": "Title Case.",
}

#: Where a shape outline may come from. A closed set for the same reason the
#: others are — and additionally because ``value`` is INTERPRETED by the
#: renderer, which makes ``kind`` a trust boundary: ``mask_image`` names a file
#: to open, and a treatment that arrives from a remote caller must not be able
#: to point that at a host path. The spec only admits the kinds; where a
#: ``mask_image`` value is allowed to come FROM is the caller-facing surface's
#: decision (see ``MASK_IMAGE_TRUSTED``).
SHAPE_KINDS: dict[str, str] = {
    "named": "A built-in outline: circle, heart, star, apple, square.",
    "svg_path": "An SVG path string (M/L/H/V/C/S/Q/T/Z) supplied inline.",
    "mask_image": "An image whose dark ink (or alpha) is the outline. Trusted "
    "callers only: the value names a file.",
}

#: The shape kinds that carry no reference to anything outside the spec. A
#: surface serving untrusted callers (the MCP tools) admits ONLY these unless it
#: has itself fetched and scoped the image.
INLINE_SHAPE_KINDS: frozenset[str] = frozenset({"named", "svg_path"})


def _enum(vocab: Mapping[str, str]) -> list[str]:
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
    field's default. This is what stops ``Palette(foo=1)`` and
    ``Typography(weight='700', tracking=None)`` from crashing a render.
    """
    from dataclasses import fields

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
            continue  # keep the default
    return cls(**kwargs)


# --------------------------------------------------------------------------
# The spec
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Palette:
    """Colours, as ``#rrggbb``. ``dim`` is the un-sung state where one exists."""

    bg: str = "#101014"
    fg: str = "#f4f4f0"
    accent: str = "#e0533d"
    dim: str = "#4a4a52"


@dataclass(frozen=True, slots=True, kw_only=True)
class Typography:
    """Type choices. ``family`` is resolved against installed/bundled fonts, and
    an unavailable family falls back rather than failing the render."""

    family: str = "DejaVu Sans"
    weight: int = 700
    case: str = "as_written"
    tracking: float = 0.0  # em
    max_line_chars: int = 28


@dataclass(frozen=True, slots=True, kw_only=True)
class Direction:
    """The song-level creative decision. The half a model is actually good at."""

    mood: str = ""
    palette: Palette = field(default_factory=Palette)
    typography: Typography = field(default_factory=Typography)
    motion_vocabulary: tuple[str, ...] = ("fade",)
    rationale: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Timing:
    """The quantisation POLICY. Never actual times."""

    quantize_to: str = "word"
    cut_style: str = "hard"
    #: Seconds a word takes to arrive. Small, or it stops reading as on-the-beat.
    attack_s: float = 0.12
    #: Seconds before a word's onset to start it. Compensates for perceived lag.
    lead_s: float = 0.0


@dataclass(frozen=True, slots=True, kw_only=True)
class ShapeRef:
    """Where a ``shape_fill`` / ``concrete_page`` outline comes from.

    ``kind='named'`` uses a built-in outline; ``kind='mask_image'`` traces an
    image the caller supplied; ``kind='svg_path'`` takes a path directly. The
    model may name a shape, but it never draws one.
    """

    kind: str = "named"  # named | mask_image | svg_path
    value: str = "circle"


@dataclass(frozen=True, slots=True, kw_only=True)
class Scene:
    """One treatment, applied to part of the song.

    ``applies_to`` names sections by label (as the lyrics document labels them),
    or ``"*"`` for the whole song. Sections muvid found but the spec does not
    mention fall back to the first ``"*"`` scene, so a one-scene spec is valid
    and complete.
    """

    applies_to: tuple[str, ...] = ("*",)
    archetype: str = "one_word_centred"
    motion: str = "fade"
    persistence: str = "clear_on_line"
    timing: Timing = field(default_factory=Timing)
    shape: ShapeRef | None = None
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

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """A JSON-native dict: tuples become lists, so what this emits is
        exactly what :func:`json_schema` validates and what a file round-trips."""
        return json.loads(json.dumps(asdict(self)))

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "TreatmentSpec":
        """Build from a plain mapping — TOTAL over what a model or a remote caller sends.

        Missing keys default. Wrong-shaped values are coerced toward the field's
        type rather than raised on: a string where a list was expected becomes
        a one-element list (``applies_to: "chorus"`` used to become
        ``('c','h','o','r','u','s')``), a numeric string becomes a number, an
        unknown key is dropped, a ``None`` sub-object is the default. What
        cannot be coerced falls to the default and :func:`repair` reports the
        enum-level substitutions afterwards.

        >>> s = TreatmentSpec.from_dict({'scenes': [None, {'applies_to': 'chorus',
        ...     'timing': 'fast', 'shape': 'circle'}],
        ...     'direction': {'palette': {'foo': 1, 'bg': '#000000'},
        ...                   'motion_vocabulary': 'pop',
        ...                   'typography': {'weight': '700', 'tracking': None}}})
        >>> s.scenes[1].applies_to, s.direction.motion_vocabulary
        (('chorus',), ('pop',))
        >>> s.direction.typography.weight, s.direction.typography.tracking
        (700, 0.0)
        >>> s.direction.palette.bg, s.scenes[1].shape
        ('#000000', None)
        """
        d = _mapping(d)

        direction = _mapping(d.get("direction"))
        pal = _build(Palette, _mapping(direction.get("palette")))
        typo = _build(Typography, _mapping(direction.get("typography")))
        mv = _seq(direction.get("motion_vocabulary")) or ("fade",)
        direction_obj = Direction(
            mood=_text(direction.get("mood")),
            rationale=_text(direction.get("rationale")),
            palette=pal,
            typography=typo,
            motion_vocabulary=tuple(str(m) for m in mv),
        )

        scenes = []
        raw_scenes = d.get("scenes")
        raw_scenes = list(raw_scenes) if isinstance(raw_scenes, (list, tuple)) else [{}]
        for raw in raw_scenes or [{}]:
            raw = _mapping(raw)
            timing = _build(Timing, _mapping(raw.get("timing")))
            shape_raw = raw.get("shape")
            shape = (
                _build(ShapeRef, _mapping(shape_raw))
                if isinstance(shape_raw, Mapping)
                else None
            )
            applies = tuple(str(a) for a in _seq(raw.get("applies_to"))) or ("*",)
            scenes.append(
                Scene(
                    applies_to=applies,
                    archetype=_text(raw.get("archetype"), "one_word_centred"),
                    motion=_text(raw.get("motion"), "fade"),
                    persistence=_text(raw.get("persistence"), "clear_on_line"),
                    timing=timing,
                    shape=shape,
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


def _bad_colour(value: str) -> bool:
    return not (
        isinstance(value, str)
        and len(value) == 7
        and value[0] == "#"
        and all(c in _HEX for c in value[1:])
    )


def validate(spec: TreatmentSpec) -> list[str]:
    """Return a list of human-readable problems. Empty means renderable.

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
    if d.typography.case not in CASES:
        errs.append(
            f"direction.typography.case {d.typography.case!r} is not one of {_enum(CASES)}"
        )
    for m in d.motion_vocabulary:
        if m not in MOTIONS:
            errs.append(
                f"direction.motion_vocabulary entry {m!r} is not a known motion"
            )
    if not spec.scenes:
        errs.append("scenes is empty — at least one scene is required")
    for i, sc in enumerate(spec.scenes):
        if sc.archetype not in ARCHETYPES:
            errs.append(
                f"scenes[{i}].archetype {sc.archetype!r} is not one of the known archetypes"
            )
        if sc.motion not in MOTIONS:
            errs.append(f"scenes[{i}].motion {sc.motion!r} is not a known motion")
        if sc.persistence not in PERSISTENCE:
            errs.append(
                f"scenes[{i}].persistence {sc.persistence!r} is not a known persistence"
            )
        if sc.timing.quantize_to not in QUANTIZE:
            errs.append(
                f"scenes[{i}].timing.quantize_to {sc.timing.quantize_to!r} is not known"
            )
        if sc.timing.cut_style not in CUT_STYLES:
            errs.append(
                f"scenes[{i}].timing.cut_style {sc.timing.cut_style!r} is not known"
            )
        if sc.timing.attack_s < 0:
            errs.append(f"scenes[{i}].timing.attack_s must be >= 0")
        if not 0 <= sc.timing.lead_s <= 1:
            errs.append(f"scenes[{i}].timing.lead_s must be within 0..1 s")
        if sc.archetype in {"shape_fill"} and sc.shape is None:
            errs.append(f"scenes[{i}].archetype {sc.archetype!r} requires a shape")
        if sc.shape is not None and sc.shape.kind not in SHAPE_KINDS:
            errs.append(
                f"scenes[{i}].shape.kind {sc.shape.kind!r} is not one of {_enum(SHAPE_KINDS)}"
            )
    return errs


def repair(spec: TreatmentSpec) -> tuple[TreatmentSpec, list[str]]:
    """Project ``spec`` onto the valid space. Returns ``(spec, notes)``.

    A model that names a motion that doesn't exist has made a small, mechanical
    mistake; projecting it onto the nearest legal value renders something good
    now, where a retry costs a round trip and may fail the same way. Every
    substitution is reported so the caller can show or log it.

    >>> fixed, notes = repair(TreatmentSpec(scenes=(Scene(archetype='swirl'),)))
    >>> fixed.scenes[0].archetype
    'one_word_centred'
    >>> notes
    ["scenes[0].archetype 'swirl' -> 'one_word_centred'"]
    """
    notes: list[str] = []

    def pick(value: str, vocab: Mapping[str, str], default: str, where: str) -> str:
        if value in vocab:
            return value
        notes.append(f"{where} {value!r} -> {default!r}")
        return default

    d = spec.direction
    pal = d.palette
    fixes = {}
    for name, value in asdict(pal).items():
        if _bad_colour(value):
            fallback = getattr(Palette(), name)
            notes.append(f"direction.palette.{name} {value!r} -> {fallback!r}")
            fixes[name] = fallback
    pal = replace(pal, **fixes) if fixes else pal

    typo = d.typography
    if typo.case not in CASES:
        typo = replace(
            typo, case=pick(typo.case, CASES, "as_written", "direction.typography.case")
        )
    vocab = tuple(m for m in d.motion_vocabulary if m in MOTIONS)
    if len(vocab) != len(d.motion_vocabulary):
        dropped = [m for m in d.motion_vocabulary if m not in MOTIONS]
        notes.append(f"direction.motion_vocabulary dropped unknown {dropped}")
    direction = replace(
        d, palette=pal, typography=typo, motion_vocabulary=vocab or ("fade",)
    )

    scenes = []
    for i, sc in enumerate(spec.scenes or (Scene(),)):
        t = sc.timing
        t = replace(
            t,
            quantize_to=pick(
                t.quantize_to, QUANTIZE, "word", f"scenes[{i}].timing.quantize_to"
            ),
            cut_style=pick(
                t.cut_style, CUT_STYLES, "hard", f"scenes[{i}].timing.cut_style"
            ),
            attack_s=max(0.0, float(t.attack_s)),
            lead_s=min(1.0, max(0.0, float(t.lead_s))),
        )
        if t.lead_s != sc.timing.lead_s:
            notes.append(
                f"scenes[{i}].timing.lead_s {sc.timing.lead_s!r} -> {t.lead_s!r}"
            )
        archetype = pick(
            sc.archetype, ARCHETYPES, "one_word_centred", f"scenes[{i}].archetype"
        )
        shape = sc.shape
        if archetype == "shape_fill" and shape is None:
            shape = ShapeRef()
            notes.append(f"scenes[{i}] shape_fill without a shape -> {shape.value!r}")
        if shape is not None and shape.kind not in SHAPE_KINDS:
            notes.append(f"scenes[{i}].shape.kind {shape.kind!r} -> 'named'/'circle'")
            shape = ShapeRef()
        scenes.append(
            replace(
                sc,
                archetype=archetype,
                motion=pick(sc.motion, MOTIONS, "fade", f"scenes[{i}].motion"),
                persistence=pick(
                    sc.persistence,
                    PERSISTENCE,
                    "clear_on_line",
                    f"scenes[{i}].persistence",
                ),
                timing=t,
                shape=shape,
            )
        )

    return (
        TreatmentSpec(
            spec_version=SPEC_VERSION,
            title=spec.title,
            direction=direction,
            scenes=tuple(scenes),
        ),
        notes,
    )


def coerce(
    obj: Mapping[str, Any] | TreatmentSpec | str,
) -> tuple[TreatmentSpec, list[str]]:
    """Take whatever a caller or a model produced and return a renderable spec.

    Accepts a :class:`TreatmentSpec`, a mapping, or a JSON string — and repairs
    it. This is the one entry point production code should use.
    """
    if isinstance(obj, TreatmentSpec):
        spec = obj
    elif isinstance(obj, str):
        spec = TreatmentSpec.from_json(obj)
    else:
        spec = TreatmentSpec.from_dict(obj)
    return repair(spec)


# --------------------------------------------------------------------------
# JSON Schema — one artefact serving the UI form, CLI validation, the MCP tool
# definition and the model's structured-output constraint.
# --------------------------------------------------------------------------


def json_schema() -> dict[str, Any]:
    """The JSON Schema for a :class:`TreatmentSpec`.

    >>> s = json_schema()
    >>> s['properties']['scenes']['items']['properties']['archetype']['enum'][0]
    'one_word_centred'
    """
    colour = {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "muvid lyric-video treatment",
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
                        "description": "One sentence: the feeling the visuals should carry.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this treatment suits this song. Shown to the "
                        "human when choosing between options.",
                    },
                    "palette": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "bg": colour,
                            "fg": colour,
                            "accent": colour,
                            "dim": colour,
                        },
                    },
                    "typography": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "family": {"type": "string"},
                            "weight": {
                                "type": "integer",
                                "minimum": 100,
                                "maximum": 900,
                            },
                            "case": {"type": "string", "enum": _enum(CASES)},
                            "tracking": {"type": "number"},
                            "max_line_chars": {"type": "integer", "minimum": 8},
                        },
                    },
                    "motion_vocabulary": {
                        "type": "array",
                        "items": {"type": "string", "enum": _enum(MOTIONS)},
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
                        "motion": {
                            "type": "string",
                            "enum": _enum(MOTIONS),
                            "description": "; ".join(
                                f"{k}: {v}" for k, v in MOTIONS.items()
                            ),
                        },
                        "persistence": {
                            "type": "string",
                            "enum": _enum(PERSISTENCE),
                            "description": "; ".join(
                                f"{k}: {v}" for k, v in PERSISTENCE.items()
                            ),
                        },
                        "timing": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "quantize_to": {
                                    "type": "string",
                                    "enum": _enum(QUANTIZE),
                                    "description": "; ".join(
                                        f"{k}: {v}" for k, v in QUANTIZE.items()
                                    ),
                                },
                                "cut_style": {
                                    "type": "string",
                                    "enum": _enum(CUT_STYLES),
                                },
                                "attack_s": {"type": "number", "minimum": 0},
                                # a negative lead puts t_in after t_out and the
                                # cue is never drawn; bound it like attack_s
                                "lead_s": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 1,
                                },
                            },
                        },
                        "shape": {
                            # to_dict() emits null for a scene without a shape,
                            # and the schema must accept what to_dict emits
                            "type": ["object", "null"],
                            "additionalProperties": False,
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": _enum(SHAPE_KINDS),
                                    "description": "; ".join(
                                        f"{k}: {v}" for k, v in SHAPE_KINDS.items()
                                    ),
                                },
                                "value": {"type": "string"},
                            },
                        },
                        "params": {"type": "object"},
                    },
                },
            },
        },
    }


def vocabulary() -> dict[str, dict[str, str]]:
    """Every closed vocabulary, for prompts and UI. One copy, several readers."""
    return {
        "archetypes": dict(ARCHETYPES),
        "motions": dict(MOTIONS),
        "persistence": dict(PERSISTENCE),
        "quantize": dict(QUANTIZE),
        "cut_styles": dict(CUT_STYLES),
        "cases": dict(CASES),
    }
