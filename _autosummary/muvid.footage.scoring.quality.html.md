# muvid.footage.scoring.quality

Quality tier: sharpness / exposure / stability_shake / face_framing → song-grid tracks.

Pure given a [`FramePass`](muvid.footage.scoring.frames.html.md#muvid.footage.scoring.frames.FramePass) (one decode pass, shared with
[`motionbeat`](muvid.footage.scoring.motionbeat.html.md#module-muvid.footage.scoring.motionbeat)). Each per-frame signal is mapped from clip time to
song time (`song_t = clip_t + offset_s`) and resampled onto the shared grid. All cheap-CPU
(OpenCV/MediaPipe), commercial-clean (Apache-2.0).

Gating: a per-frame `quality_ok` mask (sharpness/exposure above env-tunable FLOORS) is
computed so the orchestrator can AND it into coverage; the FLOORS default to disabled
(0.0) — an absolute blur/exposure threshold is fragile across cameras, so v1 prefers the
soft signal (sharpness/exposure as weighted metrics) and only hard-gates when the owner sets
a floor. See `misc/docs/footage_scoring_design.md` §3a.

### Module Attributes

| [`SHARPNESS_FLOOR`](#muvid.footage.scoring.quality.SHARPNESS_FLOOR)   | Env-tunable hard gate floors (default disabled → no hard gate; soft metric only).   |
|--------------------------------------------------------------------|-------------------------------------------------------------------------------------|

### Functions

| [`quality_gate_mask`](#muvid.footage.scoring.quality.quality_gate_mask)(frame_pass, \*, offset_s, ...)   | A `quality_ok` grid mask (True = usable), from the env FLOORS (default: all True).   |
|-----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| [`quality_tracks`](#muvid.footage.scoring.quality.quality_tracks)(frame_pass, \*, clip_id, ...)       | Sharpness / exposure / stability_shake / face_framing tracks for one clip.           |

### muvid.footage.scoring.quality.SHARPNESS_FLOOR *= 0.0*

Env-tunable hard gate floors (default disabled → no hard gate; soft metric only).

### muvid.footage.scoring.quality.quality_gate_mask(frame_pass, , offset_s, t0, hop_s, n)

A `quality_ok` grid mask (True = usable), from the env FLOORS (default: all True).

The orchestrator ANDs this into the composite mask so a truly-unusable (black / blown /
frozen) frame is excluded from selection. Disabled by default to avoid over-masking.

* **Return type:**
  `ndarray`

### muvid.footage.scoring.quality.quality_tracks(frame_pass, , clip_id, offset_s, t0, hop_s, n)

Sharpness / exposure / stability_shake / face_framing tracks for one clip.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ScoreTrack`](muvid.footage.scoring.grid.html.md#muvid.footage.scoring.grid.ScoreTrack)]
