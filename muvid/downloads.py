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


__all__ = [
    "GENRE",
    "claim",
    "resolve",
    "list_deliverables",
    "list_projects",
    "organise",
]


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


def _read_render_meta(proj, render_id: str) -> dict:
    meta_path = proj.renders_dir / render_id / "meta.json"
    if not meta_path.exists():
        return {}
    import json

    try:
        loaded = json.loads(meta_path.read_text())
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def _deliverable(proj, kind, pid, render_id, ref_n, *, thumbnail=False) -> Deliverable:
    path = _render_file(proj, kind, render_id, thumbnail=thumbnail)
    if path is None:
        what = "poster" if thumbnail else "render"
        raise KeyError(f"no such {what} {render_id!r} in project {pid!r}")
    meta = _read_render_meta(proj, render_id)
    if ref_n is None and isinstance(meta.get("ref_n"), int):
        ref_n = meta["ref_n"]
    stat = path.stat()
    span = meta.get("rendered_span") or []
    duration = round(span[1] - span[0], 2) if len(span) == 2 else meta.get("duration")
    label = ("cut-%d" % ref_n) if ref_n else render_id
    stem = f"{pid}-{label}" + ("-poster" if thumbnail else "")
    # Organise state lives in its OWN sub-dict of meta.json ("organise":
    # {title, tags, note}) — the render pipeline already writes top-level
    # keys of its own (ref_n, edl, note, download, ...), and sharing the
    # top level made the pipeline's instructional `note` surface as a
    # user-assigned one, and organise(note="") delete a pipeline key
    # (adversarial-review finding). tags/note still SURFACE in
    # Deliverable.meta under the parameter names — the seam pins the
    # read-side spelling, not the storage layout.
    org = meta.get("organise") if isinstance(meta.get("organise"), dict) else {}
    assigned_title = org.get("title") if isinstance(org.get("title"), str) else None
    out_meta = {
        "strategy": meta.get("strategy"),
        "canvas": meta.get("canvas"),
        "visual": meta.get("visual"),
        "ok": meta.get("ok"),
        "muvid_genre": kind,
    }
    if org.get("tags"):
        out_meta["tags"] = list(org["tags"])
    if org.get("note"):
        out_meta["note"] = org["note"]
    return Deliverable(
        path=path,
        content_type=_CONTENT_TYPES[path.suffix],
        filename=f"{stem}{path.suffix}",
        artifact_id=render_id + (THUMBNAIL_SUFFIX if thumbnail else ""),
        project_id=pid,
        genre=GENRE,
        ref=(format_ref(ref_n) if ref_n else None) if not thumbnail else None,
        title=(assigned_title or pid.replace("_", " "))
        + (" (poster)" if thumbnail else ""),
        duration_s=None if thumbnail else duration,
        size_bytes=stat.st_size,
        created_at=stat.st_mtime,
        meta=out_meta,
    )


def _render_id_for_title(proj, title: str) -> "str | None":
    """The render whose organise-assigned title matches, case-folded, or None."""
    rdir = proj.renders_dir
    if not rdir.is_dir():
        return None
    want = title.strip().casefold()
    for child in rdir.iterdir():
        if not child.is_dir():
            continue
        org = _read_render_meta(proj, child.name).get("organise")
        assigned = org.get("title") if isinstance(org, dict) else None
        if isinstance(assigned, str) and assigned.strip().casefold() == want:
            return child.name
    return None


def resolve(email: str, project_id: str, artifact_id: str) -> Deliverable:
    """The caller's rendered artifact as a servable file — muvid's download authority.

    ``artifact_id`` is a render id, a speakable reference (``cut 4``, ``#4``,
    ``4``), an organise-ASSIGNED title (matched case-folded within the
    project's drawer), or any of those with ``.thumbnail`` appended for the
    poster image. Precedence: a reference never falls through to titles, and
    a raw id always beats a title — an id-shaped title can therefore never
    win, which is why ``organise`` refuses id-colliding titles at write time.
    Spans both muvid genres.

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
    try:
        return _deliverable(
            proj, kind, pid, aid, _refs_for(proj, kind).get(aid), thumbnail=want_thumb
        )
    except KeyError:
        # Not an id — an organise-assigned TITLE must resolve from the moment
        # it is accepted (the seam's accepted-title-resolves obligation).
        rid = _render_id_for_title(proj, raw)
        if rid is None:
            raise
        return _deliverable(
            proj, kind, pid, rid, _refs_for(proj, kind).get(rid), thumbnail=want_thumb
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


def _count_renders(proj, kind: str) -> int:
    """Finished renders in one project — ``0`` is the load-bearing answer.

    A footage project with a song and twelve clips and no cut is exactly what
    reelee#333 exists to make findable; a listing renders ``0`` as "no cut
    yet" rather than omitting the project.
    """
    rdir = proj.renders_dir
    if not rdir.is_dir():
        return 0
    n = 0
    for child in rdir.iterdir():
        if child.is_dir() and _render_file(proj, kind, child.name) is not None:
            n += 1
    return n


def list_projects(email: str) -> list:
    """Every project of this caller's, across both muvid genres — rendered or not.

    muvid's half of ``nw.delivery.ProjectLister``. Returns
    ``list[nw.delivery.ProjectSummary]``, newest-modified first. One
    registration spans both drawers, exactly as ``resolve`` does — a
    ``project_id`` present in both yields two rows, disambiguated by
    ``meta["muvid_genre"]`` (the drawer split already bit once, muvid#23).

    The seam's error contract applies: ``[]`` is a positive claim ("no
    projects under this caller"), an infrastructure failure raises, and this
    function never creates a workspace directory just to list it —
    ``for_email`` constructs paths, only create/render paths mkdir.

    Imported lazily so an environment carrying an older ``nw`` loses exactly
    this capability, never the module (and with it ``resolve``).
    """
    from nw.delivery import ProjectSummary

    rows = []
    for kind, workspace, _stem in _sources():
        ws = workspace.for_email(email)
        for r in ws.list_projects():
            pid = r["project_id"]
            try:
                proj = ws.open_project(pid)
            except (FileNotFoundError, ValueError):
                continue  # vanished mid-scan — genuinely absent
            rows.append(
                ProjectSummary(
                    project_id=pid,
                    title=r.get("title") or pid,
                    genre=GENRE,
                    created_at=r.get("created"),
                    modified_at=r.get("modified"),
                    deliverable_count=_count_renders(proj, kind),
                    meta={"muvid_genre": kind},
                )
            )
    rows.sort(key=lambda p: p.modified_at or 0.0, reverse=True)
    return rows


def organise(
    email: str,
    project_id: str,
    artifact_id: str,
    *,
    title: "str | None" = None,
    tags: "list | None" = None,
    note: "str | None" = None,
) -> Deliverable:
    """Rename, tag or annotate one render — muvid's half of ``nw.delivery.Organiser``.

    Persistence is the render's own ``meta.json`` — genre-owned state next to
    what it names, never a host label store. The contract's durability
    guarantees, kept the way the seam demands:

    - ``artifact_id`` (the render id) never changes and no file is renamed —
      the id is what a signed token is minted against, and ``ref_n`` (the
      ``cut N`` ordinal) is never renumbered. ``organise`` edits the human
      TITLE only.
    - An accepted title resolves immediately (``resolve`` scans assigned
      titles), which is why collisions — with another assigned title OR with
      any render id in the project — are refused naming the holder.
    - ``None`` leaves a field unchanged; ``""``/``[]`` clears it. A request
      changing nothing is refused. All-or-nothing: everything is validated
      before one byte is written.
    - The return is the deliverable AS RE-READ through ``_deliverable``
      (which reads ``meta.json`` back), so the reply is exactly what the
      next listing shows. ``tags``/``note`` surface in ``Deliverable.meta``
      under the parameter names, per the seam.

    Auth is ``resolve``'s exactly: ``KeyError`` when nothing is the caller's
    (no existence leaks), ``ValueError`` when the request itself is refused.
    A ``.thumbnail`` target is refused — the poster has no name of its own.
    """
    import json

    from nw.delivery import check_title

    if title is None and tags is None and note is None:
        raise ValueError("nothing to change: pass title=, tags= and/or note=")
    raw = (artifact_id or "").strip()
    if raw.endswith(THUMBNAIL_SUFFIX):
        raise ValueError(
            "organise the render itself, not its poster — drop the "
            f"{THUMBNAIL_SUFFIX!r} suffix"
        )

    kind, pid, proj = _open(email, project_id)
    # The target must resolve as the caller's BEFORE anything is written —
    # and resolving through resolve() itself keeps one id vocabulary. (This
    # may backfill ref ordinals as a side effect: the ref store's own
    # idempotent behaviour on any resolve, not part of this request.)
    target = resolve(email, project_id, raw)
    render_id = target.artifact_id

    new_title: "str | None" = None
    if title is not None and title != "":
        new_title = check_title(title)
        if new_title.endswith(THUMBNAIL_SUFFIX):
            raise ValueError(
                f"a title cannot end with {THUMBNAIL_SUFFIX!r} — resolve "
                "strips that suffix to address posters, so the name could "
                "never win"
            )
        want = new_title.casefold()
        rdir = proj.renders_dir
        if rdir.is_dir():
            for child in rdir.iterdir():
                if not child.is_dir() or child.name == render_id:
                    continue
                if child.name.casefold() == want:
                    raise ValueError(
                        f"{new_title!r} collides with render id {child.name!r}"
                    )
                sib = _read_render_meta(proj, child.name).get("organise")
                other = sib.get("title") if isinstance(sib, dict) else None
                if isinstance(other, str) and other.strip().casefold() == want:
                    raise ValueError(
                        f"{new_title!r} is already the title of {child.name!r}"
                    )
    if tags is not None and not isinstance(tags, (list, tuple)):
        raise ValueError("tags must be a list of strings (or [] to clear)")

    meta = _read_render_meta(proj, render_id)
    org = meta.get("organise") if isinstance(meta.get("organise"), dict) else {}
    org = dict(org)
    if title is not None:
        if title == "":
            org.pop("title", None)
        else:
            org["title"] = new_title
    if tags is not None:
        if len(tags) == 0:
            org.pop("tags", None)
        else:
            org["tags"] = [str(t) for t in tags]
    if note is not None:
        if note == "":
            org.pop("note", None)
        else:
            org["note"] = str(note)
    if org:
        meta["organise"] = org
    else:
        meta.pop("organise", None)

    # Atomic replace: meta.json carries PIPELINE-critical keys (ref_n, edl,
    # download, ...) — a truncate-then-write interrupted mid-write would
    # corrupt refs and status for this render, so never write in place.
    import os
    import tempfile

    meta_path = proj.renders_dir / render_id / "meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(meta_path.parent), prefix=".meta.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(meta, indent=2))
        os.replace(tmp, meta_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # The receipt: re-read from storage, never echo the request.
    return _deliverable(
        proj, kind, pid, render_id, _refs_for(proj, kind).get(render_id)
    )
