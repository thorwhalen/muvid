"""Register muvid's ``music_video`` production genre with nw.

The footage-aligned music-video genre (thorwhalen/reelee#229): a user uploads several
video clips — each a *different-device* recording of the SAME fixed song — and muvid
aligns them to the clean song's timeline by audio cross-correlation, then assembles a
music video. Distinct from the ffmpeg-only ``music-visualizer`` genre (looks over a song):
this is real footage, chosen/auto-selected per span via a pluggable selection strategy.

Import-safe: imports only ``nw`` (the workspace + the heavy align/assemble/mixing code are
lazy-imported inside the tool bodies / the project factory), so a host can
``import muvid.genre_music_video`` without pulling numpy/moviepy/fastmcp. Engine-less at
the nw level (no Transforms / strategy_names → ``is_ready()`` True); its "looks" are output
**canvas** presets carried as :class:`nw.Template`\\ s.
"""

from __future__ import annotations

from nw import Genre, Template, register_genre, register_genre_project_factory

MUSIC_VIDEO_SLUG = "music_video"

#: Output canvas presets, exposed as the genre's Templates. params carries the canvas name
#: muvid resolves to a pixel size at assemble time (mixed-orientation phone clips are
#: scaled+padded onto this fixed canvas — never clip-0's size).
_CANVAS_INFO: dict[str, dict] = {
    "landscape": {
        "title": "Landscape 16:9",
        "description": "1920×1080, YouTube-style.",
    },
    "portrait": {
        "title": "Portrait 9:16",
        "description": "1080×1920, Reels/Shorts-style.",
    },
    "square": {"title": "Square 1:1", "description": "1080×1080."},
}


MUSIC_VIDEO: Genre = register_genre(
    Genre(
        slug=MUSIC_VIDEO_SLUG,
        title="Music Video (footage)",
        description=(
            "Assemble a music video for a fixed song from several uploaded video clips — "
            "each a different-device recording of that song. Clips are aligned to the "
            "song by audio, overlaps are resolved by a selection strategy, and the edit "
            "is rendered over the clean song audio."
        ),
        transform_names=(),
        strategy_names=(),
        projection_entrypoint=None,
        status="available",
        intake_kinds=("music-video", "footage", "multicam", "performance"),
        cost_profile=None,  # ffmpeg-only, no AI/keys — free
        defaults={"canvas": "landscape"},
        templates=tuple(
            Template(
                slug=name,
                title=info["title"],
                description=info["description"],
                params={"canvas": name},
            )
            for name, info in _CANVAS_INFO.items()
        ),
    )
)


def _music_video_project_factory(caller, project_id, *, title, template, params):
    """Create a ``music_video`` project in the CALLER's own footage workspace.

    A STATEFUL project (song + clips + alignment manifest), so — unlike the stateless
    visualizer — it uses :class:`~muvid.footage.workspace.FootageWorkspace`. The chosen
    canvas rides in ``params``. Lazy-imported so ``import muvid.genre_music_video`` stays
    light. No initializer (the song/clips are added by tools after create).
    """
    from muvid.footage.workspace import FootageWorkspace

    canvas = (params or {}).get("canvas", "landscape")
    proj = FootageWorkspace.for_email(caller).create_project(
        project_id, title=title, canvas=canvas
    )
    return {
        "project": proj,
        "project_id": project_id,
        "title": title,
        "canvas": canvas,
    }


register_genre_project_factory(MUSIC_VIDEO_SLUG, _music_video_project_factory)
