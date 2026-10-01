# muvid.importing

Bring a finished production into a host’s projects dir as a `muvid.Project`.

The verb is [`import_production()`](#muvid.importing.import_production); the CLI door is
`python -m muvid.importing MANIFEST PROJECTS_DIR [--dry-run]`. One JSON manifest per
production says where its pieces are; the importer creates (or reopens) the project at
`PROJECTS_DIR/<id>` exactly as a host would (`nw.create_genre_project` with
placement, so the genre envelope is recorded and the host opens it as a
`muvid.Project`), and fills it through the SAME operations the studio and the
connector use ([`muvid.footage.service`](muvid.footage.service.md#module-muvid.footage.service)) — so an imported edit is validated by
`validate_edl` like any other, and every file lands in the host’s artifact catalog.

**Idempotent**: run it twice and you have one project. The song, a clip or a render
whose bytes are already there is left alone; an offset or an edit that already says the
same thing is not rewritten.

Two kinds of manifest (paths absolute, `~`-expanded, or relative to the manifest):

`footage` — a music video cut from footage:

```default
{"kind": "footage", "id": "que_calor", "title": "Que Calor", "canvas": "landscape",
 "song": {"path": "source/master.m4a"}              # or {"path": x.mp4, "extract_audio": true}
 "clips": [{"id": "c01", "path": "footage/01.mp4", "name": "Camera A",
            "offset_s": 28.854}],                    # offset_s = a DECLARED offset
 "edits": [{"id": "v1", "name": "V1", "edl_path": "work/edl_v1d.json",
            "how_made": "…",                         # or "edl": [...] inline, or
            "span": [0.162, 157.13]}],               # "whole_song": "c01" (one shot);
                                                     # span: default the whole song
 "renders": [{"id": "v1e", "path": "out/v1e.mp4", "edit_id": "v1", "label": "V1"}]}
```

`lyric-video` — a lyric video (view-only in v1):

```default
{"kind": "lyric-video", "id": "il_pleut", "title": "Il Pleut",
 "template": "calligram", "song": {"path": "…"},
 "sources": [{"role": "lyrics", "path": "…"}],     # role: lyrics|treatment|timings|poem|other
 "renders": [{"id": "readable_v3", "path": "…", "label": "…"}]}
```

Manifests name private paths, so they live beside the data or under
`~/.local/share/muvid/imports/` — never in a repository.

### Functions

| [`import_production`](#muvid.importing.import_production)(manifest, projects_dir, \*)   | Import one production; returns a report of what was done (or would be).        |
|--------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`load_manifest`](#muvid.importing.load_manifest)(path)                             | `(manifest, base_dir)` — relative paths in it resolve against `base_dir`.      |
| [`edl_from_document`](#muvid.importing.edl_from_document)(doc, \*, source_sizes)        | The muvid EDL (a list of `EdlEntry` dicts) a production's edit document means. |

### Exceptions

| [`ImportRefused`](#muvid.importing.ImportRefused)   | The manifest cannot be imported as written (the message says which part).   |
|------------------------------------------------------------------|-----------------------------------------------------------------------------|

### *exception* muvid.importing.ImportRefused

Bases: `ValueError`

The manifest cannot be imported as written (the message says which part).

### muvid.importing.edl_from_document(doc, , source_sizes)

The muvid EDL (a list of `EdlEntry` dicts) a production’s edit document means.

`source_sizes` maps each clip id to its DISPLAYED `(width, height)` — needed only
for a framing document; a clip it does not name is refused rather than guessed.

* **Return type:**
  `list`[`dict`]

```pycon
>>> doc = {"edl": [
...     {"song_start": 0.0, "song_end": 2.0, "clip_id": "a",
...      "framing": {"w": 100, "h": 50, "x0": 0, "y0": 0, "x1": 0, "y1": 0}},
...     {"song_start": 2.0, "song_end": 4.0, "clip_id": "a",
...      "framing": {"w": 50, "h": 25, "x0": 10, "y0": 5, "x1": 40, "y1": 5}}]}
>>> edl = edl_from_document(doc, source_sizes={"a": (100, 50)})
>>> "crop" in edl[0], edl[1]["crop"], edl[1]["crop_end"]["x"]
(False, {'x': 0.1, 'y': 0.1, 'w': 0.5, 'h': 0.5}, 0.4)
```

### muvid.importing.import_production(manifest, projects_dir, , base_dir=None, dry_run=False, caller='importer')

Import one production; returns a report of what was done (or would be).

`manifest` is a dict or a path to one; `projects_dir` is the host’s projects
directory (the project lands at `projects_dir/<id>`). `dry_run` checks every
file and every edit conversion and writes nothing.

* **Return type:**
  `dict`

### muvid.importing.load_manifest(path)

`(manifest, base_dir)` — relative paths in it resolve against `base_dir`.

* **Return type:**
  `tuple`[`dict`, `Path`]
