# muvid.footage.scoring.grid

The song-time score grid: `ScoreTrack`, resample-to-grid, robust normalization,
the fused `ScoreTensor`, and crash-consistent persistence.

The keystone of the footage-scoring layer (thorwhalen/muvid#13): every clip’s every metric
is resolved onto ONE fixed-rate song-time grid (`t0=0`, `hop_s≈0.1` → ~10 Hz), so tracks
stack into a tensor `S[clip, frame, metric]` that BOTH the auto composer (the Viterbi
selector) and the Phase-2 multichannel editor read. Frame `k` ↔ song time `t0 + k*hop_s`,
identical across clips.

Design decisions (see `misc/docs/footage_scoring_design.md` — LOCKED post-critique):

- **Raw is the SSOT.** Extractors emit `raw_values` + a coverage `mask` (NaN where
  masked). Normalization is **per-metric-global across all clips** (robust median/IQR,
  percentile-clipped), so it can only be computed once every clip’s raw track for a metric
  is in hand — it is a tensor-assembly step, not a per-extractor one. The normalized
  `values` are derived from `raw` + the stored `norm` params, so re-normalizing is free
  and the editor can show raw *and* normalized.
- **NaN never reaches a serializer.** The manifest stores `null` for an all-masked metric’s
  norm params; the MCP wire maps masked entries to `null` and relies on the `mask` array.
- **The manifest is the SSOT for grid geometry** (`t0/hop_s/n`); a per-track geometry
  mismatch is a load-time assertion, never a silent 2× misalignment.
- **Persistence is crash-consistent**: each clip’s `.npz` is written to a temp then
  `os.replace`d; `manifest.json` is written LAST via tmp+rename, so a reader sees
  either the whole prior state or the whole new one. The dance itself is
  `muvid.footage.workspace.atomic_write_bytes` — the ONE implementation the project
  manifest, the alignments and the render metadata share (muvid#17 item 4); this module
  used to carry its own copy, which is how the other records came to have none.

Pure numpy — no cv2/torch/ffmpeg here. Imported only under the `muvid[scoring]` extra
(never on the import-light `muvid.genre_music_video` path).

### Module Attributes

| [`DEFAULT_HOP_S`](#muvid.footage.scoring.grid.DEFAULT_HOP_S)   | Default grid step (seconds) → 10 Hz.   |
|------------------------------------------------------------------|----------------------------------------|

### Functions

| [`align_fingerprint`](#muvid.footage.scoring.grid.align_fingerprint)(alignments)                     | A stable, ORDER-INDEPENDENT fingerprint of a set of alignments (offsets + durations).                                                                               |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`apply_norm`](#muvid.footage.scoring.grid.apply_norm)(raw, norm, \*, direction)              | Map raw values to a robust [0,1] score (higher = better), preserving NaN.                                                                                           |
| [`build_tensor`](#muvid.footage.scoring.grid.build_tensor)(tracks, \*, t0, hop_s, n[, ...])     | Assemble per-clip [`ScoreTrack`](#muvid.footage.scoring.grid.ScoreTrack) lists into a normalized [`ScoreTensor`](#muvid.footage.scoring.grid.ScoreTensor). |
| [`compute_norm`](#muvid.footage.scoring.grid.compute_norm)(raw_tracks, masks, \*, direction)    | Per-metric-GLOBAL robust norm params from every clip's raw track for one metric.                                                                                    |
| [`grid_len`](#muvid.footage.scoring.grid.grid_len)(song_duration[, hop_s])                  | Number of grid frames spanning `[0, song_duration]` at `hop_s`.                                                                                                     |
| `load_manifest`(project_root)                                                                      |                                                                                                                                                                     |
| [`load_tensor`](#muvid.footage.scoring.grid.load_tensor)(project_root)                         | Reconstruct the [`ScoreTensor`](#muvid.footage.scoring.grid.ScoreTensor) from persisted arrays + manifest, or `None`.                                           |
| [`manifest_is_current`](#muvid.footage.scoring.grid.manifest_is_current)(manifest, \*, song_hash, ...) | Whether persisted scores match the CURRENT song + alignments (else they are stale).                                                                                 |
| [`resample_to_grid`](#muvid.footage.scoring.grid.resample_to_grid)(sample_times, ...[, max_gap_s])  | Resample irregular `(song_time, value)` samples onto the fixed grid.                                                                                                |
| [`save_scores`](#muvid.footage.scoring.grid.save_scores)(project_root, tracks, \*, t0, ...)    | Persist per-clip raw arrays + a manifest, crash-consistently.                                                                                                       |
| `scores_dir`(project_root)                                                                         |                                                                                                                                                                     |
| `scores_present`(project_root)                                                                     |                                                                                                                                                                     |

### Classes

| [`ScoreTensor`](#muvid.footage.scoring.grid.ScoreTensor)(clip_ids, metrics, t0, hop_s, n, ...)   | The fused `S[clip, frame, metric]` (normalized) + `M[clip, frame]` mask.   |
|------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|
| [`ScoreTrack`](#muvid.footage.scoring.grid.ScoreTrack)(clip_id, metric, t0, hop_s, ...)         | One `(clip, metric)` curve on the shared song-time grid.                   |

### muvid.footage.scoring.grid.DEFAULT_HOP_S *= 0.1*

Default grid step (seconds) → 10 Hz. Ample for a UI and beat-level selection.

### *class* muvid.footage.scoring.grid.ScoreTensor(clip_ids, metrics, t0, hop_s, n, S, M, raw, norms)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The fused `S[clip, frame, metric]` (normalized) + `M[clip, frame]` mask.

`S` carries NaN where masked (never used for reward — the selector reads `M`).
`raw` keeps the un-normalized values for the editor/tooltips. Geometry
(`t0/hop_s/n`) is authoritative here (validated against the manifest at load).

### *class* muvid.footage.scoring.grid.ScoreTrack(clip_id, metric, t0, hop_s, raw_values, mask, direction='higher_better')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One `(clip, metric)` curve on the shared song-time grid.

Extractors produce `raw_values` (NaN where masked) + `mask`; `direction` says
whether higher raw = better (`"higher_better"`) or lower = better
(`"lower_better"`, e.g. a distance like LSE-D — inverted at normalization). The
normalized `values` are NOT stored here; they are derived at tensor assembly from
`raw_values` + the per-metric global `norm` params (see [`apply_norm()`](#muvid.footage.scoring.grid.apply_norm)).

#### coverage_fraction()

Fraction of frames that are valid — surfaced so an all-NA metric is visible.

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)

#### to_meta()

Everything except the arrays (arrays live in the `.npz`).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.scoring.grid.align_fingerprint(alignments)

A stable, ORDER-INDEPENDENT fingerprint of a set of alignments (offsets + durations).

The SSOT (shared by the scoring job’s idempotency key AND the read-time staleness guard):
a re-align that moves any offset changes this fingerprint, so persisted scores computed
against the old offsets are detected as stale. Sorting the full triples (not just by
clip_id) makes it independent of alignment order.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.footage.scoring.grid.apply_norm(raw, norm, , direction)

Map raw values to a robust [0,1] score (higher = better), preserving NaN.

`z = (raw − median)/IQR` (IQR==0 → neutral 0.5), clipped to `[p5,p95]` mapped to
[0,1]; `lower_better` metrics are inverted so the output is always higher_better.

* **Return type:**
  `ndarray`

### muvid.footage.scoring.grid.build_tensor(tracks, , t0, hop_s, n, metrics=None, clip_ids=None, norms=None)

Assemble per-clip [`ScoreTrack`](#muvid.footage.scoring.grid.ScoreTrack) lists into a normalized [`ScoreTensor`](#muvid.footage.scoring.grid.ScoreTensor).

`tracks` maps `clip_id -> [ScoreTrack, ...]`. The metric axis is the union of metrics
present (or the explicit `metrics` order); a clip missing a listed metric contributes
an all-masked column (so the tensor is rectangular and a missing extractor never shifts
columns). Normalization is per-metric-global (computed here if `norms` is not supplied).
Every track’s geometry must match `(t0, hop_s, n)` — a mismatch raises (the SSOT rule).

* **Return type:**
  [`ScoreTensor`](#muvid.footage.scoring.grid.ScoreTensor)

### muvid.footage.scoring.grid.compute_norm(raw_tracks, masks, , direction)

Per-metric-GLOBAL robust norm params from every clip’s raw track for one metric.

Pools all valid (unmasked, finite) raw values across clips → `{median, iqr, p5, p95}`.
Returns `None` if there are no valid values (an all-masked metric) — the caller stores
`null` and [`apply_norm()`](#muvid.footage.scoring.grid.apply_norm) then yields all-NaN (never a divide-by-zero or a NaN in
the manifest).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.footage.scoring.grid.grid_len(song_duration, hop_s=0.1)

Number of grid frames spanning `[0, song_duration]` at `hop_s`.

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

### muvid.footage.scoring.grid.load_tensor(project_root)

Reconstruct the [`ScoreTensor`](#muvid.footage.scoring.grid.ScoreTensor) from persisted arrays + manifest, or `None`.

Geometry + norms come from the manifest (SSOT); each clip’s `.npz` supplies raw+mask.
A clip/metric absent from a `.npz` is filled as an all-masked column.

* **Return type:**
  [`ScoreTensor`](#muvid.footage.scoring.grid.ScoreTensor) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.footage.scoring.grid.manifest_is_current(manifest, , song_hash, align_fingerprint)

Whether persisted scores match the CURRENT song + alignments (else they are stale).

Guards the read path: a re-align (or a new song) that raced the score job’s rmtree, or a
manifest predating the fingerprint, is detected here so no stale/mislabeled scores are
served to the editor or the weighted selector.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### muvid.footage.scoring.grid.resample_to_grid(sample_times, sample_values, , t0, hop_s, n, max_gap_s=None)

Resample irregular `(song_time, value)` samples onto the fixed grid.

Vectorized (O(n_samples + n)): samples are mean-binned onto the grid, then linearly
interpolated across bins that have no sample — but ONLY within the sampled span and
ONLY across gaps ≤ `max_gap_s` (so a real coverage gap stays masked, never invented).
Outside `[first_sample, last_sample]` the grid is masked (no extrapolation).

* **Parameters:**
  * **sample_times** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]) – song-time (s) of each sample (any order; NaN values dropped).
  * **sample_values** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]) – the sample values (parallel to `sample_times`).
  * **t0/hop_s/n** – the grid geometry (frame k ↔ `t0 + k*hop_s`, k in `[0, n)`).
  * **max_gap_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – bridge gaps up to this many seconds (default `4*hop_s`).
* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[`ndarray`, `ndarray`]
* **Returns:**
  `(values, mask)` — `values` float32[n] (NaN where masked), `mask` bool[n].

### muvid.footage.scoring.grid.save_scores(project_root, tracks, , t0, hop_s, n, metrics, song_hash, align_fingerprint='', beat_times=(), downbeat_times=(), extra=None)

Persist per-clip raw arrays + a manifest, crash-consistently.

Each clip → `scores/{clip_id}.npz` (raw + mask per metric), written tmp+\`\`os.replace\`\`.
The per-metric global `norm` params + grid geometry + beats + `song_hash` go in
`scores/manifest.json`, written LAST via tmp+rename — so a concurrent reader sees a
whole consistent state or the prior one, never a torn mix. NaN never enters the manifest
(all-masked metric → `null` norm).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)
