"""Per-user output bucket for the muvid ``music-visualizer`` MCP genre.

A remote MCP connector is stateless and multi-user, so each caller must be isolated
and address work by ``project_id`` (never a path). The visualizer is itself stateless
— a render is a pure function of (audio, cover, visual) — so a "project" here is just a
**lightweight bucket** where a caller's renders land, not a full nw/muvid project (no
graph, no asset store). That keeps the ``music-visualizer`` genre the cheapest possible
2nd genre (thorwhalen/muvid#3); a richer per-user asset library (content-addressed
uploads shared across genres) is a deliberate follow-up.

Layout (default root ``~/.local/share/muvid``; override via ``MUVID_DATA_HOME``):

- ``{root}/visualizer/projects/{email}/{project_id}/manifest.json`` — the bucket
- ``{root}/visualizer/projects/{email}/{project_id}/renders/{render_id}/`` — one render
  (its ``.mp4``, optional ``thumbnail.jpg``, and a ``meta.json`` sidecar)

Per the app-data-lifecycle rule this lives in the user-data dir, **never** inside the
app/deploy tree (a deploy's ``rsync --delete`` would erase it).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from muvid.paths import (
    DATA_HOME_ENV_VAR as _DATA_HOME_ENV_VAR,
    data_home,
    safe_component,
)

#: Env var overriding the muvid data root (where per-user visualizer buckets live).
#: Re-exported from :mod:`muvid.paths`, the SSOT — this module used to carry its own
#: verbatim copy of both, beside a second copy in ``muvid/footage/workspace.py``.
DATA_HOME_ENV_VAR = _DATA_HOME_ENV_VAR

#: The muvid data root: ``$MUVID_DATA_HOME`` or ``~/.local/share/muvid``. Public API
#: (``muvid.mcp`` re-exports it), so the name stays even though the body moved.
data_root = data_home

_safe_component = safe_component


@dataclass(frozen=True)
class VisualizerProject:
    """One caller's visualizer bucket — a folder its renders land in.

    ``root`` is exposed so ``nw.create_genre_project``'s all-or-nothing rollback
    (``nw.genres._rollback_project`` removes ``project.root``) reverts a half-created
    bucket. Kept storage-only: no nw graph, no asset library.
    """

    email: str
    project_id: str
    root: Path

    @property
    def renders_dir(self) -> Path:
        return self.root / "renders"

    def manifest(self) -> dict:
        spec = self.root / "manifest.json"
        try:
            return json.loads(spec.read_text())
        except (OSError, ValueError):
            return {"title": self.project_id}

    def new_render_dir(self, render_id: str) -> Path:
        """Create + return a fresh directory for one render (traversal-checked id)."""
        rid = _safe_component(render_id, label="render_id")
        d = self.renders_dir / rid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_render_meta(self, render_id: str, meta: dict) -> None:
        rid = _safe_component(render_id, label="render_id")
        (self.renders_dir / rid / "meta.json").write_text(json.dumps(meta, indent=2))

    def list_renders(self) -> list[dict]:
        """This bucket's renders (newest-first), from each render's ``meta.json``."""
        rdir = self.renders_dir
        if not rdir.exists():
            return []
        rows = []
        for child in sorted(rdir.iterdir()):
            meta = child / "meta.json"
            if not (child.is_dir() and meta.exists()):
                continue
            try:
                row = json.loads(meta.read_text())
            except (OSError, ValueError):
                row = {"render_id": child.name}
            row.setdefault("render_id", child.name)
            row["_mtime"] = meta.stat().st_mtime
            rows.append(row)
        rows.sort(key=lambda r: r.pop("_mtime"), reverse=True)
        return rows


@dataclass(frozen=True)
class VisualizerWorkspace:
    """A single caller's private visualizer area, addressed by ``email``.

    ``email`` and every ``project_id`` are validated as single path components, so a
    caller can never escape their own subtree.
    """

    email: str
    root: Path

    @classmethod
    def for_email(
        cls, email: str, *, root: Path | None = None
    ) -> "VisualizerWorkspace":
        return cls(email=email, root=root or data_root())

    @property
    def projects_dir(self) -> Path:
        return (
            self.root
            / "visualizer"
            / "projects"
            / _safe_component(self.email, label="email")
        )

    def project_root(self, project_id: str) -> Path:
        pid = _safe_component(project_id, label="project_id")
        return self.projects_dir / pid

    def create_project(
        self, project_id: str, *, title: str = "", force: bool = False
    ) -> VisualizerProject:
        """Create (and return) a new visualizer bucket under this user."""
        root = self.project_root(project_id)
        if root.exists() and not force:
            raise FileExistsError(
                f"project {project_id!r} already exists for {self.email}"
            )
        (root / "renders").mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(
            json.dumps({"title": title or project_id, "created": time.time()}, indent=2)
        )
        return VisualizerProject(email=self.email, project_id=project_id, root=root)

    def open_project(self, project_id: str) -> VisualizerProject:
        """Open an existing visualizer bucket (raises if it doesn't exist)."""
        root = self.project_root(project_id)
        if not (root / "manifest.json").exists():
            raise FileNotFoundError(f"no project {project_id!r} for {self.email}")
        return VisualizerProject(email=self.email, project_id=project_id, root=root)

    def list_projects(self) -> list[dict]:
        """This user's buckets: ``[{project_id, title}]`` (newest-modified first)."""
        pdir = self.projects_dir
        if not pdir.exists():
            return []
        rows = []
        for child in pdir.iterdir():
            spec = child / "manifest.json"
            if not (child.is_dir() and spec.exists()):
                continue
            try:
                manifest = json.loads(spec.read_text())
                if not isinstance(manifest, dict):
                    manifest = {}
            except (OSError, ValueError):
                manifest = {}
            try:
                mtime = spec.stat().st_mtime
            except OSError:
                continue  # vanished mid-scan
            rows.append(
                {
                    "project_id": child.name,
                    "title": manifest.get("title") or child.name,
                    "created": manifest.get("created"),
                    "modified": mtime,
                }
            )
        rows.sort(key=lambda r: r["modified"], reverse=True)
        return rows
