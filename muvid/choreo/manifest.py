"""The choreo subgenre's manifest — visual music, declared without importing it.

Deliberately stdlib-only and free of any import from the rest of
:mod:`muvid.choreo`: this module is what a catalogue, a UI form, an MCP tool
definition or an LLM choosing among installed subgenres reads, and none of them
should pay for numpy or an ffmpeg probe to do it. The renderer is named as a
string and imported only when a render actually runs — the same shape as
:mod:`muvid.lyricvid.manifest`, which is the reference plugin.

What ``choreo`` is: event-driven visual music in the Fischinger / McLaren /
Gondry lineage. Audio in, nothing else. Discrete musical **events** (onsets, per
frequency band) become **objects** with persistence on screen, and **sections**
become different scene arrangements. It is choreographed and structural, not a
spectrum readout — objects appear ON events and then live — which is what makes
it a different thing from :mod:`muvid.visualize`.
"""

from __future__ import annotations

from muvid.subgenres import Example, Subgenre

SLUG = "choreo"

#: The archetype names, restated here (rather than imported from ``spec``) so
#: the manifest module stays free of every other choreo module. A test pins
#: this tuple against ``spec.ARCHETYPES`` and ``scene.ARCHETYPE_FNS`` so the
#: three cannot drift.
ARCHETYPE_NAMES = ("fischinger", "star_guitar", "mclaren", "swarm")

#: The beat-grid sources ``analysis`` knows. ``auto`` tries ``mixing`` (librosa,
#: in the ``scoring`` extra) and falls back to the built-in numpy estimate only
#: when librosa is absent — a *missing* dependency, never a failing one.
BEAT_SOURCES = ("auto", "mixing", "numpy")

_INPUTS = {
    "type": "object",
    "required": ["audio"],
    "additionalProperties": False,
    "properties": {
        "audio": {
            "type": "string",
            "description": "Path to the song. The only required input.",
        },
        "cover": {
            "type": "string",
            "description": "Optional image whose colours seed the palette. Nothing "
            "of it is drawn; it is a palette source only.",
        },
    },
}

_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "treatment": {
            "type": "object",
            "description": "A choreo treatment spec: direction {palette, background, "
            "density} + scenes[] {applies_to, archetype, params}. Omitted, a "
            "one-scene treatment is built from `archetype`.",
        },
        "archetype": {
            "type": "string",
            "enum": list(ARCHETYPE_NAMES),
            "description": "Shortcut for a one-scene treatment. fischinger: geometric "
            "shapes ignite per onset on a grid. star_guitar: a side-scrolling "
            "landscape whose object spacing IS the rhythm. mclaren: short-lived "
            "white scratches on black. swarm: particles whose count and speed "
            "follow band energy.",
        },
        "seed": {
            "type": "integer",
            "minimum": 0,
            "default": 0,
            "description": "The only source of variation. Same audio + same seed "
            "= the same video, byte for byte at the frame level.",
        },
        "beat_source": {
            "type": "string",
            "enum": list(BEAT_SOURCES),
            "default": "auto",
        },
        "strict": {
            "type": "boolean",
            "default": False,
            "description": "Refuse a treatment that needed repairs instead of "
            "rendering the repaired one.",
        },
        "width": {"type": "integer", "default": 1920, "minimum": 16},
        "height": {"type": "integer", "default": 1080, "minimum": 16},
        "fps": {"type": "integer", "default": 30, "minimum": 1, "maximum": 60},
    },
}

CHOREO = Subgenre(
    slug=SLUG,
    title="Choreo (visual music)",
    description=(
        "Turn a song into abstract visual music, the Fischinger / McLaren / "
        "'Star Guitar' way: onsets in three frequency bands become objects that "
        "appear on the event and then live — shapes igniting on a grid, a "
        "scrolling landscape whose spacing is the rhythm, scratches on black, or "
        "a swarm — and the song's sections change the arrangement. No lyrics, no "
        "footage, no AI: audio in, nothing else. Deterministic given a seed."
    ),
    render="muvid.choreo.pipeline:render",
    inputs=_INPUTS,
    params_schema=_PARAMS,
    produces="video/mp4",
    intake_kinds=(
        "visual music",
        "abstract",
        "visualizer",
        "instrumental",
        "electronic",
        "fischinger",
        "star guitar",
        "animation",
        "song",
    ),
    #: None means genuinely free: nothing here spends anything.
    cost_profile=None,
    api_versions=("1",),
    examples=(
        Example(
            description="The simplest thing that works: a song, the default "
            "(fischinger) archetype, seed 0.",
            params={},
        ),
        Example(
            description="Star Guitar: a landscape scrolling left at constant speed; "
            "bass hits are poles, mids are buildings, highs are wires. The sky "
            "changes colour with each section.",
            params={"archetype": "star_guitar"},
        ),
        Example(
            description="McLaren: white scratches on black, one per onset, gone "
            "within a fraction of a beat. Sparse, so only the strong hits mark.",
            params={
                "treatment": {
                    "direction": {
                        "density": "sparse",
                        "palette": {"bg": "#000000", "fg": "#ffffff"},
                    },
                    "scenes": [{"archetype": "mclaren"}],
                }
            },
        ),
        Example(
            description="Two archetypes by section: a swarm in the quiet parts, "
            "Fischinger shapes on the loud ones.",
            params={
                "treatment": {
                    "direction": {"background": "gradient", "density": "dense"},
                    "scenes": [
                        {"applies_to": ["*"], "archetype": "swarm"},
                        {
                            "applies_to": ["high"],
                            "archetype": "fischinger",
                            "params": {"columns": 8, "rows": 4},
                        },
                    ],
                },
                "seed": 7,
            },
        ),
    ),
)
