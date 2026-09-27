"""Make a hosted production's media *retrievable* — the host's artifact catalog.

A song, a clip or a render that lands in a host-placed :class:`muvid.Project` is a file
on disk; the studio plays media through the host's ``GET /api/artifacts/{id}/bytes``,
which answers only for ids registered in the project's catalog. So every such file is
also registered there: :class:`HostArtifactCatalog` is the writer, and
:class:`muvid.footage.workspace.MusicVideoFootageProject`'s ``media_catalog`` seam is
where it plugs in (``None`` — the MCP connector's per-caller workspace — registers
nothing).

The layout and the row shape are the HOST's (reelee's ``reelee/artifacts.py``):
``<project>/.reelee/artifacts/blobs/<sha256>`` and
``<project>/.reelee/artifacts/catalog/<sha256>.json``. Four rules, each the point:

- **The id IS the content hash** (SHA-256 of the bytes, 64 lowercase hex), the same
  digest muvid already records as ``song_hash``.
- **Blob first, row second**: the host reads the row first, so a row with no bytes
  behind it would be a 500 mid-stream rather than a 404.
- **Hardlinked, never copied**: the media is already inside the project, and a shared
  inode is safe because the name is the digest. A filesystem that cannot link is
  REFUSED (:class:`CrossDeviceCatalog`) rather than silently doubling the bytes.
- **Never a ``file://`` url** — the row's ``url`` is the host's bytes route.

**Duplication, knowingly.** This is a second copy of ``braidio.importing._catalog``
(same layout, same row, same refusals). The host's catalog is a host concern two guest
genres now write to, so the writer belongs one layer down, in ``nw``, and both
packages should call it from there; until that lands, the two copies must agree, and
the row's field list is the thing to diff.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Where the host keeps a project's catalog, relative to the project root.
DELIVERY_CATALOG_SUBPATH: tuple[str, ...] = (".reelee", "artifacts")
_CATALOG_DIRNAME = "catalog"
_BLOBS_DIRNAME = "blobs"

#: The kinds the host's catalog holds; anything else is not registered (and said so).
CATALOG_KINDS: frozenset[str] = frozenset({"image", "video", "audio", "json"})

#: The host's bytes route — what a row's ``url`` is, never a filesystem path.
BYTES_ROUTE = "/api/artifacts/{artifact_id}/bytes"

#: The env var a host reads to pick its artifact backend; only a local one reads the
#: project's own ``blobs/`` directory.
BACKEND_ENV_KEY = "REELEE_ARTIFACT_BACKEND"
_LOCAL_BACKENDS = frozenset({"", "fs"})

_LINK_REFUSALS = (errno.EXDEV, errno.EMLINK, errno.EPERM)
_IS_DIGEST = re.compile(r"[0-9a-f]{64}")
_HASH_CHUNK = 1 << 20


class CrossDeviceCatalog(OSError):
    """The project's blob store is not on the media's filesystem (cannot hardlink)."""


class CatalogBackendMismatch(RuntimeError):
    """The host reads its artifacts from somewhere other than the project's blobs."""


def hash_file(path, *, chunk_size: int = _HASH_CHUNK) -> str:
    """SHA-256 of a file's bytes, read in chunks — the catalog id of that file.

    >>> import tempfile, pathlib
    >>> with tempfile.TemporaryDirectory() as d:
    ...     p = pathlib.Path(d, "x"); _ = p.write_bytes(b"abc")
    ...     hash_file(p)
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def assert_local_backend(env=None) -> None:
    """Refuse to register into a filesystem the host will not read.

    Rows written beside a project whose host resolves artifacts from an object store
    would make every id 404 while the write reported success — the failure this module
    exists to remove.

    >>> assert_local_backend({})
    >>> assert_local_backend({"REELEE_ARTIFACT_BACKEND": "aws"})
    Traceback (most recent call last):
        ...
    muvid.catalog.CatalogBackendMismatch: ...
    """
    backend = ((os.environ if env is None else env).get(BACKEND_ENV_KEY) or "").strip()
    if backend.lower() in _LOCAL_BACKENDS:
        return
    raise CatalogBackendMismatch(
        f"{BACKEND_ENV_KEY}={backend!r}: the host resolves artifacts from an object "
        "store, not from the project's own blobs/ directory, so rows written here "
        "would be invisible and every artifact id would 404."
    )


def catalog_row(
    artifact_id: str,
    *,
    kind: str,
    generated_at: str,
    width: Optional[int] = None,
    height: Optional[int] = None,
    duration_s: Optional[float] = None,
    note: str = "",
) -> dict:
    """The host's artifact record as JSON — the same minimal field set braidio emits.

    An unknown key fails the host's validation for its WHOLE catalog, so only fields
    long present in the host's model are emitted.

    >>> row = catalog_row("ab" * 32, kind="video", generated_at="2026-09-27T00:00:00Z")
    >>> row["id"] == row["content_hash"], row["url"].startswith("/api/artifacts/")
    (True, True)
    """
    return {
        "id": artifact_id,
        "kind": kind,
        "url": BYTES_ROUTE.format(artifact_id=artifact_id),
        "width": width,
        "height": height,
        "duration_seconds": duration_s,
        "cost_usd": None,
        "provenance": {
            "source": "manual",
            "model": None,
            "request_id": None,
            "prompt": note or None,
            "generated_at": generated_at,
            "triggered_by": None,
        },
        "content_hash": artifact_id,
    }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True)
class HostArtifactCatalog:
    """The artifact catalog of the project at ``project_root`` (the host's layout).

    >>> import tempfile, pathlib
    >>> with tempfile.TemporaryDirectory() as d:
    ...     media = pathlib.Path(d, "song.wav"); _ = media.write_bytes(b"RIFF")
    ...     cat = HostArtifactCatalog(d)
    ...     aid = cat.register(media, kind="audio")
    ...     (cat.blobs_dir / aid).exists(), (cat.rows_dir / f"{aid}.json").exists()
    (True, True)
    """

    project_root: Path

    def __post_init__(self):
        object.__setattr__(self, "project_root", Path(self.project_root))

    @property
    def root(self) -> Path:
        return self.project_root.joinpath(*DELIVERY_CATALOG_SUBPATH)

    @property
    def blobs_dir(self) -> Path:
        return self.root / _BLOBS_DIRNAME

    @property
    def rows_dir(self) -> Path:
        return self.root / _CATALOG_DIRNAME

    def has(self, artifact_id: str) -> bool:
        """Whether ``artifact_id`` resolves: its row AND its blob are both there."""
        return (self.rows_dir / f"{artifact_id}.json").exists() and (
            self.blobs_dir / artifact_id
        ).exists()

    def register(
        self,
        path,
        *,
        kind: str,
        artifact_id: Optional[str] = None,
        duration_s: Optional[float] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        note: str = "",
    ) -> Optional[str]:
        """Register ``path`` (a file inside the project); return its id, or ``None``.

        ``None`` only for a ``kind`` the catalog cannot hold — the caller records the
        file without an id rather than inventing one. ``artifact_id`` may be passed
        when the caller already hashed the file (a 300 MB clip is not worth reading
        twice); its shape is checked, its value is trusted.
        """
        if kind not in CATALOG_KINDS:
            return None
        assert_local_backend()
        src = Path(path)
        aid = artifact_id or hash_file(src)
        if not _IS_DIGEST.fullmatch(aid):
            raise ValueError(f"artifact_id {aid!r} is not a 64-character hex digest")
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.rows_dir.mkdir(parents=True, exist_ok=True)
        blob = self.blobs_dir / aid
        if not blob.exists():
            _hardlink(src, blob)
        row_path = self.rows_dir / f"{aid}.json"
        row = catalog_row(
            aid,
            kind=kind,
            generated_at=_now_iso(),
            width=width,
            height=height,
            duration_s=duration_s,
            note=note,
        )
        if not (row_path.exists() and _same_but_for_stamp(_read(row_path), row)):
            row_path.write_text(json.dumps(row, indent=2), encoding="utf-8")
        return aid


def _hardlink(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError as exc:
        if exc.errno not in _LINK_REFUSALS:
            raise
        raise CrossDeviceCatalog(
            exc.errno,
            f"cannot hardlink {src.name} into {dst.parent}: the catalog shares bytes by "
            "inode, and this filesystem cannot link them, so registering would copy "
            "the media a second time. Keep the project on one filesystem.",
        ) from exc


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _same_but_for_stamp(a: Optional[dict], b: dict) -> bool:
    """Equal rows, ignoring when they were registered (so a re-import does not churn)."""

    def strip(row: dict) -> dict:
        out = dict(row)
        prov = dict(out.get("provenance") or {})
        prov.pop("generated_at", None)
        out["provenance"] = prov
        return out

    return a is not None and strip(a) == strip(b)


__all__ = [
    "HostArtifactCatalog",
    "CrossDeviceCatalog",
    "CatalogBackendMismatch",
    "catalog_row",
    "hash_file",
    "assert_local_backend",
    "BYTES_ROUTE",
    "CATALOG_KINDS",
    "DELIVERY_CATALOG_SUBPATH",
]
