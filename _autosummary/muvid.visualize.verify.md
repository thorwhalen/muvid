# muvid.visualize.verify

Check a rendered music video against what YouTube actually wants.

Rendering can succeed and still produce something wrong: a video a few seconds
longer than the song, a pixel format half the world cannot decode, a thumbnail
over the upload limit, a track 6 LU quieter than the rest of the album. Those
failures are silent — the file plays fine locally — so they are worth asserting
rather than eyeballing.

```pycon
>>> from muvid.visualize import verify_video, report
>>> checks = verify_video("song.mp4", audio="song.wav")
>>> print(report(checks))
✓ container      h264 / aac
✓ pixel format   yuv420p
...
```

This is the machine-checkable half of the quality checklist; the `music2video`
skill carries the half that needs judgement.

### Module Attributes

| [`DURATION_TOLERANCE`](#muvid.visualize.verify.DURATION_TOLERANCE)   | How far the video may run past (or short of) the audio, in seconds.   |
|-----------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`LOUDNESS_TOLERANCE`](#muvid.visualize.verify.LOUDNESS_TOLERANCE)   | How far the integrated loudness may sit from the target, in LU.       |

### Functions

| [`failures`](#muvid.visualize.verify.failures)(checks)                                 | Just the checks that failed.                                               |
|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|
| [`report`](#muvid.visualize.verify.report)(checks)                                   | Render `checks` as an aligned, readable block.                             |
| [`verify_video`](#muvid.visualize.verify.verify_video)(video, \*[, audio, thumbnail, ...]) | Check `video` against YouTube's expectations; return one result per check. |

### Classes

| [`Check`](#muvid.visualize.verify.Check)(name, ok, detail)   | One verification result.   |
|----------------------------------------------------------------------------|----------------------------|

### *class* muvid.visualize.verify.Check(name, ok, detail)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One verification result.

### muvid.visualize.verify.DURATION_TOLERANCE *= 0.5*

How far the video may run past (or short of) the audio, in seconds. A still
video is cut on a GOP boundary, so an exact match is not achievable.

### muvid.visualize.verify.LOUDNESS_TOLERANCE *= 1.5*

How far the integrated loudness may sit from the target, in LU. Encoding to
AAC moves it slightly, so demanding an exact hit would fail every time.

### muvid.visualize.verify.failures(checks)

Just the checks that failed.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Check`](#muvid.visualize.verify.Check)]

### muvid.visualize.verify.report(checks)

Render `checks` as an aligned, readable block.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.visualize.verify.verify_video(video, , audio=None, thumbnail=None, loudness=None, check_loudness=False, duration_tolerance=0.5, expected_canvas=None)

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
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Check`](#muvid.visualize.verify.Check)]
* **Returns:**
  A list of [`Check`](#muvid.visualize.verify.Check). Falsy checks are the problems; [`report()`](#muvid.visualize.verify.report)
  renders them, and [`failures()`](#muvid.visualize.verify.failures) filters them.
