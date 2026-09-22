# muvid.choreo.render

Draw a [`ChoreoScene`](muvid.choreo.scene.html.md#muvid.choreo.scene.ChoreoScene) frame by frame and encode it.

numpy paints each frame — filled circles, rects, triangles, lines, diamonds
and rings, from coordinate grids and coverage masks with a one-pixel
anti-aliased edge — and the raw `rgb24` frames are piped straight into
ffmpeg’s stdin, which muxes the song and encodes the YouTube-spec H.264/AAC
mp4 [`muvid.visualize.video`](muvid.visualize.video.html.md#module-muvid.visualize.video) produces. Nothing touches the disk between
the scene and the mp4: a 3-minute 720p render is ~15 GB of frames, and that
is why this is a pipe and not a frame directory.

Two things keep it fast enough on a CPU:

* **only the dirty pixels are touched** — every primitive computes its
  bounding box and paints inside it; a frame’s cost is the sum of the objects
  alive in it, not the canvas size;
* **the frame buffer is the backdrop copied**, so a frame with nothing alive
  costs one `memcpy`.

This is the ONE place choreo spawns ffmpeg itself rather than through
[`muvid.visualize.ffmpeg.run_ffmpeg()`](muvid.visualize.ffmpeg.html.md#muvid.visualize.ffmpeg.run_ffmpeg), because that helper has no stdin
seam. It uses the same binary check, the same timeout env var and the same
encode/container arguments, so the mp4 it writes is the one `verify_video`
expects; when a streaming primitive lands in `muvid.visualize.ffmpeg` this
should collapse onto it.

### Functions

| [`render_scene`](#muvid.choreo.render.render_scene)(scene, \*, audio, output, workdir)   | Paint every frame of `scene`, pipe them into ffmpeg, mux `audio`.   |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| [`iter_frames`](#muvid.choreo.render.iter_frames)(scene)                                | Every frame of `scene`, in order.                                   |

### Classes

| [`RenderedVideo`](#muvid.choreo.render.RenderedVideo)(\*, output, duration_s, ...)   | What [`render_scene()`](#muvid.choreo.render.render_scene) produced, and how long it took.   |
|-----------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| [`Painter`](#muvid.choreo.render.Painter)(scene)                               | Paints frames of one scene.                                                                            |

### *class* muvid.choreo.render.Painter(scene)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Paints frames of one scene. Holds the grids and backdrops between frames.

#### alive(t)

Objects alive at `t`. Frames must be asked for in increasing `t`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Obj`](muvid.choreo.scene.html.md#muvid.choreo.scene.Obj)]

#### frame(k)

Frame `k` (at `k / fps` seconds) as a fresh `uint8` HxWx3 array.

* **Return type:**
  `ndarray`

### *class* muvid.choreo.render.RenderedVideo(, output, duration_s, n_frames, render_s)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What [`render_scene()`](#muvid.choreo.render.render_scene) produced, and how long it took.

### muvid.choreo.render.iter_frames(scene)

Every frame of `scene`, in order.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[`ndarray`]

### muvid.choreo.render.render_scene(scene, , audio, output, workdir, crf=18, preset='medium')

Paint every frame of `scene`, pipe them into ffmpeg, mux `audio`.

* **Raises:**
  * [**FfmpegError**](muvid.visualize.html.md#muvid.visualize.FfmpegError) – ffmpeg exited non-zero, died mid-stream, or overran
        `$MUVID_FFMPEG_TIMEOUT_S`; the message carries the log’s tail.
  * [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – the canvas has an odd dimension (yuv420p cannot encode one).
* **Return type:**
  [`RenderedVideo`](#muvid.choreo.render.RenderedVideo)
