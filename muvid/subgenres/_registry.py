"""The subgenre registry — in-process registration plus entry-point discovery.

Two ways in, deliberately, and they are the same ramp `register_visual` already
offers muvid users:

* :func:`register_subgenre` for something you are trying out in a notebook,
  a ``conftest.py``, or your own application. No packaging required.
* an entry point in the ``muvid.subgenres.v1`` group for something you are
  *shipping*. Discovered lazily on first use.

Discovery loads the manifest module only — never the renderer — so listing every
installed subgenre costs microseconds and cannot be broken by a plugin whose
heavy dependency is missing. A plugin that fails to load is **recorded and
skipped**, never raised: one bad plugin must not take the catalogue down with it.

Follows muvid's existing registry idiom (a module-level dict, a ``register_``
function, a ``list_`` function and a ``resolve_`` function) rather than
introducing a new container, so there is one shape to learn across
``register_visual``, ``register_aligner``, ``register_selection_strategy`` and
this.
"""

from __future__ import annotations

import warnings
from importlib import import_module
from typing import Any, Callable, Iterator

from muvid.subgenres._manifest import ENTRY_POINT_GROUP, Subgenre

#: slug -> manifest, for everything registered in-process.
_SUBGENRES: dict[str, Subgenre] = {}

#: slug -> manifest, for everything found on entry points. Populated once.
_DISCOVERED: dict[str, Subgenre] | None = None

#: Distributions whose entry point could not be loaded, and why. Kept rather
#: than raised so `subgenre_catalog()` can report a broken plugin without
#: refusing to serve the working ones.
_LOAD_ERRORS: dict[str, str] = {}


def register_subgenre(subgenre: Subgenre) -> Subgenre:
    """Register a subgenre in this process. Returns it, so it can be used inline.

    Raises :class:`ValueError` on a slug collision, matching ``nw.genres``'
    ``on_conflict="error"``: a slug is a persisted path segment and a public
    contract value, so silently replacing one is never the kind thing to do.

    >>> from muvid.subgenres import Subgenre, register_subgenre, list_subgenres
    >>> sg = register_subgenre(Subgenre(
    ...     slug='demo-doctest', title='Demo', description='.',
    ...     render='muvid.subgenres.testing:echo_renderer'))
    >>> 'demo-doctest' in list_subgenres()
    True
    >>> unregister_subgenre('demo-doctest')
    """
    if subgenre.slug in _SUBGENRES:
        raise ValueError(
            f"subgenre {subgenre.slug!r} is already registered. Slugs are "
            "persisted path segments and public contract values; pick another."
        )
    _SUBGENRES[subgenre.slug] = subgenre
    return subgenre


def unregister_subgenre(slug: str) -> None:
    """Remove an in-process registration. Mostly for tests and doctests."""
    _SUBGENRES.pop(slug, None)


def _discover(*, refresh: bool = False) -> dict[str, Subgenre]:
    """Load every ``muvid.subgenres.v1`` entry point's MANIFEST (not renderer)."""
    global _DISCOVERED
    if _DISCOVERED is not None and not refresh:
        return _DISCOVERED
    from importlib.metadata import entry_points

    found: dict[str, Subgenre] = {}
    _LOAD_ERRORS.clear()
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        dist = getattr(getattr(ep, "dist", None), "name", None) or ep.name
        try:
            obj = ep.load()
            if not isinstance(obj, Subgenre):
                raise TypeError(
                    f"expected a muvid.subgenres.Subgenre, got {type(obj).__name__}"
                )
            # Record who provided it; the manifest is frozen, so rebuild.
            from dataclasses import replace

            obj = replace(obj, provider=dist)
            if obj.slug in found:
                raise ValueError(f"duplicate subgenre slug {obj.slug!r}")
            found[obj.slug] = obj
        except Exception as exc:  # one bad plugin must not break the catalogue
            _LOAD_ERRORS[dist] = f"{type(exc).__name__}: {exc}"
            warnings.warn(
                f"muvid: could not load subgenre plugin {dist!r}: "
                f"{type(exc).__name__}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
    _DISCOVERED = found
    return found


def _all(*, refresh: bool = False) -> dict[str, Subgenre]:
    """In-process registrations win over discovered ones with the same slug."""
    merged = dict(_discover(refresh=refresh))
    merged.update(_SUBGENRES)
    return merged


def list_subgenres(*, refresh: bool = False) -> list[str]:
    """Every available subgenre slug, sorted. Imports no renderer."""
    return sorted(_all(refresh=refresh))


def get_subgenre(slug: str, *, refresh: bool = False) -> Subgenre:
    """The manifest for ``slug``. Imports no renderer."""
    available = _all(refresh=refresh)
    try:
        return available[slug]
    except KeyError:
        raise KeyError(
            f"unknown subgenre {slug!r}. Available: {sorted(available) or '(none)'}"
        ) from None


def subgenre_catalog(*, refresh: bool = False) -> dict[str, Any]:
    """JSON-able catalogue: what a CLI, an HTTP route, an MCP tool or an agent serves.

    Imports no renderer, so this stays cheap however many plugins are installed.
    ``load_errors`` is part of the payload on purpose — a broken plugin should be
    visible to whoever is choosing, not silently absent.
    """
    available = _all(refresh=refresh)
    return {
        "api_version": __import__(
            "muvid.subgenres._manifest", fromlist=["API_VERSION"]
        ).API_VERSION,
        "subgenres": [available[s].to_dict() for s in sorted(available)],
        "load_errors": dict(_LOAD_ERRORS),
    }


def resolve_renderer(slug: str) -> Callable[..., Any]:
    """Import and return the renderer for ``slug``.

    This is the only function in the module that imports plugin code, and it is
    called at render time, not at listing time.
    """
    sg = get_subgenre(slug)
    module_name, _, func_name = sg.render.partition(":")
    try:
        module = import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f"subgenre {slug!r} declares render={sg.render!r} but "
            f"{module_name!r} could not be imported: {exc}. If this plugin needs "
            "an optional dependency, install it."
        ) from exc
    try:
        return getattr(module, func_name)
    except AttributeError:
        raise AttributeError(
            f"subgenre {slug!r} declares render={sg.render!r} but "
            f"{module_name!r} has no attribute {func_name!r}."
        ) from None


def iter_subgenres(*, refresh: bool = False) -> Iterator[Subgenre]:
    """Iterate manifests in slug order."""
    available = _all(refresh=refresh)
    for slug in sorted(available):
        yield available[slug]
