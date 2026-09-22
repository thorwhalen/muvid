# muvid.lyricvid.render_web

The web backend: a [`Scene`](muvid.lyricvid.scene.html.md#muvid.lyricvid.scene.Scene) as a deterministic
HTML page, screenshotted frame by frame and muxed with the song.

ASS ([`muvid.lyricvid.render_ass`](muvid.lyricvid.render_ass.html.md#module-muvid.lyricvid.render_ass)) is the default backend and should stay
that way — it is fast, has no browser to install, and libass is what every
player already runs. This module is the **escape hatch**: CSS filters, blend
modes, variable fonts and arbitrary easing are things a subtitle format cannot
express, and a treatment that wants them should not have to become a different
product. One compiler, two renderers.

**Determinism is the whole design, not a nice property.** The page computes
every visual property as a pure function of the time handed to
`window.setTime(t)`. There are no CSS declarations that interpolate over wall
time, no keyframes, no `requestAnimationFrame`-driven state, no `Date.now`,
no `Math.random` — so a screenshot at `t` is the same image on every run, on
every machine, and the capture loop can take as long as it likes per frame
without the video drifting against the audio. Anything that makes the page’s
appearance depend on *when* it was asked rather than *what* it was asked is a
bug in this module, and [`scene_to_html()`](#muvid.lyricvid.render_web.scene_to_html)’s doctests assert the absence of
the usual offenders.

The technique (deterministic `setTime` + Playwright capture + an ffmpeg mux)
is ported from a working 81-second render; the poem-specific parts of that
prototype are not here.

**This renderer does no layout.** A cue’s centre, size and stacking come from
[`compile_scene()`](muvid.lyricvid.scene.html.md#muvid.lyricvid.scene.compile_scene) and are written into the markup
once, unchanged; the page never re-measures text and never nudges a coordinate.
That is what makes the two backends comparable — the same scene, drawn twice —
and it means a layout mistake upstream shows up here as a visibly wrong frame
rather than being quietly absorbed. Absorbing it would be worse: it would make
the browser and ASS renders disagree, and would then be wrong again the day the
compiler changed.

Playwright is an **optional** dependency (`pip install 'muvid[lyricvid-web]'`
plus `playwright install chromium`). It is imported inside function bodies, so
importing this module costs nothing and stays on muvid’s import-safe path.

### Module Attributes

| [`INSTALL_HINT`](#muvid.lyricvid.render_web.INSTALL_HINT)   | What to tell a caller who has not installed the optional backend.   |
|-----------------------------------------------------------------|---------------------------------------------------------------------|

### Functions

| [`scene_to_html`](#muvid.lyricvid.render_web.scene_to_html)(scene)                             | A self-contained page exposing a deterministic `setTime(t)`.   |
|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| [`render`](#muvid.lyricvid.render_web.render)(scene, \*, audio, output, workdir[, ...]) | Capture the scene's page with Playwright and mux the song.     |

### Exceptions

| [`WebRenderUnavailable`](#muvid.lyricvid.render_web.WebRenderUnavailable)   | Playwright (or its Chromium build) is not installed.   |
|-------------------------------------------------------------------------|--------------------------------------------------------|

### muvid.lyricvid.render_web.INSTALL_HINT *= "muvid's web lyric-video backend needs Playwright and a Chromium build:\\n    pip install 'muvid[lyricvid-web]'\\n    playwright install chromium\\nThe ASS backend (muvid.lyricvid.render_ass) needs neither and is the default."*

What to tell a caller who has not installed the optional backend. Both lines
matter: the wheel and the browser binary are separate installs, and missing
the second is the failure people actually hit.

### *exception* muvid.lyricvid.render_web.WebRenderUnavailable

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Playwright (or its Chromium build) is not installed. Carries the remedy.

### muvid.lyricvid.render_web.render(scene, , audio, output, workdir, crf=18, scale=1)

Capture the scene’s page with Playwright and mux the song.

* **Parameters:**
  * **scene** ([`Scene`](muvid.lyricvid.scene.html.md#muvid.lyricvid.scene.Scene)) – the compiled scene. Its canvas fixes the size and frame rate.
  * **audio** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – the song. Its stream is copied in unmodified.
  * **output** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – where the mp4 goes.
  * **workdir** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – scratch space owned by this render. The page is left behind as
    an artifact; the frames are deleted.
  * **crf** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – libx264 quality (lower is better; 18 is visually lossless-ish).
  * **scale** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – device pixel ratio for the capture. The viewport is
    `canvas / scale` CSS pixels and the screenshot comes back at the
    canvas size either way, so the output resolution and the (all-`vh`)
    layout are unchanged — what moves is the CSS pixel grid the browser
    lays out and rasterises on. Leave it at `1` unless a font is
    rasterising badly at the canvas’s own grid.
* **Return type:**
  [`RenderResult`](muvid.subgenres.html.md#muvid.subgenres.RenderResult)
* **Returns:**
  A [`RenderResult`](muvid.subgenres.html.md#muvid.subgenres.RenderResult) whose `artifacts['page']` is
  the HTML that produced it — open it in a browser and call `setTime`.
* **Raises:**
  * [**WebRenderUnavailable**](#muvid.lyricvid.render_web.WebRenderUnavailable) – Playwright or its Chromium build is missing.
  * [**FileNotFoundError**](https://docs.python.org/3/builtins/exceptions.html#FileNotFoundError) – `audio` does not exist.
  * [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – the canvas cannot be captured or encoded as asked.

### muvid.lyricvid.render_web.scene_to_html(scene)

A self-contained page exposing a deterministic `setTime(t)`.

One absolutely-positioned element per cue, placed once from the cue’s
normalised centre and sized in `vh`; the script only ever changes
opacity, transform, colour and clip — never content, never position — so
words cannot reflow. Nothing in the page depends on wall-clock time.

* **Parameters:**
  **scene** ([`Scene`](muvid.lyricvid.scene.html.md#muvid.lyricvid.scene.Scene)) – the compiled scene. Colours must be `#rrggbb`.
* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
* **Returns:**
  A complete HTML document, safe to write to a file and open.

```pycon
>>> from muvid.lyricvid import spec as spec_mod
>>> from muvid.lyricvid.scene import compile_scene
>>> from muvid.lyricvid.timed_text import from_words
>>> tt = from_words([('one', 0.0, 0.5), ('two', 0.5, 1.0)], duration=1.0)
>>> scene = compile_scene(spec_mod.TreatmentSpec(), tt)
>>> page = scene_to_html(scene)
```

A page, with a deterministic entry point:

```pycon
>>> page.startswith('<!doctype html>')
True
>>> 'function setTime(t)' in page and 'window.setTime = setTime' in page
True
>>> 'window.__ready' in page
True
```

One element per cue, and the words themselves are in the markup:

```pycon
>>> page.count('class="cue"') == len(scene.cues)
True
>>> '>one<' in page and '>two<' in page
True
```

And none of the ways a page stops being a pure function of `t`:

```pycon
>>> forbidden = ('Math.random', 'Date.now', 'requestAnimationFrame',
...              'performance.now', 'transition:', 'animation:',
...              '@keyframes', 'setInterval', 'setTimeout')
>>> [bad for bad in forbidden if bad in page]
[]
```
