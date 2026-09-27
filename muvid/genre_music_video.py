"""Register muvid's ``music_video`` production genre with nw.

The footage-aligned music-video genre (thorwhalen/reelee#229): a user uploads several
video clips — each a *different-device* recording of the SAME fixed song — and muvid
aligns them to the clean song's timeline by audio cross-correlation, then assembles a
music video. Distinct from the ffmpeg-only ``music-visualizer`` genre (looks over a song):
this is real footage, chosen/auto-selected per span via a pluggable selection strategy.

Import-safe: imports only ``nw`` and the import-light :mod:`muvid.footage.service` (the
workspace + the heavy align/assemble/mixing code are lazy-imported inside the operation
bodies / the project factory), so a host can ``import muvid.genre_music_video`` without
pulling numpy/moviepy/fastmcp. Engine-less at the nw level (no Transforms /
strategy_names → ``is_ready()`` True); its "looks" are output **canvas** presets carried
as :class:`nw.Template`\\ s.

**Its operations are registered with nw** (:data:`FOOTAGE_OPS`, ``nw.register_genre_ops``)
so a host serves them without importing muvid's internals: one ``nw.GenreOp`` per row of
:data:`muvid.footage.service.FOOTAGE_OP_SPECS`, each taking the host's project (a
:class:`muvid.Project`) and running the operation on its ``footage``.

**Placement** (nw#84): the factory takes ``projects_dir``. Given, the project is created
there as a :class:`muvid.Project` (the studio's path); ``None`` keeps the MCP connector's
per-caller workspace, unchanged.
"""

from __future__ import annotations

import inspect

from nw import (
    Genre,
    GenreOp,
    Template,
    register_genre,
    register_genre_ops,
    register_genre_project_factory,
)

from muvid.footage import service as _service

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


def _music_video_project_factory(
    caller, project_id, *, title, template, params, projects_dir=None
):
    """Create a ``music_video`` project where the CALLER asks, else in muvid's workspace.

    - ``projects_dir`` given — a :class:`muvid.Project` at ``projects_dir/<project_id>``
      (the host that will serve it — the studio — decides where; nw#84), its footage at
      ``<project>/footage`` with the chosen canvas.
    - ``None`` — the caller's own footage workspace
      (:class:`~muvid.footage.workspace.FootageWorkspace`), as the MCP connector has
      always done.

    The canvas rides in ``params``. Lazy-imported so ``import muvid.genre_music_video``
    stays light. No initializer (the song/clips are added by operations after create).
    """
    canvas = (params or {}).get("canvas", "landscape")
    if projects_dir is not None:
        from muvid.production import create_project_at

        proj = create_project_at(
            projects_dir,
            project_id,
            genre=MUSIC_VIDEO_SLUG,
            title=title,
            template=template,
        )
    else:
        from muvid.footage.workspace import FootageWorkspace

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


# -- the operations a host serves on a music video (nw genre ops) --------------------


def _footage_of(project):
    """The footage production an op works on: a hosted project's ``footage``, or a
    footage project handed over directly."""
    footage = getattr(project, "footage", None)
    return footage if footage is not None else project


def _as_genre_op(spec) -> GenreOp:
    """One catalogue row → an ``nw.GenreOp`` whose ``fn`` takes the HOST's project.

    The signature is the operation's own (annotations evaluated) with the project as the
    first parameter and the catalogue's hidden parameters removed — so the JSON Schema a
    host serves cannot drift from the function it calls.
    """
    op = getattr(_service, spec.name)
    sig = inspect.signature(op, eval_str=True)
    first, *rest = sig.parameters.values()
    params = [first.replace(name="project")] + [
        p for p in rest if p.name not in spec.hide
    ]

    def fn(project, **kwargs):
        return op(_footage_of(project), **kwargs)

    fn.__name__ = fn.__qualname__ = spec.name
    fn.__doc__ = op.__doc__
    fn.__signature__ = sig.replace(parameters=params)
    return GenreOp(
        name=spec.name,
        fn=fn,
        title=spec.title,
        effect=spec.effect,
        runs=spec.runs,
        # an upload's server path and original name: the host's, never a client's
        host_params=spec.host_params,
        max_upload_bytes=spec.max_upload_bytes,
    )


#: The catalogue a host serves for ``music_video`` — one ``nw.GenreOp`` per operation.
FOOTAGE_OPS: tuple[GenreOp, ...] = register_genre_ops(
    MUSIC_VIDEO_SLUG, [_as_genre_op(spec) for spec in _service.FOOTAGE_OP_SPECS]
)
