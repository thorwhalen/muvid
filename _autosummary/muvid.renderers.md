# muvid.renderers

Render dispatch — turn a single ShotSpec into `shots/<id>/output.mp4`.

Each render strategy is a small function `render_<strategy>(project,
shot, *, audio_slice_path, ctx) -> Path`. The dispatcher resolves
shared dependencies (audio slice, lyric lines that fall in the shot,
character anchor image, environment anchor image) once and passes them
in.

Caching: each shot output’s name is content-derived. If
`shots/<id>/output.mp4` exists and the recorded `shot.json` hash
matches the current ShotSpec, we skip.

### Functions

| [`billed_video_seconds`](#muvid.renderers.billed_video_seconds)(duration_s)                  | The seconds a video-generation call is BILLED for a shot of this length.                                         |
|----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| `render_all`(project, \*[, quality, force])                                                        |                                                                                                                  |
| [`render_shot`](#muvid.renderers.render_shot)(project, shot_id, \*[, quality, ...]) | Render a single shot.                                                                                            |
| [`shot_is_rendered`](#muvid.renderers.shot_is_rendered)(project, shot, global_style, \*) | Whether [`render_shot()`](#muvid.renderers.render_shot) would return the cached output untouched. |

### Classes

| [`RenderContext`](#muvid.renderers.RenderContext)(\*, project, shot, shot_dir, ...)   | Shared resolved inputs for rendering a shot.   |
|----------------------------------------------------------------------------------------------------|------------------------------------------------|

### *class* muvid.renderers.RenderContext(, project, shot, shot_dir, audio_slice_path, character_image_paths, environment_image_path, lyric_lines, global_style='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Shared resolved inputs for rendering a shot.

### muvid.renderers.billed_video_seconds(duration_s)

The seconds a video-generation call is BILLED for a shot of this length.

The fal video models take an integer duration with a floor of one second —
`max(1, round())` is the exact expression both video renderers send — so
an estimate must price THIS number, never the raw float: a 0.4 s shot is
billed one second, and a zero-duration shot priced at $0.00 with
`has_unknown_costs` false is the muvid#52 defect one level down (the
estimate paraphrasing the renderer’s arithmetic instead of calling it).

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

### muvid.renderers.render_shot(project, shot_id, , quality='balanced', force=False)

Render a single shot. Returns the path to the produced mp4.

Skipped (returns the existing path) if a previously-rendered output
matches the current shot definition’s hash, unless `force=True` —
the [`shot_is_rendered()`](#muvid.renderers.shot_is_rendered) predicate, which the cost estimate shares.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### muvid.renderers.shot_is_rendered(project, shot, global_style, , force=False)

Whether [`render_shot()`](#muvid.renderers.render_shot) would return the cached output untouched.

THE definition of “already rendered” — there is exactly one, and both the
renderer and `muvid.cost.estimate_render_cost` call it. The estimate used
to apply a weaker proxy (`output.mp4` exists), so an edited-but-rendered
shot (file present, hash stale) and a `--force` run were both priced at
$0.00 and then billed (muvid#52) — the same defect family as “unknown reads
as free” (muvid#47), with “pending reads as done” as the variant. Two
predicates that must agree is the shape that produced it; this function is
the agreement.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### Modules

| [`animation`](muvid.renderers.animation.md#module-muvid.renderers.animation)           | Render strategy: animation — handoff to the `an` package.           |
|-------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| [`image_to_video`](muvid.renderers.image_to_video.md#module-muvid.renderers.image_to_video) | Render strategy: image_to_video.                                    |
| [`lipsync`](muvid.renderers.lipsync.md#module-muvid.renderers.lipsync)               | Render strategy: lipsync.                                           |
| [`still`](muvid.renderers.still.md#module-muvid.renderers.still)                   | Render strategy: still — a single image held for the shot duration. |
| [`text_to_video`](muvid.renderers.text_to_video.md#module-muvid.renderers.text_to_video)   | Render strategy: text_to_video.                                     |
