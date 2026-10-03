# muvid.lyricvid.render_ass

The default lyric-video renderer: a [`Scene`](muvid.lyricvid.scene.md#muvid.lyricvid.scene.Scene) as ASS.

One [`Scene`](muvid.lyricvid.scene.md#muvid.lyricvid.scene.Scene) in, an `.ass` subtitle document and a
burnt-in mp4 out. This is the *default* backend for the lyric-video subgenre for
three reasons, in the order they mattered:

* **It is frame-exact.** libass positions and animates text against the video’s
  own clock, so a word that the aligner measured at 12.34 s is drawn at 12.34 s
  — not at “whatever frame the compositor got to”.
* **It adds no non-Python dependency.** `ffmpeg` is already a hard system
  requirement of muvid, and its `subtitles` filter *is* libass. The only new
  Python dependency is `pysubs2` (MIT, pure Python, zero dependencies of its
  own), behind the `lyricvid` extra.
* **The intermediate is a deliverable.** The `.ass` file is a plain text
  document a human can open, retime and restyle, then re-burn — so a render that
  is 95% right is *editable* rather than a reason to re-run the pipeline.

The whole of the renderer’s opinion lives in one table, `Cue` -> ASS:

Fonts are resolved through fontconfig when it is available: a family the machine
does not have falls back (to the next of [`FONT_FALLBACKS`](#muvid.lyricvid.render_ass.FONT_FALLBACKS)) rather than
failing the render, and the substitution is recorded in the result’s `meta` so
a caller can see that the type is not what the treatment asked for.

Import-safe: stdlib plus the two stdlib-only muvid modules it types against.
`pysubs2`, `ffmpeg` and the verifier are imported inside the functions that
need them, so importing this module costs a caller nothing.

### Module Attributes

| [`RENDERER_NAME`](#muvid.lyricvid.render_ass.RENDERER_NAME)   | What the result's `meta` reports as the renderer that made the file.     |
|------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`STYLE_NAME`](#muvid.lyricvid.render_ass.STYLE_NAME)      | The single ASS style every event references.                             |
| [`FONT_FALLBACKS`](#muvid.lyricvid.render_ass.FONT_FALLBACKS)  | Families to try, in order, when the treatment's family is not installed. |
| [`MOTION_FNS`](#muvid.lyricvid.render_ass.MOTION_FNS)      | `motion` name -> the events it draws.                                    |

### Functions

| [`register_motion`](#muvid.lyricvid.render_ass.register_motion)(name)                            | Register how one `motion` family draws itself.                 |
|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| [`list_motions`](#muvid.lyricvid.render_ass.list_motions)()                                   | The motion families this renderer can draw.                    |
| [`scene_to_ass`](#muvid.lyricvid.render_ass.scene_to_ass)(scene, \*[, font])                  | Render a Scene as an ASS (Advanced SubStation Alpha) document. |
| [`render`](#muvid.lyricvid.render_ass.render)(scene, \*, audio, output, workdir[, ...]) | Burn the scene over a solid background and mux the song.       |

### muvid.lyricvid.render_ass.FONT_FALLBACKS *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]* *= ('DejaVu Sans', 'Liberation Sans', 'Noto Sans', 'Arial', 'Helvetica', 'sans-serif')*

Families to try, in order, when the treatment’s family is not installed.
DejaVu first because it is what a Linux render box actually has.

### muvid.lyricvid.render_ass.MOTION_FNS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Callable](https://docs.python.org/3/library/typing.html#typing.Callable)[[\_Placed], [list](https://docs.python.org/3/builtins/stdtypes.html#list)[\_Event]]]* *= {'cut': <function \_motion_cut>, 'fade': <function \_motion_fade>, 'pop': <function \_motion_pop>, 'rise': <function \_motion_rise>, 'typewriter': <function \_motion_typewriter>, 'wipe': <function \_motion_wipe>}*

`motion` name -> the events it draws. muvid’s house registry idiom
(`register_visual`, `register_archetype`, `register_selection_strategy`):
a new motion is a function plus an entry in
[`muvid.lyricvid.spec.MOTIONS`](muvid.lyricvid.spec.md#muvid.lyricvid.spec.MOTIONS), never a branch in the writer.

### muvid.lyricvid.render_ass.RENDERER_NAME *= 'lyricvid.render_ass'*

What the result’s `meta` reports as the renderer that made the file.

### muvid.lyricvid.render_ass.STYLE_NAME *= 'Lyric'*

The single ASS style every event references. One style plus per-event
`\fs`/`\c` overrides, rather than a style per (size, colour) pair: a
scene routinely holds hundreds of distinct sizes, and a styles table that
long is unreadable to the human this file is also written for.

### muvid.lyricvid.render_ass.list_motions()

The motion families this renderer can draw.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### Examples

```pycon
>>> list_motions()
['cut', 'fade', 'pop', 'rise', 'typewriter', 'wipe']
```

### muvid.lyricvid.render_ass.register_motion(name)

Register how one `motion` family draws itself.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`_Placed`], [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`_Event`]]], [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`_Placed`], [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`_Event`]]]

### Examples

```pycon
>>> @register_motion('doctest-demo')
... def _demo(p): return []
>>> 'doctest-demo' in list_motions()
True
>>> del MOTION_FNS['doctest-demo']
```

### muvid.lyricvid.render_ass.render(scene, , audio, output, workdir, ass_path=None, crf=18)

Burn the scene over a solid background and mux the song.

Three steps, and the middle one is the whole renderer: write the `.ass`,
generate a flat `color` source at the canvas’s size and rate, and let
libass draw the document onto it while the song is mapped through untouched.

The video runs for the *song’s* measured duration rather than the scene’s, so
a treatment that stops short of the last bar still produces a video the
length of the track — which is what
[`verify_video()`](muvid.visualize.verify.md#muvid.visualize.verify.verify_video)’s duration check compares
against, and it is run here on the finished file.

* **Parameters:**
  * **scene** ([`Scene`](muvid.lyricvid.scene.md#muvid.lyricvid.scene.Scene)) – The compiled scene.
  * **audio** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The song. Its duration sets the video’s.
  * **output** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Where the mp4 goes.
  * **workdir** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Directory for intermediates — this render owns it.
  * **ass_path** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Where to keep the subtitle document (default:
    `workdir/<output stem>.ass`). It is a deliverable, not a temp file.
  * **crf** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – x264 quality, lower is better. 18 is visually lossless for flat
    colour and text.
* **Return type:**
  [`RenderResult`](muvid.subgenres.md#muvid.subgenres.RenderResult)
* **Returns:**
  A [`RenderResult`](muvid.subgenres.md#muvid.subgenres.RenderResult) carrying the mp4, its
  measured duration, the `.ass` under `artifacts['ass']`, and a `meta`
  recording the renderer, the font actually used (and what was asked for,
  when they differ), the cue/event counts and any verification failure.
* **Raises:**
  [**FfmpegError**](muvid.visualize.ffmpeg.md#muvid.visualize.ffmpeg.FfmpegError) – This ffmpeg has no `subtitles`
      filter (it is a libass build option, so a working ffmpeg is not
      enough), or the burn failed.

### muvid.lyricvid.render_ass.scene_to_ass(scene, , font=None)

Render a Scene as an ASS (Advanced SubStation Alpha) document.

The document is built through `pysubs2` (MIT, pure Python, no dependencies
of its own) rather than by string-formatting the format’s header by hand: it
owns the `[Script Info]` / `[V4+ Styles]` field order and the
centisecond time format, and it parses back, so the output is round-trip
checkable rather than merely plausible.

`PlayResX`/`PlayResY` are the canvas, so libass’s coordinates are the
video’s pixels 1:1 and the same document burns correctly at 1080p or 4K only
by changing the canvas it was compiled for.

* **Parameters:**
  * **scene** ([`Scene`](muvid.lyricvid.scene.md#muvid.lyricvid.scene.Scene)) – The compiled scene. Every number in it is already resolved.
  * **font** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Family to typeset in. Defaults to the scene’s typography; an
    uninstalled family falls back (see `_resolve_font()`).
* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
* **Returns:**
  The `.ass` document, as text.

### Examples

```pycon
>>> from muvid.lyricvid.scene import Canvas, Cue, Scene
>>> from muvid.lyricvid.spec import Typography
>>> scene = Scene(
...     canvas=Canvas(width=1920, height=1080, fps=30),
...     duration=2.0,
...     background='#101014',
...     cues=(
...         Cue(text='red', x=0.25, y=0.5, size=0.2, t_in=0.0, t_full=0.0,
...             t_out=1.0, t_gone=1.0, colour='#ff0000', motion='cut'),
...         Cue(text='fade', x=0.5, y=0.25, size=0.1, t_in=1.0, t_full=1.2,
...             t_out=1.8, t_gone=2.0, colour='#e0533d', layer=2),
...     ),
...     typography=Typography(family='DejaVu Sans'),
... )
>>> doc = scene_to_ass(scene)
```

The normalised centre becomes absolute pixels, and the style anchors at
centre-centre (alignment 5) so `\pos` means what `Cue` says it means:

```pycon
>>> r'\pos(480,540)' in doc      # 0.25*1920, 0.5*1080
True
>>> r'\pos(960,270)' in doc      # 0.5*1920, 0.25*1080
True
```

Colours reverse to `&HBBGGRR&` — red is `&H0000FF&`, not `&HFF0000&`:

```pycon
>>> r'\c&H0000FF&' in doc
True
>>> r'\c&HFF0000&' in doc
False
```

`size` is a fraction of canvas height, so 0.2 of 1080 is a 216px font,
and the motions emit their own tags:

```pycon
>>> r'\fs216' in doc, r'\fad(200,200)' in doc
(True, True)
```

It parses back — the round trip is what makes the document a deliverable
rather than a guess:

```pycon
>>> import pysubs2
>>> subs = pysubs2.SSAFile.from_string(doc)
>>> len(subs.events), subs.events[0].style, subs.events[1].layer
(2, 'Lyric', 2)
>>> subs.info['PlayResX'], subs.info['PlayResY']
('1920', '1080')
```
