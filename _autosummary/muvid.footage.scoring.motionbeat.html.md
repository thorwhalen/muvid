# muvid.footage.scoring.motionbeat

Motion-to-beat tier: `motion_beat_bas` + `motion_onset_xcorr` → song-grid tracks.

Pure given a [`FramePass`](muvid.footage.scoring.frames.html.md#muvid.footage.scoring.frames.FramePass) (the camera-compensated motion
envelope) + the master `mixing.audio.BeatGrid` (computed ONCE on the clean song). No
per-clip beat/onset recomputation — everything maps to song time via the clip’s offset.

- `motion_beat_bas` — a per-beat Beat Alignment Score (AIST++ [11] idea, localized): each
  audio beat scores how close its nearest MOTION peak is (`exp(−(Δt/σ)²/2)`); the score is
  held over that beat’s interval. NA where the clip has no motion (static / no person) — never
  0 (a 0 would penalize valid instrumental footage). Person-present signal.
- `motion_onset_xcorr` — the clip-level normalized cross-correlation of the motion envelope
  vs the master onset envelope at the best lag within a bounded A/V-latency window (the lag
  refines per-clip capture latency). Content-agnostic (covers no-person clips). Held over the
  clip’s coverage.

Commercial-clean (librosa beats via `mixing[beats]`, numpy motion). See the design §3b.

### Functions

| [`motionbeat_tracks`](#muvid.footage.scoring.motionbeat.motionbeat_tracks)(frame_pass, \*, clip_id, ...)   | The two motion-to-beat tracks for one clip.   |
|----------------------------------------------------------------------------------------------------|-----------------------------------------------|

### muvid.footage.scoring.motionbeat.motionbeat_tracks(frame_pass, , clip_id, offset_s, beat_times, onset_env, onset_hop_s, t0, hop_s, n)

The two motion-to-beat tracks for one clip.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ScoreTrack`](muvid.footage.scoring.grid.html.md#muvid.footage.scoring.grid.ScoreTrack)]
