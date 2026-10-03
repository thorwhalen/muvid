# muvid.facade

Top-level facade — the verbs the CLI / skill / UI all call.

These are the same functions, just packaged so each one takes a
project root path (string) instead of a `MusicVideoProject`. They
are deliberately thin: each one resolves the project, then delegates
to the underlying module.

### Functions

| `add_character`(root, name, \*[, description, ...])                                                |                                                                                                                                        |
|----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| `add_character_images`(root, name, paths)                                                          |                                                                                                                                        |
| `add_environment`(root, name, \*[, ...])                                                           |                                                                                                                                        |
| [`align_lyrics`](#muvid.facade.align_lyrics)(root, \*[, aligner])                 | Build `lyrics/alignment.annot` from transcript + lyrics.md.                                                                            |
| `compose`(root, \*[, out_name, use_song_audio])                                                    |                                                                                                                                        |
| `curate_character`(root, name, \*[, k, recipe])                                                    |                                                                                                                                        |
| [`curate_character_interactive`](#muvid.facade.curate_character_interactive)(root, name, \*, ...) | Interactive curate driven by a pre-recorded decisions JSON.                                                                            |
| [`default_project_root`](#muvid.facade.default_project_root)(name)                        | Where a project called `name` belongs by default, as an absolute path.                                                                 |
| [`estimate_render_cost`](#muvid.facade.estimate_render_cost)(root, \*[, quality, force])  | Return a [`muvid.cost.CostRollup`](muvid.cost.md#muvid.cost.CostRollup) for the project's pending shots. |
| [`format_status`](#muvid.facade.format_status)(status_dict)                        | Human-readable rendering of [`status()`](#muvid.facade.status)'s output.                                        |
| `generate_character_images`(root, name, \*[, ...])                                                 |                                                                                                                                        |
| [`init_project`](#muvid.facade.init_project)(root, \*[, title, song])             | Create a new music video project.                                                                                                      |
| `parse_script`(root)                                                                               |                                                                                                                                        |
| [`render`](#muvid.facade.render)(root, \*[, quality, force, budget, ...])   | Render every shot.                                                                                                                     |
| `render_environment`(root, name, \*[, quality])                                                    |                                                                                                                                        |
| `render_shot`(root, shot_id, \*[, quality, force])                                                 |                                                                                                                                        |
| [`status`](#muvid.facade.status)(root)                                      | Return a summary dict of the project's current state.                                                                                  |
| [`transcribe_song`](#muvid.facade.transcribe_song)(root, \*[, api_key])              | Run ElevenLabs Scribe on the project's song.                                                                                           |
| `write_script`(root)                                                                               |                                                                                                                                        |

### muvid.facade.align_lyrics(root, , aligner='scribe-greedy', \*\*aligner_kwargs)

Build `lyrics/alignment.annot` from transcript + lyrics.md.

`aligner` selects the alignment strategy (see
[`muvid.align.list_aligners()`](muvid.align.md#muvid.align.list_aligners)); extra kwargs are forwarded to
the chosen aligner. Defaults to `scribe-greedy`.

Returns the path to the alignment store.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.facade.curate_character_interactive(root, name, , decisions, k=8, recipe='person_mock', present=6, max_rounds=20)

Interactive curate driven by a pre-recorded decisions JSON.

`decisions` is either a path to a JSON file or an in-memory list,
each element shaped like `{"keep": [<image_id>], "reject": [...],
"stop": false}`. The decisions are applied in order, one per
round. Useful for skill-driven flows: the agent shows the user the
candidates, collects their answers, writes a JSON, and re-runs.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.facade.default_project_root(name)

Where a project called `name` belongs by default, as an absolute path.

`root` is a required positional on every other verb here and on
[`muvid.project.MusicVideoProject`](muvid.project.md#muvid.project.MusicVideoProject), deliberately — a pipeline that guessed
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

### muvid.facade.estimate_render_cost(root, , quality='balanced', force=False)

Return a [`muvid.cost.CostRollup`](muvid.cost.md#muvid.cost.CostRollup) for the project’s pending shots.

“Pending” is the renderer’s own definition (hash + `force`), so pass the
same `force` you will render with to price what that run will actually do.

### muvid.facade.format_status(status_dict)

Human-readable rendering of [`status()`](#muvid.facade.status)’s output.

Single-screen-ish: title, song, stage checkmarks, render progress
bar, alignment quality summary. No colour (we don’t pull in a TTY
library).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.facade.init_project(root, , title='', song=None)

Create a new music video project. Returns the absolute root path.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.facade.render(root, , quality='balanced', force=False, budget=None, allow_unpriced=False)

Render every shot. Returns the produced mp4 paths.

`budget` (USD): when set, refuses to start if
[`estimate_render_cost()`](#muvid.facade.estimate_render_cost) exceeds it — \*\*or if any part of the project
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

### muvid.facade.status(root)

Return a summary dict of the project’s current state.

Useful for the skill / UI to show the user where they are in the
pipeline. No side effects.

Returns a structured shape with stage progression, per-shot render
status, and (when an alignment store exists) a word-confidence
histogram. Stable enough to be programmatic; pass through
[`format_status()`](#muvid.facade.format_status) for human-readable text.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.facade.transcribe_song(root, , api_key=None)

Run ElevenLabs Scribe on the project’s song.

Writes the raw response to `lyrics/transcript.json` and a draft
`lyrics/lyrics.md` with auto-detected line breaks. The user is
expected to edit `lyrics.md` to fix mishears and add real section
tags. Returns the path to the lyrics markdown.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
