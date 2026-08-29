"""Resolve a caller's rendered artifact to its file — muvid's half of delivery.

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

The type this returns is ``nw.delivery.Deliverable``, and that matters
-----------------------------------------------------------------------
This module used to define its own ``ResolvedArtifact``. The host's route was
typed ``-> Path`` and did ``Path(resolved).suffix``, so every muvid download
raised ``TypeError`` and answered **HTTP 500** — from the day the resolver was
registered until it was found. Both sides had green tests; each tested only
itself. The shared type now lives in ``nw.delivery``, the one package both muvid
and the host depend on and which depends on neither, so there is a single
definition rather than two that agreed by luck.

Speakable references
--------------------
``artifact_id`` accepts either the raw render id (``b02fc05417ea``) or the
reference a human can actually say (``cut 4``, ``#4``, ``4``). Only muvid can
resolve the second, because only muvid knows the ordering that gives it meaning
— which is why the seam passes the string through rather than pre-parsing it.
"""

from __future__ import annotations

from pathlib import Path

from nw.delivery import Deliverable, format_ref, parse_ref

#: The genre identifier the connector's resolver registry keys on.
GENRE = "muvid"

#: Extensions a render may legitimately carry, with their content types.
_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}

__all__ = ["GENRE", "claim", "resolve", "list_deliverables"]


def claim(project_id: str, artifact_id: str) -> dict:
    """The claim dict a tool returns — what the connector turns into a signed URL."""
    return {"genre": GENRE, "project_id": project_id, "artifact_id": artifact_id}


def _open(email: str, project_id: str):
    """The caller's project, or ``KeyError``. Never distinguishes "not yours"."""
    from muvid.footage.workspace import FootageWorkspace, safe_component

    # The same id rule the workspace itself applies — a second, stricter charset here
    # would refuse artifacts the workspace happily created.
    try:
        pid = safe_component(project_id, label="project_id")
    except ValueError as e:
        raise KeyError(str(e)) from None
    try:
        return pid, FootageWorkspace.for_email(email).open_project(pid)
    except FileNotFoundError:
        raise KeyError(f"no such project {project_id!r}") from None


def _render_file(proj, render_id: str) -> "Path | None":
    render_dir = proj.root / "renders" / render_id
    for ext in _CONTENT_TYPES:
        p = render_dir / f"final{ext}"
        if p.exists():
            return p
    return None


def _deliverable(proj, pid: str, render_id: str, ref_n: "int | None") -> Deliverable:
    path = _render_file(proj, render_id)
    if path is None:
        raise KeyError(f"no such render {render_id!r} in project {pid!r}")
    meta = {}
    meta_path = proj.root / "renders" / render_id / "meta.json"
    if meta_path.exists():
        import json

        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            meta = {}
    if ref_n is None and isinstance(meta.get("ref_n"), int):
        ref_n = meta["ref_n"]
    stat = path.stat()
    span = meta.get("rendered_span") or []
    duration = round(span[1] - span[0], 2) if len(span) == 2 else None
    # Name the download after something a human recognises. `cut-4` beats the
    # uuid slice in a Downloads folder, and the id stays available as the
    # unambiguous handle a token is minted against.
    stem = f"{pid}-{('cut-%d' % ref_n) if ref_n else render_id}"
    return Deliverable(
        path=path,
        content_type=_CONTENT_TYPES[path.suffix],
        filename=f"{stem}{path.suffix}",
        artifact_id=render_id,
        project_id=pid,
        genre=GENRE,
        ref=format_ref(ref_n) if ref_n else None,
        title=pid.replace("_", " "),
        duration_s=duration,
        size_bytes=stat.st_size,
        created_at=stat.st_mtime,
        meta={
            "strategy": meta.get("strategy"),
            "canvas": meta.get("canvas"),
            "ok": meta.get("ok"),
        },
    )


def resolve(email: str, project_id: str, artifact_id: str) -> Deliverable:
    """The caller's rendered artifact as a servable file — muvid's download authority.

    ``artifact_id`` is a render id OR a speakable reference (``cut 4``, ``#4``,
    ``4``). Raises ``KeyError`` for anything that does not resolve to an artifact
    of *this* caller — including ids shaped like an attack and other users'
    renders. (No ``PermissionError`` branch: workspaces are scoped by email, so
    "someone else's render" and "no such render" are indistinguishable here, and
    saying which would leak existence.)
    """
    from muvid.footage.workspace import safe_component

    pid, proj = _open(email, project_id)

    n = parse_ref(artifact_id)
    if n is not None:
        # A reference. Only we can resolve it, because only we know the ordering.
        for rid, ref_n in proj.ensure_render_refs().items():
            if ref_n == n and _render_file(proj, rid) is not None:
                return _deliverable(proj, pid, rid, n)
        raise KeyError(f"no {format_ref(n)!r} in project {project_id!r}")

    try:
        aid = safe_component(artifact_id, label="artifact_id")
    except ValueError as e:
        raise KeyError(str(e)) from None
    # Backfill here too, so a render reports the SAME reference however it was
    # asked for. Resolving by id used to skip this, which meant `cut 4` and
    # `b02fc05417ea` described one file with two different labels.
    return _deliverable(proj, pid, aid, proj.ensure_render_refs().get(aid))


def list_deliverables(email: str, project_id: str = None) -> "list[Deliverable]":
    """Every render this caller can be handed, newest first.

    Without this a reference is undiscoverable — a user could only name a render
    they still remembered from an earlier conversation, which is the state that
    left five finished videos unreachable. ``project_id=None`` spans every
    music-video project of theirs.
    """
    from muvid.footage.workspace import FootageWorkspace

    ws = FootageWorkspace.for_email(email)
    if project_id:
        try:
            pids = [_open(email, project_id)[0]]
        except KeyError:
            return []
    else:
        pids = [p["project_id"] for p in ws.list_projects() if p.get("project_id")]

    out = []
    for pid in pids:
        try:
            proj = ws.open_project(pid)
        except FileNotFoundError:
            continue
        refs = proj.ensure_render_refs()
        for rid, ref_n in refs.items():
            try:
                out.append(_deliverable(proj, pid, rid, ref_n))
            except KeyError:
                # A render dir with meta but no video file — half-written or
                # cleaned up. Skip it rather than failing the whole listing.
                continue
    out.sort(key=lambda d: d.created_at or 0, reverse=True)
    return out
