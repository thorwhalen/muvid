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

Two genres, one resolver
------------------------
muvid hosts two genres behind one ``muvid_`` prefix and two separate per-user
workspaces: the footage music-video pipeline and the ffmpeg visualizer. Both
render, both were unreachable, and a caller says ``genre='muvid'`` for either —
so resolution spans both rather than making the caller know which drawer their
project is in. (That drawer split is exactly what produced "no project X for
you" against a project that existed — thorwhalen/muvid#23.)

The visualizer's retrieval was filed as **muvid#8**, deferred behind "a download
URL depends on the storage backend minting one (``dol.content_url`` is None for
a local store)". That premise is obsolete: the connector's signed route never
calls ``dol.content_url`` — it resolves a path and streams it. The storage
migration was never the blocker; the missing resolver was.

The visualizer also writes a ``thumbnail.jpg`` beside its video, which is listed
as a deliverable of its own. It is the one small image in the whole federation,
and the only artifact that could plausibly be returned inline in a chat.

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
    ".jpg": "image/jpeg",
    ".png": "image/png",
}

#: Suffix that addresses a render's poster image rather than its video. A single
#: component, so it survives ``safe_component`` — ``<render_id>/thumbnail`` would
#: not, and inventing a second id space for one file would be worse.
THUMBNAIL_SUFFIX = ".thumbnail"


#: The two genres muvid hosts, each a (workspace class, render filename stem)
#: pair. Order matters: footage first, because that is where the work is and a
#: project_id present in both should resolve to the one the caller meant.
def _sources():
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.workspace import VisualizerWorkspace

    return (
        ("footage", FootageWorkspace, "final"),
        ("visualizer", VisualizerWorkspace, "video"),
    )


__all__ = ["GENRE", "claim", "resolve", "list_deliverables"]


def claim(project_id: str, artifact_id: str) -> dict:
    """The claim dict a tool returns — what the connector turns into a signed URL."""
    return {"genre": GENRE, "project_id": project_id, "artifact_id": artifact_id}


def _open(email: str, project_id: str):
    """``(kind, pid, project)`` for whichever genre owns this project, or ``KeyError``.

    Never distinguishes "not yours" from "no such thing": both workspaces are
    email-scoped, so saying which would leak the existence of another tenant's
    project.
    """
    from muvid.footage.workspace import safe_component

    try:
        pid = safe_component(project_id, label="project_id")
    except ValueError as e:
        raise KeyError(str(e)) from None
    for kind, workspace, _stem in _sources():
        try:
            return kind, pid, workspace.for_email(email).open_project(pid)
        except (FileNotFoundError, ValueError):
            continue
    raise KeyError(f"no such project {project_id!r}")


def _render_file(proj, kind: str, render_id: str, *, thumbnail: bool = False):
    """The video (or poster) for one render, or ``None``."""
    stem = next(st for k, _w, st in _sources() if k == kind)
    render_dir = proj.renders_dir / render_id
    if thumbnail:
        for ext in (".jpg", ".png"):
            p = render_dir / f"thumbnail{ext}"
            if p.exists():
                return p
        return None
    for ext in _CONTENT_TYPES:
        p = render_dir / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def _refs_for(proj, kind: str) -> dict:
    """``{render_id: ordinal}``. Only the footage genre persists ordinals.

    The visualizer's buckets have no ``ensure_render_refs`` — adding one would be
    a schema change to a second store for a genre whose renders nobody has
    referred to yet. Its deliverables carry no ``ref`` and are addressed by id,
    which is honest rather than a fabricated numbering.
    """
    if kind != "footage":
        return {}
    return proj.ensure_render_refs()


def _deliverable(proj, kind, pid, render_id, ref_n, *, thumbnail=False) -> Deliverable:
    path = _render_file(proj, kind, render_id, thumbnail=thumbnail)
    if path is None:
        what = "poster" if thumbnail else "render"
        raise KeyError(f"no such {what} {render_id!r} in project {pid!r}")
    meta = {}
    meta_path = proj.renders_dir / render_id / "meta.json"
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
    duration = round(span[1] - span[0], 2) if len(span) == 2 else meta.get("duration")
    label = ("cut-%d" % ref_n) if ref_n else render_id
    stem = f"{pid}-{label}" + ("-poster" if thumbnail else "")
    return Deliverable(
        path=path,
        content_type=_CONTENT_TYPES[path.suffix],
        filename=f"{stem}{path.suffix}",
        artifact_id=render_id + (THUMBNAIL_SUFFIX if thumbnail else ""),
        project_id=pid,
        genre=GENRE,
        ref=(format_ref(ref_n) if ref_n else None) if not thumbnail else None,
        title=pid.replace("_", " ") + (" (poster)" if thumbnail else ""),
        duration_s=None if thumbnail else duration,
        size_bytes=stat.st_size,
        created_at=stat.st_mtime,
        meta={
            "strategy": meta.get("strategy"),
            "canvas": meta.get("canvas"),
            "visual": meta.get("visual"),
            "ok": meta.get("ok"),
            "muvid_genre": kind,
        },
    )


def resolve(email: str, project_id: str, artifact_id: str) -> Deliverable:
    """The caller's rendered artifact as a servable file — muvid's download authority.

    ``artifact_id`` is a render id, a speakable reference (``cut 4``, ``#4``,
    ``4``), or a render id with ``.thumbnail`` appended for the visualizer's
    poster image. Spans both muvid genres.

    Raises ``KeyError`` for anything that does not resolve to an artifact of
    *this* caller — including ids shaped like an attack and other users' renders.
    (No ``PermissionError`` branch: workspaces are scoped by email, so "someone
    else's render" and "no such render" are indistinguishable here, and saying
    which would leak existence.)
    """
    from muvid.footage.workspace import safe_component

    kind, pid, proj = _open(email, project_id)

    raw = (artifact_id or "").strip()
    want_thumb = raw.endswith(THUMBNAIL_SUFFIX)
    if want_thumb:
        raw = raw[: -len(THUMBNAIL_SUFFIX)]

    n = parse_ref(raw)
    if n is not None:
        # A reference. Only we can resolve it, because only we know the ordering.
        for rid, ref_n in _refs_for(proj, kind).items():
            if ref_n == n and _render_file(proj, kind, rid) is not None:
                return _deliverable(proj, kind, pid, rid, n, thumbnail=want_thumb)
        raise KeyError(f"no {format_ref(n)!r} in project {project_id!r}")

    try:
        aid = safe_component(raw, label="artifact_id")
    except ValueError as e:
        raise KeyError(str(e)) from None
    # Backfill here too, so a render reports the SAME reference however it was
    # asked for. Resolving by id used to skip this, which meant `cut 4` and
    # `b02fc05417ea` described one file with two different labels.
    return _deliverable(
        proj, kind, pid, aid, _refs_for(proj, kind).get(aid), thumbnail=want_thumb
    )


def list_deliverables(email: str, project_id: str = None) -> "list[Deliverable]":
    """Every render this caller can be handed, newest first, across both genres.

    Without this a reference is undiscoverable — a user could only name a render
    they still remembered from an earlier conversation, which is the state that
    left five finished videos unreachable. ``project_id=None`` spans every
    project of theirs.
    """
    out = []
    for kind, workspace, _stem in _sources():
        ws = workspace.for_email(email)
        try:
            pids = (
                [project_id]
                if project_id
                else [
                    p["project_id"] for p in ws.list_projects() if p.get("project_id")
                ]
            )
        except (OSError, ValueError):
            continue
        for pid in pids:
            try:
                proj = ws.open_project(pid)
            except (FileNotFoundError, ValueError):
                continue
            refs = _refs_for(proj, kind)
            rdir = proj.renders_dir
            if not rdir.is_dir():
                continue
            for child in sorted(rdir.iterdir()):
                if not child.is_dir():
                    continue
                rid = child.name
                for thumb in (False, True):
                    try:
                        out.append(
                            _deliverable(
                                proj, kind, pid, rid, refs.get(rid), thumbnail=thumb
                            )
                        )
                    except KeyError:
                        # No video (half-written) or no poster (the common case).
                        continue
    out.sort(key=lambda d: d.created_at or 0, reverse=True)
    return out
