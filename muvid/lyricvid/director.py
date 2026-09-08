"""The creative director — a song in, one or more :class:`TreatmentSpec`\\ s out.

This is the half of the subgenre that has taste, and it has to work in two
runtimes that look nothing alike:

**agentic**
    inside Claude Code or claude.ai, where a skill hands the *host* model the
    lyrics and the vocabularies and the host model writes the treatment;

**transactional**
    in production, as one bounded structured-output call from a web frontend,
    with a cost, a timeout and a schema constraint.

Duplicating the taste between a ``SKILL.md`` and a Python system prompt is how
those two runtimes silently diverge — one gets a new archetype, the other keeps
recommending the old one, and nobody notices until a render looks wrong. So the
shared artefacts are ranked, and there are only three of them:

1. the **schema** (:mod:`muvid.lyricvid.spec`), which constrains both runtimes;
2. the **pure functions** in this module, which have no runtime opinions at all;
3. the **prompt text**, which lives in ``muvid/data/prompts/*.md`` and is read
   from there by both. There is exactly one copy of every opinion, and the
   vocabularies are *interpolated into* the prompt from
   :func:`muvid.lyricvid.spec.vocabulary` rather than restated in it.

``llm`` is the seam, and it is one keyword argument: a callable
``(messages, schema) -> dict``. Its default is not a stub. :func:`heuristic_llm`
is a real director that reads the lyrics — density, repetition, line shape,
vocabulary richness, section structure — and picks an archetype, a palette and a
motion vocabulary from measurement alone. The whole subgenre therefore runs end
to end with no API key, no network and no cost, and :func:`anthropic_llm` is an
upgrade rather than a prerequisite.

Diversity is the other design decision worth stating. Asking one director for
``n`` options and turning the temperature up yields ``n`` blurred copies of one
idea; the differences become noise rather than intent. So options come from
:data:`PERSONAS` — several art directors who genuinely disagree about how much
of the frame belongs to reading and how much to feeling — each asked once, each
told which archetypes the other options already took.

Everything degrades honestly. A model that returns something unusable falls back
to the heuristic director, and the fallback is *recorded*: :func:`director_meta`
reads it back off the returned spec, and the reason is appended to the human-
facing rationale. A silent fallback that returns a plausible artifact is the
failure mode muvid has been bitten by before (muvid#46, muvid#38) and it is not
repeated here.

Import-safe: stdlib only at module scope. ``anthropic`` is imported inside
:func:`anthropic_llm`'s returned callable.
"""

from __future__ import annotations

import base64
import json
import math
import mimetypes
import re
import statistics
import warnings
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from muvid.lyricvid import spec as spec_mod
from muvid.lyricvid.timed_text import TimedText

__all__ = [
    "Persona",
    "PERSONAS",
    "register_persona",
    "list_personas",
    "resolve_persona",
    "director_prompt",
    "output_schema",
    "prompt_context",
    "build_messages",
    "heuristic_director",
    "heuristic_llm",
    "anthropic_llm",
    "propose_treatments",
    "rank_treatments",
    "director_meta",
]

#: An ``llm`` seam implementation: ``(messages, schema) -> dict``. ``messages``
#: is what :func:`build_messages` produced; ``schema`` is :func:`output_schema`.
#: The returned mapping is fed to :func:`muvid.lyricvid.spec.coerce`, so it may
#: be slightly wrong without being useless.
Llm = Callable[[Sequence[Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]]


# --------------------------------------------------------------------------
# prompt files — one copy, two runtimes
# --------------------------------------------------------------------------

#: Where the prompt text lives, relative to the ``muvid`` package. Read through
#: :mod:`importlib.resources` rather than ``__file__`` so it works from an
#: installed wheel and from a zipimport, not only from a source checkout.
PROMPT_DIR = ("data", "prompts")
DIRECTOR_PROMPT_FILE = "lyricvid_director.md"
PERSONAS_FILE = "lyricvid_personas.md"

#: Interpolation points in the director prompt. Markers rather than
#: ``str.format`` placeholders because the file is full of literal braces.
VOCABULARIES_MARKER = "<!-- muvid:vocabularies -->"
SCHEMA_MARKER = "<!-- muvid:schema -->"

#: Fenced block the song context travels in, so the heuristic director can read
#: back the very same message a model would have been given.
_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


@lru_cache(maxsize=None)
def prompt_file(name: str) -> str:
    """Read one packaged prompt file.

    >>> prompt_file(DIRECTOR_PROMPT_FILE).splitlines()[0]
    '# You are the creative director of a lyric video'
    """
    from importlib.resources import files

    resource = files("muvid")
    for part in (*PROMPT_DIR, name):
        resource = resource / part
    return resource.read_text(encoding="utf-8")


def _vocabulary_markdown() -> str:
    """The closed vocabularies as prose, generated from the SSOT."""
    titles = {
        "archetypes": "`archetype` — how words are placed on screen",
        "motions": "`motion` — how a single word arrives",
        "persistence": "`persistence` — what a word does once it is no longer current",
        "quantize": "`timing.quantize_to` — what the clock is snapped to",
        "cut_styles": "`timing.cut_style` — how one scene becomes the next",
        "cases": "`typography.case`",
    }
    out: list[str] = []
    for key, entries in spec_mod.vocabulary().items():
        out.append(f"### {titles.get(key, key)}\n")
        out.extend(f"- `{name}` — {why}" for name, why in entries.items())
        out.append("")
    return "\n".join(out).rstrip()


def director_prompt() -> str:
    """The director's system prompt, with the vocabularies and schema filled in.

    The same text is the skill's guidance and the API call's ``system``. Nothing
    in it is written twice: the vocabularies come from
    :func:`muvid.lyricvid.spec.vocabulary` and the schema from
    :func:`output_schema`.

    >>> text = director_prompt()
    >>> VOCABULARIES_MARKER in text or SCHEMA_MARKER in text
    False
    >>> '`karaoke_wipe`' in text and '"one_word_centred"' in text
    True
    """
    text = prompt_file(DIRECTOR_PROMPT_FILE)
    text = text.replace(VOCABULARIES_MARKER, _vocabulary_markdown())
    schema = json.dumps(output_schema(), indent=2, sort_keys=False)
    return text.replace(SCHEMA_MARKER, f"```json\n{schema}\n```")


# --------------------------------------------------------------------------
# the model-facing schema
# --------------------------------------------------------------------------

#: Properties dropped from the model-facing schema, and why. ``spec_version`` is
#: a constant the model has no business restating. ``params`` is the one place in
#: the spec where numbers *are* geometry (sizes, spreads, a y offset) — exactly
#: what rule 1 forbids the model from emitting — and every archetype has a
#: working default for all of them, so removing it costs nothing and closes the
#: only hole through which a coordinate could arrive.
_SCHEMA_OMIT = {"spec_version", "params"}


def _strictify(node: Any) -> Any:
    """Make every object in a JSON Schema total: all keys required, none extra.

    Structured-output decoding wants a closed schema. Doing this here rather
    than by hand in ``spec.json_schema()`` keeps the validation schema (which
    tolerates omissions, because :func:`~muvid.lyricvid.spec.repair` fills them)
    separate from the generation schema (which must not).
    """
    if isinstance(node, list):
        return [_strictify(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: _strictify(v) for k, v in node.items() if k not in _SCHEMA_OMIT}
    props = out.get("properties")
    if isinstance(props, dict):
        for name in _SCHEMA_OMIT:
            props.pop(name, None)
        out["required"] = list(props)
        out["additionalProperties"] = False
    return out


def output_schema() -> dict[str, Any]:
    """The JSON Schema a model generates against — a closed form of the spec's.

    >>> s = output_schema()
    >>> s['required']
    ['title', 'direction', 'scenes']
    >>> 'params' in s['properties']['scenes']['items']['properties']
    False
    >>> s['properties']['scenes']['items']['properties']['archetype']['enum'][0]
    'one_word_centred'
    """
    schema = _strictify(spec_mod.json_schema())
    schema.pop("$schema", None)
    return schema


# --------------------------------------------------------------------------
# personas — the registry
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Persona:
    """One art director's doctrine, plus the dials that encode it.

    :param doctrine: the prose a model reads. Never restated in Python.
    :param legibility: 0..1. How far this director bends toward "the words are
        the picture" (1.0) versus "the words are the texture of a picture" (0.0).
        It is the axis the personas actually disagree on, and it is what makes
        two of them choose differently from the *same* measurements.
    :param prefers: archetypes in order of preference.
    """

    slug: str
    name: str
    doctrine: str = ""
    legibility: float = 0.5
    prefers: tuple[str, ...] = ()
    mood: str = ""
    palette: Mapping[str, str] = field(default_factory=dict)
    typography: Mapping[str, Any] = field(default_factory=dict)
    motion: tuple[str, ...] = ("fade",)
    persistence: str = "clear_on_line"
    quantize: str = "word"
    cut_style: str = "hard"
    attack_s: float = 0.12
    lead_s: float = 0.0

    def block(self) -> str:
        """The persona as prompt text: heading, doctrine, dials."""
        dials = {
            "legibility": self.legibility,
            "prefers": list(self.prefers),
            "mood": self.mood,
            "palette": dict(self.palette),
            "typography": dict(self.typography),
            "motion": list(self.motion),
            "persistence": self.persistence,
            "quantize": self.quantize,
            "cut_style": self.cut_style,
        }
        return (
            f"## You are directing as: {self.name} (`{self.slug}`)\n\n"
            f"{self.doctrine}\n\n"
            "These are your defaults, not your output — depart from them when "
            "the song asks you to, and say so in the rationale.\n\n"
            f"```json\n{json.dumps(dials, indent=2)}\n```"
        )


#: The persona registry. Populated from the packaged markdown on first use;
#: :func:`register_persona` adds more. Same idiom as ``register_visual`` /
#: ``register_selection_strategy`` / ``register_archetype``.
PERSONAS: dict[str, Persona] = {}

_PERSONA_HEADING = re.compile(r"^##\s+(?P<name>.+?)\s+\(`(?P<slug>[a-z0-9_\-]+)`\)\s*$")


def register_persona(persona: Persona) -> Persona:
    """Register a persona under its slug (returns it, for inline use)."""
    if not isinstance(persona, Persona):
        raise TypeError(f"expected a Persona, got {type(persona).__name__}")
    PERSONAS[persona.slug] = persona
    return persona


def _load_packaged_personas() -> None:
    """Parse ``lyricvid_personas.md`` into the registry, once."""
    text = prompt_file(PERSONAS_FILE)
    blocks = re.split(r"\n(?=## )", text)
    for block in blocks:
        head = _PERSONA_HEADING.match(block.splitlines()[0])
        if head is None:
            continue
        fence = _JSON_FENCE.search(block)
        if fence is None:
            continue
        dials = json.loads(fence.group(1))
        doctrine = block[block.index("\n") + 1 : fence.start()].strip()
        register_persona(
            Persona(
                slug=dials.get("slug", head.group("slug")),
                name=dials.get("name", head.group("name")),
                doctrine=doctrine,
                legibility=float(dials.get("legibility", 0.5)),
                prefers=tuple(dials.get("prefers", ())),
                mood=dials.get("mood", ""),
                palette=dict(dials.get("palette", {})),
                typography=dict(dials.get("typography", {})),
                motion=tuple(dials.get("motion", ("fade",))),
                persistence=dials.get("persistence", "clear_on_line"),
                quantize=dials.get("quantize", "word"),
                cut_style=dials.get("cut_style", "hard"),
                attack_s=float(dials.get("attack_s", 0.12)),
                lead_s=float(dials.get("lead_s", 0.0)),
            )
        )


def list_personas() -> list[Persona]:
    """Every registered persona, packaged ones first, in file order.

    >>> [p.slug for p in list_personas()]
    ['typographer', 'atmospherist', 'concrete_poet', 'club_vj', 'karaoke_host', 'minimalist']
    >>> sorted({p.legibility for p in list_personas()}) == \
        [0.2, 0.35, 0.55, 0.7, 0.9, 1.0]
    True
    """
    if not PERSONAS:
        _load_packaged_personas()
    return list(PERSONAS.values())


def resolve_persona(persona: "str | Persona") -> Persona:
    """Resolve a slug or a :class:`Persona` to a :class:`Persona`.

    >>> resolve_persona('karaoke_host').name
    'The Karaoke Host'
    """
    if isinstance(persona, Persona):
        return persona
    list_personas()
    try:
        return PERSONAS[persona]
    except KeyError:
        raise KeyError(
            f"unknown persona {persona!r}; known: {sorted(PERSONAS)}"
        ) from None


# --------------------------------------------------------------------------
# measurement — what the director is allowed to reason from
# --------------------------------------------------------------------------

#: Words per second each archetype can carry before it stops being readable.
#: ``karaoke_wipe`` shows two whole lines and highlights inside them, so it
#: absorbs the most; ``scatter`` shows the least per fixation.
ARCHETYPE_CAPACITY_WPS: dict[str, float] = {
    "karaoke_wipe": 5.0,
    "stacked_lines": 4.2,
    "concrete_page": 3.6,
    "one_word_centred": 2.8,
    "text_on_path": 2.4,
    "shape_fill": 2.0,
    "scatter": 1.8,
}

#: Full-scale values used to normalise each raw measurement into ``0..1``. A
#: constant here is a knob, not a magic number: retuning the director is editing
#: this table and :data:`ARCHETYPE_CHARACTER`, never a branch.
SIGNAL_SCALES: dict[str, float] = {
    #: words/second at which text is "as dense as lyric video text gets"
    "words_per_second": 4.0,
    #: characters at which a line is "as long as lyric lines get"
    "line_chars": 44.0,
    #: coefficient of variation of line length at which a lyric reads as *shaped*
    "line_chars_cv": 0.55,
    #: share of content words taken by the single most repeated one
    "dominance": 0.10,
    #: distinct section labels at which a song is "fully structured"
    "sections": 4.0,
}

#: A page is a page while it fits on one.
PAGE_MAX_LINES = 22
PAGE_MAX_CHARS = 46

#: Words too common to be anyone's central image.
_STOPWORDS = frozenset(
    "a an the and or but if so as at by for in of on to up with from into "
    "is am are was were be been being do does did done have has had "
    "i you he she it we they me him her us them my your his its our their "
    "this that these those there here not no nor too very can will just "
    "don't dont won't wont it's its i'm im you're youre".split()
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


def _line_words_per_second(tt: TimedText) -> float:
    """Median in-line word rate — density where the words actually are.

    Dividing the word count by the song duration is the wrong instrument: a
    four-minute song with ninety seconds of instrumental reads as sparse when
    its sung passages are relentless. The median over lines ignores the gaps.
    """
    rates = [
        len(line.words) / span
        for line in tt.lines()
        if line.words and (span := line.end - line.start) > 1e-6
    ]
    return statistics.median(rates) if rates else 0.0


def _raw_signals(tt: TimedText) -> dict[str, Any]:
    """Everything measurable about the text, before any interpretation."""
    lines = list(tt.lines())
    words = list(tt.words())
    lengths = [len(line.text) for line in lines] or [0]
    texts = [" ".join(w.text.lower() for w in line.words) for line in lines]
    tokens = [re.sub(r"[^\w']+", "", w.text.lower()) for w in words]
    tokens = [t for t in tokens if t]
    content = [t for t in tokens if t not in _STOPWORDS]
    top = 0
    if content:
        counts: dict[str, int] = {}
        for t in content:
            counts[t] = counts.get(t, 0) + 1
        top = max(counts.values())
    mean_chars = statistics.fmean(lengths)
    spread = statistics.pstdev(lengths) if len(lengths) > 1 else 0.0
    labels = [s.label for s in tt.sections]
    return {
        "n_words": len(words),
        "n_lines": len(lines),
        "n_sections": len(tt.sections),
        "n_distinct_labels": len({l.lower() for l in labels}),
        "duration_s": round(float(tt.duration), 3),
        "words_per_second": round(_line_words_per_second(tt), 3),
        "repeated_line_fraction": round(
            1 - (len(set(texts)) / len(texts)) if texts else 0.0, 3
        ),
        "mean_line_chars": round(mean_chars, 1),
        "max_line_chars": max(lengths),
        "line_chars_cv": round(spread / mean_chars, 3) if mean_chars else 0.0,
        "type_token_ratio": round(len(set(tokens)) / len(tokens), 3) if tokens else 0.0,
        "top_content_word_share": (
            round((top - 1) / len(content), 3) if content else 0.0
        ),
        "timing_measured": tt.measured,
        "timing_source": tt.source,
    }


def _normalised(raw: Mapping[str, Any]) -> dict[str, float]:
    """The raw signals projected into ``0..1``, which is what the tables read.

    >>> n = _normalised({'words_per_second': 2.0, 'mean_line_chars': 22.0,
    ...                  'max_line_chars': 22, 'line_chars_cv': 0.0, 'n_lines': 4,
    ...                  'repeated_line_fraction': 0.5, 'type_token_ratio': 0.8,
    ...                  'top_content_word_share': 0.0, 'n_distinct_labels': 1})
    >>> n['density'], n['brevity'], n['variety']
    (0.5, 0.5, 0.5)
    """
    s = SIGNAL_SCALES
    density = _clamp(raw["words_per_second"] / s["words_per_second"])
    line_length = _clamp(raw["mean_line_chars"] / s["line_chars"])
    repetition = _clamp(float(raw["repeated_line_fraction"]))
    page_fits = _clamp(PAGE_MAX_LINES / max(1, raw["n_lines"])) * _clamp(
        PAGE_MAX_CHARS / max(1, raw["max_line_chars"])
    )
    return {
        "density": round(density, 3),
        "sparsity": round(1 - density, 3),
        "repetition": round(repetition, 3),
        "variety": round(1 - repetition, 3),
        "line_length": round(line_length, 3),
        "brevity": round(1 - line_length, 3),
        "shapedness": round(_clamp(raw["line_chars_cv"] / s["line_chars_cv"]), 3),
        "richness": round(_clamp(float(raw["type_token_ratio"])), 3),
        "dominance": round(_clamp(raw["top_content_word_share"] / s["dominance"]), 3),
        "page_fits": round(page_fits, 3),
        "structure": round(
            _clamp((raw["n_distinct_labels"] - 1) / (s["sections"] - 1)), 3
        ),
    }


#: How well each archetype suits the *character* of a lyric, as a linear model
#: over the normalised signals: ``base + sum(coefficient * signal)``, clamped.
#:
#: A table rather than a cascade of ``if``\\ s on purpose — muvid's house rule is
#: that adding a metric is a column plus a weight, not a branch — and it is the
#: half of the decision that is about what the song *is*. The other half, how
#: fast it goes past, is :data:`ARCHETYPE_CAPACITY_WPS`; a persona's
#: ``legibility`` says how to weigh one against the other.
ARCHETYPE_CHARACTER: dict[str, dict[str, float]] = {
    # a hook you already know: one word, big, unmissable
    "one_word_centred": {
        "base": 0.30,
        "repetition": 0.30,
        "brevity": 0.25,
        "sparsity": 0.15,
    },
    # a story you have to follow: keep what has been sung on screen
    "stacked_lines": {
        "base": 0.25,
        "line_length": 0.35,
        "variety": 0.25,
        "density": 0.15,
    },
    # sing along: two lines, one wiped, the next one already visible
    "karaoke_wipe": {
        "base": 0.35,
        "density": 0.25,
        "line_length": 0.20,
        "structure": 0.10,
    },
    # the lyric sheet has a shape worth showing, and it fits on one page
    "concrete_page": {
        "base": 0.10,
        "shapedness": 0.35,
        "page_fits": 0.35,
        "richness": 0.10,
    },
    # one image the song keeps returning to
    "shape_fill": {
        "base": 0.10,
        "dominance": 0.40,
        "sparsity": 0.20,
        "brevity": 0.15,
    },
    # a single repeated phrase, given a curve to ride
    "text_on_path": {
        "base": 0.15,
        "repetition": 0.35,
        "brevity": 0.30,
        "sparsity": 0.10,
    },
    # sparse, wide-ranging, fragmentary
    "scatter": {
        "base": 0.10,
        "richness": 0.30,
        "sparsity": 0.30,
        "brevity": 0.20,
    },
}

#: How the two halves of the archetype decision are weighed against the
#: persona's own ordering. Song first, but not by so much that six directors
#: return the same answer.
FIT_WEIGHT = 0.55
PERSONA_WEIGHT = 0.45


def _character_fit(archetype: str, signals: Mapping[str, float]) -> float:
    row = ARCHETYPE_CHARACTER.get(archetype, {})
    total = row.get("base", 0.0)
    total += sum(
        coef * signals.get(name, 0.0) for name, coef in row.items() if name != "base"
    )
    return _clamp(total)


def _legibility_fit(archetype: str, words_per_second: float) -> float:
    capacity = ARCHETYPE_CAPACITY_WPS.get(archetype, 2.5)
    if words_per_second <= 0:
        return 1.0
    return _clamp(capacity / words_per_second)


def _rank_archetypes(
    signals: Mapping[str, float], *, persona: Persona, words_per_second: float
) -> list[tuple[str, float]]:
    """Every archetype scored for this song *and* this director, best first."""
    prefers = [a for a in persona.prefers if a in spec_mod.ARCHETYPES]
    span = max(1, len(prefers) - 1)
    scored: list[tuple[str, float]] = []
    for archetype in spec_mod.ARCHETYPES:
        fit = persona.legibility * _legibility_fit(archetype, words_per_second) + (
            1 - persona.legibility
        ) * _character_fit(archetype, signals)
        pref = 1 - prefers.index(archetype) / span if archetype in prefers else 0.0
        scored.append((archetype, FIT_WEIGHT * fit + PERSONA_WEIGHT * pref))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored


# --------------------------------------------------------------------------
# the context a director is shown
# --------------------------------------------------------------------------


def prompt_context(
    timed_text: TimedText,
    *,
    song_title: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything the director should see, and nothing else. No audio bytes.

    The lyrics with their section labels, the measurements, whether the word
    times were *measured* or interpolated, and the closed vocabularies. Anything
    a caller has already measured elsewhere — tempo, key, a beat count — goes in
    through ``extra`` and lands under ``measurements``, so the director can use
    a real number without this module growing an audio dependency.

    >>> from muvid.lyricvid.timed_text import from_words
    >>> tt = from_words([('hold', 0.0, .5), ('on', .5, 1.0)], duration=2.0)
    >>> ctx = prompt_context(tt, song_title='Demo', extra={'tempo_bpm': 128})
    >>> ctx['song_title'], ctx['measurements']['tempo_bpm']
    ('Demo', 128)
    >>> ctx['sections'][0]['lines'][0]['text']
    'hold on'
    >>> ctx['timing']['measured'], sorted(ctx['vocabularies'])[0]
    (True, 'archetypes')
    """
    raw = _raw_signals(timed_text)
    return {
        "song_title": song_title,
        "duration_s": raw["duration_s"],
        "timing": {
            "source": raw["timing_source"],
            "measured": raw["timing_measured"],
            "note": (
                "Word onsets were measured — word/syllable quantisation is honest."
                if raw["timing_measured"]
                else "Word times were INTERPOLATED inside lines, not measured. "
                "Ask for 'line' or 'beat' quantisation; word-level sync would be "
                "a claim the timing cannot support."
            ),
        },
        "measurements": {
            "tempo_bpm": None,
            **{k: v for k, v in (extra or {}).items()},
        },
        "signals": {k: v for k, v in raw.items() if not k.startswith("timing_")},
        "signals_normalised": _normalised(raw),
        "sections": [
            {
                "label": section.label,
                "n_lines": len(section.lines),
                "start_s": round(section.start, 3),
                "end_s": round(section.end, 3),
                "lines": [
                    {"index": line.index, "text": line.text, "n_words": len(line.words)}
                    for line in section.lines
                ],
            }
            for section in timed_text.sections
        ],
        "vocabularies": spec_mod.vocabulary(),
    }


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------

#: The vocabularies are already in the system prompt; sending them again in the
#: context payload would be a second copy on the wire and a second thing to
#: drift. Dropped from the user turn, kept in :func:`prompt_context`'s return so
#: a UI or a skill that never builds messages still has them.
_CONTEXT_OMIT = ("vocabularies",)

_REFERENCE_INSTRUCTION = (
    "A reference image is attached. Describe its LAYOUT as an archetype from the "
    "vocabulary plus, where one applies, a named shape — never as coordinates, a "
    "grid, a bounding box or a percentage of the frame. Take from it the palette, "
    "the typographic weight and case, the density, and which archetype it is an "
    "example of. If its layout is not expressible as one of the archetypes, say "
    "so in the rationale and choose the nearest one."
)


def _image_block(reference_image: "str | Path | Mapping[str, Any]") -> dict[str, Any]:
    """An Anthropic image content block, from a path or a ready-made block."""
    if isinstance(reference_image, Mapping):
        return dict(reference_image)
    path = Path(reference_image)
    media_type = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def build_messages(
    context: Mapping[str, Any],
    *,
    persona: "str | Persona | None" = None,
    reference_image: "str | Path | Mapping[str, Any] | None" = None,
) -> list[dict[str, Any]]:
    """Assemble the director's messages: prompt files plus this song's context.

    The system prompt is emitted as *two* messages — the shared director prompt
    then the persona — so a transactional caller can cache the first across all
    ``n`` options while only the second varies. :func:`anthropic_llm` does
    exactly that.

    The context travels as a fenced JSON block, which is also how
    :func:`heuristic_llm` reads it back: the zero-cost director and the model are
    given literally the same message rather than two views of one idea.

    >>> from muvid.lyricvid.timed_text import from_words
    >>> tt = from_words([('we', 0.0, .4), ('go', .4, .9)], duration=1.0)
    >>> msgs = build_messages(prompt_context(tt), persona='minimalist')
    >>> [m['role'] for m in msgs]
    ['system', 'system', 'user']
    >>> 'The Minimalist' in msgs[1]['content']
    True
    >>> _context_from_messages(msgs)['signals']['n_words']
    2
    >>> img = build_messages(prompt_context(tt),
    ...                      reference_image={'type': 'image', 'source': {}})
    >>> [b['type'] for b in img[-1]['content']]
    ['image', 'text']
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": director_prompt()}]
    if persona is not None:
        messages.append({"role": "system", "content": resolve_persona(persona).block()})

    payload = {k: v for k, v in context.items() if k not in _CONTEXT_OMIT}
    if persona is not None:
        payload["persona"] = resolve_persona(persona).slug
    body = (
        "Here is the song. Choose a treatment for it.\n\n"
        f"```json\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n```"
    )
    if reference_image is None:
        messages.append({"role": "user", "content": body})
    else:
        messages.append(
            {
                "role": "user",
                "content": [
                    _image_block(reference_image),
                    {"type": "text", "text": f"{_REFERENCE_INSTRUCTION}\n\n{body}"},
                ],
            }
        )
    return messages


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text"
        )
    return ""


def _context_from_messages(messages: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Recover the song context from the messages :func:`build_messages` made."""
    found: dict[str, Any] | None = None
    for message in messages:
        for fence in _JSON_FENCE.finditer(_message_text(message)):
            candidate = json.loads(fence.group(1))
            if isinstance(candidate, dict) and "signals" in candidate:
                found = candidate
    if found is None:
        raise ValueError(
            "no song context found in these messages — build them with "
            "build_messages() so the context travels in a ```json block"
        )
    return found


# --------------------------------------------------------------------------
# the heuristic director — the default, and a real one
# --------------------------------------------------------------------------

#: Section labels that carry the lift, matched as substrings, case-insensitively.
LIFT_LABELS = ("chorus", "hook", "refrain", "drop", "tag")


def _is_lift(label: str) -> bool:
    low = label.lower()
    return any(word in low for word in LIFT_LABELS)


def _descriptor(raw: Mapping[str, Any], norm: Mapping[str, float]) -> str:
    """A phrase describing the song, for the mood line."""
    pace = (
        "fast"
        if norm["density"] > 0.6
        else ("unhurried" if norm["density"] < 0.3 else "steady")
    )
    shape = (
        "highly repetitive"
        if norm["repetition"] > 0.4
        else ("through-written" if norm["repetition"] < 0.15 else "part-repeating")
    )
    return (
        f"{pace} and {shape} — {raw['n_words']} words across "
        f"{raw['n_lines']} line{'s' if raw['n_lines'] != 1 else ''} "
        f"in {raw['duration_s']:.0f}s"
    )


def heuristic_director(
    context: Mapping[str, Any], *, persona: "str | Persona | None" = None
) -> dict[str, Any]:
    """Pick a treatment from measurement alone. No model, no network, no key.

    The decision is two linear scores and one rule. :data:`ARCHETYPE_CHARACTER`
    asks what the lyric *is* — repetitive, long-lined, shaped, image-dominated,
    sparse and various. :data:`ARCHETYPE_CAPACITY_WPS` asks how fast it goes
    past. The persona's ``legibility`` decides how much each of those counts,
    and its ``prefers`` order breaks the remaining ties — which is why six
    personas over one song give six genuinely different treatments rather than
    six samples of one. ``context['avoid_archetypes']`` (set by
    :func:`propose_treatments`) then keeps a set of options from converging.

    Sections are honoured when the lyrics carry real labels: a chorus-ish
    section gets the runner-up archetype as a lift. In that case *every* section
    gets its own scene and none is ``"*"``, because the compiler renders a
    ``"*"`` scene over the whole song and the words would double up.

    >>> from muvid.lyricvid.timed_text import from_words
    >>> words = [(w, i * .5, i * .5 + .45) for i, w in
    ...          enumerate('hold on hold on hold on'.split())]
    >>> d = heuristic_director(prompt_context(from_words(words, duration=4.0)),
    ...                        persona='karaoke_host')
    >>> d['scenes'][0]['archetype']
    'karaoke_wipe'
    >>> d['direction']['palette']['bg']
    '#101828'
    >>> spec_mod.validate(spec_mod.TreatmentSpec.from_dict(d))
    []
    """
    who = resolve_persona(persona) if persona is not None else list_personas()[0]
    raw = dict(context.get("signals") or {})
    norm = dict(context.get("signals_normalised") or {})
    wps = float(raw.get("words_per_second", 0.0) or 0.0)
    measured = bool((context.get("timing") or {}).get("measured", True))
    avoid = set(context.get("avoid_archetypes") or ())

    ranked = _rank_archetypes(norm, persona=who, words_per_second=wps)
    ordered = [a for a, _ in ranked]
    primary = next((a for a in ordered if a not in avoid), ordered[0])
    secondary = next((a for a in ordered if a not in avoid and a != primary), primary)

    # Timing policy: never claim sync the measurement cannot support.
    quantize = who.quantize
    if not measured and quantize in {"word", "syllable"}:
        quantize = "line"

    sections = context.get("sections") or []
    labels = [s.get("label", "*") for s in sections]
    per_section = len({l.lower() for l in labels}) > 1 and "*" not in labels

    def scene(archetype: str, applies_to: tuple[str, ...]) -> dict[str, Any]:
        return {
            "applies_to": list(applies_to),
            "archetype": archetype,
            "motion": who.motion[0] if who.motion else "fade",
            "persistence": _persistence_for(archetype, who.persistence),
            "timing": {
                "quantize_to": quantize,
                "cut_style": who.cut_style,
                "attack_s": who.attack_s,
                "lead_s": who.lead_s,
            },
            **(
                {"shape": {"kind": "named", "value": _shape_for(raw)}}
                if archetype == "shape_fill"
                else {}
            ),
        }

    if per_section:
        scenes = [
            scene(secondary if _is_lift(label) else primary, (label,))
            for label in dict.fromkeys(labels)
        ]
    else:
        scenes = [scene(primary, ("*",))]

    lifts = [s for s in scenes if s["archetype"] == secondary and secondary != primary]
    rationale = (
        f"Sung at {wps:.1f} words/s with "
        f"{100 * float(raw.get('repeated_line_fraction', 0)):.0f}% repeated lines and "
        f"{raw.get('mean_line_chars', 0):.0f}-character lines. "
        f"{primary!r} carries that "
        f"(capacity {ARCHETYPE_CAPACITY_WPS.get(primary, 2.5):.1f} words/s), and it is "
        f"where {who.name} would start: {who.mood}."
    )
    if lifts:
        rationale += f" The chorus lifts to {secondary!r} so the sections read apart."

    return {
        "title": context.get("song_title", ""),
        "direction": {
            "mood": f"{who.mood}; {_descriptor(raw, norm)}",
            "rationale": rationale,
            "palette": dict(who.palette) or {},
            "typography": dict(who.typography) or {},
            "motion_vocabulary": list(who.motion) or ["fade"],
        },
        "scenes": scenes,
    }


def _persistence_for(archetype: str, preferred: str) -> str:
    """Keep a persistence policy that would defeat the archetype from shipping.

    A ``concrete_page`` that clears is not a page; a ``shape_fill`` that clears
    never fills; ``text_on_path`` places every line on the same arc, so holding
    stacks them on top of each other.
    """
    if archetype in {"concrete_page", "shape_fill"} and preferred.startswith("clear"):
        return "dim"
    if archetype == "text_on_path" and preferred == "hold":
        return "clear_on_line"
    return preferred


def _shape_for(raw: Mapping[str, Any]) -> str:
    """A named outline for ``shape_fill``. The model may name one; it never draws one."""
    return "heart" if raw.get("top_content_word_share", 0.0) > 0.05 else "circle"


def heuristic_llm() -> Llm:
    """The zero-cost director, as an ``llm`` seam implementation.

    Conforming to the seam rather than sitting beside it means the default path
    and the production path run the *same* code in :func:`propose_treatments` —
    same messages, same schema, same repair, same meta — so the free path is
    exercised by every test the paid one would be.

    >>> from muvid.lyricvid.timed_text import from_words
    >>> tt = from_words([('all', 0.0, .5), ('night', .5, 1.2)], duration=2.0)
    >>> call = heuristic_llm()
    >>> out = call(build_messages(prompt_context(tt), persona='club_vj'),
    ...            output_schema())
    >>> out['scenes'][0]['archetype']
    'one_word_centred'
    """

    def call(messages, schema):  # noqa: ARG001 - the schema is the model's constraint
        context = _context_from_messages(messages)
        return heuristic_director(context, persona=context.get("persona"))

    return call


# --------------------------------------------------------------------------
# the production seam
# --------------------------------------------------------------------------

#: Default model. Opus 5's exact id, with no date suffix — the ids are complete
#: as written and a date-suffixed variant is not a real model.
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 8000

#: First-party API list price, USD per million tokens, as of 2026-06. Prices
#: drift, so a model absent from this table prices as **unknown** rather than as
#: zero: muvid's budget gate is conjunctive and an unpriceable call must force
#: approval, never encode as ``0.0``.
MODEL_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def _price(model: str, usage: Mapping[str, int]) -> tuple[float | None, bool]:
    prices = MODEL_PRICES_USD_PER_MTOK.get(model)
    if prices is None:
        return None, True
    per_in, per_out = prices
    cost = (
        usage.get("input_tokens", 0) * per_in + usage.get("output_tokens", 0) * per_out
    ) / 1_000_000
    return round(cost, 6), False


def _split_system(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Leading ``system`` messages become the top-level ``system`` blocks.

    The first block carries ``cache_control``: across ``n`` options the director
    prompt is byte-identical and only the persona block after it changes, so the
    cached prefix survives every option but the first.
    """
    system: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system" and not turns:
            system.append({"type": "text", "text": message["content"]})
        else:
            turns.append(dict(message))
    if system:
        system[0] = {**system[0], "cache_control": {"type": "ephemeral"}}
    return system, turns


def anthropic_llm(
    *,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
    client: Any | None = None,
) -> Llm:
    """The production ``llm`` seam: one structured-output call to Claude.

    The response is constrained by :func:`output_schema` through
    ``output_config.format``, so the returned text is guaranteed-parseable JSON
    in the treatment's shape — the model cannot invent an archetype or hand back
    prose, which removes the whole retry-on-malformed-JSON layer.

    Usage rides back on the returned mapping under ``"_usage"`` (and accumulates
    on the callable's own ``.usage`` list, for a caller that drives the seam
    directly). It carries ``estimated_cost_usd`` **and** ``has_unknown_costs``,
    because a price this module cannot determine is unknown, not zero.

    ``anthropic`` is imported inside the call, not at module scope: ``import
    muvid`` must not pull an SDK, and this seam is optional by construction.

    :param client: an already-built SDK client, mostly for tests. When given,
        ``api_key`` is ignored.
    """

    def call(messages, schema):
        import anthropic

        cl = client
        if cl is None:
            cl = anthropic.Anthropic(**({"api_key": api_key} if api_key else {}))
        system, turns = _split_system(messages)
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": dict(schema)}
        }
        if effort is not None:
            output_config["effort"] = effort
        response = cl.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=turns,
            output_config=output_config,
        )
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            raise RuntimeError(
                "the model declined this request "
                f"(category={getattr(details, 'category', None)!r})"
            )
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)

        raw_usage = getattr(response, "usage", None)
        counted = {
            name: int(getattr(raw_usage, name, 0) or 0)
            for name in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        }
        cost, unknown = _price(model, counted)
        usage = {
            "model": model,
            **counted,
            "estimated_cost_usd": cost,
            "has_unknown_costs": unknown,
        }
        call.usage.append(usage)
        data["_usage"] = usage
        return data

    call.usage = []
    return call


# --------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------

#: Reserved key in ``scenes[0].params`` carrying this module's provenance.
#: :class:`~muvid.lyricvid.spec.TreatmentSpec` is frozen and has no ``meta``
#: field, and ``params`` is explicitly free-form, survives ``to_dict`` /
#: ``from_dict`` / ``repair`` untouched, and is read key-by-key by the
#: archetypes — so an extra key is inert at render time and durable on disk.
DIRECTOR_META_KEY = "_director"


def director_meta(spec: spec_mod.TreatmentSpec) -> dict[str, Any]:
    """Read this module's provenance back off a spec it produced.

    ``source`` is ``'heuristic'``, ``'llm'`` or ``'heuristic-fallback'`` — the
    last meaning a model was asked and its answer could not be used. That case
    also carries ``fallback_reason`` and appends it to the rationale, because a
    degraded result that presents as an intended one is the failure muvid keeps
    paying for elsewhere.

    >>> from muvid.lyricvid.timed_text import from_words
    >>> tt = from_words([('go', 0.0, .5)], duration=1.0)
    >>> director_meta(propose_treatments(tt, n=1)[0])['source']
    'heuristic'
    """
    if not spec.scenes:
        return {}
    return dict(spec.scenes[0].params.get(DIRECTOR_META_KEY) or {})


def _with_meta(
    spec: spec_mod.TreatmentSpec, meta: Mapping[str, Any]
) -> spec_mod.TreatmentSpec:
    first = spec.scenes[0]
    params = {**dict(first.params), DIRECTOR_META_KEY: dict(meta)}
    return replace(spec, scenes=(replace(first, params=params), *spec.scenes[1:]))


def _resolve_personas(
    personas: "Sequence[str | Persona] | None", n: int
) -> list[Persona]:
    pool = [resolve_persona(p) for p in personas] if personas else list_personas()
    if not pool:
        raise ValueError("no personas available")
    return [pool[i % len(pool)] for i in range(max(1, n))]


def propose_treatments(
    timed_text: TimedText,
    *,
    n: int = 3,
    song_title: str = "",
    reference_image: "str | Path | Mapping[str, Any] | None" = None,
    llm: Llm | None = None,
    personas: "Sequence[str | Persona] | None" = None,
) -> list[spec_mod.TreatmentSpec]:
    """Propose ``n`` treatments for one song. Every one of them is renderable.

    ``llm=None`` is the working default, not a stub: :func:`heuristic_llm` reads
    the lyrics and directs from measurement, so the subgenre runs end to end with
    zero AI and zero cost. Pass :func:`anthropic_llm` to upgrade the taste; pass
    anything ``(messages, schema) -> dict`` to substitute your own.

    Each option is directed by a different :class:`Persona` and is told which
    archetypes the earlier options took, which is what makes ``n`` options
    genuinely different rather than ``n`` samples of one.

    Every returned spec has been through :func:`~muvid.lyricvid.spec.coerce`, so
    ``validate`` is empty by construction; the substitutions repair made are
    recorded in :func:`director_meta` under ``repairs``.

    >>> from muvid.lyricvid.timed_text import from_words
    >>> words = 'we are the champions my friends'.split()
    >>> tt = from_words([(w, i * .45, i * .45 + .4) for i, w in enumerate(words)],
    ...                 duration=4.0)
    >>> options = propose_treatments(tt, n=3, song_title='Demo')
    >>> [s.scenes[0].archetype for s in options]
    ['stacked_lines', 'scatter', 'concrete_page']
    >>> [spec_mod.validate(s) for s in options]
    [[], [], []]
    >>> options[0].title
    'Demo'

    A model that returns something unusable degrades to the heuristic, and says
    so rather than passing off a fallback as a decision:

    >>> import warnings
    >>> with warnings.catch_warnings():
    ...     warnings.simplefilter('ignore')
    ...     broken = propose_treatments(tt, n=1, llm=lambda m, s: 'not a treatment')
    >>> director_meta(broken[0])['source']
    'heuristic-fallback'
    >>> director_meta(broken[0])['fallback_reason']
    'the director returned str, not a mapping'
    >>> 'fell back to the heuristic director' in broken[0].direction.rationale
    True
    """
    context = prompt_context(timed_text, song_title=song_title)
    schema = output_schema()
    call = heuristic_llm() if llm is None else llm
    chosen = _resolve_personas(personas, n)

    out: list[spec_mod.TreatmentSpec] = []
    taken: list[str] = []
    for who in chosen:
        request = dict(context, avoid_archetypes=list(dict.fromkeys(taken)))
        messages = build_messages(request, persona=who, reference_image=reference_image)
        source = "heuristic" if llm is None else "llm"
        reason: str | None = None
        raw: Any = None
        try:
            raw = call(messages, schema)
        except Exception as exc:  # noqa: BLE001 - a caller-supplied seam, and the
            # whole contract of this function is that a video still gets made. The
            # failure is not swallowed: it is named in the meta, appended to the
            # human-facing rationale, and warned about.
            if llm is None:
                raise
            reason = f"{type(exc).__name__}: {exc}"
        if reason is None and not isinstance(raw, Mapping):
            reason = f"the director returned {type(raw).__name__}, not a mapping"

        usage = dict(raw.pop("_usage", {})) if isinstance(raw, dict) else {}
        if reason is not None:
            warnings.warn(
                f"lyricvid director: {reason} — falling back to the heuristic "
                f"director for persona {who.slug!r}",
                RuntimeWarning,
                stacklevel=2,
            )
            raw = heuristic_director(request, persona=who)
            source = "heuristic-fallback"

        spec, repairs = spec_mod.coerce(raw)
        if spec_mod.validate(spec) and source != "heuristic-fallback":
            reason = "; ".join(spec_mod.validate(spec))
            warnings.warn(
                f"lyricvid director: unrepairable treatment ({reason}) — falling "
                f"back to the heuristic director for persona {who.slug!r}",
                RuntimeWarning,
                stacklevel=2,
            )
            spec, repairs = spec_mod.coerce(heuristic_director(request, persona=who))
            source = "heuristic-fallback"

        if not spec.title:
            spec = replace(spec, title=song_title)
        if reason is not None:
            spec = replace(
                spec,
                direction=replace(
                    spec.direction,
                    rationale=(
                        f"{spec.direction.rationale} "
                        f"[fell back to the heuristic director: {reason}]"
                    ).strip(),
                ),
            )
        meta: dict[str, Any] = {
            "source": source,
            "persona": who.slug,
            "persona_name": who.name,
            "signals": context["signals_normalised"],
            "repairs": repairs,
        }
        if reason is not None:
            meta["fallback_reason"] = reason
        if usage:
            meta["usage"] = usage
        spec = _with_meta(spec, meta)

        taken.extend(s.archetype for s in spec.scenes)
        out.append(spec)
    return out


# --------------------------------------------------------------------------
# ranking — ordering N options without a human
# --------------------------------------------------------------------------

#: What a good option is made of. Legibility dominates because an unreadable
#: lyric video has failed at the one thing it is for; contrast is second because
#: it is the failure a palette can hide until render time.
RANK_WEIGHTS: dict[str, float] = {
    "legibility": 0.40,
    "contrast": 0.25,
    "coverage": 0.20,
    "coherence": 0.15,
}


def _combine(parts: "dict[str, float]") -> float:
    """Combine the rank terms so a disqualifying one cannot be averaged away.

    A weighted **geometric** mean, not an arithmetic one. The distinction is the
    whole point: a treatment whose text is invisible against its background
    (contrast 0.03) but which is otherwise fine scored 0.71 out of 1.0 under an
    arithmetic mean — three good terms hid one fatal one, and the ranker
    happily recommended an unreadable video.

    This is the same lesson muvid's budget gate records as "a number alone is
    not a decision": a summary statistic that can hide a disqualifying failure
    is worse than no summary at all. The geometric mean goes to zero as any
    term goes to zero, and behaves like the arithmetic mean when the terms are
    close together, which is the common case.

    >>> round(_combine({'legibility': 1.0, 'contrast': 1.0,
    ...                 'coverage': 1.0, 'coherence': 1.0}), 3)
    1.0
    >>> round(_combine({'legibility': 0.8, 'contrast': 0.8,
    ...                 'coverage': 0.8, 'coherence': 0.8}), 3)
    0.8
    >>> round(_combine({'legibility': 1.0, 'contrast': 0.03,
    ...                 'coverage': 1.0, 'coherence': 1.0}), 3)
    0.416
    """
    import math

    total = 0.0
    for name, weight in RANK_WEIGHTS.items():
        # A zero term must sink the score, not raise a math-domain error.
        total += weight * math.log(max(parts[name], 1e-6))
    return math.exp(total)


#: Contrast ratio a foreground should clear against its background, and the
#: lower bar for an accent, which appears in short bursts on large type.
TARGET_FG_CONTRAST = 4.5
TARGET_ACCENT_CONTRAST = 3.0
#: A ``dim`` below this has receded out of existence rather than into the back.
MIN_DIM_CONTRAST = 1.6

#: Quantising to a word whose onset was interpolated rather than measured is a
#: claim the timing cannot support; it looks like drift, not like style.
UNMEASURED_QUANTIZE_PENALTY = 0.6

#: Pairings the renderer honours and that fight each other. Not "unusual" —
#: self-defeating: the archetype's whole point is undone by the policy.
INCOHERENT_PAIRS: dict[tuple[str, str], str] = {
    ("concrete_page", "clear_on_line"): "a page that clears is not a page",
    ("concrete_page", "clear_on_section"): "a page that clears is not a page",
    ("shape_fill", "clear_on_line"): "the shape never gets to fill",
    ("shape_fill", "clear_on_section"): "the shape never gets to fill",
    ("text_on_path", "hold"): "every line rides the same arc and they overlap",
    ("scatter", "hold"): "the frame saturates and nothing reads",
}

#: Above two archetypes a treatment stops reading as one treatment.
COHERENT_ARCHETYPE_BUDGET = 2


def _relative_luminance(colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(a: str, b: str) -> float:
    """WCAG-style contrast ratio between two ``#rrggbb`` colours.

    >>> round(_contrast_ratio('#ffffff', '#000000'), 1)
    21.0
    >>> round(_contrast_ratio('#777777', '#777777'), 1)
    1.0
    """
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _lines_covered(scene: spec_mod.Scene, timed_text: TimedText) -> int:
    if "*" in scene.applies_to:
        return sum(len(s.lines) for s in timed_text.sections)
    wanted = {a.lower() for a in scene.applies_to}
    return sum(len(s.lines) for s in timed_text.sections if s.label.lower() in wanted)


def _score_legibility(
    spec: spec_mod.TreatmentSpec, timed_text: TimedText, wps: float
) -> float:
    measured = timed_text.measured
    total = weighted = 0.0
    for scene in spec.scenes:
        share = max(1, _lines_covered(scene, timed_text))
        value = _legibility_fit(scene.archetype, wps)
        if not measured and scene.timing.quantize_to in {"word", "syllable"}:
            value *= UNMEASURED_QUANTIZE_PENALTY
        weighted += value * share
        total += share
    return weighted / total if total else 0.0


def _score_coverage(spec: spec_mod.TreatmentSpec, timed_text: TimedText) -> float:
    lines = sum(len(s.lines) for s in timed_text.sections)
    if not lines:
        return 1.0
    covered = set()
    for scene in spec.scenes:
        if "*" in scene.applies_to:
            return 1.0
        covered.update(a.lower() for a in scene.applies_to)
    hit = sum(len(s.lines) for s in timed_text.sections if s.label.lower() in covered)
    return hit / lines


def _score_contrast(spec: spec_mod.TreatmentSpec) -> float:
    palette = spec.direction.palette
    fg = _contrast_ratio(palette.fg, palette.bg)
    accent = _contrast_ratio(palette.accent, palette.bg)
    dim = _contrast_ratio(palette.dim, palette.bg)
    fg_score = _clamp((fg - 1) / (TARGET_FG_CONTRAST - 1))
    accent_score = _clamp((accent - 1) / (TARGET_ACCENT_CONTRAST - 1))
    if dim >= fg:
        dim_score = 0.0  # the recessed state is louder than the live one
    elif dim < MIN_DIM_CONTRAST:
        dim_score = _clamp((dim - 1) / (MIN_DIM_CONTRAST - 1))
    else:
        dim_score = 1.0
    return 0.60 * fg_score + 0.25 * accent_score + 0.15 * dim_score


def _score_coherence(spec: spec_mod.TreatmentSpec) -> tuple[float, list[str]]:
    notes: list[str] = []
    declared = set(spec.direction.motion_vocabulary)
    used = [s.motion for s in spec.scenes]
    motion_score = sum(1 for m in used if m in declared) / len(used) if used else 1.0
    if motion_score < 1:
        notes.append("a scene uses a motion the direction does not declare")

    kinds = {s.archetype for s in spec.scenes}
    spread_score = _clamp(
        1 - max(0, len(kinds) - COHERENT_ARCHETYPE_BUDGET) / COHERENT_ARCHETYPE_BUDGET
    )
    if len(kinds) > COHERENT_ARCHETYPE_BUDGET:
        notes.append(f"{len(kinds)} archetypes in one treatment")

    bad = [
        INCOHERENT_PAIRS[key]
        for s in spec.scenes
        if (key := (s.archetype, s.persistence)) in INCOHERENT_PAIRS
    ]
    pair_score = 1 - len(bad) / len(spec.scenes) if spec.scenes else 1.0
    notes.extend(dict.fromkeys(bad))

    prose = (bool(spec.direction.mood) + bool(spec.direction.rationale)) / 2
    if prose < 1:
        notes.append("no mood or no rationale for a human to choose by")
    return statistics.fmean([motion_score, spread_score, pair_score, prose]), notes


def rank_treatments(
    specs: Sequence[spec_mod.TreatmentSpec], timed_text: TimedText
) -> list[tuple[spec_mod.TreatmentSpec, float, str]]:
    """Order options without a human. Deterministic, and it explains itself.

    Four terms, combined by :func:`_combine` (a weighted *geometric* mean, so a
    single disqualifying term cannot be averaged away by three good ones):
    **legibility** (the song's
    word rate against the archetype's capacity, penalised for word-level
    quantisation the timing cannot support), **contrast** (WCAG-style ratios for
    ``fg``/``accent``/``dim``), **coverage** (does every section actually get a
    scene) and **coherence** (declared motions, archetype count, and pairings
    that defeat themselves).

    Returns ``(spec, score, why)``, best first, with ties broken on the leading
    archetype so the order is stable across runs.

    >>> from muvid.lyricvid.timed_text import from_words
    >>> tt = from_words([(w, i * .4, i * .4 + .35) for i, w in
    ...                  enumerate('one two three four'.split())], duration=3.0)
    >>> readable = spec_mod.TreatmentSpec(
    ...     direction=spec_mod.Direction(mood='m', rationale='r'),
    ...     scenes=(spec_mod.Scene(archetype='karaoke_wipe', motion='fade'),))
    >>> murky = spec_mod.TreatmentSpec(
    ...     direction=spec_mod.Direction(
    ...         mood='m', rationale='r',
    ...         palette=spec_mod.Palette(bg='#202020', fg='#2a2a2a',
    ...                                  accent='#242424', dim='#606060')),
    ...     scenes=(spec_mod.Scene(archetype='scatter', motion='fade'),))
    >>> ordered = rank_treatments([murky, readable], tt)
    >>> [round(score, 3) for _, score, _ in ordered]
    [1.0, 0.358]
    >>> ordered[0][0] is readable
    True
    >>> ordered[1][2]
    'legibility 0.70 · contrast 0.03 · coverage 1.00 · coherence 1.00 — weakest is contrast'
    """
    wps = _line_words_per_second(timed_text)
    ranked: list[tuple[spec_mod.TreatmentSpec, float, str]] = []
    for spec in specs:
        parts = {
            "legibility": _score_legibility(spec, timed_text, wps),
            "contrast": _score_contrast(spec),
            "coverage": _score_coverage(spec, timed_text),
        }
        coherence, notes = _score_coherence(spec)
        parts["coherence"] = coherence
        score = _combine(parts)
        why = " · ".join(f"{name} {parts[name]:.2f}" for name in RANK_WEIGHTS)
        weakest = min(RANK_WEIGHTS, key=lambda name: parts[name])
        if parts[weakest] < 1.0:
            why += f" — weakest is {weakest}"
            if weakest == "legibility":
                worst = min(
                    spec.scenes, key=lambda s: _legibility_fit(s.archetype, wps)
                )
                why += (
                    f" ({worst.archetype} at {wps:.1f} w/s against a capacity of "
                    f"{ARCHETYPE_CAPACITY_WPS.get(worst.archetype, 2.5):.1f})"
                )
            elif notes:
                why += f" ({notes[0]})"
        ranked.append((spec, round(score, 6), why))
    ranked.sort(key=lambda row: (-row[1], row[0].scenes[0].archetype))
    return ranked
