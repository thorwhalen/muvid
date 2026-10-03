# muvid.schema

Schema for an muvid project — the SSOT data shapes.

A music video project is a folder. `project.json` at the root holds the
plan: song metadata, named characters/environments, song sections, and
shots with start/end times and a chosen render strategy. Every other
file in the project (lyrics, alignment, character cards, storyboards,
shot videos) is a derived artifact that points back to entries here.

Schemas are dataclasses (frozen=True) so equality/hash are structural
and edits create new instances. `to_dict` / `from_dict` are
mechanical (asdict / kwargs); `schema_version` lets us migrate later.

### Module Attributes

| [`RenderStrategy`](#muvid.schema.RenderStrategy)   | Render strategies a single shot can use.   |
|-------------------------------------------------------------------|--------------------------------------------|

### Classes

| [`CharacterRef`](#muvid.schema.CharacterRef)(\*, name[, description])             | Pointer to a character folder under `characters/<name>/`.      |
|----------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| [`EnvironmentRef`](#muvid.schema.EnvironmentRef)(\*, name[, description])           | Pointer to an environment folder under `environments/<name>/`. |
| [`ProjectSpec`](#muvid.schema.ProjectSpec)(\*[, schema_version, title, ...])     | The top-level project SSOT, persisted as `project.json`.       |
| [`SectionSpec`](#muvid.schema.SectionSpec)(\*, id, start_s, end_s[, label, ...]) | A non-overlapping span of the song with a label.               |
| [`ShotSpec`](#muvid.schema.ShotSpec)(\*, id, start_s, end_s[, ...])           | A timeline-locked visual unit of the music video.              |
| [`SongInfo`](#muvid.schema.SongInfo)(\*, audio_path, duration_s[, ...])       | Metadata for the master audio file.                            |

### *class* muvid.schema.CharacterRef(, name, description='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Pointer to a character folder under `characters/<name>/`.

The folder contains the canonical card.json + curated reference
images. We only carry the name + a quick description here so the
project SSOT stays small.

### *class* muvid.schema.EnvironmentRef(, name, description='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Pointer to an environment folder under `environments/<name>/`.

### *class* muvid.schema.ProjectSpec(, schema_version=1, title='', song=None, characters=(), environments=(), sections=(), shots=(), global_style='', notes='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The top-level project SSOT, persisted as `project.json`.

### muvid.schema.RenderStrategy

Render strategies a single shot can use. The `render` subpackage
dispatches on this string.

alias of [`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal)[‘lipsync’, ‘image_to_video’, ‘text_to_video’, ‘animation’, ‘still’]

### *class* muvid.schema.SectionSpec(, id, start_s, end_s, label='', energy='', mood='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A non-overlapping span of the song with a label.

`label` is free-form (“intro”, “verse”, “chorus”, “bridge”,
“outro”) so users can use whatever taxonomy fits their song.

### *class* muvid.schema.ShotSpec(, id, start_s, end_s, section_id='', render_strategy='image_to_video', environment='', characters=(), description='', camera='', framing='medium', notes='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A timeline-locked visual unit of the music video.

`[start_s, end_s)` is half-open. Shots within a project are
sorted by `start_s` and should be non-overlapping (the validator
warns otherwise — overlap can be intentional for transitions but
isn’t supported by the basic compositor).

### *class* muvid.schema.SongInfo(, audio_path, duration_s, sample_rate=0, bitrate=0, bpm=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Metadata for the master audio file.
