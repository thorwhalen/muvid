# muvid.cost

Cost estimation for a muvid project.

Walks the project’s ShotSpecs and returns the same CostRollup shape
`falaw.estimate_scene_cost` returns, but priced against muvid’s
render strategies (lipsync / image_to_video / text_to_video / animation
/ still) instead of falaw’s Scene/Beat IR. The pricing pulls
`falaw.pick_model` per category + `falaw.estimate_call_cost` per
ModelRecord, so any improvements to `falaw.cost` flow through.

Used by:

- `muvid.facade.estimate_render_cost(root, *, quality, force)()`
- `muvid status` shows the rollup as a summary line.
- `muvid render --budget=$X` aborts before any fal call when the
  estimate exceeds X.

### Functions

| [`estimate_render_cost`](#muvid.cost.estimate_render_cost)(project, \*[, quality, ...])   | Estimate USD cost of running `muvid render` for the whole project.   |
|------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|

### Classes

| [`CostLine`](#muvid.cost.CostLine)   |    |
|-------------------------------------------------------------|----|
| [`CostRollup`](#muvid.cost.CostRollup) |    |

### muvid.cost.CostLine

alias of `_RolledLine`

### muvid.cost.CostRollup

alias of `_Rollup`

### muvid.cost.estimate_render_cost(project, , quality='balanced', force=False)

Estimate USD cost of running `muvid render` for the whole project.

Returns a structured rollup. Per-shot pricing depends on
`shot.render_strategy`: `image_to_video` and `text_to_video`
cost 1 image-gen + 1 video-gen × duration; `lipsync` is 1
avatar × duration; `still` is 1 image-gen; `animation` is
free of fal calls (rendered locally via `an`).

`force` must mirror the flag the render will run with: under
`--force` nothing is cached, so everything is pending and priced.
Which shots are “already rendered” is decided by the RENDERER’S own
predicate (`muvid.renderers.shot_is_rendered`), not re-derived here —
the re-derivation (`output.mp4` exists) priced an edited shot and a
forced re-render at $0.00 and then billed them (muvid#52).

* **Return type:**
  `_Rollup`
