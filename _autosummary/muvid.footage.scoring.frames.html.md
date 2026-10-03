# muvid.footage.scoring.frames

One decode pass per clip → the shared per-frame artifacts the quality + motion extractors
both consume.

The design review flagged that quality.py and motionbeat.py must NOT each decode the clip and
each estimate camera motion — that doubles IO/CPU on the memory-fragile box and makes the
“camera-motion computed once” claim false. So a single sequential [`sample_clip_frames()`](#muvid.footage.scoring.frames.sample_clip_frames)
pass computes, per sampled frame: sharpness, exposure, an (injected) face score, the
camera-compensated motion residual, and the global-motion (shake) magnitude — in clip time.
The orchestrator maps clip time → song time via the clip’s offset and hands this
[`FramePass`](#muvid.footage.scoring.frames.FramePass) to both extractors.

cv2 is lazy-imported (`muvid[scoring]` extra). Hard caps (`max_frames`) bound the work;
`should_cancel` is polled so a cancel lands within a few frames.

### Module Attributes

| [`DEFAULT_SAMPLE_FPS`](#muvid.footage.scoring.frames.DEFAULT_SAMPLE_FPS)   | Default frame sample rate (Hz) — plenty for quality + a motion envelope onto a 10 Hz grid.   |
|-----------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| [`DEFAULT_MAX_FRAMES`](#muvid.footage.scoring.frames.DEFAULT_MAX_FRAMES)   | Default hard cap on sampled frames per clip (bounds CPU + memory regardless of duration).    |

### Functions

| [`make_face_scorer`](#muvid.footage.scoring.frames.make_face_scorer)()                       | Build a `bgr_frame -> face_framing_score | None` (or `None` if unavailable).                                                 |
|-------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| [`sample_clip_frames`](#muvid.footage.scoring.frames.sample_clip_frames)(clip_path, \*[, ...]) | Decode `clip_path` ONCE, sampling ~\`\`sample_fps\`\` frames → a [`FramePass`](#muvid.footage.scoring.frames.FramePass). |

### Classes

| [`FramePass`](#muvid.footage.scoring.frames.FramePass)(clip_times, sharpness, exposure, ...)   | Per-sampled-frame metrics for one clip, in CLIP time (seconds from the clip start).   |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|

### muvid.footage.scoring.frames.DEFAULT_MAX_FRAMES *= 1200*

Default hard cap on sampled frames per clip (bounds CPU + memory regardless of duration).

### muvid.footage.scoring.frames.DEFAULT_SAMPLE_FPS *= 5.0*

Default frame sample rate (Hz) — plenty for quality + a motion envelope onto a 10 Hz grid.

### *class* muvid.footage.scoring.frames.FramePass(clip_times, sharpness, exposure, face, motion_residual, global_dx, global_dy, fps, n_sampled)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Per-sampled-frame metrics for one clip, in CLIP time (seconds from the clip start).

### muvid.footage.scoring.frames.make_face_scorer()

Build a `bgr_frame -> face_framing_score | None` (or `None` if unavailable).

Best-effort and never fatal: face_framing is a soft metric (not one of the two named
signals), so ANY failure → `None` and the metric reads as *unmeasured* (NaN →
masked → not counted in the composite denominator), never as a zero score. Tries the
classic `mp.solutions.face_detection` (mediapipe 0.10 full build); falls back to the
Tasks `FaceDetector` ONLY when the operator supplies a model via
`MUVID_MEDIAPIPE_FACE_MODEL` (no auto-download). Score = best face’s area×centering.

### muvid.footage.scoring.frames.sample_clip_frames(clip_path, , sample_fps=5.0, max_frames=1200, face_fn=None, flow_downscale=4, should_cancel=None)

Decode `clip_path` ONCE, sampling ~\`\`sample_fps\`\` frames → a [`FramePass`](#muvid.footage.scoring.frames.FramePass).

* **Parameters:**
  * **clip_path** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – the video file.
  * **sample_fps** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – target frames/second to analyze (strided over the native fps).
  * **max_frames** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – hard cap on analyzed frames (bounds cost).
  * **face_fn** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`ndarray`], [`float`](https://docs.python.org/3/builtins/functions.html#float)]]) – optional `bgr_frame -> face_score | None` (mediapipe, injected by the
    caller so this module stays cv2-only). `None` → face score is NaN
    everywhere, i.e. *not measured* rather than *measured as worst*.
  * **flow_downscale** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – downscale factor for the Farneback flow (cost bound).
  * **should_cancel** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[], [`bool`](https://docs.python.org/3/builtins/functions.html#bool)]]) – polled every frame; returns early (a partial pass) when it goes True.
* **Return type:**
  [`FramePass`](#muvid.footage.scoring.frames.FramePass)
