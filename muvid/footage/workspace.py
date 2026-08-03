"""Per-user, STATEFUL project for the footage-aligned ``music_video`` genre.

Unlike the visualizer (a pure function of audio+cover), a music-video project accumulates
state across calls: one fixed song, several uploaded clips, a persisted alignment
manifest, and rendered outputs. This is deliberately NEW infrastructure (not the stateless
``VisualizerWorkspace``), reusing only the identity + fetch + tool-aggregation seams.

Layout (default root ``~/.local/share/muvid``; override via ``MUVID_DATA_HOME`` — shared
with the visualizer's root, different subtree). **Never** inside the app/deploy tree:

- ``{root}/music_video/projects/{email}/{project_id}/manifest.json`` — title, canvas, song
- ``.../song/song.<ext>`` — the one fixed clean song
- ``.../clips/{clip_id}.<ext>`` — an uploaded footage clip
- ``.../alignments.json`` — the persisted per-clip alignment
- ``.../renders/{render_id}/`` — an assembled music video
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from muvid.footage.edl import FootageAlignment

DATA_HOME_ENV_VAR = "MUVID_DATA_HOME"

#: Named output canvases a project may choose at create (the genre Templates).
CANVASES: dict[str, tuple[int, int]] = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
    "square": (1080, 1080),
}
DEFAULT_CANVAS_NAME = "landscape"


def data_root() -> Path:
    override = os.environ.get(DATA_HOME_ENV_VAR)
    return Path(override) if override else Path.home() / ".local" / "share" / "muvid"


def _safe_component(value: str, *, label: str) -> str:
    v = (value or "").strip()
    if not v or v in (".", "..") or "/" in v or "\\" in v or "\x00" in v:
        raise ValueError(f"invalid {label}: {value!r}")
    return v


@dataclass(frozen=True)
class MusicVideoFootageProject:
    """One caller's stateful music-video project (song + clips + alignments + renders)."""

    email: str
    project_id: str
    root: Path

    # -- manifest ------------------------------------------------------------
    def _manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def manifest(self) -> dict:
        try:
            return json.loads(self._manifest_path().read_text())
        except (OSError, ValueError):
            return {"title": self.project_id, "canvas": DEFAULT_CANVAS_NAME}

    def _write_manifest(self, m: dict) -> None:
        self._manifest_path().write_text(json.dumps(m, indent=2))

    def canvas(self) -> tuple[int, int]:
        return CANVASES.get(
            self.manifest().get("canvas"), CANVASES[DEFAULT_CANVAS_NAME]
        )

    # -- the fixed song ------------------------------------------------------
    def set_song(self, src_path: str, *, ext: str) -> None:
        """Store (replacing) the project's one clean song from a local file."""
        import shutil

        song_dir = self.root / "song"
        if song_dir.exists():
            shutil.rmtree(song_dir)
        song_dir.mkdir(parents=True, exist_ok=True)
        dest = song_dir / f"song{_safe_ext(ext)}"
        shutil.copyfile(src_path, dest)
        m = self.manifest()
        m["song"] = dest.name
        m["song_duration"] = _probe_duration(dest)
        self._write_manifest(m)
        # The song is the alignment reference — changing it invalidates every persisted
        # offset. Drop the alignment so footage_timeline/assemble demand a fresh
        # align_footage rather than silently cutting to a song the offsets no longer match.
        (self.root / "alignments.json").unlink(missing_ok=True)

    def song_path(self) -> Path:
        name = self.manifest().get("song")
        if not name:
            raise FileNotFoundError("no song set — call set_song first")
        return self.root / "song" / name

    def song_duration(self) -> float:
        d = self.manifest().get("song_duration")
        return float(d) if d is not None else _probe_duration(self.song_path())

    def has_song(self) -> bool:
        return bool(self.manifest().get("song"))

    # -- footage clips -------------------------------------------------------
    def add_clip(self, clip_id: str, src_path: str, *, ext: str, name: str = "") -> str:
        """Store a footage clip from a local file; returns its ``clip_id``."""
        import shutil

        cid = _safe_component(clip_id, label="clip_id")
        clips_dir = self.root / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        # Remove any prior file for this clip_id (a re-add with a different extension would
        # otherwise orphan the old one) before writing the new one.
        for old in clips_dir.glob(f"{cid}.*"):
            old.unlink()
        dest = clips_dir / f"{cid}{_safe_ext(ext)}"
        shutil.copyfile(src_path, dest)
        m = self.manifest()
        clips = m.setdefault("clips", [])
        clips[:] = [c for c in clips if c.get("clip_id") != cid]
        clips.append({"clip_id": cid, "file": dest.name, "name": name or cid})
        self._write_manifest(m)
        return cid

    def clip_paths(self) -> dict:
        out = {}
        for c in self.manifest().get("clips", []):
            out[c["clip_id"]] = str(self.root / "clips" / c["file"])
        return out

    def list_clips(self) -> list[dict]:
        return [
            {"clip_id": c["clip_id"], "name": c.get("name", c["clip_id"])}
            for c in self.manifest().get("clips", [])
        ]

    # -- alignments ----------------------------------------------------------
    def save_alignments(self, aligns: list[FootageAlignment]) -> None:
        (self.root / "alignments.json").write_text(
            json.dumps([a.to_dict() for a in aligns], indent=2)
        )

    def load_alignments(self) -> list[FootageAlignment]:
        p = self.root / "alignments.json"
        if not p.exists():
            return []
        try:
            return [FootageAlignment.from_dict(d) for d in json.loads(p.read_text())]
        except (OSError, ValueError):
            return []

    # -- renders -------------------------------------------------------------
    def new_render_dir(self, render_id: str) -> Path:
        rid = _safe_component(render_id, label="render_id")
        d = self.root / "renders" / rid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_render_meta(self, render_id: str, meta: dict) -> None:
        rid = _safe_component(render_id, label="render_id")
        (self.root / "renders" / rid / "meta.json").write_text(
            json.dumps(meta, indent=2)
        )

    def list_renders(self) -> list[dict]:
        rdir = self.root / "renders"
        if not rdir.exists():
            return []
        rows = []
        for child in sorted(rdir.iterdir()):
            meta = child / "meta.json"
            if child.is_dir() and meta.exists():
                try:
                    row = json.loads(meta.read_text())
                except (OSError, ValueError):
                    row = {"render_id": child.name}
                row.setdefault("render_id", child.name)
                row["_mtime"] = meta.stat().st_mtime
                rows.append(row)
        rows.sort(key=lambda r: r.pop("_mtime"), reverse=True)
        return rows


def _safe_ext(ext: str) -> str:
    e = (ext or "").strip().lower().lstrip(".")
    if not e or not e.isalnum() or len(e) > 5:
        return ".bin"
    return "." + e


def _probe_duration(path: Path) -> float:
    from muvid.visualize.ffmpeg import media_duration  # lazy

    return float(media_duration(path))


@dataclass(frozen=True)
class FootageWorkspace:
    """A caller's private music-video area, addressed by ``email``."""

    email: str
    root: Path

    @classmethod
    def for_email(cls, email: str, *, root: Path | None = None) -> "FootageWorkspace":
        return cls(email=email, root=root or data_root())

    @property
    def projects_dir(self) -> Path:
        return (
            self.root
            / "music_video"
            / "projects"
            / _safe_component(self.email, label="email")
        )

    def project_root(self, project_id: str) -> Path:
        return self.projects_dir / _safe_component(project_id, label="project_id")

    def create_project(
        self, project_id: str, *, title: str = "", canvas: str = DEFAULT_CANVAS_NAME
    ) -> MusicVideoFootageProject:
        root = self.project_root(project_id)
        if root.exists():
            raise FileExistsError(
                f"project {project_id!r} already exists for {self.email}"
            )
        root.mkdir(parents=True, exist_ok=True)
        canvas_name = canvas if canvas in CANVASES else DEFAULT_CANVAS_NAME
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "title": title or project_id,
                    "canvas": canvas_name,
                    "created": time.time(),
                },
                indent=2,
            )
        )
        return MusicVideoFootageProject(self.email, project_id, root)

    def open_project(self, project_id: str) -> MusicVideoFootageProject:
        root = self.project_root(project_id)
        if not (root / "manifest.json").exists():
            raise FileNotFoundError(f"no project {project_id!r} for {self.email}")
        return MusicVideoFootageProject(self.email, project_id, root)

    def list_projects(self) -> list[dict]:
        pdir = self.projects_dir
        if not pdir.exists():
            return []
        rows = []
        for child in pdir.iterdir():
            spec = child / "manifest.json"
            if child.is_dir() and spec.exists():
                try:
                    title = json.loads(spec.read_text()).get("title", child.name)
                except (OSError, ValueError):
                    title = child.name
                rows.append(
                    {
                        "project_id": child.name,
                        "title": title,
                        "_mtime": spec.stat().st_mtime,
                    }
                )
        rows.sort(key=lambda r: r.pop("_mtime"), reverse=True)
        return rows
