# muvid.footage.workspace

Per-user, STATEFUL project for the footage-aligned `music_video` genre.

Unlike the visualizer (a pure function of audio+cover), a music-video project accumulates
state across calls: one fixed song, several uploaded clips, a persisted alignment
manifest, and rendered outputs. This is deliberately NEW infrastructure (not the stateless
`VisualizerWorkspace`), reusing only the identity + fetch + tool-aggregation seams.

Layout (default root `~/.local/share/muvid`; override via `MUVID_DATA_HOME` — shared
with the visualizer’s root, different subtree). **Never** inside the app/deploy tree:

- `{root}/music_video/projects/{email}/{project_id}/manifest.json` — title, canvas, song
- `.../song/song.<ext>` — the one fixed clean song
- `.../clips/{clip_id}.<ext>` — an uploaded footage clip
- `.../alignments.json` — the persisted per-clip alignment
- `.../renders/{render_id}/` — an assembled music video
- `.../edits/{edit_id}.json` — a named, persisted edit (an EDL plus how it was made)
- `.../cover.jpg` — the project’s cover frame (hosted projects only)

**The catalog seam.** `MusicVideoFootageProject(..., media_catalog=None)`: when a host
places the project (`muvid.Project`), every song, clip, render and cover that
lands here is also registered in the host’s artifact catalog
([`muvid.catalog.HostArtifactCatalog`](muvid.catalog.html.md#muvid.catalog.HostArtifactCatalog)) and its `artifact_id` recorded beside it,
so the host can serve the bytes. `None` — the MCP connector’s per-caller workspace —
registers nothing and records nothing, so its on-disk records are unchanged.

**Every JSON record here is replaced, never truncated in place** (muvid#17 item 4).
`manifest.json` and `alignments.json` used to be bare `write_text` calls — a
truncate-then-write — while `manifest()` deliberately swallows `OSError`/`ValueError`
so an unreadable project reads as an EMPTY one. Put together, a process killed between
the truncate and the write left a project that presented as having no song and no clips:
not an error a caller could act on, but a plausible state a caller would act on wrongly
(re-uploading everything, or rendering nothing). The scoring layer three directories
away already wrote tmp + `os.replace`; [`atomic_write_text()`](#muvid.footage.workspace.atomic_write_text) is the ONE such dance
now, shared by every manifest/alignment/meta write here, `scoring/grid.py` (through
[`atomic_write_bytes()`](#muvid.footage.workspace.atomic_write_bytes), its `.npz` primitive) and `downloads.organise`. A reader
sees the whole prior record or the whole new one. The on-disk FORMAT is untouched: that
would be a migration.

**Invalidation runs BEFORE the write that would make the stale artifact look current.**
`set_song` drops `alignments.json` and the score tracks first and replaces the manifest
LAST, so no crash window leaves a manifest naming the new song beside an alignment measured
against the old one — the offsets would then be read as if they belonged to it, which is
muvid#59’s silently-out-of-sync video by another route.

Invalidation is deliberate and lives here, beside the state it protects: `set_song`
and `remove_clip` both drop `alignments.json` and every persisted score track,
because the alignment is a measurement of exactly the song and the clip set that were
present when it was taken (muvid#22 — a removed clip’s offset must not outlive the
clip, and the score tensor is keyed on that alignment’s fingerprint).

### Module Attributes

| [`DATA_HOME_ENV_VAR`](#muvid.footage.workspace.DATA_HOME_ENV_VAR)   | Re-exported from [`muvid.paths`](muvid.paths.html.md#module-muvid.paths), the SSOT for where muvid's data lives.   |
|----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| [`CANVASES`](#muvid.footage.workspace.CANVASES)            | Named output canvases a project may choose at create (the genre Templates).                                                                |
| [`ID_PATTERN`](#muvid.footage.workspace.ID_PATTERN)          | What a caller-chosen id (a clip's, an edit's) may be — after surrounding whitespace is dropped.                                            |

### Functions

| [`atomic_write_bytes`](#muvid.footage.workspace.atomic_write_bytes)(path, data)                    | Replace `path`'s content with `data` so a reader never sees a torn file.                                                                                                                                                               |
|----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`atomic_write_text`](#muvid.footage.workspace.atomic_write_text)(path, text)                     | Atomically replace `path` with `text` (UTF-8) — see [`atomic_write_bytes()`](#muvid.footage.workspace.atomic_write_bytes).                                                                                                             |
| [`data_root`](#muvid.footage.workspace.data_root)()                                       | The muvid data root: `$MUVID_DATA_HOME` or `~/.local/share/muvid`.                                                                                                                                                                     |
| [`file_lock`](#muvid.footage.workspace.file_lock)(path)                                   | An exclusive advisory lock on `path` for a read-modify-write (POSIX `flock`; `msvcrt.locking` on Windows).                                                                                                                             |
| [`fresh_output`](#muvid.footage.workspace.fresh_output)(dest)                                | A temp path beside `dest` for a writer that cannot be told to be careful (ffmpeg); on success it is renamed onto `dest` — a new inode, as in [`replace_file()`](#muvid.footage.workspace.replace_file) — and on failure removed. |
| [`init_footage_project`](#muvid.footage.workspace.init_footage_project)(root, \*, project_id[, ...]) | A footage project rooted at `root`, writing its manifest if there is none yet.                                                                                                                                                         |
| [`normalise_id`](#muvid.footage.workspace.normalise_id)(value, \*, label)                    | `value` stripped, and refused (`ValueError`) unless it matches [`ID_PATTERN`](#muvid.footage.workspace.ID_PATTERN).                                                                                                            |
| [`replace_file`](#muvid.footage.workspace.replace_file)(src, dest)                           | Put a copy of `src` at `dest` as a NEW file (temp sibling + `os.replace`).                                                                                                                                                             |

### Classes

| [`FootageWorkspace`](#muvid.footage.workspace.FootageWorkspace)(email, root)                     | A caller's private music-video area, addressed by `email`.                       |
|----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`MusicVideoFootageProject`](#muvid.footage.workspace.MusicVideoFootageProject)(email, project_id, root) | One caller's stateful music-video project (song + clips + alignments + renders). |

### muvid.footage.workspace.CANVASES *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[int](https://docs.python.org/3/builtins/functions.html#int), [int](https://docs.python.org/3/builtins/functions.html#int)]]* *= {'landscape': (1920, 1080), 'portrait': (1080, 1920), 'square': (1080, 1080)}*

Named output canvases a project may choose at create (the genre Templates).

### muvid.footage.workspace.DATA_HOME_ENV_VAR *= 'MUVID_DATA_HOME'*

Re-exported from [`muvid.paths`](muvid.paths.html.md#module-muvid.paths), the SSOT for where muvid’s data lives. Kept as
names here because both are public API of this module (`muvid.mcp` re-exports
`data_root`, and `downloads` imports `safe_component` from here).

### *class* muvid.footage.workspace.FootageWorkspace(email, root)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A caller’s private music-video area, addressed by `email`.

### muvid.footage.workspace.ID_PATTERN *= re.compile('^[A-Za-z0-9_-]{1,64}$')*

What a caller-chosen id (a clip’s, an edit’s) may be — after surrounding whitespace is
dropped. Letters, digits, `_` and `-` only: an id names a FILE (`clips/<id>.mp4`,
`edits/<id>.json`), and a looser rule let `*` through to a glob that deleted every
clip, and `" c1"` replace `c1`’s bytes under a different manifest key.

### *class* muvid.footage.workspace.MusicVideoFootageProject(email, project_id, root, media_catalog=None, defaults=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One caller’s stateful music-video project (song + clips + alignments + renders).

#### add_clip(clip_id, src_path, , ext, name='')

Store a footage clip from a local file; returns its `clip_id`.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

#### cover_info()

`{file, artifact_id, taken_from}` of the cover frame, or `None`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### defaults *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)*

What `manifest()` reads before anything is written — a hosted project’s
title and canvas come from its genre envelope, so READING its footage creates
nothing; the first write persists them.

#### delete_edit(edit_id)

Remove one edit record (and its undo history); whether it existed.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### edits_lock()

Serialise a read-modify-write of this project’s edits (see [`file_lock()`](#muvid.footage.workspace.file_lock)).

#### ensure_render_refs()

Give every render a stable, speakable reference; return `{id: n}`.

A render id is a uuid4 slice (`b02fc05417ea`) — fine for a URL, useless
in a sentence. Nobody can ask for “a bit less of the wide shot in
b02fc05417ea”. So each render also carries a small ordinal, rendered as
`cut 4` by `nw.delivery.format_ref()` at the delivery boundary.

This module stores the INTEGER only. The word “cut” belongs to
`nw.delivery` and is spelled in exactly one place; core muvid does not
depend on nw (it is in the `mcp` extra, so `muvid.visualize` and
downstreams like `yb` stay lightweight), and a local second spelling
is precisely the drift `nw.delivery` exists to prevent.

Two properties make it worth persisting rather than deriving:

- **Stable.** Assigned once, at creation, and never renumbered. A
  position in a sorted list would shift under the user every time they
  rendered again, so the reference they wrote down would rot.
- **Chronological.** Backfill runs OLDEST first, so `cut 1` is the
  first thing they made, which is what someone means by “the first cut”.

Self-healing on read, in the same spirit as an open-time schema
migration: renders made before refs existed acquire one the first time
anything lists or resolves them, and the assignment is written back so
it never moves again.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

#### invalidate_scores()

Delete persisted score tracks — the primary invalidation on song/offset change.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### list_clips()

`[{clip_id, name}]` — plus `artifact_id` when the host catalog holds it.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### list_edit_records()

Every readable edit record, oldest first (by its `created` stamp).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### media_catalog *: [object](https://docs.python.org/3/builtins/functions.html#object)* *= None*

Where this project’s media is registered for a host to serve it — a
[`muvid.catalog.HostArtifactCatalog`](muvid.catalog.html.md#muvid.catalog.HostArtifactCatalog) (anything with its `register`), or
`None` for the MCP workspace, which registers nothing.

#### next_render_ref()

The ordinal the next render will carry (1-based, never reused).

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

#### read_edit(edit_id)

One edit record; `KeyError` if there is no such edit.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

#### read_edit_history(edit_id)

`{"undo": [...], "redo": [...]}` — earlier/later versions of one edit.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

#### remove_clip(clip_id)

Delete one clip — its file(s) and its manifest entry — and invalidate what
was measured against it (muvid#22).

A removal changes the clip SET the alignment describes, so `alignments.json`
is dropped and, with it, every persisted score track — the same invalidation
`set_song` performs, and for the same reason: scores are keyed on the alignment
fingerprint, which no longer matches. Whole-artifact, not surgical: pruning one
record from the alignment would still move the fingerprint and strand the
score manifest, and editing that manifest in place is a migration-surface change
this method deliberately does not make.

The steps run in an order where every crash-intermediate state is a valid
project: alignment and scores go first (leaving “present, unaligned”), then the
manifest (leaving at worst an orphan file `add_clip` would sweep), then the
file — never a manifest entry pointing at a file that is gone.

Returns what happened: `{clip_id, name, files, alignment_invalidated,
scores_invalidated}`. Raises `KeyError` for a `clip_id` the manifest does
not hold; the MCP tool turns that into a refusal naming the known ids.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

#### *property* renders_dir *: [Path](https://docs.python.org/3/library/pathlib.html#pathlib.Path)*

Where this project’s renders live.

Named to match `VisualizerProject.renders_dir` so anything that spans
both muvid genres — `muvid.downloads` — sees one shape instead of
branching on which drawer it is looking in.

#### set_cover(image_path, , taken_from)

Store `image_path` as `cover.jpg`, register it; return its artifact id.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### set_song(src_path, , ext, name='', duration_s=None)

Store (replacing) the project’s one clean song from a local file.

The order of operations is the contract (muvid#17 item 4), in three phases:

1. **Everything that can fail runs first, against a staged copy** — the copy
   itself, the duration probe, the content hash. A missing source or an
   un-probeable file raises here and the project is exactly as it was: the old
   song, its alignment and its scores all still stand.
2. **What the old song vouched for is dropped** — `alignments.json` and the
   score tracks — BEFORE the manifest can name the new song. The song is the
   alignment reference, so an offset measured against the old one is meaningless
   against the new one; but nothing on disk ties an alignment record to a song,
   so a manifest that named the new song beside the old alignment would have
   those offsets read as its own and cut a video silently out of sync (the
   muvid#59 failure shape, by a different route). Dropping first means the worst
   a crash can leave is a project that demands a fresh `align_footage`.
3. **The song file lands, then the manifest is replaced LAST** — it names the
   file, so the file must exist before any reader can be pointed at it.

`name` is the display name (an uploaded file’s original name); `duration_s`
is a duration the caller already probed, so a 100 MB song is not probed twice.
With a host catalog the song is registered too, and its id recorded as
`song_artifact_id` (it equals `song_hash`: both are the SHA-256 of the bytes).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### song_hash()

The clean song’s content hash (cached in the manifest; computed if missing).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

#### song_info()

The song’s display facts (`None` before one is set).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### write_edit(edit_id, record)

Persist one named edit record (replacing it atomically).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### write_edit_history(edit_id, history)

Persist one edit’s undo/redo stacks (atomically; callers hold the lock).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.footage.workspace.atomic_write_bytes(path, data)

Replace `path`’s content with `data` so a reader never sees a torn file.

The ONE tmp + fsync + `os.replace` dance in the package (muvid#17 item 4); every
JSON record in this module goes through [`atomic_write_text()`](#muvid.footage.workspace.atomic_write_text), and the
scoring layer’s `.npz` arrays come here directly. The pieces, and why each is
load-bearing:

- \*\*The temp file is created in `path`’s own directory\*\* (`tempfile.mkstemp`,
  so a concurrent writer gets its own name rather than racing on a fixed
  `.tmp`). `os.replace` is only a rename — and only atomic — within one
  filesystem; a temp under `/tmp` would make it a copy with a torn window.
- **The temp is fsync’d before the rename.** Without it the rename can reach the
  journal before the data reaches the disk, and a power cut leaves the new name
  pointing at zeros — the failure mode a rename alone is wrongly believed to
  exclude.
- **The directory is fsync’d after the rename, best-effort.** That is what makes
  the rename itself — and any `unlink` a caller did in the same directory just
  before it, which is how [`MusicVideoFootageProject.set_song()`](#muvid.footage.workspace.MusicVideoFootageProject.set_song) orders its
  invalidation — durable. Directories cannot be opened for fsync on every platform
  (Windows), so an `OSError` there is the one exception swallowed: the data is
  already safe on disk; only the metadata’s promptness is lost.
- **On any failure the temp is removed and the error propagates.** `write_text`
  raised too; the difference is that `path` still holds the previous complete
  record instead of a truncated one.

`mkstemp` creates the file `0600`, which is what these per-user records want and
what `downloads.organise` already did for `meta.json`; the mode of an existing
file is not carried over, deliberately, so the outcome does not depend on history.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.footage.workspace.atomic_write_text(path, text)

Atomically replace `path` with `text` (UTF-8) — see [`atomic_write_bytes()`](#muvid.footage.workspace.atomic_write_bytes).

Every `manifest.json`, `alignments.json` and render `meta.json` write goes
through here. UTF-8 is a superset of the ASCII `json.dumps` emits by default, so
the bytes on disk are identical to what `write_text` produced and the locale-default
`read_text` on the read side is untouched — the FORMAT does not change, only the
way it gets there.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.footage.workspace.data_root()

The muvid data root: `$MUVID_DATA_HOME` or `~/.local/share/muvid`.

A thin forwarder rather than `data_root = data_home`: a plain alias keeps
`__module__ == "muvid.paths"`, which drops the name out of anything that filters
a module’s members by where they were defined — `automodule :members:` (so this
docstring would not render here) and this repo’s own drift-test idiom in
`tests/test_mcp.py`. Costs one call; keeps the module’s surface introspectable.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### muvid.footage.workspace.file_lock(path)

An exclusive advisory lock on `path` for a read-modify-write (POSIX `flock`;
`msvcrt.locking` on Windows). Serialises concurrent edits of one project’s files;
each write is also atomic, so a reader never needs the lock.

### muvid.footage.workspace.fresh_output(dest)

A temp path beside `dest` for a writer that cannot be told to be careful
(ffmpeg); on success it is renamed onto `dest` — a new inode, as in
[`replace_file()`](#muvid.footage.workspace.replace_file) — and on failure removed.

### muvid.footage.workspace.init_footage_project(root, , project_id, title='', canvas='landscape', media_catalog=None, email='')

A footage project rooted at `root`, writing its manifest if there is none yet.

The one place a footage manifest is born, for both the MCP workspace (below) and a
host-placed `muvid.Project` (whose footage lives at `<project>/footage`).
Idempotent: an existing manifest is left exactly as it is.

* **Return type:**
  [`MusicVideoFootageProject`](#muvid.footage.workspace.MusicVideoFootageProject)

### muvid.footage.workspace.normalise_id(value, , label)

`value` stripped, and refused (`ValueError`) unless it matches
[`ID_PATTERN`](#muvid.footage.workspace.ID_PATTERN).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> normalise_id(" c01 ", label="clip_id")
'c01'
>>> normalise_id("*", label="clip_id")
Traceback (most recent call last):
    ...
ValueError: invalid clip_id '*': use 1-64 letters, digits, '_' or '-'
```

### muvid.footage.workspace.replace_file(src, dest)

Put a copy of `src` at `dest` as a NEW file (temp sibling + `os.replace`).

Never writes into an existing `dest`: a project’s media is hardlinked into the
host’s content-addressed catalog (`blobs/<sha256>`), so an in-place overwrite would
silently change the bytes behind an id that names the old ones. A rename gives
`dest` a new inode and leaves the blob exactly as it was.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
