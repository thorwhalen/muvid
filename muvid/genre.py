"""Register muvid's ``music-visualizer`` production genre with nw.

The deterministic, ffmpeg-only half of muvid (:mod:`muvid.visualize`) expressed as
an :class:`nw.Genre` so any host — the unified reelee AV-production MCP connector
(thorwhalen/reelee#229, Phase 3) — can offer it alongside other genres straight from
``nw.genres``. It is the **cheapest** genre: no AI, no network at render time, no API
keys, **$0** — the clean proof that the multi-genre substrate works end to end
(catalog → create → render).

This module is deliberately light and **import-safe**: it imports only ``nw`` and the
tiny :func:`muvid.visualize.visuals.list_visuals` (which never pulls ffmpeg/PIL/mixing
at import), so a host can ``import muvid.genre`` without dragging in fastmcp/py2mcp or
the heavy visual machinery. The MCP tool surface lives in the optional
:mod:`muvid.mcp` (the ``[mcp]`` extra); the project factory below lazy-imports it, so
nothing here needs fastmcp (mirrors ``braidio.genre``).

The genre carries **no engine of its own**: it has no nw Transforms
(``transform_names=()``, ``projection_entrypoint=None``) — the visualizer is a single
deterministic ffmpeg render invoked by the ``muvid_render_visualizer`` tool, not an
nw.execute pipeline. Its "looks" are :class:`nw.Template`\\ s (one per visual strategy),
each carrying an opaque ``params={"visual": ...}`` muvid resolves at render time. It
registers **no strategy_names**: muvid's visuals are muvid-internal ffmpeg strategies,
not ``nw.renderers`` strategies, so listing them there would make ``is_ready()`` false
for an ``available`` genre (a wiring bug — see thorwhalen/muvid#3 review).
"""

from __future__ import annotations

# `register_genre_project_factory` (nw >= 0.0.15) doubles as the nw version guard: on a
# too-old nw this import fails loudly rather than at first create. muvid pins the floor
# in its [mcp] extra; muvid CORE never imports this module, so muvid stays nw-free.
from nw import Genre, Template, register_genre, register_genre_project_factory

# Registering the footage-aligned `music_video` genre too, so a host's single
# `import muvid.genre` surfaces both muvid genres in the nw catalog. Import-safe (nw only
# at module top; heavy align/assemble code is lazy). thorwhalen/reelee#229.
import muvid.genre_music_video  # noqa: F401,E402 — registers music_video + its factory

from muvid.visualize.visuals import list_visuals

MUSIC_VISUALIZER_SLUG = "music-visualizer"

#: The visuals exposed as genre Templates (v1). ``ken_burns`` is intentionally EXCLUDED:
#: it renders frames in Python (Pillow) at several times the song's duration — the one
#: slow, non-ffmpeg visual — so a synchronous hosted render must not offer it. It stays
#: available in ``muvid.visualize`` for local/batch use. ``auto`` (not a registered
#: visual, a selector) picks ``still`` when a cover is given else ``cqt`` — never
#: ken_burns — so the default is always fast.
_EXCLUDED_VISUALS = frozenset({"ken_burns"})

#: Human-facing identity for each exposed visual (the render internals stay in
#: muvid.visualize). ``needs_cover`` flags the ones that require a cover image.
_VISUAL_INFO: dict[str, dict] = {
    "still": {
        "title": "Still cover",
        "description": "The cover art held still for the whole track. Needs a cover image.",
        "needs_cover": True,
    },
    "cqt": {
        "title": "CQT sonogram",
        "description": "A constant-Q spectrogram that pulses with the music (with or without a cover).",
        "needs_cover": False,
    },
    "bars": {
        "title": "Spectrum bars",
        "description": "Classic frequency bars reacting to the audio.",
        "needs_cover": False,
    },
    "spectrum": {
        "title": "Spectrogram",
        "description": "A scrolling spectrogram of the track.",
        "needs_cover": False,
    },
    "waves": {
        "title": "Waveform",
        "description": "The audio waveform drawn across the frame.",
        "needs_cover": False,
    },
    "scope": {
        "title": "Vectorscope",
        "description": "A stereo vectorscope (Lissajous figure) of the audio.",
        "needs_cover": False,
    },
}

#: The exposed visual slugs, in a stable declared order, intersected with what muvid
#: actually registers (so a future muvid that drops a visual can't leave a dangling
#: Template). Never includes an excluded visual.
EXPOSED_VISUALS: tuple[str, ...] = tuple(
    v for v in _VISUAL_INFO if v in set(list_visuals()) and v not in _EXCLUDED_VISUALS
)


def _templates() -> tuple[Template, ...]:
    """One :class:`nw.Template` per exposed visual; ``params`` carries the visual slug."""
    return tuple(
        Template(
            slug=v,
            title=_VISUAL_INFO[v]["title"],
            description=_VISUAL_INFO[v]["description"],
            params={"visual": v},
        )
        for v in EXPOSED_VISUALS
    )


MUSIC_VISUALIZER: Genre = register_genre(
    Genre(
        slug=MUSIC_VISUALIZER_SLUG,
        title="Music Visualizer",
        description=(
            "Turn a song (and usually a cover image) into a 16:9, YouTube-ready "
            "visualizer video — a still cover or an audio-reactive look (CQT, "
            "spectrogram, bars, waveform, vectorscope) — plus a matching thumbnail. "
            "Deterministic, ffmpeg-only, no AI and no cost."
        ),
        # No nw Transforms and no nw strategies: the render is a single ffmpeg pass via
        # muvid.visualize, not an nw pipeline (see the module docstring). Keeping both
        # empty (+ projection_entrypoint None) is what makes is_ready() True for an
        # available, engine-less genre.
        transform_names=(),
        strategy_names=(),
        projection_entrypoint=None,
        status="available",
        intake_kinds=("music", "song", "audio", "visualizer"),
        # Free: the visualizer spends nothing (no fal/ElevenLabs). No cost profile.
        cost_profile=None,
        # "Start from scratch" → let muvid pick the cheapest look for the inputs.
        defaults={"visual": "auto"},
        templates=_templates(),
    )
)


def _music_visualizer_project_factory(caller, project_id, *, title, template, params):
    """Create a ``music-visualizer`` output bucket in the CALLER's own muvid workspace.

    The nw project-factory (thorwhalen/muvid#3) a host connector calls via
    ``nw.create_genre_project`` so the unified reelee connector can create visualizer
    projects it doesn't natively host. The visualizer is stateless — a "project" is just
    a per-user bucket where its renders land — so this creates a lightweight
    :class:`~muvid.mcp.workspace.VisualizerProject` (a folder + manifest), not a full nw
    project. ``VisualizerWorkspace`` is imported **lazily** so ``import muvid.genre``
    stays fastmcp-free. The chosen ``visual`` rides in the returned info + create's
    envelope; there is no initializer (nothing to seed at create — render is on demand).
    """
    from muvid.mcp.workspace import VisualizerWorkspace

    proj = VisualizerWorkspace.for_email(caller).create_project(project_id, title=title)
    return {
        "project": proj,
        "project_id": project_id,
        "title": title,
        "visual": params.get("visual"),
    }


register_genre_project_factory(MUSIC_VISUALIZER_SLUG, _music_visualizer_project_factory)
