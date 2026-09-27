"""Bring a finished production into a host's projects dir as a :class:`muvid.Project`.

The verb is :func:`import_production`; the CLI door is
``python -m muvid.importing MANIFEST PROJECTS_DIR [--dry-run]``. One JSON manifest per
production says where its pieces are; the importer creates (or reopens) the project at
``PROJECTS_DIR/<id>`` exactly as a host would (``nw.create_genre_project`` with
placement, so the genre envelope is recorded and the host opens it as a
``muvid.Project``), and fills it through the SAME operations the studio and the
connector use (:mod:`muvid.footage.service`) — so an imported edit is validated by
``validate_edl`` like any other, and every file lands in the host's artifact catalog.

**Idempotent**: run it twice and you have one project. The song, a clip or a render
whose bytes are already there is left alone; an offset or an edit that already says the
same thing is not rewritten.

Two kinds of manifest (paths absolute, ``~``-expanded, or relative to the manifest):

``footage`` — a music video cut from footage::

    {"kind": "footage", "id": "que_calor", "title": "Que Calor", "canvas": "landscape",
     "song": {"path": "source/master.m4a"}              # or {"path": x.mp4, "extract_audio": true}
     "clips": [{"id": "c01", "path": "footage/01.mp4", "name": "Camera A",
                "offset_s": 28.854}],                    # offset_s = a DECLARED offset
     "edits": [{"id": "v1", "name": "V1", "edl_path": "work/edl_v1d.json",
                "how_made": "…",                         # or "edl": [...] inline, or
                "span": [0.162, 157.13]}],               # "whole_song": "c01" (one shot);
                                                         # span: default the whole song
     "renders": [{"id": "v1e", "path": "out/v1e.mp4", "edit_id": "v1", "label": "V1"}]}

``lyric-video`` — a lyric video (view-only in v1)::

    {"kind": "lyric-video", "id": "il_pleut", "title": "Il Pleut",
     "template": "calligram", "song": {"path": "…"},
     "sources": [{"role": "lyrics", "path": "…"}],     # role: lyrics|treatment|timings|poem|other
     "renders": [{"id": "readable_v3", "path": "…", "label": "…"}]}

Manifests name private paths, so they live beside the data or under
``~/.local/share/muvid/imports/`` — never in a repository.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Optional

from muvid.importing._edl import clip_in_drift, edl_from_document, is_framing_edl

#: The manifest kinds and the genre each is created as.
KIND_GENRES = {"footage": "music_video", "lyric-video": "lyric-video"}
#: Offsets closer than this (seconds) are the same declaration.
_OFFSET_TOLERANCE_S = 1e-6


class ImportRefused(ValueError):
    """The manifest cannot be imported as written (the message says which part)."""


def load_manifest(path) -> tuple[dict, Path]:
    """``(manifest, base_dir)`` — relative paths in it resolve against ``base_dir``."""
    path = Path(path).expanduser()
    manifest = json.loads(path.read_text())
    if not isinstance(manifest, dict):
        raise ImportRefused(f"{path.name}: a manifest is a JSON object")
    return manifest, path.parent


def import_production(
    manifest,
    projects_dir,
    *,
    base_dir=None,
    dry_run: bool = False,
    caller: str = "importer",
) -> dict:
    """Import one production; returns a report of what was done (or would be).

    ``manifest`` is a dict or a path to one; ``projects_dir`` is the host's projects
    directory (the project lands at ``projects_dir/<id>``). ``dry_run`` checks every
    file and every edit conversion and writes nothing.
    """
    if not isinstance(manifest, Mapping):
        manifest, default_base = load_manifest(manifest)
        base_dir = base_dir or default_base
    base = Path(base_dir or ".").expanduser()
    kind = manifest.get("kind")
    if kind not in KIND_GENRES:
        raise ImportRefused(
            f"unknown manifest kind {kind!r}; one of {sorted(KIND_GENRES)}"
        )
    pid = manifest.get("id")
    if not pid:
        raise ImportRefused("the manifest has no 'id'")
    resolve = _resolver(base)
    _check_files(manifest, resolve)
    report = {"id": pid, "kind": kind, "dry_run": dry_run}
    if dry_run:
        if kind == "footage":
            report["edits"] = {
                e["id"]: (
                    f"one shot of {e['whole_song']}"
                    if "whole_song" in e
                    else f"{len(_edit_edl(e, manifest, resolve))} entries"
                )
                for e in manifest.get("edits", [])
            }
        return report
    project, created = _open_or_create(manifest, Path(projects_dir), caller=caller)
    report.update(project=str(project.root), created=created)
    if kind == "footage":
        report.update(_import_footage(project.footage, manifest, resolve))
    else:
        report.update(_import_lyric(project.lyric, manifest, resolve))
    return report


# -- the project ---------------------------------------------------------------


def _open_or_create(manifest: Mapping, projects_dir: Path, *, caller: str):
    """``(muvid.Project, created)`` — created through nw as a host would."""
    import nw

    import muvid.genre  # noqa: F401 — registers muvid's genres and their factories
    from muvid.production import Project

    genre = KIND_GENRES[manifest["kind"]]
    root = projects_dir / manifest["id"]
    if (root / "project.json").exists():
        project = Project(root)
        if project.genre != genre:
            raise ImportRefused(
                f"{root} is a {project.genre!r} project, not a {genre!r} one"
            )
        return project, False
    template = (
        manifest.get("canvas") if genre == "music_video" else manifest.get("template")
    )
    nw.create_genre_project(
        genre,
        caller,
        manifest["id"],
        title=manifest.get("title") or manifest["id"],
        template=template,
        projects_dir=projects_dir,
    )
    return Project(root), True


# -- footage ---------------------------------------------------------------------


def _import_footage(fp, manifest: Mapping, resolve) -> dict:
    from muvid.footage import service

    report: dict = {}
    report["song"] = _import_song(fp, manifest.get("song"), resolve)
    clips = {}
    for c in manifest.get("clips", []):
        clips[c["id"]] = _import_clip(fp, c, resolve)
    report["clips"] = clips
    offsets = {}
    for c in manifest.get("clips", []):
        if c.get("offset_s") is not None:
            offsets[c["id"]] = _import_offset(fp, c["id"], float(c["offset_s"]))
    report["offsets"] = offsets
    report["edits"] = {
        e["id"]: _import_edit(fp, e, _edit_edl(e, manifest, resolve, fp=fp))
        for e in manifest.get("edits", [])
    }
    drift = {}
    for e in manifest.get("edits", []):
        rows = _edit_drift(e, manifest, resolve, fp=fp)
        if rows:
            drift[e["id"]] = rows
    report["clip_in_drift"] = {
        eid: {
            "cuts_over_one_frame": len(rows),
            "max_drift_s": max(abs(r["drift_s"]) for r in rows),
            "cuts": rows,
        }
        for eid, rows in drift.items()
    }
    report["warnings"] = [
        f"edit {eid!r}: {len(rows)} cut(s) start more than one frame from where the "
        f"planner cut them (up to {max(abs(r['drift_s']) for r in rows):.3f} s) — muvid "
        "derives each in-point from the clip's offset, so those cuts show different "
        "frames than the original render"
        for eid, rows in drift.items()
    ]
    report["renders"] = {
        r["id"]: service.import_render(
            fp,
            path=str(resolve(r["path"])),
            render_id=r["id"],
            label=r.get("label", ""),
            edit_id=r.get("edit_id", ""),
        ).get("artifact_id")
        for r in manifest.get("renders", [])
    }
    report["cover_artifact_id"] = service.refresh_cover(fp)
    return report


def _import_song(fp, song, resolve) -> str:
    from muvid.catalog import hash_file
    from muvid.footage import service

    if not song:
        return "none"
    spec = {"path": song} if isinstance(song, str) else dict(song)
    src = resolve(spec["path"])
    with tempfile.TemporaryDirectory() as tmp:
        if spec.get("extract_audio"):
            src = _extract_audio(src, Path(tmp) / f"{src.stem}.m4a")
        if fp.has_song() and fp.song_hash() == hash_file(src):
            return "unchanged"
        service.set_song(
            fp, path=str(src), filename=spec.get("name") or Path(spec["path"]).name
        )
    return "set"


def _extract_audio(video: Path, dest: Path) -> Path:
    """The audio track of ``video`` as-is (stream copy, bit-exact, no metadata), so the
    same source always yields the same bytes and a re-import sees "unchanged"."""
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "copy",
            "-map_metadata",
            "-1",
            "-fflags",
            "+bitexact",
            "-flags:a",
            "+bitexact",
            str(dest),
        ],
        check=True,
    )
    return dest


def _import_clip(fp, clip: Mapping, resolve) -> str:
    from muvid.catalog import hash_file
    from muvid.footage import service

    src = resolve(clip["path"])
    stored = fp.clip_paths().get(clip["id"])
    if stored and Path(stored).exists() and hash_file(stored) == hash_file(src):
        return "unchanged"
    verdict = "added"
    if stored:
        service.remove_clip(fp, clip_id=clip["id"])
        verdict = "replaced"
    service.add_clip(
        fp,
        path=str(src),
        clip_id=clip["id"],
        name=clip.get("name", ""),
        filename=src.name,
    )
    return verdict


def _import_offset(fp, clip_id: str, offset_s: float) -> str:
    from muvid.footage import service
    from muvid.footage.edl import DECLARED

    current = next((a for a in fp.load_alignments() if a.clip_id == clip_id), None)
    if (
        current is not None
        and current.source == DECLARED
        and abs(current.offset_s - offset_s) <= _OFFSET_TOLERANCE_S
    ):
        return "unchanged"
    service.set_offset(fp, clip_id=clip_id, offset_s=offset_s)
    return "declared"


def _edit_edl(edit: Mapping, manifest: Mapping, resolve, *, fp=None) -> list[dict]:
    """The muvid EDL an edit entry of the manifest means (converted if need be).

    ``whole_song: <clip_id>`` is one shot of that clip over the whole song — the edit of
    a production that was never cut (its song length is the project's, so it needs one).
    """
    if "whole_song" in edit:
        return [
            {
                "song_start": 0.0,
                "song_end": fp.song_duration(),
                "clip_id": edit["whole_song"],
            }
        ]
    doc = edit.get("edl")
    if doc is None:
        doc = json.loads(resolve(edit["edl_path"]).read_text())
    sizes = _source_sizes(manifest, resolve, fp=fp) if is_framing_edl(doc) else {}
    return edl_from_document(doc, source_sizes=sizes)


def _edit_drift(edit: Mapping, manifest: Mapping, resolve, *, fp) -> list[dict]:
    """The planner in-points (``clip_in``) that disagree with muvid's by over a frame."""
    if "edl_path" not in edit and not isinstance(edit.get("edl"), (list, dict)):
        return []
    doc = edit.get("edl")
    if doc is None:
        doc = json.loads(resolve(edit["edl_path"]).read_text())
    offsets = {
        c["id"]: float(c["offset_s"])
        for c in manifest.get("clips", [])
        if c.get("offset_s") is not None
    }
    return clip_in_drift(doc, offsets=offsets, fps=_clip_rates(fp))


def _clip_rates(fp) -> dict:
    from muvid.footage.service import frame_rate

    rates = {}
    for cid, path in fp.clip_paths().items():
        rate = frame_rate(path)
        if rate:
            rates[cid] = rate
    return rates


def _source_sizes(manifest: Mapping, resolve, *, fp=None) -> dict:
    """Each clip's DISPLAYED frame size, from the project's copy or the source file."""
    from muvid.footage.service import frame_size

    stored = fp.clip_paths() if fp is not None else {}
    sizes = {}
    for c in manifest.get("clips", []):
        size = frame_size(stored.get(c["id"]) or resolve(c["path"]))
        if size:
            sizes[c["id"]] = tuple(size)
    return sizes


def _import_edit(fp, edit: Mapping, edl: list[dict]) -> str:
    """Save the edit, or bring an existing one in line with the manifest — its ``span``
    (the part of the song it covers, default the whole song) and its cut list."""
    from muvid.footage import service

    span = edit.get("span")
    if not fp.has_edit(edit["id"]):
        service.save_edit(
            fp,
            edl=edl,
            name=edit.get("name") or edit["id"],
            how_made=edit.get("how_made") or "imported",
            edit_id=edit["id"],
            span=span,
        )
        return "saved"
    current = service.get_edit(fp, edit_id=edit["id"])
    whole = [0.0, fp.song_duration()]
    wanted_span = [float(x) for x in span] if span else whole
    verdict = "unchanged"
    if current["span"] != wanted_span:
        service.set_span(
            fp, edit_id=edit["id"], start_s=wanted_span[0], end_s=wanted_span[1]
        )
        verdict = "replaced"
    proposed = [service.edl_json(e) for e in _normalised(edl, fp.song_duration())]
    if service.get_edit(fp, edit_id=edit["id"])["edl"] != proposed:
        service.replace_edit(fp, edit_id=edit["id"], edl=edl)
        verdict = "replaced"
    return verdict


def _normalised(edl, song_duration: float):
    """The edit as stored: the whole song, gap-filled (a span is only a window)."""
    from muvid.footage.edl import fill_gaps

    return fill_gaps(edl, song_duration)


# -- lyric video -----------------------------------------------------------------


def _import_lyric(production, manifest: Mapping, resolve) -> dict:
    report: dict = {}
    song = manifest.get("song")
    if song:
        spec = {"path": song} if isinstance(song, str) else dict(song)
        report["song"] = production.set_song(
            resolve(spec["path"]), name=spec.get("name", "")
        ).get("artifact_id")
    report["sources"] = {
        s.get("name") or Path(s["path"]).name: production.add_source(
            resolve(s["path"]), role=s["role"], name=s.get("name", "")
        ).get("artifact_id")
        for s in manifest.get("sources", [])
    }
    report["renders"] = {
        r["id"]: production.add_render(
            resolve(r["path"]), render_id=r["id"], label=r.get("label", "")
        ).get("artifact_id")
        for r in manifest.get("renders", [])
    }
    report["cover_artifact_id"] = production.refresh_cover()
    return report


# -- files -------------------------------------------------------------------------


def _resolver(base: Path):
    def resolve(p) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else base / path

    return resolve


def _check_files(manifest: Mapping, resolve) -> None:
    """Every file the manifest names exists AND can be read — checked before anything
    is written. Readability is its own check because macOS can refuse a file that
    plainly exists (a Downloads file carrying an app-scoped ``com.apple.macl`` answers
    ``Operation not permitted``), and finding that halfway through an import leaves a
    half-built project."""
    named = []
    song = manifest.get("song")
    if song:
        named.append(song if isinstance(song, str) else song["path"])
    for key in ("clips", "renders", "sources"):
        named += [item["path"] for item in manifest.get(key, [])]
    named += [e["edl_path"] for e in manifest.get("edits", []) if "edl_path" in e]
    missing = [str(resolve(p)) for p in named if not resolve(p).is_file()]
    if missing:
        raise ImportRefused(f"missing files: {missing}")
    unreadable = []
    for p in named:
        try:
            with open(resolve(p), "rb") as f:
                f.read(1)
        except OSError as e:
            unreadable.append(f"{resolve(p)} ({e.strerror})")
    if unreadable:
        raise ImportRefused(f"unreadable files: {unreadable}")


__all__ = ["import_production", "load_manifest", "ImportRefused", "edl_from_document"]
