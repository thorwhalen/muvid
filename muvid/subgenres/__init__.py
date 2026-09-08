"""muvid subgenres — the plugin surface for *another kind of video*.

muvid's three parts each know how to make one thing. A **subgenre** is how a
fourth kind gets added — by muvid, or by anyone else — without editing muvid.

    from muvid.subgenres import Subgenre, register_subgenre

    MY_THING = Subgenre(
        slug="ransom-note",
        title="Ransom note",
        description="Lyrics cut from magazines, one word per beat.",
        render="my_package.render:render",       # a STRING, imported lazily
        inputs={"type": "object", "required": ["audio"],
                "properties": {"audio": {"type": "string", "format": "path"}}},
    )

Ship it by pointing an entry point at the manifest::

    [project.entry-points."muvid.subgenres.v1"]
    ransom-note = "my_package.subgenre:MY_THING"

Two things make this worth having over a plain dict:

**The entry point's value is a manifest, not a renderer.** Listing every
installed subgenre imports no rendering code, so a UI or an LLM can choose among
twelve of them in microseconds and a plugin whose heavy dependency is missing
degrades to a recorded error instead of taking the catalogue down.

**It composes with ``nw`` rather than competing with it.** ``nw.Genre`` is the
cross-package catalogue a host connector reads; a muvid subgenre is one more
``nw.Template`` underneath muvid's own genre, carrying ``params={"subgenre":
slug}``. So a plugin author depends on ``muvid`` and nothing else, and the
connector picks the plugin up for free.

Where each piece lives:

* :mod:`muvid.subgenres._manifest` — the :class:`Subgenre` data, stdlib only.
* :mod:`muvid.subgenres._registry` — registration, discovery, resolution.
* :mod:`muvid.subgenres._contract` — the :class:`Renderer` protocol.
* :mod:`muvid.subgenres.testing` — a conformance kit for plugin authors.

This module imports nothing heavier than the standard library, so
``import muvid.subgenres`` is safe on any import-safe path.
"""

from __future__ import annotations

from muvid.subgenres._contract import RenderRequest, RenderResult, Renderer
from muvid.subgenres._manifest import (
    API_VERSION,
    ENTRY_POINT_GROUP,
    Example,
    Subgenre,
)
from muvid.subgenres._registry import (
    get_subgenre,
    iter_subgenres,
    list_subgenres,
    register_subgenre,
    resolve_renderer,
    subgenre_catalog,
    unregister_subgenre,
)

__all__ = [
    "API_VERSION",
    "ENTRY_POINT_GROUP",
    "Example",
    "RenderRequest",
    "RenderResult",
    "Renderer",
    "Subgenre",
    "get_subgenre",
    "iter_subgenres",
    "list_subgenres",
    "register_subgenre",
    "render_subgenre",
    "resolve_renderer",
    "subgenre_catalog",
    "unregister_subgenre",
]


def render_subgenre(
    slug: str,
    *,
    inputs: dict,
    params: dict | None = None,
    workdir,
    output,
) -> RenderResult:
    """Render ``slug`` — the one call that imports plugin code.

    Deliberately takes paths and primitives rather than a
    :class:`~muvid.project.MusicVideoProject`: a plugin coupled to muvid's
    project schema would break on every schema change, and could not be driven
    from a CLI, an HTTP request or an MCP tool without muvid building the
    project first. A plugin that needs more asks muvid for it through a narrow
    accessor.
    """
    from pathlib import Path

    from muvid.subgenres._schema import SchemaError, validate as _validate

    manifest = get_subgenre(slug)
    inputs = dict(inputs)
    params = dict(params or {})

    # The manifest's schemas are a contract, and a contract nobody checks is
    # decoration. Validate BEFORE resolving the renderer: a typo'd input should
    # fail in microseconds, not after importing a rendering stack.
    problems = _validate(inputs, manifest.inputs, where="inputs")
    problems += _validate(params, manifest.params_schema, where="params")
    if problems:
        raise SchemaError([f"subgenre {slug!r}: {p}" for p in problems])

    workdir = Path(workdir)
    output = Path(output)
    # The RenderRequest contract promises the renderer that workdir exists and
    # output's parent exists. The HOST keeps that promise, not the plugin.
    workdir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    renderer = resolve_renderer(slug)
    request = RenderRequest(
        subgenre=slug, inputs=inputs, params=params, workdir=workdir, output=output
    )
    result = renderer(request)
    if not isinstance(result, RenderResult):
        raise TypeError(
            f"subgenre {slug!r} renderer returned {type(result).__name__}, "
            "expected a muvid.subgenres.RenderResult"
        )
    # A host serves request.output. A renderer that wrote somewhere else — or
    # nowhere — must be a loud error here, not a 404 (or a served stray path)
    # later. The conformance kit has always checked this; the runtime now agrees.
    if Path(result.output).resolve() != output.resolve():
        raise ValueError(
            f"subgenre {slug!r} renderer reported output {result.output}, "
            f"but was asked to write {output}"
        )
    if not output.exists():
        raise FileNotFoundError(
            f"subgenre {slug!r} renderer reported success but {output} does not exist"
        )
    return result
