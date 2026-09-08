"""Bridge every installed subgenre up into the ``nw`` genre catalogue.

The README promises that a subgenre plugin "depends on ``muvid`` alone and a
host connector picks it up for free". This module is the code that keeps that
promise: it walks :func:`muvid.subgenres.iter_subgenres` and registers one
``nw.Genre`` per subgenre, with the manifest's examples as its Templates
(``params={"subgenre": slug, **example.params}``) — so a third party ships a
manifest and an entry point and never imports ``nw``.

Two rules keep it from fighting the hand-written genres:

* a subgenre whose slug is **already** an nw genre is skipped, not re-registered.
  ``lyric-video`` is bridged by :mod:`muvid.genre_lyric_video` with archetype
  Templates that are richer than its examples; the generic bridge defers to it.
  ``nw.genres`` is ``on_conflict="error"`` and there is no ``replace=``, so this
  is the only safe order.
* a manifest that fails to load stays in ``subgenre_catalog()['load_errors']``
  and simply has no genre. One broken plugin cannot break the catalogue.

Import-safe: ``nw`` and the stdlib-only :mod:`muvid.subgenres` at module top;
nothing here imports a renderer. Listing plugins costs their manifest modules
only, which is the property the plugin surface exists to provide.
"""

from __future__ import annotations

from nw import (
    Genre,
    Template,
    list_genres,
    register_genre,
    register_genre_project_factory,
)

from muvid.subgenres import Subgenre, iter_subgenres

__all__ = ["bridge_subgenres", "BRIDGED"]

#: slug -> Genre for every subgenre this module registered (not the hand-written ones).
BRIDGED: dict[str, Genre] = {}


def _templates(sg: Subgenre) -> tuple[Template, ...]:
    """One Template per Example; the example's params ride along with the slug.

    A subgenre with no examples still gets one plain Template so it can be
    chosen, and so ``create_genre_project(template=...)`` has a name to use.
    """
    if not sg.examples:
        return (
            Template(
                slug="default",
                title=sg.title,
                description=sg.description,
                params={"subgenre": sg.slug},
            ),
        )
    out = []
    for i, ex in enumerate(sg.examples):
        out.append(
            Template(
                slug=f"example-{i}",
                title=ex.description[:60],
                description=ex.description,
                params={"subgenre": sg.slug, **dict(ex.params)},
            )
        )
    return tuple(out)


def _factory(sg: Subgenre):
    """A project factory: a per-caller bucket in the visualizer workspace.

    Subgenres are stateless the way the visualizer is — a "project" is where
    renders land — so the lightweight ``VisualizerProject`` is the right home.
    Imported lazily so this module stays fastmcp-free.
    """

    def factory(caller, project_id, *, title, template, params):
        from muvid.mcp.workspace import VisualizerWorkspace

        proj = VisualizerWorkspace.for_email(caller).create_project(
            project_id, title=title
        )
        return {
            "project": proj,
            "project_id": project_id,
            "title": title,
            "subgenre": sg.slug,
            "params": {k: v for k, v in dict(params or {}).items() if k != "subgenre"},
        }

    factory.__name__ = f"_{sg.slug.replace('-', '_')}_project_factory"
    return factory


def bridge_subgenres(*, refresh: bool = False) -> dict[str, Genre]:
    """Register an nw Genre for every installed subgenre not already bridged.

    Idempotent: calling it twice registers nothing new. Returns the genres this
    module owns.
    """
    existing = set(list_genres())
    for sg in iter_subgenres(refresh=refresh):
        if sg.slug in existing or sg.slug in BRIDGED:
            continue
        genre = register_genre(
            Genre(
                slug=sg.slug,
                title=sg.title,
                description=sg.description,
                transform_names=(),
                strategy_names=(),
                projection_entrypoint=None,
                status="available",
                intake_kinds=tuple(sg.intake_kinds),
                cost_profile=sg.cost_profile,
                defaults={"subgenre": sg.slug},
                templates=_templates(sg),
            )
        )
        register_genre_project_factory(sg.slug, _factory(sg))
        BRIDGED[sg.slug] = genre
        existing.add(sg.slug)
    return dict(BRIDGED)


# Bridge at import, the way muvid's other genre modules register at import:
# one `import muvid.genre` surfaces everything. The hand-written genres are
# imported by muvid.genre BEFORE this module, so they win their slugs.
bridge_subgenres()
