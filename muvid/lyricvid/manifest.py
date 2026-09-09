"""The lyric-video subgenre's manifest — muvid's own first plugin.

Deliberately stdlib-only and free of any import from the rest of
:mod:`muvid.lyricvid`: this module is what a catalogue, a UI form, an MCP tool
definition or an LLM choosing among installed subgenres reads, and none of them
should pay for pysubs2, numpy or Playwright to do it. The renderer is named as
a string and imported only when a render actually runs.

A third-party plugin should look exactly like this file.
"""

from __future__ import annotations

from muvid.subgenres import Example, Subgenre

SLUG = "lyric-video"

#: Hyphenated, matching the newer ``music-visualizer``. muvid's two existing
#: genre slugs disagree on separator and the design record asks that the next
#: one choose knowingly — this is the choice, and it is a persisted path
#: segment and a public contract value from the moment it ships.

_INPUTS = {
    "type": "object",
    "required": ["audio"],
    "additionalProperties": False,
    "properties": {
        "audio": {
            "type": "string",
            "description": "Path to the song. The only required input.",
        },
        "lyrics": {
            "type": "string",
            "description": "Path to the lyrics. muvid's lyrics markdown "
            "([section] headers, one line per sung line) or plain text. "
            "Omitted, the words are transcribed from the audio.",
        },
        "subtitles": {
            "type": "string",
            "description": "Path to an .srt or .lrc. Enhanced .lrc carries real "
            "per-word times; plain .srt/.lrc gives line times only, and words "
            "inside a line are then interpolated.",
        },
        "project": {
            "type": "string",
            "description": "Path to an existing muvid project, to reuse the "
            "word timings it has already aligned rather than re-deriving them.",
        },
    },
}

_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "treatment": {
            "type": "object",
            "description": "A muvid lyric-video treatment spec. Omitted, one is "
            "proposed — with no AI and no cost unless an LLM is configured.",
        },
        "renderer": {
            "type": "string",
            "enum": ["auto", "ass", "web"],
            "default": "auto",
            "description": "auto: ass where this ffmpeg can burn subtitles in "
            "(needs a libass build), else web. ass: frame-exact, no browser, "
            "leaves an editable .ass subtitle file — fails loudly if libass is "
            "absent rather than quietly rendering something else. web: headless "
            "Chromium, for effects ASS cannot express.",
        },
        "title": {"type": "string"},
        "persona": {
            "type": "string",
            "description": "Which art-director persona proposes the treatment. "
            "Omitted, one is chosen.",
        },
        "aligner": {
            "type": "string",
            "description": "Which registered muvid aligner to use for word times.",
        },
        "width": {"type": "integer", "default": 1920, "minimum": 16},
        "height": {"type": "integer", "default": 1080, "minimum": 16},
        "fps": {"type": "integer", "default": 30, "minimum": 1, "maximum": 120},
    },
}

LYRIC_VIDEO = Subgenre(
    slug=SLUG,
    title="Lyric video (kinetic typography)",
    description=(
        "Turn a song into a typographic music video: the words appear in time "
        "with the singing. The layout is chosen from a closed set of archetypes "
        "— one word centred, stacked lines, karaoke wipe, a fixed concrete page "
        "whose words ignite in reading order, a calligram of slanting streaks of "
        "upright letters, words packed into a shape, text on a path, or scatter "
        "— and every position and time is computed from measurement, never "
        "guessed. Runs with no AI and no cost by default."
    ),
    render="muvid.lyricvid.pipeline:render",
    inputs=_INPUTS,
    params_schema=_PARAMS,
    produces="video/mp4",
    intake_kinds=("lyric video", "lyrics", "kinetic typography", "karaoke", "song"),
    # None means genuinely free: the default path spends nothing. An LLM-proposed
    # treatment is opt-in and priced by the caller that configures it — an unknown
    # cost must force approval rather than encode as zero.
    cost_profile=None,
    api_versions=("1",),
    examples=(
        Example(
            description="The simplest thing that works: a song and nothing else.",
            params={},
        ),
        Example(
            description="Karaoke: two lines low in the frame, wiped as sung.",
            params={
                "treatment": {
                    "direction": {"mood": "singalong"},
                    "scenes": [
                        {
                            "archetype": "karaoke_wipe",
                            "motion": "wipe",
                            "timing": {"quantize_to": "word"},
                        }
                    ],
                }
            },
        ),
        Example(
            description=(
                "A concrete poem: the whole text typeset as a fixed page, each "
                "word darkening as it is sung, so the shape it makes is the picture."
            ),
            params={
                "treatment": {
                    "direction": {
                        "palette": {"bg": "#f4ecdd", "fg": "#17130e", "dim": "#cfc3ae"},
                        "typography": {"family": "Palatino", "weight": 400},
                    },
                    "scenes": [
                        {
                            "archetype": "concrete_page",
                            "motion": "fade",
                            "persistence": "dim",
                            "timing": {"quantize_to": "word", "attack_s": 0.14},
                        }
                    ],
                }
            },
        ),
        Example(
            description="Words packed into an apple, filling it as the song goes.",
            params={
                "treatment": {
                    "scenes": [
                        {
                            "archetype": "shape_fill",
                            "shape": {"kind": "named", "value": "apple"},
                            "persistence": "hold",
                        }
                    ]
                }
            },
        ),
    ),
)
