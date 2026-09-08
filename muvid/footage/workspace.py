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

**Every JSON record here is replaced, never truncated in place** (muvid#17 item 4).
``manifest.json`` and ``alignments.json`` used to be bare ``write_text`` calls — a
truncate-then-write — while ``manifest()`` deliberately swallows ``OSError``/``ValueError``
so an unreadable project reads as an EMPTY one. Put together, a process killed between
the truncate and the write left a project that presented as having no song and no clips:
not an error a caller could act on, but a plausible state a caller would act on wrongly
(re-uploading everything, or rendering nothing). The scoring layer three directories
away already wrote tmp + ``os.replace``; :func:`atomic_write_text` is the ONE such dance
now, shared by every manifest/alignment/meta write here, ``scoring/grid.py`` (through
:func:`atomic_write_bytes`, its ``.npz`` primitive) and ``downloads.organise``. A reader
sees the whole prior record or the whole new one. The on-disk FORMAT is untouched: that
would be a migration.

**Invalidation runs BEFORE the write that would make the stale artifact look current.**
``set_song`` drops ``alignments.json`` and the score tracks first and replaces the manifest
LAST, so no crash window leaves a manifest naming the new song beside an alignment measured
against the old one — the offsets would then be read as if they belonged to it, which is
muvid#59's silently-out-of-sync video by another route.
"""

from __future__ import annotations

import json
import os
import tempfile
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
        atomic_write_text(self._manifest_path(), json.dumps(m, indent=2))

    def canvas(self) -> tuple[int, int]:
        return CANVASES.get(
            self.manifest().get("canvas"), CANVASES[DEFAULT_CANVAS_NAME]
        )

    # -- the fixed song ------------------------------------------------------
    def set_song(self, src_path: str, *, ext: str) -> None:
        """Store (replacing) the project's one clean song from a local file.

        The order of operations is the contract (muvid#17 item 4), in three phases:

        1. **Everything that can fail runs first, against a staged copy** — the copy
           itself, the duration probe, the content hash. A missing source or an
           un-probeable file raises here and the project is exactly as it was: the old
           song, its alignment and its scores all still stand.
        2. **What the old song vouched for is dropped** — ``alignments.json`` and the
           score tracks — BEFORE the manifest can name the new song. The song is the
           alignment reference, so an offset measured against the old one is meaningless
           against the new one; but nothing on disk ties an alignment record to a song,
           so a manifest that named the new song beside the old alignment would have
           those offsets read as its own and cut a video silently out of sync (the
           muvid#59 failure shape, by a different route). Dropping first means the worst
           a crash can leave is a project that demands a fresh ``align_footage``.
        3. **The song file lands, then the manifest is replaced LAST** — it names the
           file, so the file must exist before any reader can be pointed at it.
        """
        import shutil

        suffix = _safe_ext(ext)
        song_dir = self.root / "song"
        dest = song_dir / f"song{suffix}"
        # Phase 1: stage beside the song dir (same filesystem, so the final move is a
        # rename) and measure. Nothing the project owns has been touched yet.
        fd, staged_name = tempfile.mkstemp(
            dir=str(self.root), prefix=".song.", suffix=suffix
        )
        os.close(fd)
        staged = Path(staged_name)
        try:
            shutil.copyfile(src_path, staged)
            duration = _probe_duration(staged)
            # Compute the song hash ONCE here (chunked) and cache it — scoring/reads
            # compare the stored hash rather than re-hashing a 100 MB file on every poll.
            digest = _hash_file(staged)
            # Phase 2: invalidate what the OLD song vouched for, before anything can name
            # the new one. footage_timeline/assemble then demand a fresh align_footage
            # rather than silently cutting to a song the offsets no longer match.
            (self.root / "alignments.json").unlink(missing_ok=True)
            self.invalidate_scores()
            # Phase 3a: the file. Replace the whole dir so a prior song under a different
            # extension cannot be orphaned beside the new one.
            if song_dir.exists():
                shutil.rmtree(song_dir)
            song_dir.mkdir(parents=True, exist_ok=True)
            os.replace(staged, dest)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        # Phase 3b: the manifest, LAST.
        m = self.manifest()
        m["song"] = dest.name
        m["song_duration"] = duration
        m["song_hash"] = digest
        self._write_manifest(m)

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
        atomic_write_text(
            self.root / "alignments.json",
            json.dumps([a.to_dict() for a in aligns], indent=2),
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
        atomic_write_text(
            self.root / "renders" / rid / "meta.json", json.dumps(meta, indent=2)
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
                atomic_write_text(meta_path, json.dumps(meta, indent=2))
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


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace ``path``'s content with ``data`` so a reader never sees a torn file.

    The ONE tmp + fsync + ``os.replace`` dance in the package (muvid#17 item 4); every
    JSON record in this module goes through :func:`atomic_write_text`, and the
    scoring layer's ``.npz`` arrays come here directly. The pieces, and why each is
    load-bearing:

    - **The temp file is created in ``path``'s own directory** (``tempfile.mkstemp``,
      so a concurrent writer gets its own name rather than racing on a fixed
      ``.tmp``). ``os.replace`` is only a rename — and only atomic — within one
      filesystem; a temp under ``/tmp`` would make it a copy with a torn window.
    - **The temp is fsync'd before the rename.** Without it the rename can reach the
      journal before the data reaches the disk, and a power cut leaves the new name
      pointing at zeros — the failure mode a rename alone is wrongly believed to
      exclude.
    - **The directory is fsync'd after the rename, best-effort.** That is what makes
      the rename itself — and any ``unlink`` a caller did in the same directory just
      before it, which is how :meth:`MusicVideoFootageProject.set_song` orders its
      invalidation — durable. Directories cannot be opened for fsync on every platform
      (Windows), so an ``OSError`` there is the one exception swallowed: the data is
      already safe on disk; only the metadata's promptness is lost.
    - **On any failure the temp is removed and the error propagates.** ``write_text``
      raised too; the difference is that ``path`` still holds the previous complete
      record instead of a truncated one.

    ``mkstemp`` creates the file ``0600``, which is what these per-user records want and
    what ``downloads.organise`` already did for ``meta.json``; the mode of an existing
    file is not carried over, deliberately, so the outcome does not depend on history.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    """Flush a directory's metadata (renames, unlinks) — best-effort, see the caller."""
    try:
        dfd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return  # a platform (Windows) or filesystem that cannot open a directory
    try:
        os.fsync(dfd)
    except OSError:
        pass  # same: the data is on disk; only the directory entry's promptness is lost
    finally:
        os.close(dfd)


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace ``path`` with ``text`` (UTF-8) — see :func:`atomic_write_bytes`.

    Every ``manifest.json``, ``alignments.json`` and render ``meta.json`` write goes
    through here. UTF-8 is a superset of the ASCII ``json.dumps`` emits by default, so
    the bytes on disk are identical to what ``write_text`` produced and the locale-default
    ``read_text`` on the read side is untouched — the FORMAT does not change, only the
    way it gets there.
    """
    atomic_write_bytes(path, text.encode("utf-8"))


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
        atomic_write_text(
            root / "manifest.json",
            json.dumps(
                {
                    "title": title or project_id,
                    "canvas": canvas_name,
                    "created": time.time(),
                },
                indent=2,
            ),
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
            # `modified`/`created` stay on the row: every consumer used to
            # re-guess an order this method had already computed and popped
            # (the delivery seam's ProjectLister sorts a cross-genre union).
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
