# muvid.project

Project facade — folder layout, persistence, and a small `dol`-backed mall.

A `MusicVideoProject` is just a directory with a `project.json`. Everything
else (lyrics, characters, environments, shots) lives in a predictable
sub-folder so external tools (`lookbook`, `lacing`, `mixing`, `an`)
can address slices of it directly.

The mall is a `MutableMapping` view over the same folders: useful when
called from a notebook or agent that wants to write a single character card
without learning the full schema.

### Classes

| [`MusicVideoProject`](#muvid.project.MusicVideoProject)(root)   | Filesystem-backed music video project.   |
|----------------------------------------------------------------------------|------------------------------------------|

### *class* muvid.project.MusicVideoProject(root)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Filesystem-backed music video project.

All write methods touch the disk immediately; readers always re-read
the SSOT (no in-memory cache) so external edits are picked up.

#### *classmethod* init(root, , title='', song_path=None, copy_song=True, exist_ok=False)

Create a fresh project directory.

If `song_path` is given, the audio is copied (or moved if
`copy_song=False`) into `song/`, probed for duration, and
registered in `project.json`.

* **Return type:**
  [`MusicVideoProject`](#muvid.project.MusicVideoProject)

#### log_decision(kind, \*\*payload)

Append a one-line JSON entry to `.muvid/decisions.jsonl`.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### set_song(source, , copy=True)

Register an audio file as this project’s song.

The file is copied (or moved) to `song/`, probed for duration
with ffprobe, and recorded in `project.json`.

* **Return type:**
  [`SongInfo`](muvid.schema.html.md#muvid.schema.SongInfo)

#### update_spec(\*\*changes)

Read, replace, write. Returns the new spec.

* **Return type:**
  [`ProjectSpec`](muvid.schema.html.md#muvid.schema.ProjectSpec)
