"""``muvid.Project`` — a music-video production a HOST places and serves.

The studio (reelee) opens every project as its genre's own class: it reads the genre
envelope nw persisted at creation, finds the genre's owning package, and builds that
package's ``Project`` (``reelee.genres.project_class_for``). This is muvid's: an
:class:`nw.Project` sibling folder, created where the host asks
(:func:`create_project_at`, nw#84 placement), holding one of muvid's productions:

- a ``music_video`` production keeps muvid's footage layout unchanged, one level down,
  at ``<project>/footage/`` (:attr:`Project.footage` — the same
  :class:`~muvid.footage.workspace.MusicVideoFootageProject` the MCP connector uses, so
  every operation in :mod:`muvid.footage.service` works on it as-is);
- a ``lyric-video`` production keeps its song, its sources (lyrics, treatment, word
  timings, the poem) and its renders at ``<project>/lyric/``
  (:attr:`Project.lyric`, a :class:`LyricVideoProduction`). v1 shows these; editing a
  treatment is phase 2.

Everything media that lands in either is also registered in the host's artifact catalog
(:attr:`Project.media_catalog`, :mod:`muvid.catalog`), so the host's
``GET /api/artifacts/{id}/bytes`` can play it, and each production has a cover frame
(:meth:`Project.cover_artifact_id`).

This module imports ``nw`` (the ``mcp`` extra); ``import muvid`` does not import it —
``muvid.Project`` resolves lazily through the package's ``__getattr__``.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Optional

import nw

from muvid.catalog import HostArtifactCatalog, hash_file
from muvid.footage.workspace import (
    DEFAULT_CANVAS_NAME,
    MusicVideoFootageProject,
    atomic_write_text,
    init_footage_project,
)
from muvid.paths import safe_component

#: Where a hosted project keeps each production kind's files.
FOOTAGE_DIRNAME = "footage"
LYRIC_DIRNAME = "lyric"

#: What a lyric-video source file is to the production.
SOURCE_ROLES = ("lyrics", "treatment", "timings", "poem", "other")
#: A text source at most this large is returned inline by ``status`` (bytes).
SOURCE_INLINE_MAX_BYTES = 64 * 1024
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".json", ".srt", ".ass", ".lrc", ".csv"})


class Project(nw.Project):
    """A muvid production placed by a host — an ``nw.Project`` with muvid's surface."""

    @cached_property
    def media_catalog(self) -> HostArtifactCatalog:
        """The host's artifact catalog of this project (``<root>/.reelee/artifacts``)."""
        return HostArtifactCatalog(self.root)

    @property
    def genre(self) -> Optional[str]:
        """The genre this project was created as (``music_video``, ``lyric-video``)."""
        return (self.resolved_genre() or {}).get("genre")

    @property
    def title(self) -> str:
        return self.read_spec().title or self.root.name

    @cached_property
    def footage(self) -> MusicVideoFootageProject:
        """The footage production at ``<root>/footage`` (created on first use, with the
        canvas this project was created with)."""
        params = (self.resolved_genre() or {}).get("params") or {}
        return init_footage_project(
            self.root / FOOTAGE_DIRNAME,
            project_id=self.root.name,
            title=self.title,
            canvas=params.get("canvas") or DEFAULT_CANVAS_NAME,
            media_catalog=self.media_catalog,
        )

    @cached_property
    def lyric(self) -> "LyricVideoProduction":
        """The lyric-video production at ``<root>/lyric``."""
        return LyricVideoProduction(
            self.root / LYRIC_DIRNAME, media_catalog=self.media_catalog
        )

    def cover_artifact_id(self) -> Optional[str]:
        """The artifact id of the frame that stands for this production on a card.

        The footage production's cover (its newest render, else its first clip), else
        the lyric production's (its first render). ``None`` when there is no picture
        yet — a card with a placeholder is honest. Read-only.
        """
        for manifest in (
            self.root / FOOTAGE_DIRNAME / "manifest.json",
            self.root / LYRIC_DIRNAME / "manifest.json",
        ):
            cover = (_read_json(manifest) or {}).get("cover") or {}
            if cover.get("artifact_id"):
                return cover["artifact_id"]
        return None


def create_project_at(
    projects_dir,
    project_id: str,
    *,
    title: str = "",
    canvas: Optional[str] = None,
    force: bool = False,
) -> Project:
    """Create a muvid production at ``projects_dir/<project_id>`` — the host-placed create.

    The counterpart to ``FootageWorkspace.create_project`` (muvid's own per-caller
    workspace): a host that will SERVE the project puts it where its own resolver and
    lister look (nw#84). ``project_id`` is a single traversal-safe component, so the
    result is always a direct child of ``projects_dir``. ``canvas`` (a music video's)
    creates the footage production right away with that canvas; ``None`` leaves it to
    first use, which reads the canvas from the genre envelope.
    """
    root = Path(projects_dir) / safe_component(project_id, label="project_id")
    root.parent.mkdir(parents=True, exist_ok=True)
    project = Project.init(root, title=title or project_id, force=force)
    if canvas is not None:
        init_footage_project(
            project.root / FOOTAGE_DIRNAME,
            project_id=project.root.name,
            title=title or project_id,
            canvas=canvas,
            media_catalog=project.media_catalog,
        )
    return project


# -- the lyric-video production (view-only in v1) ------------------------------


@dataclass(frozen=True)
class LyricVideoProduction:
    """A lyric video's song, sources and renders, under one directory.

    Layout: ``manifest.json`` (song, sources, renders, cover), ``song/``, ``sources/``,
    ``renders/<render_id>/final.mp4``. Every write is atomic and idempotent by content:
    adding the same bytes under the same name again changes nothing.
    """

    root: Path
    media_catalog: object = field(default=None, compare=False, repr=False)

    # -- manifest -------------------------------------------------------------
    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def manifest(self) -> dict:
        return _read_json(self.manifest_path) or {"sources": [], "renders": []}

    def _write_manifest(self, m: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.manifest_path, json.dumps(m, indent=2))

    def _register(self, path: Path, *, kind: str, **meta) -> Optional[str]:
        if self.media_catalog is None:
            return None
        return self.media_catalog.register(path, kind=kind, **meta)

    # -- writes ---------------------------------------------------------------
    def set_song(self, src, *, name: str = "") -> dict:
        """Store (replacing) the song; returns its record."""
        src = Path(src)
        dest = self.root / "song" / f"song{src.suffix.lower()}"
        digest = hash_file(src)
        m = self.manifest()
        if (m.get("song") or {}).get("hash") != digest:
            shutil.rmtree(self.root / "song", ignore_errors=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        record = {
            "file": dest.name,
            "name": name or src.name,
            "hash": digest,
            "duration": _duration(dest),
        }
        _set_or_drop(
            record,
            "artifact_id",
            self._register(
                dest, kind="audio", artifact_id=digest, duration_s=record["duration"]
            ),
        )
        m["song"] = record
        self._write_manifest(m)
        return record

    def add_source(self, src, *, role: str, name: str = "") -> dict:
        """Store one source file (lyrics, treatment, timings, poem …); returns its record."""
        if role not in SOURCE_ROLES:
            raise ValueError(f"unknown source role {role!r}; one of {SOURCE_ROLES}")
        src = Path(src)
        fname = safe_component(name or src.name, label="source name")
        dest = self.root / "sources" / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not (dest.exists() and hash_file(dest) == hash_file(src)):
            shutil.copyfile(src, dest)
        record = {"file": fname, "role": role, "hash": hash_file(dest)}
        if dest.suffix.lower() == ".json":
            _set_or_drop(record, "artifact_id", self._register(dest, kind="json"))
        m = self.manifest()
        m["sources"] = [s for s in m.get("sources", []) if s.get("file") != fname]
        m["sources"].append(record)
        self._write_manifest(m)
        return record

    def add_render(self, src, *, render_id: str, label: str = "") -> dict:
        """Store one finished video; returns its record (listed in insertion order)."""
        rid = safe_component(render_id, label="render_id")
        src = Path(src)
        dest = self.root / "renders" / rid / "final.mp4"
        digest = hash_file(src)
        if not (dest.exists() and hash_file(dest) == digest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        record = {
            "render_id": rid,
            "label": label or rid,
            "file": f"renders/{rid}/final.mp4",
            "duration": _duration(dest),
        }
        _set_or_drop(
            record,
            "artifact_id",
            self._register(
                dest, kind="video", artifact_id=digest, duration_s=record["duration"]
            ),
        )
        m = self.manifest()
        rows = [r for r in m.get("renders", []) if r.get("render_id") != rid]
        # Keep the position a re-import finds it at; a new render goes last.
        position = next(
            (
                i
                for i, r in enumerate(m.get("renders", []))
                if r.get("render_id") == rid
            ),
            len(rows),
        )
        rows.insert(position, record)
        m["renders"] = rows
        self._write_manifest(m)
        return record

    def refresh_cover(self) -> Optional[str]:
        """Take the cover frame from the FIRST render (the production's main one)."""
        from muvid.footage.service import grab_cover_frame

        renders = self.manifest().get("renders") or []
        if not renders or self.media_catalog is None:
            return None
        frame = self.root / "cover.jpg"
        grab_cover_frame(self.root / renders[0]["file"], frame)
        artifact_id = self._register(frame, kind="image")
        m = self.manifest()
        cover = {"file": frame.name, "taken_from": f"render:{renders[0]['render_id']}"}
        _set_or_drop(cover, "artifact_id", artifact_id)
        m["cover"] = cover
        self._write_manifest(m)
        return artifact_id

    # -- reads ----------------------------------------------------------------
    def status(self) -> dict:
        """Song, sources (small text ones inline), renders and cover — for the screen."""
        m = self.manifest()
        return {
            "song": m.get("song"),
            "sources": [self._source_row(s) for s in m.get("sources", [])],
            "renders": list(m.get("renders", [])),
            "cover_artifact_id": (m.get("cover") or {}).get("artifact_id"),
            "editable": False,
        }

    def _source_row(self, record: dict) -> dict:
        row = dict(record)
        path = self.root / "sources" / record["file"]
        if (
            path.suffix.lower() in _TEXT_SUFFIXES
            and path.is_file()
            and path.stat().st_size <= SOURCE_INLINE_MAX_BYTES
        ):
            row["text"] = path.read_text(encoding="utf-8", errors="replace")
        return row


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _set_or_drop(d: dict, key: str, value) -> None:
    if value is None:
        d.pop(key, None)
    else:
        d[key] = value


def _duration(path: Path) -> Optional[float]:
    from muvid.visualize.ffmpeg import FfmpegError, media_duration

    try:
        return round(float(media_duration(path)), 3)
    except FfmpegError:
        return None


__all__ = [
    "Project",
    "create_project_at",
    "LyricVideoProduction",
    "SOURCE_ROLES",
]
