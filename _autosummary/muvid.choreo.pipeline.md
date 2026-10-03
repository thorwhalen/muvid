# muvid.choreo.pipeline

The choreo pipeline — the one path from a song to a finished video.

> audio (+ optional cover)
> : -> Analysis        events, beat grid, sections   (muvid.choreo.analysis)
>   -> TreatmentSpec   the creative decision          (muvid.choreo.spec)
>   -> ChoreoScene     every object computed          (muvid.choreo.scene)
>   -> mp4             numpy frames -> ffmpeg         (muvid.choreo.render)

Each arrow is a seam with a working default, so the whole thing runs on a bare
`song.wav` with no cover, no AI, no network. [`render()`](#muvid.choreo.pipeline.render) satisfies
[`muvid.subgenres.Renderer`](muvid.subgenres.md#muvid.subgenres.Renderer); it is what the manifest names.

Resource bounds are **refusals, not clamps** — the same shape and defaults as
`muvid.lyricvid.pipeline.check_render_bounds()` under `MUVID_CHOREO_*`
names — because a clamped request silently produces a different video than
the one asked for. The bounds are checked before the analysis runs, so an
oversized request fails in milliseconds.

The cover, when given, is a **palette source only**: decoded once through
ffmpeg to a 24x24 thumbnail and reduced to six colours by numpy. No Pillow.

### Module Attributes

| [`MAX_PIXELS`](#muvid.choreo.pipeline.MAX_PIXELS)      | Resource bounds, env-configurable.     |
|------------------------------------------------------------------|----------------------------------------|
| [`MAX_INPUT_FILES`](#muvid.choreo.pipeline.MAX_INPUT_FILES) | How many files any one input may name. |

### Functions

| [`check_input_counts`](#muvid.choreo.pipeline.check_input_counts)(inputs)              | Refuse any input naming more than [`MAX_INPUT_FILES`](#muvid.choreo.pipeline.MAX_INPUT_FILES) files.   |
|------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| [`check_render_bounds`](#muvid.choreo.pipeline.check_render_bounds)(canvas, duration_s) | Refuse a render that would exceed the resource bounds.                                                      |
| [`palette_from_cover`](#muvid.choreo.pipeline.palette_from_cover)(cover, \*, workdir)  | Six colours from an image: dark bg pair, light fg, three spread accents.                                    |
| [`render`](#muvid.choreo.pipeline.render)(request)                         | Render one choreo video.                                                                                    |

### muvid.choreo.pipeline.MAX_INPUT_FILES *= 64*

How many files any one input may name. The inputs today are single paths;
the bound exists so a list-valued input added later is bounded from day one.

### muvid.choreo.pipeline.MAX_PIXELS *= 8294400*

Resource bounds, env-configurable. Same defaults as the lyric video’s.

### muvid.choreo.pipeline.check_input_counts(inputs)

Refuse any input naming more than [`MAX_INPUT_FILES`](#muvid.choreo.pipeline.MAX_INPUT_FILES) files.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

```pycon
>>> check_input_counts({'audio': 'a.wav'})
>>> check_input_counts({'frames': ['x'] * 65})
Traceback (most recent call last):
...
ValueError: inputs['frames'] names 65 files; the bound is 64 (MUVID_CHOREO_MAX_INPUT_FILES)
```

### muvid.choreo.pipeline.check_render_bounds(canvas, duration_s)

Refuse a render that would exceed the resource bounds. Refuse, not clamp.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

```pycon
>>> check_render_bounds(Canvas(width=1920, height=1080, fps=30), 200.0)
>>> check_render_bounds(Canvas(width=30000, height=30000, fps=30), 10.0)
Traceback (most recent call last):
...
ValueError: canvas 30000x30000 is 900000000 px/frame; the bound is 8294400 (MUVID_CHOREO_MAX_PIXELS)
```

### muvid.choreo.pipeline.palette_from_cover(cover, , workdir)

Six colours from an image: dark bg pair, light fg, three spread accents.

The image is decoded by ffmpeg to a `_PALETTE_THUMB` square (one
process, any format ffmpeg reads), then: `bg` is the mean of the darkest
quarter (darkened further so objects always read), `bg2` the next quarter,
`fg` the lightest eighth pushed toward white, `low` the most saturated
pixel, and `mid`/`high` that hue a third of a turn on — so the three
bands are always distinguishable whatever the cover.

* **Return type:**
  [`Palette`](muvid.choreo.spec.md#muvid.choreo.spec.Palette)

### muvid.choreo.pipeline.render(request)

Render one choreo video. Satisfies [`muvid.subgenres.Renderer`](muvid.subgenres.md#muvid.subgenres.Renderer).

`request.inputs` takes `audio` (required) and `cover`. `request.params`
takes `treatment` or `archetype`, `seed`, `beat_source`, `strict`,
`width`, `height`, `fps`.

* **Return type:**
  [`RenderResult`](muvid.subgenres.md#muvid.subgenres.RenderResult)
