# muvid

muvid — tools to make music videos.

Two independent halves:

- **Narrative pipeline** (needs the AI extras: `falaw`, `lacing`,
  `lookbook`): transcribe a song, align lyrics, define characters and
  environments, write a shot script, render and compose. The verbs below are
  also the CLI. Project model: [`MusicVideoProject`](#muvid.MusicVideoProject) and the schema
  dataclasses.
- **Visualizer** ([`muvid.visualize`](muvid.visualize.html.md#module-muvid.visualize), needs only `ffmpeg` + `mixing`):
  turn a song and a cover into a still / Ken Burns / audio-reactive music video,
  plus a thumbnail. Deterministic, no AI, no network.
  ```pycon
  >>> from muvid.visualize import render_audio_video
  >>> render_audio_video("song.wav", image="cover.png")
  ```

The narrative-pipeline names are imported **lazily** so that `import muvid` (and
hence `import muvid.visualize`) does not require the heavy AI extras — the
import of a given name only pulls its dependencies when you actually use it.

### *class* muvid.CharacterRef(, name, description='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Pointer to a character folder under `characters/<name>/`.

The folder contains the canonical card.json + curated reference
images. We only carry the name + a quick description here so the
project SSOT stays small.

### *class* muvid.EnvironmentRef(, name, description='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Pointer to an environment folder under `environments/<name>/`.

### *class* muvid.MusicVideoProject(root)

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
  [`MusicVideoProject`](muvid.project.html.md#muvid.project.MusicVideoProject)

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

### *class* muvid.ProjectSpec(, schema_version=1, title='', song=None, characters=(), environments=(), sections=(), shots=(), global_style='', notes='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The top-level project SSOT, persisted as `project.json`.

### *class* muvid.SectionSpec(, id, start_s, end_s, label='', energy='', mood='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A non-overlapping span of the song with a label.

`label` is free-form (“intro”, “verse”, “chorus”, “bridge”,
“outro”) so users can use whatever taxonomy fits their song.

### *class* muvid.ShotSpec(, id, start_s, end_s, section_id='', render_strategy='image_to_video', environment='', characters=(), description='', camera='', framing='medium', notes='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A timeline-locked visual unit of the music video.

`[start_s, end_s)` is half-open. Shots within a project are
sorted by `start_s` and should be non-overlapping (the validator
warns otherwise — overlap can be intentional for transitions but
isn’t supported by the basic compositor).

### *class* muvid.SongInfo(, audio_path, duration_s, sample_rate=0, bitrate=0, bpm=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Metadata for the master audio file.

### muvid.align_lyrics(root, , aligner='scribe-greedy', \*\*aligner_kwargs)

Build `lyrics/alignment.annot` from transcript + lyrics.md.

`aligner` selects the alignment strategy (see
[`muvid.align.list_aligners()`](muvid.align.html.md#muvid.align.list_aligners)); extra kwargs are forwarded to
the chosen aligner. Defaults to `scribe-greedy`.

Returns the path to the alignment store.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.default_project_root(name)

Where a project called `name` belongs by default, as an absolute path.

`root` is a required positional on every other verb here and on
[`muvid.project.MusicVideoProject`](muvid.project.html.md#muvid.project.MusicVideoProject), deliberately — a pipeline that guessed
which project it was operating on would be worse than one that asks. But required
*everywhere* left **no** code answering where a NEW project should go, so the only
written-down answer lived in prose: the `muvid` skill and the `README` both said
`~/muvid/<song-stem>`, which put a real project’s 36 MB in an app-named directory
under `$HOME` that nothing owns. This is that answer in code, where it can be
tested and where callers — the skill, the README, the CLI, a script — can *ask*
instead of restating it. It creates nothing.

The CLI’s `serve` is the one exception to “required”: it defaults `root` to the
cwd. See `muvid/paths.py` — the default is right, and it was the UI’s missing
is-this-a-project precondition that made it write.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.init_project(root, , title='', song=None)

Create a new music video project. Returns the absolute root path.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.render(root, , quality='balanced', force=False, budget=None, allow_unpriced=False)

Render every shot. Returns the produced mp4 paths.

`budget` (USD): when set, refuses to start if
`estimate_render_cost()` exceeds it — \*\*or if any part of the project
could not be priced at all\*\*. Pass `None` (CLI: `--budget=-1`) to skip
the gate entirely.

`allow_unpriced`: proceed despite unpriceable work, after reading what it
is. The gate is otherwise TWO conditions, and the second is the one muvid#47
was filed about: a price this code could not determine contributes nothing to
`total_amount`, so comparing the number alone let an unpriceable shot clear
**any** budget. Unknown is not zero.

The escape exists because a threshold and an approval are different things. A
hard refusal with no way past it would make `--budget` unusable for a
project containing one exotic model — so the default refuses, and a caller
who has read the names can accept them. What it must never become is a
silent default.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.render_audio_video(audio, image=None, , visual='auto', saveas=None, size=(1920, 1080), fps=24, title=None, layout=None, title_style=None, normalize=False, loudness=None, crf=18, preset='medium', audio_bitrate='384k', gop_seconds=2.0, options=None, workdir=None)

Render `audio` into a video, using `visual` for the picture.

The video is exactly as long as the audio, 16:9, H.264/yuv420p + AAC — what
YouTube asks for. With `normalize=True` the audio is brought to a fixed
EBU R128 loudness with a two-pass `loudnorm`, which is what makes a batch
of songs play back at a consistent level.

* **Parameters:**
  * **audio** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The song (`.wav` is preferred when you have it — YouTube
    re-encodes regardless, so give it the cleanest input).
  * **image** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Cover art. Used for the picture, and composed onto a 16:9 canvas.
  * **visual** (`Union`[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`VisualContext`](muvid.visualize.visuals.html.md#muvid.visualize.visuals.VisualContext)], [`VisualPlan`](muvid.visualize.visuals.html.md#muvid.visualize.visuals.VisualPlan) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]) – A registered strategy name (`"still"`, `"ken_burns"`,
    `"cqt"`, `"bars"`, `"spectrum"`, `"waves"`, `"scope"`),
    `"auto"`, or any callable (see [`muvid.visualize.visuals`](muvid.visualize.visuals.html.md#module-muvid.visualize.visuals)).
  * **saveas** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Output path (default: `<audio-stem>.mp4`).
  * **size** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]) – Canvas size; the default is 1080p.
  * **fps** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Frame rate.
  * **title** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Burn this title into the frame.
  * **layout** ([`CoverLayout`](muvid.visualize.canvas.html.md#muvid.visualize.canvas.CoverLayout) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – How the cover sits on the canvas.
  * **title_style** ([`TitleStyle`](muvid.visualize.canvas.html.md#muvid.visualize.canvas.TitleStyle) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – How the title is drawn.
  * **normalize** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Loudness-normalize the audio (two-pass EBU R128).
  * **loudness** ([`Loudness`](muvid.visualize.ffmpeg.html.md#muvid.visualize.ffmpeg.Loudness) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – The loudness target; a YouTube-appropriate default is used
    when omitted.
  * **gop_seconds** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Encoder knobs.
  * **options** ([`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Strategy-specific options, passed to the visual.
  * **workdir** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Where intermediates go (a temporary directory by default).
* **Return type:**
  [`RenderResult`](muvid.visualize.video.html.md#muvid.visualize.video.RenderResult)
* **Returns:**
  A `RenderResult`.
* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – `size` has an odd dimension — H.264 at yuv420p (the only
      pixel format every player decodes) cannot encode one.

### muvid.status(root)

Return a summary dict of the project’s current state.

Useful for the skill / UI to show the user where they are in the
pipeline. No side effects.

Returns a structured shape with stage progression, per-shot render
status, and (when an alignment store exists) a word-confidence
histogram. Stable enough to be programmatic; pass through
`format_status()` for human-readable text.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.transcribe_song(root, , api_key=None)

Run ElevenLabs Scribe on the project’s song.

Writes the raw response to `lyrics/transcript.json` and a draft
`lyrics/lyrics.md` with auto-detected line breaks. The user is
expected to edit `lyrics.md` to fix mishears and add real section
tags. Returns the path to the lyrics markdown.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### Modules

| [`align`](muvid.align.html.md#module-muvid.align)               | Lyric → audio alignment.                                                             |
|-----------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| [`characters`](muvid.characters.html.md#module-muvid.characters)     | Character cards + reference image curation via lookbook.                             |
| [`choreo`](muvid.choreo.html.md#module-muvid.choreo)             | Choreo — event-driven visual music, muvid's second subgenre plugin.                  |
| [`compose`](muvid.compose.html.md#module-muvid.compose)           | Compose all rendered shots into the final music video.                               |
| [`contracts`](muvid.contracts.html.md#module-muvid.contracts)       | Adapters between muvid's SSOT and sibling-package shapes.                            |
| [`cost`](muvid.cost.html.md#module-muvid.cost)                 | Cost estimation for a muvid project.                                                 |
| [`data`](muvid.data.html.md#module-muvid.data)                 |                                                                                      |
| [`environments`](muvid.environments.html.md#module-muvid.environments) | Environment cards + canonical establishing image generation.                         |
| [`events`](muvid.events.html.md#module-muvid.events)             | Surface fal progress events into the muvid project.                                  |
| [`facade`](muvid.facade.html.md#module-muvid.facade)             | Top-level facade — the verbs the CLI / skill / UI all call.                          |
| [`footage`](muvid.footage.html.md#module-muvid.footage)           | Footage-aligned music video — align several device recordings of one song, assemble. |
| [`lyrics`](muvid.lyrics.html.md#module-muvid.lyrics)             | Lyrics — transcription and markdown round-trip.                                      |
| [`lyricvid`](muvid.lyricvid.html.md#module-muvid.lyricvid)         | Lyric video (kinetic typography) — muvid's first subgenre plugin.                    |
| [`mcp`](muvid.mcp.html.md#module-muvid.mcp)                   | muvid MCP server — the `music-visualizer` tool surface for a remote connector.       |
| [`montage`](muvid.montage.html.md#module-muvid.montage)           | Montage — a beat-cut video from a pool of stills and clips (subgenre plugin).        |
| [`paths`](muvid.paths.html.md#module-muvid.paths)               | The ONE place muvid decides where its data lives.                                    |
| [`project`](muvid.project.html.md#module-muvid.project)           | Project facade — folder layout, persistence, and a small `dol`-backed mall.          |
| [`renderers`](muvid.renderers.html.md#module-muvid.renderers)       | Render dispatch — turn a single ShotSpec into `shots/<id>/output.mp4`.               |
| [`schema`](muvid.schema.html.md#module-muvid.schema)             | Schema for an muvid project — the SSOT data shapes.                                  |
| [`script`](muvid.script.html.md#module-muvid.script)             | Script (screenplay) markdown ↔ ShotSpec list.                                        |
| [`subgenres`](muvid.subgenres.html.md#module-muvid.subgenres)       | muvid subgenres — the plugin surface for *another kind of video*.                    |
| [`ui`](muvid.ui.html.md#module-muvid.ui)                     | Minimal local web UI for an muvid project.                                           |
| [`visualize`](muvid.visualize.html.md#module-muvid.visualize)       | Turn a song (+ optional cover) into a visualizer music video.                        |
