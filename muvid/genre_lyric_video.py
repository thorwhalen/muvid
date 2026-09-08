"""Register muvid's ``lyric-video`` production genre with nw.

The kinetic-typography subgenre (:mod:`muvid.lyricvid`) expressed as an
:class:`nw.Genre`, so a host — the unified reelee AV-production connector —
offers it alongside ``music-visualizer`` and ``music_video`` straight from
``nw.genres``.

Like the other two muvid genres it is **engine-less at the nw level**
(``transform_names=()``, ``strategy_names=()``, ``projection_entrypoint=None``):
the render is one call into :func:`muvid.lyricvid.pipeline.render`, not an
``nw.execute`` pipeline, and that combination is what makes ``is_ready()`` true.
Its "looks" are the layout **archetypes**, carried as :class:`nw.Template`\\ s
with an opaque ``params={"archetype": ...}`` that muvid resolves at render time
— exactly how ``muvid.genre`` carries ``params={"visual": ...}``.

Import-safe: only ``nw`` and the stdlib-only
:mod:`muvid.lyricvid.spec` vocabulary at module top. Nothing here pulls
pysubs2, numpy, Playwright, fastmcp or an LLM client.

**The slug is ``lyric-video``, hyphenated.** muvid's two existing slugs disagree
on separator (``music-visualizer`` vs ``music_video``) and the design record asks
that the next one choose knowingly. It matches the newer of the two. It is a
persisted path segment and a live connector contract value from the day it
ships, so it does not get to change later.
"""

from __future__ import annotations

from nw import Genre, Template, register_genre, register_genre_project_factory

from muvid.lyricvid.spec import ARCHETYPES

LYRIC_VIDEO_SLUG = "lyric-video"

#: Short, human-facing titles for the archetypes. The long descriptions already
#: live in ``muvid.lyricvid.spec.ARCHETYPES`` and are reused verbatim as the
#: Template descriptions, so there is one copy of the prose.
_ARCHETYPE_TITLES: dict[str, str] = {
    "one_word_centred": "One word at a time",
    "stacked_lines": "Stacked lines",
    "karaoke_wipe": "Karaoke wipe",
    "concrete_page": "Concrete page",
    "shape_fill": "Words in a shape",
    "text_on_path": "Text on a path",
    "scatter": "Scatter",
}


def _templates() -> tuple[Template, ...]:
    """One :class:`nw.Template` per archetype; ``params`` carries the slug.

    Intersected with what :mod:`muvid.lyricvid.spec` actually declares, so a
    future muvid that drops an archetype cannot leave a dangling Template.
    """
    return tuple(
        Template(
            slug=name,
            title=_ARCHETYPE_TITLES.get(name, name.replace("_", " ").capitalize()),
            description=ARCHETYPES[name],
            params={"archetype": name},
        )
        for name in _ARCHETYPE_TITLES
        if name in ARCHETYPES
    )


LYRIC_VIDEO: Genre = register_genre(
    Genre(
        slug=LYRIC_VIDEO_SLUG,
        title="Lyric Video (kinetic typography)",
        description=(
            "Turn a song into a typographic music video — the words appear in "
            "time with the singing, laid out by an archetype chosen for the "
            "song: one word centred, stacked lines, a karaoke wipe, a fixed "
            "concrete page whose words ignite in reading order, words packed "
            "into a shape, text on a path, or scatter. Word timings are "
            "measured, never guessed. Runs with no AI and no cost; an LLM "
            "creative director is opt-in."
        ),
        transform_names=(),
        strategy_names=(),
        projection_entrypoint=None,
        status="available",
        intake_kinds=("lyric video", "lyrics", "kinetic typography", "karaoke", "song"),
        # None means genuinely free: the default director is a heuristic that
        # spends nothing. Turning on the LLM director is the caller's opt-in and
        # is priced there — an unknown cost must force approval, never encode as 0.
        cost_profile=None,
        defaults={"archetype": "one_word_centred", "renderer": "ass"},
        templates=_templates(),
    )
)


def _lyric_video_project_factory(caller, project_id, *, title, template, params):
    """Create a ``lyric-video`` output bucket in the CALLER's own muvid workspace.

    Like the visualizer, a lyric video is stateless — a "project" is a per-user
    bucket its renders land in — so this creates the same lightweight
    :class:`~muvid.mcp.workspace.VisualizerProject` rather than a full nw
    project. ``VisualizerWorkspace`` is imported **lazily** so
    ``import muvid.genre_lyric_video`` stays fastmcp-free.
    """
    from muvid.mcp.workspace import VisualizerWorkspace

    proj = VisualizerWorkspace.for_email(caller).create_project(project_id, title=title)
    return {
        "project": proj,
        "project_id": project_id,
        "title": title,
        "archetype": params.get("archetype"),
        "renderer": params.get("renderer", "ass"),
    }


register_genre_project_factory(LYRIC_VIDEO_SLUG, _lyric_video_project_factory)
