# muvid.catalog

Make a hosted production’s media *retrievable* — the host’s artifact catalog.

A song, a clip or a render that lands in a host-placed `muvid.Project` is a file
on disk; the studio plays media through the host’s `GET /api/artifacts/{id}/bytes`,
which answers only for ids registered in the project’s catalog. So every such file is
also registered there: [`HostArtifactCatalog`](#muvid.catalog.HostArtifactCatalog) is the writer, and
[`muvid.footage.workspace.MusicVideoFootageProject`](muvid.footage.workspace.html.md#muvid.footage.workspace.MusicVideoFootageProject)’s `media_catalog` seam is
where it plugs in (`None` — the MCP connector’s per-caller workspace — registers
nothing).

The layout and the row shape are the HOST’s (reelee’s `reelee/artifacts.py`):
`<project>/.reelee/artifacts/blobs/<sha256>` and
`<project>/.reelee/artifacts/catalog/<sha256>.json`. Four rules, each the point:

- **The id IS the content hash** (SHA-256 of the bytes, 64 lowercase hex), the same
  digest muvid already records as `song_hash`.
- **Blob first, row second**: the host reads the row first, so a row with no bytes
  behind it would be a 500 mid-stream rather than a 404.
- **Hardlinked, never copied**: the media is already inside the project, and a shared
  inode is safe because the name is the digest. A filesystem that cannot link is
  REFUSED ([`CrossDeviceCatalog`](#muvid.catalog.CrossDeviceCatalog)) rather than silently doubling the bytes.
- \*\*Never a `file://` url\*\* — the row’s `url` is the host’s bytes route.

**Duplication, knowingly.** This is a second copy of `braidio.importing._catalog`
(same layout, same row, same refusals). The host’s catalog is a host concern two guest
genres now write to, so the writer belongs one layer down, in `nw`, and both
packages should call it from there; until that lands, the two copies must agree, and
the row’s field list is the thing to diff.

### Module Attributes

| [`DELIVERY_CATALOG_SUBPATH`](#muvid.catalog.DELIVERY_CATALOG_SUBPATH)   | Where the host keeps a project's catalog, relative to the project root.            |
|-----------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| [`CATALOG_KINDS`](#muvid.catalog.CATALOG_KINDS)              | The kinds the host's catalog holds; anything else is not registered (and said so). |
| [`BYTES_ROUTE`](#muvid.catalog.BYTES_ROUTE)                | The host's bytes route — what a row's `url` is, never a filesystem path.           |

### Functions

| [`catalog_row`](#muvid.catalog.catalog_row)(artifact_id, \*, kind, generated_at)   | The host's artifact record as JSON — the same minimal field set braidio emits.   |
|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`hash_file`](#muvid.catalog.hash_file)(path, \*[, chunk_size])                  | SHA-256 of a file's bytes, read in chunks — the catalog id of that file.         |
| [`assert_local_backend`](#muvid.catalog.assert_local_backend)([env])                        | Refuse to register into a filesystem the host will not read.                     |

### Classes

| [`HostArtifactCatalog`](#muvid.catalog.HostArtifactCatalog)(project_root)   | The artifact catalog of the project at `project_root` (the host's layout).   |
|--------------------------------------------------------------------------------------|------------------------------------------------------------------------------|

### Exceptions

| [`CrossDeviceCatalog`](#muvid.catalog.CrossDeviceCatalog)     | The project's blob store is not on the media's filesystem (cannot hardlink).   |
|-------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`CatalogBackendMismatch`](#muvid.catalog.CatalogBackendMismatch) | The host reads its artifacts from somewhere other than the project's blobs.    |

### muvid.catalog.BYTES_ROUTE *= '/api/artifacts/{artifact_id}/bytes'*

The host’s bytes route — what a row’s `url` is, never a filesystem path.

### muvid.catalog.CATALOG_KINDS *: [frozenset](https://docs.python.org/3/builtins/stdtypes.html#frozenset)[[str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= frozenset({'audio', 'image', 'json', 'video'})*

The kinds the host’s catalog holds; anything else is not registered (and said so).

### *exception* muvid.catalog.CatalogBackendMismatch

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

The host reads its artifacts from somewhere other than the project’s blobs.

### *exception* muvid.catalog.CrossDeviceCatalog

Bases: [`OSError`](https://docs.python.org/3/builtins/exceptions.html#OSError)

The project’s blob store is not on the media’s filesystem (cannot hardlink).

### muvid.catalog.DELIVERY_CATALOG_SUBPATH *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]* *= ('.reelee', 'artifacts')*

Where the host keeps a project’s catalog, relative to the project root.

### *class* muvid.catalog.HostArtifactCatalog(project_root)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The artifact catalog of the project at `project_root` (the host’s layout).

```pycon
>>> import tempfile, pathlib
>>> with tempfile.TemporaryDirectory() as d:
...     media = pathlib.Path(d, "song.wav"); _ = media.write_bytes(b"RIFF")
...     cat = HostArtifactCatalog(d)
...     aid = cat.register(media, kind="audio")
...     (cat.blobs_dir / aid).exists(), (cat.rows_dir / f"{aid}.json").exists()
(True, True)
```

#### has(artifact_id)

Whether `artifact_id` resolves: its row AND its blob are both there.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### register(path, , kind, artifact_id=None, duration_s=None, width=None, height=None, note='')

Register `path` (a file inside the project); return its id, or `None`.

`None` only for a `kind` the catalog cannot hold — the caller records the
file without an id rather than inventing one. `artifact_id` may be passed
when the caller already hashed the file (a 300 MB clip is not worth reading
twice); its shape is checked, its value is trusted.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.catalog.assert_local_backend(env=None)

Refuse to register into a filesystem the host will not read.

Rows written beside a project whose host resolves artifacts from an object store
would make every id 404 while the write reported success — the failure this module
exists to remove.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

```pycon
>>> assert_local_backend({})
>>> assert_local_backend({"REELEE_ARTIFACT_BACKEND": "aws"})
Traceback (most recent call last):
    ...
muvid.catalog.CatalogBackendMismatch: ...
```

### muvid.catalog.catalog_row(artifact_id, , kind, generated_at, width=None, height=None, duration_s=None, note='')

The host’s artifact record as JSON — the same minimal field set braidio emits.

An unknown key fails the host’s validation for its WHOLE catalog, so only fields
long present in the host’s model are emitted.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

```pycon
>>> row = catalog_row("ab" * 32, kind="video", generated_at="2026-09-27T00:00:00Z")
>>> row["id"] == row["content_hash"], row["url"].startswith("/api/artifacts/")
(True, True)
```

### muvid.catalog.hash_file(path, , chunk_size=1048576)

SHA-256 of a file’s bytes, read in chunks — the catalog id of that file.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> import tempfile, pathlib
>>> with tempfile.TemporaryDirectory() as d:
...     p = pathlib.Path(d, "x"); _ = p.write_bytes(b"abc")
...     hash_file(p)
'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
```
