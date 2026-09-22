# muvid.visualize

Turn a song (+ optional cover) into a visualizer music video.

The lightweight, deterministic, ffmpeg-only half of `muvid`: given audio and
usually a cover image, it produces a 16:9, H.264, loudness-normalized mp4 — a
still cover, a Ken Burns pan, or an audio-reactive visualizer (CQT, spectrogram,
waveform, bars, vectorscope) — plus a matching thumbnail. No AI, no network; the
narrative pipeline ([`muvid.facade`](muvid.facade.md#module-muvid.facade), [`muvid.renderers`](muvid.renderers.md#module-muvid.renderers)) is a separate
concern and this subpackage stands on its own.

The one call most people need:

```pycon
>>> from muvid.visualize import render_audio_video
>>> render_audio_video("song.wav", image="cover.png")
```

Everything else is a knob on that: [`list_visuals()`](muvid.visualize.visuals.md#muvid.visualize.visuals.list_visuals)
names the built-in looks, [`register_visual()`](muvid.visualize.visuals.md#muvid.visualize.visuals.register_visual) adds
your own, [`CoverLayout`](muvid.visualize.canvas.md#muvid.visualize.canvas.CoverLayout) controls how the cover
sits on the canvas, and [`thumbnail_image()`](muvid.visualize.canvas.md#muvid.visualize.canvas.thumbnail_image) derives
a 16:9 thumbnail from that same composition. `
verify_video()` checks a render against what a platform will actually accept.

Because the whole song is known before the first frame is drawn, a visual can
also be driven by *precomputed* audio analysis:
[`flash_filter()`](muvid.visualize.reactive.md#muvid.visualize.reactive.flash_filter) turns an onset envelope into an
ffmpeg `sendcmd` script, which is how the spectrogram pulses on the beat.

Needs `ffmpeg` (and `ffprobe`) on the PATH. Every built-in visual is
ffmpeg-native, except Ken Burns, which renders through `burns` (already a
dependency of `mixing`, so it needs no extra).

### Functions

| [`failures`](#muvid.visualize.failures)(checks)                                 | Just the checks that failed.                                                                                   |
|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| [`report`](#muvid.visualize.report)(checks)                                   | Render `checks` as an aligned, readable block.                                                                 |
| [`verify_video`](#muvid.visualize.verify_video)(video, \*[, audio, thumbnail, ...]) | Check `video` against YouTube's expectations; return one result per check.                                     |
| [`decode_pcm`](#muvid.visualize.decode_pcm)(audio, \*, sample_rate[, channels])   | Decode `audio` to raw `PCM_SAMPLE_FORMAT` samples on stdout.                                                   |
| [`has_filter`](#muvid.visualize.has_filter)(name)                                 | Whether this ffmpeg build has the `name` filter compiled in.                                                   |
| [`measure_loudness`](#muvid.visualize.measure_loudness)(audio[, target])                | Analyse `audio` (loudnorm pass 1) and return `target` with the result.                                         |
| [`media_duration`](#muvid.visualize.media_duration)(media)                            | Duration of `media` in seconds.                                                                                |
| [`probe`](#muvid.visualize.probe)(media)                                     | Return `ffprobe`'s `format` + `streams` JSON for `media`.                                                      |
| [`require_ffmpeg`](#muvid.visualize.require_ffmpeg)(\*tools)                          | Raise a helpful [`FfmpegError`](#muvid.visualize.FfmpegError) if any of `tools` is not on PATH. |
| [`run_ffmpeg`](#muvid.visualize.run_ffmpeg)(args, \*[, overwrite])                | Run `ffmpeg` with `args`, raising a readable error on failure.                                                 |
| [`canvas_image`](#muvid.visualize.canvas_image)(image, \*[, saveas, size, ...])     | Render the composed canvas (background + centred cover + title) as a PNG.                                      |
| [`thumbnail_image`](#muvid.visualize.thumbnail_image)(image, \*[, saveas, size, ...])  | Render `image` as a 16:9 JPEG thumbnail that YouTube will accept.                                              |
| [`flash_filter`](#muvid.visualize.flash_filter)(audio, \*, fps, duration, workdir)  | A filter fragment that makes the stream it follows pulse with the beat.                                        |
| [`onset_envelope`](#muvid.visualize.onset_envelope)(audio, \*, fps[, duration, ...])  | Per-video-frame onset strength in `[0, 1]`, with phosphor-style decay.                                         |
| [`list_visuals`](#muvid.visualize.list_visuals)()                                   | The names of every registered visual strategy.                                                                 |
| [`register_visual`](#muvid.visualize.register_visual)(name)                            | Register a visual strategy under `name` (the open-closed seam).                                                |
| [`resolve_visual`](#muvid.visualize.resolve_visual)(visual, ctx)                      | Turn `visual` (a name, or any callable) into a [`VisualPlan`](#muvid.visualize.VisualPlan).    |
| [`render_audio_video`](#muvid.visualize.render_audio_video)(audio[, image, visual, ...])  | Render `audio` into a video, using `visual` for the picture.                                                   |

### Classes

| [`Check`](#muvid.visualize.Check)(name, ok, detail)                          | One verification result.                                               |
|---------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| [`Loudness`](#muvid.visualize.Loudness)([integrated, true_peak, lra, measured]) | An EBU R128 loudness target, plus the measurement of a specific track. |
| [`CoverLayout`](#muvid.visualize.CoverLayout)([background, blur_sigma, dim, ...])  | How a cover image is placed on the canvas.                             |
| [`TitleStyle`](#muvid.visualize.TitleStyle)([size_fraction, color, font, ...])    | How a burnt-in title is drawn (ffmpeg `drawtext`).                     |
| [`VisualContext`](#muvid.visualize.VisualContext)(audio, image, duration, size, fps) | Everything a visual strategy needs to know about the render.           |
| [`VisualPlan`](#muvid.visualize.VisualPlan)([inputs, filters, video, ...])        | The ffmpeg fragments that render one strategy's video stream.          |
| [`RenderResult`](#muvid.visualize.RenderResult)(path, duration, size, fps, visual)  | A rendered video and what is worth knowing about it.                   |

### Exceptions

| [`FfmpegError`](#muvid.visualize.FfmpegError)   | An ffmpeg/ffprobe invocation failed, or a needed tool/filter is absent.   |
|----------------------------------------------------------------|---------------------------------------------------------------------------|

### *class* muvid.visualize.Check(name, ok, detail)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One verification result.

### *class* muvid.visualize.CoverLayout(background='blur', blur_sigma=30.0, dim=0.65, saturation=0.8, cover_fraction=0.92, cover_alpha=1.0, background_color='black')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

How a cover image is placed on the canvas.

#### background

`"blur"` (a blurred, darkened copy of the cover fills the
frame) or `"color"` (a flat [`background_color`](#muvid.visualize.CoverLayout.background_color)).

#### blur_sigma

Gaussian blur strength for the `"blur"` background.

#### dim

How much to darken the background, 0 (unchanged) to 1 (black).
Multiplicative — the background’s luma is *scaled* by `1 - dim`
about black, so a shadow gets darker rather than being deleted. See
`dim_saturation_lut()`; the constant is not comparable with the
additive offset that preceded it (muvid#70).

#### saturation

Background saturation (< 1 desaturates, so the sharp cover
stays the focal point).

#### cover_fraction

How much of the frame the sharp cover fills. The cover
is scaled up, keeping its aspect ratio, until it reaches this
fraction of *either* the frame width or the frame height — whichever
it hits first (so a wide cover is width-bound, a tall one
height-bound). `1.0` touches the edges; below 1 leaves padding.

#### cover_alpha

Opacity of the sharp cover, 0 (invisible) to 1 (opaque).
Below 1 lets whatever is behind the cover — a reactive visualizer,
the blurred background — show through it.

#### background_color

Fill colour when `background="color"`.

#### dim *: [float](https://docs.python.org/3/builtins/functions.html#float)* *= 0.65*

the multiplicative dim that lands the plate’s mean
DISPLAY luma where the additive `0.25` left it, pooled over four
photographs (muvid#70). The two forms are not comparable at the same
nominal value — see `dim_saturation_lut()`.

* **Type:**
  Measured, not chosen

### *exception* muvid.visualize.FfmpegError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

An ffmpeg/ffprobe invocation failed, or a needed tool/filter is absent.

### *class* muvid.visualize.Loudness(integrated=-14.0, true_peak=-1.0, lra=11.0, measured=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

An EBU R128 loudness target, plus the measurement of a specific track.

`measured` is the `loudnorm` analysis pass output (`None` until
[`measure_loudness()`](#muvid.visualize.measure_loudness) has run). Carrying both lets [`filter_spec()`](#muvid.visualize.Loudness.filter_spec)
emit the accurate two-pass filter when a measurement exists and fall back
to the (less accurate) single-pass form when it does not.

#### filter_spec()

The `loudnorm` filter string for this target.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### *class* muvid.visualize.RenderResult(path, duration, size, fps, visual, loudness=None, canvas=None, extras=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A rendered video and what is worth knowing about it.

Usable anywhere a path is (it implements `os.PathLike`).

#### path

The rendered mp4.

#### duration

Its duration in seconds.

#### size

Frame size.

#### fps

Frame rate.

#### visual

The strategy that produced it.

#### loudness

The applied loudness target and measurement, if normalized.

#### canvas

The composed canvas image, when the strategy built one — reuse
it as the thumbnail rather than re-deriving it.

### *class* muvid.visualize.TitleStyle(size_fraction=0.045, color='white', font=None, margin_fraction=0.06, box=True, box_color='black@0.45')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

How a burnt-in title is drawn (ffmpeg `drawtext`).

#### size_fraction

Font size as a fraction of canvas height.

#### color

Text colour.

#### font

Font file path, or `None` to auto-detect one.

#### margin_fraction

Distance from the bottom edge, as a fraction of height.

#### box

Draw a translucent plate behind the text (keeps it legible over
busy artwork).

#### box_color

Colour (with alpha) of that plate.

### *class* muvid.visualize.VisualContext(audio, image, duration, size, fps, layout=<factory>, title=None, title_style=None, workdir=<factory>, options=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything a visual strategy needs to know about the render.

#### audio

The audio file (ffmpeg input 0).

#### image

The cover art, if the caller supplied one.

#### duration

Audio duration in seconds.

#### size

Canvas size (width, height).

#### fps

Output frame rate.

#### layout

How the cover sits on the canvas.

#### title

Title to burn in, if any.

#### title_style

How to draw that title.

#### workdir

A directory the strategy may write intermediate files into.

#### options

Strategy-specific knobs, passed straight through by the caller.

#### require_image(visual)

The cover image, or a [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError) naming what to do instead.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### *class* muvid.visualize.VisualPlan(inputs=<factory>, filters=<factory>, video='vbg', uses_audio=False, has_cover=False, has_title=False, still=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The ffmpeg fragments that render one strategy’s video stream.

#### inputs

Extra ffmpeg input argument groups (each ends with `-i PATH`),
numbered from input 1.

#### filters

`filter_complex` chains, joined with `;` by the renderer.

#### video

Label of the video stream the chains emit.

#### uses_audio

The plan consumes the `[aviz]` audio copy.

#### has_cover

The plan already placed the cover; the renderer must not
overlay it again.

#### has_title

The plan already burnt in the title; the renderer must not
draw it again.

#### still

When set, the video *is* this static image — the renderer takes a
much cheaper path (encode one short segment, then loop it) and
ignores `inputs`/`filters`.

### muvid.visualize.canvas_image(image, , saveas=None, size=(1920, 1080), layout=None, title=None, title_style=None)

Render the composed canvas (background + centred cover + title) as a PNG.

Composing once into an image — rather than re-running a 1080p blur on every
frame — is what makes a still-image music video cheap to render, and it
gives the thumbnail and the video’s first frame a single source of truth.

* **Parameters:**
  * **image** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The cover art.
  * **saveas** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Output PNG path (default: `<image-stem>.canvas.png`).
  * **size** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]) – Canvas size.
  * **layout** ([`CoverLayout`](muvid.visualize.canvas.md#muvid.visualize.canvas.CoverLayout) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Placement/treatment of the cover (a default one when omitted).
  * **title** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Burn this title into the canvas (omit for no title).
  * **title_style** ([`TitleStyle`](muvid.visualize.canvas.md#muvid.visualize.canvas.TitleStyle) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – How to draw that title.
* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
* **Returns:**
  Path to the rendered PNG.

### muvid.visualize.decode_pcm(audio, , sample_rate, channels=1)

Decode `audio` to raw `PCM_SAMPLE_FORMAT` samples on stdout.

Analysis passes (loudness envelopes, onset detection) want *samples*, not a
container. This is the single place muvid turns a media file into raw PCM,
so `$MUVID_FFMPEG_TIMEOUT_S` bounds that decode like every other one.

* **Parameters:**
  * **audio** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The media file to decode.
  * **sample_rate** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Resample to this rate. Analysis rarely needs full quality,
    and a low rate keeps a long track’s decode cheap.
  * **channels** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Downmix to this many channels (1 = mono).
* **Return type:**
  [`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes)
* **Returns:**
  The raw PCM bytes — empty when ffmpeg could not decode `audio`.
  Returning empty rather than raising lets a caller treat “no usable
  audio” as “no effect” (see [`muvid.visualize.reactive.flash_filter()`](muvid.visualize.reactive.md#muvid.visualize.reactive.flash_filter)).

### muvid.visualize.failures(checks)

Just the checks that failed.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Check`](muvid.visualize.verify.md#muvid.visualize.verify.Check)]

### muvid.visualize.flash_filter(audio, , fps, duration, workdir, label='flash', brightness=0.25, saturation=0.8, decay=0.5)

A filter fragment that makes the stream it follows pulse with the beat.

Computes the envelope, writes the `sendcmd` script into `workdir`, and
returns the chain `,sendcmd=f=…,lutyuv@<label>=…` to append after the visual
filter (e.g. `showspectrum`).

Returns `""` — a fragment that changes nothing — when the audio yields no
envelope or this ffmpeg build lacks `FLASH_FILTERS`, so a caller can
append it unconditionally and still render.

The `lutyuv` starts as an identity table (brightness 0, saturation 1); the
script drives it. It is a LUT rather than an `eq` because `eq` exists only
in a GPL-configured ffmpeg (muvid#69) — see
[`brightness_saturation_lut()`](muvid.visualize.canvas.md#muvid.visualize.canvas.brightness_saturation_lut).

* **Parameters:**
  * **audio** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The track whose beats drive the flash.
  * **fps** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – The render’s frame rate (one command per component per frame).
  * **duration** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Clamp the flash to this many seconds (`None` = whole track).
  * **workdir** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Directory to write the `sendcmd` script into.
  * **label** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – `sendcmd` label for this flash’s `lutyuv`.
  * **brightness** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Peak brightness boost on a beat.
  * **saturation** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Peak saturation boost on a beat.
  * **decay** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Per-frame afterglow of a pulse.
* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.visualize.has_filter(name)

Whether this ffmpeg build has the `name` filter compiled in.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### muvid.visualize.list_visuals()

The names of every registered visual strategy.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.visualize.measure_loudness(audio, target=None)

Analyse `audio` (loudnorm pass 1) and return `target` with the result.

Two-pass `loudnorm` is the only accurate way to hit a loudness target:
pass 1 measures the program loudness, pass 2 applies a *linear* gain from
that measurement. Single-pass loudnorm is a dynamic normalizer and will
both miss the target and squash the dynamics of music.

* **Parameters:**
  * **audio** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The audio (or video) file to measure.
  * **target** ([`Loudness`](muvid.visualize.ffmpeg.md#muvid.visualize.ffmpeg.Loudness) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – The loudness target; a default one is used when omitted.
* **Return type:**
  [`Loudness`](muvid.visualize.ffmpeg.md#muvid.visualize.ffmpeg.Loudness)
* **Returns:**
  A new [`Loudness`](#muvid.visualize.Loudness) with `measured` populated.

### muvid.visualize.media_duration(media)

Duration of `media` in seconds.

Falls back to the longest stream duration when the container has none.

* **Raises:**
  [**FfmpegError**](#muvid.visualize.FfmpegError) – The duration could not be determined.
* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)

### muvid.visualize.onset_envelope(audio, , fps, duration=None, sr=22050, decay=0.5)

Per-video-frame onset strength in `[0, 1]`, with phosphor-style decay.

Decodes `audio` to mono, measures frame-wise loudness, takes the
half-wave-rectified *rise* in loudness (an onset/transient measure, so
sustained loud passages don’t stay lit — only attacks do), scales it
robustly to `[0, 1]`, then lets each pulse fade by `decay` per frame so a
beat flashes and trails off rather than blinking for a single frame.

* **Parameters:**
  * **audio** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The track to analyse.
  * **fps** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Video frame rate — one envelope value per frame.
  * **duration** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Clamp the envelope to this many seconds (defaults to the whole
    track).
  * **sr** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Analysis sample rate.
  * **decay** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Per-frame persistence of a pulse, 0 (no trail) to <1 (longer
    afterglow).
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]
* **Returns:**
  One value per frame. Empty if the audio could not be decoded.

### muvid.visualize.probe(media)

Return `ffprobe`’s `format` + `streams` JSON for `media`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.visualize.register_visual(name)

Register a visual strategy under `name` (the open-closed seam).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`VisualContext`](muvid.visualize.visuals.md#muvid.visualize.visuals.VisualContext)], [`VisualPlan`](muvid.visualize.visuals.md#muvid.visualize.visuals.VisualPlan) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]], [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`VisualContext`](muvid.visualize.visuals.md#muvid.visualize.visuals.VisualContext)], [`VisualPlan`](muvid.visualize.visuals.md#muvid.visualize.visuals.VisualPlan) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### Examples

```pycon
>>> @register_visual("black")
... def _black(ctx):
...     w, h = ctx.size
...     return VisualPlan(filters=[f"color=c=black:s={w}x{h}[vbg]"])
>>> "black" in list_visuals()
True
>>> _ = _VISUALS.pop("black")  # (keep the registry tidy for the next doctest)
```

### muvid.visualize.render_audio_video(audio, image=None, , visual='auto', saveas=None, size=(1920, 1080), fps=24, title=None, layout=None, title_style=None, normalize=False, loudness=None, crf=18, preset='medium', audio_bitrate='384k', gop_seconds=2.0, options=None, workdir=None)

Render `audio` into a video, using `visual` for the picture.

The video is exactly as long as the audio, 16:9, H.264/yuv420p + AAC — what
YouTube asks for. With `normalize=True` the audio is brought to a fixed
EBU R128 loudness with a two-pass `loudnorm`, which is what makes a batch
of songs play back at a consistent level.

* **Parameters:**
  * **audio** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The song (`.wav` is preferred when you have it — YouTube
    re-encodes regardless, so give it the cleanest input).
  * **image** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Cover art. Used for the picture, and composed onto a 16:9 canvas.
  * **visual** (`Union`[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`VisualContext`](muvid.visualize.visuals.md#muvid.visualize.visuals.VisualContext)], [`VisualPlan`](muvid.visualize.visuals.md#muvid.visualize.visuals.VisualPlan) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]) – A registered strategy name (`"still"`, `"ken_burns"`,
    `"cqt"`, `"bars"`, `"spectrum"`, `"waves"`, `"scope"`),
    `"auto"`, or any callable (see [`muvid.visualize.visuals`](muvid.visualize.visuals.md#module-muvid.visualize.visuals)).
  * **saveas** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Output path (default: `<audio-stem>.mp4`).
  * **size** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]) – Canvas size; the default is 1080p.
  * **fps** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Frame rate.
  * **title** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Burn this title into the frame.
  * **layout** ([`CoverLayout`](muvid.visualize.canvas.md#muvid.visualize.canvas.CoverLayout) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – How the cover sits on the canvas.
  * **title_style** ([`TitleStyle`](muvid.visualize.canvas.md#muvid.visualize.canvas.TitleStyle) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – How the title is drawn.
  * **normalize** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Loudness-normalize the audio (two-pass EBU R128).
  * **loudness** ([`Loudness`](muvid.visualize.ffmpeg.md#muvid.visualize.ffmpeg.Loudness) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – The loudness target; a YouTube-appropriate default is used
    when omitted.
  * **gop_seconds** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Encoder knobs.
  * **options** ([`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Strategy-specific options, passed to the visual.
  * **workdir** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Where intermediates go (a temporary directory by default).
* **Return type:**
  [`RenderResult`](muvid.visualize.video.md#muvid.visualize.video.RenderResult)
* **Returns:**
  A [`RenderResult`](#muvid.visualize.RenderResult).
* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – `size` has an odd dimension — H.264 at yuv420p (the only
      pixel format every player decodes) cannot encode one.

### muvid.visualize.report(checks)

Render `checks` as an aligned, readable block.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.visualize.require_ffmpeg(\*tools)

Raise a helpful [`FfmpegError`](#muvid.visualize.FfmpegError) if any of `tools` is not on PATH.

* **Parameters:**
  **\*tools** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Binaries to require (defaults to `ffmpeg` and `ffprobe`).
* **Raises:**
  [**FfmpegError**](#muvid.visualize.FfmpegError) – With per-platform install instructions.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.visualize.resolve_visual(visual, ctx)

Turn `visual` (a name, or any callable) into a [`VisualPlan`](#muvid.visualize.VisualPlan).

`"auto"` picks the cheapest strategy that suits the inputs: a still cover
when there is an image, an audio-reactive CQT when there is not.

A callable may return a [`VisualPlan`](#muvid.visualize.VisualPlan), or the path of a silent video
it rendered itself — the latter is the escape hatch for backends that do not
express themselves as an ffmpeg filtergraph (librosa/matplotlib, projectM,
a headless-browser capture…).

* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – `visual` names a strategy that is not registered.
* **Return type:**
  [`VisualPlan`](muvid.visualize.visuals.md#muvid.visualize.visuals.VisualPlan)

### muvid.visualize.run_ffmpeg(args, , overwrite=True)

Run `ffmpeg` with `args`, raising a readable error on failure.

* **Parameters:**
  * **args** ([`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Arguments after the global flags (inputs, filters, output).
  * **overwrite** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Pass `-y` (overwrite the output without prompting).
* **Return type:**
  [`CompletedProcess`](https://docs.python.org/3/library/subprocess.html#subprocess.CompletedProcess)
* **Returns:**
  The completed process.
* **Raises:**
  [**FfmpegError**](#muvid.visualize.FfmpegError) – ffmpeg exited non-zero; the message carries the tail of
      stderr and the full command, which is what you actually need to
      debug a filtergraph.

### muvid.visualize.thumbnail_image(image, , saveas=None, size=(1280, 720), layout=None, title=None, title_style=None, max_bytes=2097152)

Render `image` as a 16:9 JPEG thumbnail that YouTube will accept.

Same composition as the video canvas, so the thumbnail matches what the
viewer sees when they press play. JPEG quality is stepped down until the
file fits `max_bytes` (YouTube’s hard limit).

* **Parameters:**
  * **image** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The cover art.
  * **saveas** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Output JPEG path (default: `<image-stem>.thumb.jpg`).
  * **size** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]) – Thumbnail size (YouTube wants >= 1280x720, 16:9).
  * **layout** ([`CoverLayout`](muvid.visualize.canvas.md#muvid.visualize.canvas.CoverLayout) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Placement/treatment of the cover.
  * **title** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Burn this title into the thumbnail (omit for none).
  * **title_style** ([`TitleStyle`](muvid.visualize.canvas.md#muvid.visualize.canvas.TitleStyle) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – How to draw that title.
  * **max_bytes** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Hard size ceiling.
* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
* **Returns:**
  Path to the rendered JPEG.

### muvid.visualize.verify_video(video, , audio=None, thumbnail=None, loudness=None, check_loudness=False, duration_tolerance=0.5, expected_canvas=None)

Check `video` against YouTube’s expectations; return one result per check.

* **Parameters:**
  * **video** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The rendered mp4.
  * **audio** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – The source song — enables the duration-match check, which is the
    one that catches a mis-built filtergraph.
  * **thumbnail** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – The thumbnail to check against YouTube’s limits.
  * **loudness** ([`Loudness`](muvid.visualize.ffmpeg.md#muvid.visualize.ffmpeg.Loudness) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – The target the video was normalized to.
  * **check_loudness** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Actually measure the output’s loudness. This decodes the
    whole track, so it is off by default.
  * **duration_tolerance** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Allowed audio/video duration difference, in seconds.
  * **expected_canvas** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – The `(width, height)` the render was ASKED for. When
    given, the aspect/resolution checks verify the output matches it —
    a deliberate portrait render must not fail a hard-coded 16:9 check.
    When `None`, the classic YouTube-landscape expectations apply.
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Check`](muvid.visualize.verify.md#muvid.visualize.verify.Check)]
* **Returns:**
  A list of [`Check`](#muvid.visualize.Check). Falsy checks are the problems; [`report()`](#muvid.visualize.report)
  renders them, and [`failures()`](#muvid.visualize.failures) filters them.

### Modules

| [`canvas`](muvid.visualize.canvas.md#module-muvid.visualize.canvas)     | Lay a cover image out on a 16:9 canvas, and derive a thumbnail from it.                                             |
|-------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| [`ffmpeg`](muvid.visualize.ffmpeg.md#module-muvid.visualize.ffmpeg)     | Low-level ffmpeg/ffprobe primitives shared across [`muvid.visualize`](#module-muvid.visualize). |
| [`reactive`](muvid.visualize.reactive.md#module-muvid.visualize.reactive) | Precomputed audio-reactivity: pulse a video filter in time with the music.                                          |
| [`verify`](muvid.visualize.verify.md#module-muvid.visualize.verify)     | Check a rendered music video against what YouTube actually wants.                                                   |
| [`video`](muvid.visualize.video.md#module-muvid.visualize.video)       | Render an audio file into a video: pick a visual, mux, normalize, encode.                                           |
| [`visuals`](muvid.visualize.visuals.md#module-muvid.visualize.visuals)   | Visual strategies: how the *picture* of an audio-driven video is produced.                                          |
