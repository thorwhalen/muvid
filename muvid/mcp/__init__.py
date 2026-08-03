"""muvid MCP server — the ``music-visualizer`` tool surface for a remote connector.

Optional subpackage (extra ``muvid[mcp]``: ``fastmcp`` + ``py2mcp`` + ``nw``).
``import muvid`` never imports this. The connector references the tools by name via
:data:`TOOL_REFS`; :func:`register_tools` aggregates them onto a host FastMCP server
(the unified reelee AV connector, thorwhalen/muvid#3), and :func:`build_server`
assembles a standalone server for local (stdio) testing.

Every tool is **free** (:data:`FREE_TOOLS` == :data:`TOOL_NAMES`): the visualizer spends
no money. There are no costed tools, so there is no metering here — a host records the
calls and, when it sets ``metered_tools``, these bypass its credit gate.
"""

from muvid.mcp._guide import INSTRUCTIONS
from muvid.mcp.identity import current_email, token_email, use_email
from muvid.mcp.workspace import VisualizerWorkspace, data_root

#: The tools this package exposes (all free). Bare names; a host may prefix them.
TOOL_NAMES = [
    "list_visuals",
    "list_projects",
    "project_status",
    "render_visualizer",
]

#: Alias — muvid has no costed tools.
FREE_TOOLS = list(TOOL_NAMES)
COSTED_TOOLS: list[str] = []

#: ``mod:fn`` references for py2mcp-style aggregation.
TOOL_REFS = [f"muvid.mcp.tools:{name}" for name in TOOL_NAMES]


def register_tools(server, *, prefix="", include=None, exclude=None):
    """Register muvid's MCP tools onto an EXISTING FastMCP ``server`` — the aggregation
    seam for a host connector (the unified reelee connector, muvid#3).

    Mirrors ``braidio.mcp.register_tools`` / ``falaw.bridges.mcp.register_tools``.
    ``prefix`` namespaces the tool names (e.g. ``"muvid_"``) to avoid collisions;
    ``include`` / ``exclude`` (sets of bare names) select a subset. Returns the
    registered (prefixed) names. The host installs identity/metering separately —
    muvid's tools resolve the caller via :func:`current_email`, which works under any
    host middleware.
    """
    from py2mcp.util import import_object

    names = list(include) if include is not None else list(TOOL_NAMES)
    skip = set(exclude or ())
    registered = []
    for name in names:
        if name in skip:
            continue
        fn = import_object(f"muvid.mcp.tools:{name}")
        server.tool(
            fn, name=f"{prefix}{name}", description=(fn.__doc__ or "").strip() or None
        )
        registered.append(f"{prefix}{name}")
    return registered


def build_server(
    *, name="muvid", instructions=INSTRUCTIONS, auth=None, middleware=None
):
    """Assemble a standalone FastMCP server exposing muvid's tools (stdio dev/testing).

    The hosted path aggregates onto the reelee connector via :func:`register_tools`;
    this is the convenience builder for ``muvid.mcp`` on its own.
    """
    from fastmcp import FastMCP

    server = FastMCP(name=name, instructions=instructions, auth=auth)
    if middleware:
        for mw in middleware if isinstance(middleware, (list, tuple)) else [middleware]:
            server.add_middleware(mw)
    register_tools(server)
    return server


__all__ = [
    "TOOL_NAMES",
    "FREE_TOOLS",
    "COSTED_TOOLS",
    "TOOL_REFS",
    "INSTRUCTIONS",
    "register_tools",
    "build_server",
    "current_email",
    "token_email",
    "use_email",
    "VisualizerWorkspace",
    "data_root",
]
