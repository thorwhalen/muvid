"""The montage subgenre's manifest — a beat-cut montage from a pool of stills and clips.

Deliberately stdlib-only and free of any import from the rest of
:mod:`muvid.montage`: this module is what a catalogue, a UI form, an MCP tool
definition or an LLM choosing among installed subgenres reads, and none of them
should pay for numpy or ffmpeg to do it. The renderer is named as a string and
imported only when a render actually runs.

The shape copies :mod:`muvid.lyricvid.manifest` on purpose — that file is the
reference a third-party plugin is told to look like, and a second in-tree plugin
that drifted from it would make the reference ambiguous.
"""

from __future__ import annotations

from muvid.subgenres import Example, Subgenre

#: Hyphen-free because it is one word; the slug is a persisted path segment and
#: a public contract value from the day it ships, so it is chosen once.
SLUG = "montage"

#: The largest list of files one input may carry. Stated here (and enforced in
#: :mod:`muvid.montage.pipeline`, which reads ``MUVID_MONTAGE_MAX_MEDIA``) so a
#: UI can show the bound before a caller uploads 200 photos. The runtime schema
#: validator does not enforce ``maxItems``; the pipeline REFUSES past it.
MAX_MEDIA_PER_INPUT = 64

_INPUTS = {
    "type": "object",
    "required": ["audio"],
    "additionalProperties": False,
    # At least one of photos/clips is required. The runtime validator does not
    # enforce anyOf (it is documentation for a UI); the pipeline refuses an
    # empty pool with a clear message.
    "anyOf": [{"required": ["photos"]}, {"required": ["clips"]}],
    "properties": {
        "audio": {
            "type": "string",
            "description": "Path to the song. The only required input.",
        },
        "photos": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_MEDIA_PER_INPUT,
            "description": "Paths to still images (any format ffmpeg decodes). "
            "Photos and/or clips: at least one of the two must be non-empty.",
        },
        "clips": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_MEDIA_PER_INPUT,
            "description": "Paths to video clips with no timeline of their own. "
            "Each use of a clip trims a different stretch of it.",
        },
        "cover": {
            "type": "string",
            "description": "Optional cover image. Pinned to the first and the "
            "last slot of the montage, and used nowhere else.",
        },
    },
}

_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "treatment": {
            "type": "object",
            "description": "A muvid montage treatment spec (see "
            "muvid.montage.spec.json_schema). Omitted, a one-scene treatment "
            "is built from `archetype`.",
        },
        "archetype": {
            "type": "string",
            "enum": ["ballad_dissolve", "beat_cut", "grid", "stop_motion"],
            "default": "beat_cut",
            "description": "The cutting archetype for a one-scene treatment. "
            "Ignored when `treatment` is given.",
        },
        "strict": {
            "type": "boolean",
            "default": False,
            "description": "Refuse a treatment that needed repairs rather than "
            "rendering the repaired one.",
        },
        "beats": {
            "type": "string",
            "enum": ["auto", "mixing", "numpy"],
            "default": "auto",
            "description": "Beat-grid source. auto: mixing.audio.beat_grid "
            "(librosa) where installed, else muvid's own numpy estimator; "
            "naming one never falls back.",
        },
        "beats_per_bar": {
            "type": "integer",
            "enum": [2, 3, 4, 6],
            "default": 4,
            "description": "Meter, for the bar grid the cuts sit on.",
        },
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["label", "start", "end"],
                "properties": {
                    "label": {"type": "string"},
                    "start": {"type": "number", "minimum": 0},
                    "end": {"type": "number", "minimum": 0},
                },
            },
            "description": "Labelled sections (intro/verse/chorus/bridge/outro) "
            "in song seconds. Omitted, coarse ones are derived from energy: a "
            "loud stretch is a chorus.",
        },
        "width": {"type": "integer", "default": 1920, "minimum": 16},
        "height": {"type": "integer", "default": 1080, "minimum": 16},
        "fps": {"type": "integer", "default": 30, "minimum": 1, "maximum": 120},
    },
}

MONTAGE = Subgenre(
    slug=SLUG,
    title="Montage (beat-cut photo/clip montage)",
    description=(
        "Cut a pool of photos and short clips to the song's beat grid and section "
        "structure — the CapCut 'photo beat sync' / Animoto kind of video. A "
        "planner turns the pool into an edit list: cuts on beats and bars, "
        "denser in the choruses, a slow Ken Burns move or a punch-zoom per "
        "still, crossfades or hard cuts or a 2x2 grid, and an explicit reuse "
        "policy so twelve photos carry a three-minute song without the same "
        "image twice in a row. Deterministic, no AI, no network, no cost."
    ),
    render="muvid.montage.pipeline:render",
    inputs=_INPUTS,
    params_schema=_PARAMS,
    produces="video/mp4",
    intake_kinds=(
        "montage", "photo montage", "slideshow", "beat sync", "photo beat sync",
        "photos", "memories", "recap",
    ),
    # None means genuinely free: nothing here spends anything.
    cost_profile=None,
    api_versions=("1",),
    examples=(
        Example(
            description="The simplest thing that works: a song and a folder of "
            "photos, cut hard on the beat with a punch-zoom.",
            params={"archetype": "beat_cut"},
        ),
        Example(
            description="A ballad: cut every few bars on the downbeat, one-beat "
            "crossfades, slow Ken Burns drift, a warm accent grade.",
            params={
                "treatment": {
                    "direction": {
                        "cut_feel": "slow",
                        "grade": "tint",
                        "palette": {"accent": "#e8a86a"},
                    },
                    "scenes": [{"archetype": "ballad_dissolve"}],
                }
            },
        ),
        Example(
            description="Verses as a 2x2 grid swapping one tile per beat, "
            "choruses as hard beat cuts.",
            params={
                "treatment": {
                    "direction": {"cut_feel": "driving"},
                    "scenes": [
                        {"applies_to": ["*"], "archetype": "grid"},
                        {"applies_to": ["chorus"], "archetype": "beat_cut"},
                    ],
                }
            },
        ),
        Example(
            description="Stop-motion flip-book: stills held for an eighth note, "
            "no motion, monochrome.",
            params={
                "treatment": {
                    "direction": {"grade": "mono", "cut_feel": "driving"},
                    "scenes": [
                        {"archetype": "stop_motion", "params": {"subdivision": 2}}
                    ],
                }
            },
        ),
    ),
)
