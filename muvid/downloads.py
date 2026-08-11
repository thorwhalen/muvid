"""Resolve a caller's rendered artifact to its file — muvid's half of downloads.

The music-video pipeline could render a 93 MB video the caller had no way to obtain:
``meta['video']`` is a server-side path, unreadable from the other end of an MCP
connection (muvid#24 B2). Retrieval is split along ownership lines, per the seam agreed
with the connector redesign (reelee#252 §1 Option B):

- **muvid owns resolution** (this module): claim → file, authorised by the same
  per-email workspace scoping every tool uses. muvid never touches token or URL code.
- **The connector owns transport**: ONE generic download route, mounted next to
  ``/mcp``, turns claims into signed short-lived URLs via per-genre resolvers —
  ``resolvers={'muvid': muvid.downloads.resolve, ...}`` — shared by reelee's own
  exports, so this is the first registration, not a parallel mechanism.

A tool therefore returns a **claim** — ``{genre, project_id, artifact_id}`` — and the
connector-side wrapper (or the human operator, server-side) resolves it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: The genre identifier the connector's resolver registry keys on.
GENRE = "muvid"


@dataclass(frozen=True)
class ResolvedArtifact:
    """A resolved claim: the file plus what the transport needs to serve it."""

    path: Path
    content_type: str
    filename: str


#: Extensions a render may legitimately carry, with their content types.
_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}


def claim(project_id: str, artifact_id: str) -> dict:
    """The claim dict a tool returns — what the connector turns into a signed URL."""
    return {"genre": GENRE, "project_id": project_id, "artifact_id": artifact_id}


def resolve(email: str, project_id: str, artifact_id: str) -> ResolvedArtifact:
    """The caller's rendered artifact as a servable file — muvid's download authority.

    ``artifact_id`` is a render id. Raises ``KeyError`` for anything that does not
    resolve to an artifact of *this* caller — including ids shaped like an attack and
    other users' renders. (No ``PermissionError`` branch: workspaces are scoped by
    email, so "someone else's render" and "no such render" are indistinguishable here,
    and saying which would leak existence.)
    """
    from muvid.footage.workspace import FootageWorkspace, safe_component

    # The same id rule the workspace itself applies — a second, stricter charset here
    # would refuse artifacts the workspace happily created.
    try:
        pid = safe_component(project_id, label="project_id")
        aid = safe_component(artifact_id, label="artifact_id")
    except ValueError as e:
        raise KeyError(str(e)) from None
    try:
        proj = FootageWorkspace.for_email(email).open_project(pid)
    except FileNotFoundError:
        raise KeyError(f"no such project {project_id!r}") from None
    render_dir = proj.root / "renders" / aid
    candidates = [
        p for p in (render_dir / f"final{ext}" for ext in _CONTENT_TYPES) if p.exists()
    ]
    if not candidates:
        raise KeyError(f"no such render {artifact_id!r} in project {project_id!r}")
    path = candidates[0]
    return ResolvedArtifact(
        path=path,
        content_type=_CONTENT_TYPES[path.suffix],
        filename=f"{project_id}-{artifact_id}{path.suffix}",
    )
