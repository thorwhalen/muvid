"""Make a hosted production's media *retrievable* — the host's artifact catalog.

A song, a clip or a render that lands in a host-placed :class:`muvid.Project` is a file
on disk; the studio plays media through the host's ``GET /api/artifacts/{id}/bytes``,
which answers only for ids registered in the project's catalog. So every such file is
also registered there: :class:`HostArtifactCatalog` is the writer, and
:class:`muvid.footage.workspace.MusicVideoFootageProject`'s ``media_catalog`` seam is
where it plugs in (``None`` — the MCP connector's per-caller workspace — registers
nothing).

The writer itself is :mod:`nw.media_catalog` (nw#92). muvid and braidio each carried a
copy of it; this module keeps muvid's import path and names so callers are unchanged.
Its docstring states the rules (id = content hash, blob before row, hardlinked, never a
``file://`` url, refused on an object-store host).
"""

from __future__ import annotations

from nw.media_catalog import (
    BACKEND_ENV_KEY,
    BYTES_ROUTE,
    CATALOG_KINDS,
    DELIVERY_CATALOG_SUBPATH,
    CatalogBackendMismatch,
    CorruptBlob,
    CrossDeviceCatalog,
    HostArtifactCatalog,
    assert_local_backend,
    catalog_row,
    hash_file,
)

__all__ = [
    "HostArtifactCatalog",
    "CorruptBlob",
    "CrossDeviceCatalog",
    "CatalogBackendMismatch",
    "catalog_row",
    "hash_file",
    "assert_local_backend",
    "BACKEND_ENV_KEY",
    "BYTES_ROUTE",
    "CATALOG_KINDS",
    "DELIVERY_CATALOG_SUBPATH",
]
