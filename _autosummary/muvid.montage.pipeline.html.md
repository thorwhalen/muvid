# muvid.montage.pipeline

The montage pipeline — the one path from a song and a pool to a finished video.

> audio + photos[] / clips[] (+ cover)
> : -> Analysis        beat grid, bars, sections     (muvid.montage.analysis)
>   -> TreatmentSpec   the creative decision         (muvid.montage.spec)
>   -> Plan            every cut and framing computed (muvid.montage.plan)
>   -> mp4             ffmpeg, bounded stages         (muvid.montage.render)

Each arrow is a seam with a working default, so the whole thing runs on a bare
`song.wav` and three photos with no AI, no API key and no network — and the
plan is written to `plan.json` BEFORE any frame is rendered, so a render
that fails still leaves the edit list behind for inspection.

Resource bounds are REFUSED, not clamped: a clamped request silently produces
a different video than the one asked for. The bounds read the same env-var
shape as the lyric video (`MUVID_MONTAGE_MAX_PIXELS` etc.) with the same
defaults, plus a per-input file-count bound — 64 photos is a montage, 6,400
is a denial of service.

### Module Attributes

| [`MAX_PIXELS`](#muvid.montage.pipeline.MAX_PIXELS)   | Resource bounds, env-configurable like `MUVID_LYRICVID_*`.   |
|---------------------------------------------------------------|--------------------------------------------------------------|

### Functions

| [`build_treatment`](#muvid.montage.pipeline.build_treatment)(params)                            | `(treatment, repair_notes, source)` from a request's params.   |
|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| [`check_render_bounds`](#muvid.montage.pipeline.check_render_bounds)(canvas, duration_s, \*[, ...]) | Refuse a render that would exceed the resource bounds.         |
| [`render`](#muvid.montage.pipeline.render)(request)                                    | Render one montage.                                            |

### muvid.montage.pipeline.MAX_PIXELS *= 8294400*

Resource bounds, env-configurable like `MUVID_LYRICVID_*`. Same defaults:
a 4K frame, 60 fps, a 15-minute song. The media bound is this subgenre’s own.

### muvid.montage.pipeline.build_treatment(params)

`(treatment, repair_notes, source)` from a request’s params.

A supplied `treatment` is coerced and repaired; otherwise a one-scene
treatment is built from `archetype` (default `beat_cut`).

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`TreatmentSpec`](muvid.montage.spec.html.md#muvid.montage.spec.TreatmentSpec), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

```pycon
>>> t, notes, source = build_treatment({'archetype': 'grid'})
>>> t.scenes[0].archetype, notes, source
('grid', [], 'archetype')
>>> build_treatment({'treatment': {'scenes': [{'archetype': 'nope'}]}})[1:]
(["scenes[0].archetype 'nope' -> 'beat_cut'"], 'supplied')
```

### muvid.montage.pipeline.check_render_bounds(canvas, duration_s, , n_photos=0, n_clips=0)

Refuse a render that would exceed the resource bounds. Refuse, not clamp.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

```pycon
>>> check_render_bounds(Canvas(width=1920, height=1080, fps=30), 200.0, n_photos=12)
>>> check_render_bounds(Canvas(width=30000, height=30000, fps=30), 10.0)
Traceback (most recent call last):
...
ValueError: canvas 30000x30000 is 900000000 px/frame; the bound is 8294400 (MUVID_MONTAGE_MAX_PIXELS)
>>> check_render_bounds(Canvas(width=640, height=360, fps=24), 10.0, n_photos=65)
Traceback (most recent call last):
...
ValueError: 65 photos; the bound is 64 per input (MUVID_MONTAGE_MAX_MEDIA)
```

### muvid.montage.pipeline.render(request)

Render one montage. Satisfies [`muvid.subgenres.Renderer`](muvid.subgenres.html.md#muvid.subgenres.Renderer).

`request.inputs` takes `audio` (required), `photos` and/or `clips`
(at least one non-empty) and an optional `cover`. `request.params`
takes `treatment` or `archetype`, `strict`, `beats`,
`beats_per_bar`, `sections`, `width`, `height` and `fps`.

* **Return type:**
  [`RenderResult`](muvid.subgenres.html.md#muvid.subgenres.RenderResult)
