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


def safe_component(value: str, *, label: str) -> str:
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
        # Compute the song hash ONCE here (chunked) and cache it — scoring/reads compare the
        # stored hash rather than re-hashing a 100 MB file on every poll (persistence-lens).
        m["song_hash"] = _hash_file(dest)
        self._write_manifest(m)
        # The song is the alignment reference — changing it invalidates every persisted
        # offset. Drop the alignment so footage_timeline/assemble demand a fresh
        # align_footage rather than silently cutting to a song the offsets no longer match.
        (self.root / "alignments.json").unlink(missing_ok=True)
        # A new song also invalidates every persisted score track (the grid mapping moved).
        self.invalidate_scores()

    def song_hash(self) -> str:
        """The clean song's content hash (cached in the manifest; computed if missing)."""
        m = self.manifest()
        h = m.get("song_hash")
        if not h:
            h = _hash_file(self.song_path())
            m["song_hash"] = h
            self._write_manifest(m)
        return h

    def invalidate_scores(self) -> None:
        """Delete persisted score tracks — the primary invalidation on song/offset change."""
        import shutil

        shutil.rmtree(self.root / "scores", ignore_errors=True)

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

        cid = safe_component(clip_id, label="clip_id")
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
    @property
    def renders_dir(self) -> Path:
        """Where this project's renders live.

        Named to match ``VisualizerProject.renders_dir`` so anything that spans
        both muvid genres — ``muvid.downloads`` — sees one shape instead of
        branching on which drawer it is looking in.
        """
        return self.root / "renders"

    def new_render_dir(self, render_id: str) -> Path:
        rid = safe_component(render_id, label="render_id")
        d = self.renders_dir / rid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_render_meta(self, render_id: str, meta: dict) -> None:
        rid = safe_component(render_id, label="render_id")
        (self.root / "renders" / rid / "meta.json").write_text(
            json.dumps(meta, indent=2)
        )

    def ensure_render_refs(self) -> dict:
        """Give every render a stable, speakable reference; return ``{id: n}``.

        A render id is a uuid4 slice (``b02fc05417ea``) — fine for a URL, useless
        in a sentence. Nobody can ask for "a bit less of the wide shot in
        b02fc05417ea". So each render also carries a small ordinal, rendered as
        ``cut 4`` by :func:`nw.delivery.format_ref` at the delivery boundary.

        This module stores the INTEGER only. The word "cut" belongs to
        ``nw.delivery`` and is spelled in exactly one place; core muvid does not
        depend on nw (it is in the ``mcp`` extra, so ``muvid.visualize`` and
        downstreams like ``yb`` stay lightweight), and a local second spelling
        is precisely the drift ``nw.delivery`` exists to prevent.

        Two properties make it worth persisting rather than deriving:

        - **Stable.** Assigned once, at creation, and never renumbered. A
          position in a sorted list would shift under the user every time they
          rendered again, so the reference they wrote down would rot.
        - **Chronological.** Backfill runs OLDEST first, so ``cut 1`` is the
          first thing they made, which is what someone means by "the first cut".

        Self-healing on read, in the same spirit as an open-time schema
        migration: renders made before refs existed acquire one the first time
        anything lists or resolves them, and the assignment is written back so
        it never moves again.
        """
        rdir = self.root / "renders"
        if not rdir.exists():
            return {}
        rows = []
        for child in sorted(rdir.iterdir()):
            meta_path = child / "meta.json"
            if not (child.is_dir() and meta_path.exists()):
                continue
            try:
                meta = json.loads(meta_path.read_text())
            except (OSError, ValueError):
                continue
            rows.append((meta_path.stat().st_mtime, child.name, meta, meta_path))

        assigned = {
            name: int(meta["ref_n"])
            for _, name, meta, _ in rows
            if isinstance(meta.get("ref_n"), int)
        }
        # Oldest first: the earliest render becomes cut 1.
        rows.sort(key=lambda r: r[0])
        nxt = max(assigned.values(), default=0) + 1
        for _, name, meta, meta_path in rows:
            if name in assigned:
                continue
            meta["ref_n"] = nxt
            try:
                meta_path.write_text(json.dumps(meta, indent=2))
            except OSError:
                # A read-only or racing write must not break listing; the ref is
                # still correct for THIS call, it just isn't durable yet.
                pass
            assigned[name] = nxt
            nxt += 1
        return assigned

    def next_render_ref(self) -> int:
        """The ordinal the next render will carry (1-based, never reused)."""
        return max(self.ensure_render_refs().values(), default=0) + 1

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
        # Backfill is cheap (a stat + a parse already done above) and makes the
        # reference visible everywhere a render is, which is the only way a user
        # learns it exists.
        refs = self.ensure_render_refs()
        for row in rows:
            n = refs.get(row.get("render_id"))
            if n is not None:
                row.setdefault("ref_n", n)
        return rows


def _safe_ext(ext: str) -> str:
    e = (ext or "").strip().lower().lstrip(".")
    if not e or not e.isalnum() or len(e) > 5:
        return ".bin"
    return "." + e


def _probe_duration(path: Path) -> float:
    from muvid.visualize.ffmpeg import media_duration  # lazy

    return float(media_duration(path))


def _hash_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """sha256 of a file, read in ``chunk``-sized blocks (never load the whole file)."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


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
            / safe_component(self.email, label="email")
        )

    def project_root(self, project_id: str) -> Path:
        return self.projects_dir / safe_component(project_id, label="project_id")

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
