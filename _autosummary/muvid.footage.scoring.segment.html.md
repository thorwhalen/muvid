# muvid.footage.scoring.segment

Shot boundaries (PySceneDetect) + the per-clip coverage mask.

- `coverage_mask` is dependency-free: the boolean of song-grid frames a clip actually
  covers (from its offset + duration). It is the base mask every metric ANDs with, and the
  authority for “does this clip exist at song time t”.
- `shot_boundaries` returns song-times of within-clip scene cuts (PySceneDetect, BSD-3).
  Consumed by the selector ONLY when `boundary_mode="beats+shots"` (they become *inter-clip*
  cut candidates at shot-boundary times — a within-clip jump cut is unrepresentable in the
  single-offset EDL model). Optional dep: absent → `[]` and the mode silently degrades to
  beats-only.

### Functions

| [`coverage_mask`](#muvid.footage.scoring.segment.coverage_mask)(\*, offset_s, duration_s, ...)   | Grid frames (bool[n]) the clip covers — using its clamped `coverage` span.     |
|-------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`shot_boundaries`](#muvid.footage.scoring.segment.shot_boundaries)(clip_path, \*, offset_s)       | Song-times of within-clip scene cuts (PySceneDetect ContentDetector), or `[]`. |

### muvid.footage.scoring.segment.coverage_mask(, offset_s, duration_s, coverage, t0, hop_s, n)

Grid frames (bool[n]) the clip covers — using its clamped `coverage` span.

* **Return type:**
  `ndarray`

### muvid.footage.scoring.segment.shot_boundaries(clip_path, , offset_s)

Song-times of within-clip scene cuts (PySceneDetect ContentDetector), or `[]`.

Returns `[]` (never raises) if PySceneDetect is not installed, so the selector’s
`beats+shots` mode degrades cleanly to beats-only.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]
