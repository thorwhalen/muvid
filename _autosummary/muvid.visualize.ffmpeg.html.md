# muvid.visualize.ffmpeg

Low-level ffmpeg/ffprobe primitives shared across [`muvid.visualize`](muvid.visualize.html.md#module-muvid.visualize).

Thin, dependency-free wrappers around the `ffmpeg`/`ffprobe` binaries that
`muvid.visualize` assumes on the PATH: running a command with a readable
error, probing duration and streams, checking that an optional filter was
compiled in, decoding raw PCM for an analysis pass, and measuring loudness
(EBU R128) for two-pass normalization.

Nothing here knows about music or a destination platform — it is the shared
substrate under [`muvid.visualize.canvas`](muvid.visualize.canvas.html.md#module-muvid.visualize.canvas), [`muvid.visualize.visuals`](muvid.visualize.visuals.html.md#module-muvid.visualize.visuals),
[`muvid.visualize.reactive`](muvid.visualize.reactive.html.md#module-muvid.visualize.reactive), and [`muvid.visualize.video`](muvid.visualize.video.html.md#module-muvid.visualize.video).

### Module Attributes

| [`FFMPEG_TIMEOUT_ENV_VAR`](#muvid.visualize.ffmpeg.FFMPEG_TIMEOUT_ENV_VAR)   | Env var bounding a single ffmpeg invocation's wall-clock (seconds).                                                                                                  |
|---------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`DEFAULT_LOUDNESS_I`](#muvid.visualize.ffmpeg.DEFAULT_LOUDNESS_I)       | Loudness targets, in the units EBU R128 / ffmpeg `loudnorm` uses.                                                                                                    |
| [`PCM_SAMPLE_FORMAT`](#muvid.visualize.ffmpeg.PCM_SAMPLE_FORMAT)        | little-endian 32-bit float, which is exactly `numpy.float32`'s memory layout, so an analysis pass can read the bytes straight into an array with no conversion step. |

### Functions

| [`decode_pcm`](#muvid.visualize.ffmpeg.decode_pcm)(audio, \*, sample_rate[, channels])   | Decode `audio` to raw [`PCM_SAMPLE_FORMAT`](#muvid.visualize.ffmpeg.PCM_SAMPLE_FORMAT) samples on stdout.    |
|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| [`has_filter`](#muvid.visualize.ffmpeg.has_filter)(name)                                 | Whether this ffmpeg build has the `name` filter compiled in.                                                   |
| [`measure_loudness`](#muvid.visualize.ffmpeg.measure_loudness)(audio[, target])                | Analyse `audio` (loudnorm pass 1) and return `target` with the result.                                         |
| [`media_duration`](#muvid.visualize.ffmpeg.media_duration)(media)                            | Duration of `media` in seconds.                                                                                |
| [`probe`](#muvid.visualize.ffmpeg.probe)(media)                                     | Return `ffprobe`'s `format` + `streams` JSON for `media`.                                                      |
| [`require_ffmpeg`](#muvid.visualize.ffmpeg.require_ffmpeg)(\*tools)                          | Raise a helpful [`FfmpegError`](#muvid.visualize.ffmpeg.FfmpegError) if any of `tools` is not on PATH. |
| [`require_filter`](#muvid.visualize.ffmpeg.require_filter)(name, \*, needed_for)             | Raise unless this ffmpeg build has the `name` filter.                                                          |
| [`run_ffmpeg`](#muvid.visualize.ffmpeg.run_ffmpeg)(args, \*[, overwrite])                | Run `ffmpeg` with `args`, raising a readable error on failure.                                                 |

### Classes

| [`Loudness`](#muvid.visualize.ffmpeg.Loudness)([integrated, true_peak, lra, measured])   | An EBU R128 loudness target, plus the measurement of a specific track.   |
|-----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|

### Exceptions

| [`FfmpegError`](#muvid.visualize.ffmpeg.FfmpegError)   | An ffmpeg/ffprobe invocation failed, or a needed tool/filter is absent.   |
|----------------------------------------------------------------|---------------------------------------------------------------------------|

### muvid.visualize.ffmpeg.DEFAULT_LOUDNESS_I *= -14.0*

Loudness targets, in the units EBU R128 / ffmpeg `loudnorm` uses.
The defaults match what YouTube normalizes playback to, so a track mastered
here is neither turned down nor left quiet relative to the rest of a playlist.

### muvid.visualize.ffmpeg.FFMPEG_TIMEOUT_ENV_VAR *= 'MUVID_FFMPEG_TIMEOUT_S'*

Env var bounding a single ffmpeg invocation’s wall-clock (seconds). Unset =
no timeout (the historical behaviour). A long-running host (the reelee MCP
connector renders synchronously over HTTP) sets this so an oversized or
adversarial input can’t pin a worker indefinitely — a belt-and-braces bound
on top of the caller’s own input-duration cap.

### *exception* muvid.visualize.ffmpeg.FfmpegError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

An ffmpeg/ffprobe invocation failed, or a needed tool/filter is absent.

### *class* muvid.visualize.ffmpeg.Loudness(integrated=-14.0, true_peak=-1.0, lra=11.0, measured=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

An EBU R128 loudness target, plus the measurement of a specific track.

`measured` is the `loudnorm` analysis pass output (`None` until
[`measure_loudness()`](#muvid.visualize.ffmpeg.measure_loudness) has run). Carrying both lets [`filter_spec()`](#muvid.visualize.ffmpeg.Loudness.filter_spec)
emit the accurate two-pass filter when a measurement exists and fall back
to the (less accurate) single-pass form when it does not.

#### filter_spec()

The `loudnorm` filter string for this target.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.visualize.ffmpeg.PCM_SAMPLE_FORMAT *= 'f32le'*

little-endian 32-bit float, which is
exactly `numpy.float32`’s memory layout, so an analysis pass can read the
bytes straight into an array with no conversion step.

* **Type:**
  Sample format the raw-PCM decode emits

### muvid.visualize.ffmpeg.decode_pcm(audio, , sample_rate, channels=1)

Decode `audio` to raw [`PCM_SAMPLE_FORMAT`](#muvid.visualize.ffmpeg.PCM_SAMPLE_FORMAT) samples on stdout.

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
  audio” as “no effect” (see [`muvid.visualize.reactive.flash_filter()`](muvid.visualize.reactive.html.md#muvid.visualize.reactive.flash_filter)).

### muvid.visualize.ffmpeg.has_filter(name)

Whether this ffmpeg build has the `name` filter compiled in.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### muvid.visualize.ffmpeg.measure_loudness(audio, target=None)

Analyse `audio` (loudnorm pass 1) and return `target` with the result.

Two-pass `loudnorm` is the only accurate way to hit a loudness target:
pass 1 measures the program loudness, pass 2 applies a *linear* gain from
that measurement. Single-pass loudnorm is a dynamic normalizer and will
both miss the target and squash the dynamics of music.

* **Parameters:**
  * **audio** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The audio (or video) file to measure.
  * **target** ([`Loudness`](#muvid.visualize.ffmpeg.Loudness) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – The loudness target; a default one is used when omitted.
* **Return type:**
  [`Loudness`](#muvid.visualize.ffmpeg.Loudness)
* **Returns:**
  A new [`Loudness`](#muvid.visualize.ffmpeg.Loudness) with `measured` populated.

### muvid.visualize.ffmpeg.media_duration(media)

Duration of `media` in seconds.

Falls back to the longest stream duration when the container has none.

* **Raises:**
  [**FfmpegError**](#muvid.visualize.ffmpeg.FfmpegError) – The duration could not be determined.
* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)

### muvid.visualize.ffmpeg.probe(media)

Return `ffprobe`’s `format` + `streams` JSON for `media`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.visualize.ffmpeg.require_ffmpeg(\*tools)

Raise a helpful [`FfmpegError`](#muvid.visualize.ffmpeg.FfmpegError) if any of `tools` is not on PATH.

* **Parameters:**
  **\*tools** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Binaries to require (defaults to `ffmpeg` and `ffprobe`).
* **Raises:**
  [**FfmpegError**](#muvid.visualize.ffmpeg.FfmpegError) – With per-platform install instructions.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.visualize.ffmpeg.require_filter(name, , needed_for)

Raise unless this ffmpeg build has the `name` filter.

Filters like `drawtext` (libfreetype) and `showcqt` (libfftw/avfilter
extras) are build-time options, so a working ffmpeg is not enough — the
specific filter has to be there.

* **Raises:**
  [**FfmpegError**](#muvid.visualize.ffmpeg.FfmpegError) – Naming the filter, the feature that needs it, and the fix.
* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.visualize.ffmpeg.run_ffmpeg(args, , overwrite=True)

Run `ffmpeg` with `args`, raising a readable error on failure.

* **Parameters:**
  * **args** ([`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Arguments after the global flags (inputs, filters, output).
  * **overwrite** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Pass `-y` (overwrite the output without prompting).
* **Return type:**
  [`CompletedProcess`](https://docs.python.org/3/library/subprocess.html#subprocess.CompletedProcess)
* **Returns:**
  The completed process.
* **Raises:**
  [**FfmpegError**](#muvid.visualize.ffmpeg.FfmpegError) – ffmpeg exited non-zero; the message carries the tail of
      stderr and the full command, which is what you actually need to
      debug a filtergraph.
