# muvid.visualize.video

Render an audio file into a video: pick a visual, mux, normalize, encode.

This is the assembler. A [`visuals`](muvid.visualize.visuals.md#module-muvid.visualize.visuals) strategy says what the
frames look like; everything platform-facing lives here — the 16:9 canvas, the
H.264/AAC encode YouTube prefers, EBU R128 loudness normalization, and the
guarantee that the video ends exactly when the song does.

```pycon
>>> from muvid.visualize import render_audio_video
>>> result = render_audio_video("song.wav", image="cover.png")
>>> result.path, result.duration
(PosixPath('song.mp4'), 154.92)
```

### Module Attributes

| [`DEFAULT_CRF`](#muvid.visualize.video.DEFAULT_CRF)           | visually lossless enough for a source YouTube re-encodes.                                                                              |
|------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| [`DEFAULT_AUDIO_BITRATE`](#muvid.visualize.video.DEFAULT_AUDIO_BITRATE) | YouTube's recommended audio for stereo uploads.                                                                                        |
| [`AUDIO_SAMPLE_RATE`](#muvid.visualize.video.AUDIO_SAMPLE_RATE)     | `loudnorm` upsamples to 192 kHz internally for true-peak detection, and leaks that rate into the output unless the rate is fixed here. |
| [`DEFAULT_GOP_SECONDS`](#muvid.visualize.video.DEFAULT_GOP_SECONDS)   | Seconds between keyframes.                                                                                                             |

### Functions

| [`render_audio_video`](#muvid.visualize.video.render_audio_video)(audio[, image, visual, ...])   | Render `audio` into a video, using `visual` for the picture.   |
|----------------------------------------------------------------------------------------------------|----------------------------------------------------------------|

### Classes

| [`RenderResult`](#muvid.visualize.video.RenderResult)(path, duration, size, fps, visual)   | A rendered video and what is worth knowing about it.   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------|

### muvid.visualize.video.AUDIO_SAMPLE_RATE *= 48000*

`loudnorm` upsamples to 192 kHz internally for true-peak
detection, and leaks that rate into the output unless the rate is fixed here.

* **Type:**
  Pinned deliberately

### muvid.visualize.video.DEFAULT_AUDIO_BITRATE *= '384k'*

YouTube’s recommended audio for stereo uploads.

### muvid.visualize.video.DEFAULT_CRF *= 18*

visually lossless enough for a source YouTube re-encodes.

* **Type:**
  Constant-rate-factor

### muvid.visualize.video.DEFAULT_GOP_SECONDS *= 2.0*

Seconds between keyframes. YouTube nominally asks for a GOP of half the frame
rate (a keyframe every 0.5 s); for the static and near-static pictures this
module produces, that multiplies the intra-frames — and the upload size — for
no gain, since YouTube re-encodes anyway. Two seconds is the usual compromise.
Pass `gop_seconds=0.5` for strict compliance.

### *class* muvid.visualize.video.RenderResult(path, duration, size, fps, visual, loudness=None, canvas=None, extras=<factory>)

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

### muvid.visualize.video.render_audio_video(audio, image=None, , visual='auto', saveas=None, size=(1920, 1080), fps=24, title=None, layout=None, title_style=None, normalize=False, loudness=None, crf=18, preset='medium', audio_bitrate='384k', gop_seconds=2.0, options=None, workdir=None)

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
  [`RenderResult`](#muvid.visualize.video.RenderResult)
* **Returns:**
  A [`RenderResult`](#muvid.visualize.video.RenderResult).
* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – `size` has an odd dimension — H.264 at yuv420p (the only
      pixel format every player decodes) cannot encode one.
