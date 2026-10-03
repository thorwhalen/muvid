# muvid.lyricvid.pipeline

The lyric-video pipeline — the one path from a song to a finished video.

> audio (+ optional lyrics / subtitles / project)
> : -> TimedText          measured word times  (muvid.lyricvid.timed_text)
>   -> TreatmentSpec      the creative decision (muvid.lyricvid.director)
>   -> Scene              every number computed (muvid.lyricvid.scene)
>   -> mp4                a renderer            (render_ass | render_web)

Each arrow is a seam with a working default, so the whole thing runs on a bare
`song.wav` with no lyrics, no AI, no API key and no network — and every stage
can be replaced without touching its neighbours.

The renderer seam defaults to `auto`, which picks **ASS** where this ffmpeg can
burn subtitles in (frame-exact, no browser, and the `.ass` file it leaves behind
is an editable deliverable in its own right) and **web** otherwise. Asking for a
backend by name never falls back – it fails loudly.

### Module Attributes

| [`RENDERERS`](#muvid.lyricvid.pipeline.RENDERERS)   | function", resolved lazily so listing costs no import.   |
|--------------------------------------------------------------|----------------------------------------------------------|

### Functions

| [`register_renderer`](#muvid.lyricvid.pipeline.register_renderer)(name, target)            | Register a renderer backend as `"module:function"`.                   |
|---------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`resolve_renderer`](#muvid.lyricvid.pipeline.resolve_renderer)(name)                     | Import and return a renderer backend.                                 |
| [`select_renderer`](#muvid.lyricvid.pipeline.select_renderer)([name])                    | Resolve `"auto"` to a backend that can actually run here.             |
| [`build_timed_text`](#muvid.lyricvid.pipeline.build_timed_text)(\*, audio[, lyrics, ...]) | Get measured word times from whichever input the caller actually has. |
| [`render`](#muvid.lyricvid.pipeline.render)(request)                            | Render one lyric video.                                               |

### muvid.lyricvid.pipeline.RENDERERS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'ass': 'muvid.lyricvid.render_ass:render', 'web': 'muvid.lyricvid.render_web:render'}*

function”, resolved lazily so listing costs no import.

* **Type:**
  name -> “module

### muvid.lyricvid.pipeline.build_timed_text(, audio, lyrics=None, subtitles=None, project=None, aligner=None)

Get measured word times from whichever input the caller actually has.

Order of preference is by how much the input is *trusted*: an existing muvid
alignment beats a subtitle file, which beats aligning lyrics ourselves,
which beats transcribing from nothing.

* **Return type:**
  [`TimedText`](muvid.lyricvid.timed_text.html.md#muvid.lyricvid.timed_text.TimedText)

### muvid.lyricvid.pipeline.register_renderer(name, target)

Register a renderer backend as `"module:function"`.

The house lazy-registry idiom (cf. `muvid.footage.strategy`): the target
is a string so adding a backend costs the import path nothing.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

```pycon
>>> register_renderer('doctest-demo', 'muvid.lyricvid.render_ass:render')
>>> 'doctest-demo' in RENDERERS
True
>>> del RENDERERS['doctest-demo']
```

### muvid.lyricvid.pipeline.render(request)

Render one lyric video. Satisfies [`muvid.subgenres.Renderer`](muvid.subgenres.html.md#muvid.subgenres.Renderer).

`request.inputs` takes `audio` (required) and any of `lyrics`,
`subtitles`, `project`. `request.params` takes `treatment` (a
treatment spec as a mapping, or omitted to have one proposed), `renderer`,
`width`, `height`, `fps`, `aligner` and `persona`.

* **Return type:**
  [`RenderResult`](muvid.subgenres.html.md#muvid.subgenres.RenderResult)

### muvid.lyricvid.pipeline.resolve_renderer(name)

Import and return a renderer backend. Accepts a callable unchanged.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)

### muvid.lyricvid.pipeline.select_renderer(name='auto')

Resolve `"auto"` to a backend that can actually run here.

`auto` is a **selector**, not a registered renderer — the same shape as
`visual="auto"` in [`muvid.visualize`](muvid.visualize.html.md#module-muvid.visualize). It picks `ass` when this
ffmpeg can burn subtitles in (it needs a libass-enabled build) and `web`
otherwise.

This is deliberately not a fallback inside `ass`. Asking for `ass` on a
build without libass **fails loudly**, because a renderer that silently
produces something other than what was asked for is the exact shape muvid
has been bitten by before. `auto` is the caller opting in to “whichever
works”, and the choice it made is recorded in the result’s `meta`.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> select_renderer('web')
'web'
>>> select_renderer('auto') in {'ass', 'web'}
True
```
