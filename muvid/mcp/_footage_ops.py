"""Which MCP tool serves each footage operation — derived from the catalogue.

The footage operations are listed ONCE, in
:data:`muvid.footage.service.FOOTAGE_OP_SPECS`. This module maps each to the tool that
serves it on the connector, so the tool lists in :mod:`muvid.mcp` are computed rather
than written a second time:

- an operation whose transport differs from a plain call has a HAND-WRITTEN tool, named
  in :data:`FOOTAGE_OP_TOOLS` (``set_song`` fetches a URL, ``score`` enqueues an
  ``nw.jobs`` job, ``render`` adds the download claim, and the rest keep the names they
  shipped under — a tool name is a live contract);
- every other operation gets a GENERATED tool, ``footage_<op>``, built by
  :mod:`muvid.mcp.footage_tools` from the operation's own signature and docstring.

Tools that serve no operation are transport-only and listed in
:data:`FOOTAGE_TRANSPORT_TOOLS` / :data:`SCORING_TRANSPORT_TOOLS`.
"""

from __future__ import annotations

from muvid.footage.service import FOOTAGE_OP_SPECS

#: Operation name → the hand-written tool serving it.
FOOTAGE_OP_TOOLS: dict[str, str] = {
    "status": "footage_status",
    "set_song": "set_song",
    "add_clip": "add_footage",
    "remove_clip": "remove_footage",
    "align": "align_footage",
    "timeline": "footage_timeline",
    "beat_grid": "beat_grid",
    "score": "score_footage",
    "scores": "footage_scores",
    "strategies": "list_strategies",
    "propose_edit": "propose_edit",
    "render": "footage_render",
    "editor_document": "footage_editor_document",
}

#: Operations whose tool lives in :mod:`muvid.mcp.scoring_tools` (the job surface).
SCORING_OPS = frozenset({"score", "scores"})

#: Footage tools that serve no single operation: a URL folder import, the
#: workspace-level project listing, the one-call auto/explicit render, and the
#: editor's write-back half.
FOOTAGE_TRANSPORT_TOOLS = [
    "add_footage_folder",
    "assemble_music_video",
    "list_music_video_projects",
    "footage_edl_from_annotations",
]

#: The scoring job's status poll — the connector's own job, not an operation.
SCORING_TRANSPORT_TOOLS = ["footage_score_status"]


def op_tool_name(op_name: str) -> str:
    """The MCP tool serving operation ``op_name`` (hand-written, else ``footage_<op>``)."""
    return FOOTAGE_OP_TOOLS.get(op_name, f"footage_{op_name}")


#: The operations that get a generated tool.
GENERATED_OPS = tuple(
    s.name for s in FOOTAGE_OP_SPECS if s.name not in FOOTAGE_OP_TOOLS
)

#: The footage tool list, in catalogue order then the transport-only tools.
FOOTAGE_TOOLS = [
    op_tool_name(s.name) for s in FOOTAGE_OP_SPECS if s.name not in SCORING_OPS
] + FOOTAGE_TRANSPORT_TOOLS

#: The scoring tool list.
SCORING_TOOLS = [
    op_tool_name(s.name) for s in FOOTAGE_OP_SPECS if s.name in SCORING_OPS
] + SCORING_TRANSPORT_TOOLS
