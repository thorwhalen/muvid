# muvid.footage.select_score

The score-driven `weighted` selection strategy: a beat-snapped semi-Markov Viterbi DP.

Given the fused score tensor `S[clip, frame, metric]` (from [`muvid.footage.scoring.grid`](muvid.footage.scoring.grid.md#module-muvid.footage.scoring.grid))
and the master beat grid, choose which clip is on-air over each span of the song so that the
weighted composite reward is maximized, cuts land only on beat (or beat∪shot) boundaries,
shot lengths obey `[L_min, L_max]`, and each switch pays a Potts penalty `λ_switch`. The
same optimizer the Phase-2 editor steers (a manual pin becomes a hard constraint a re-solve
fills around).

The recurrence is the corrected form from the design’s algorithm review
(`misc/docs/footage_scoring_design.md` §4c′) — the load-bearing details:

- **Feasibility is CONTINUOUS containment** (`b_i ≥ offset_c − _EPS` and
  `b_j ≤ offset_c + duration_c + _EPS`), the *exact* check `validate_edl` runs — NOT a
  grid-frame mask (a sub-hop overhang would slip past a 10 Hz mask and make `validate_edl`
  raise). So the emitted EDL passes `validate_edl` by construction.
- **Reward is a TIME integral of the normalized composite** `ĝ = Σ_m w_m·S·M / W ∈ [0,1]`:
  `R(c,i,j) = hop_s·(P[c][κ(b_j)] − P[c][κ(b_i)])` (prefix-summed → O(1) per segment,
  hop/tempo-independent). A switch then costs `λ_switch` directly in those units
  (“a cut must earn `λ_switch` seconds of perfect-footage reward”).
- **L_min is relaxed on the first & last segment**; an infeasible timeline is *classified*
  (coverage gap vs dwell-infeasible) and retried with `L_min→0` before any fallback —
  never a silent junk EDL.
- The `allowed(i,j)` clip-domain hook + the injectable boundary set make the Phase-2
  manual-pin re-solve a *pruning*, not a reformulation.
- **An unvouched alignment is a reward penalty, not a pruning** (muvid#88): a clip the
  aligner will not vouch for is charged [`UNVOUCHED_REWARD_PENALTY`](#muvid.footage.select_score.UNVOUCHED_REWARD_PENALTY) reward-seconds
  per second, which is larger than the composite’s whole range and therefore ranks trust
  above every METRIC, while leaving the clip reachable where it is the only coverage.
  Pruning it would be a second trust gate; scoring it as a metric column would let
  sharpness outbid it. It does **not** dominate the DP’s non-composite terms — see the
  constant, which says what it does and does not buy.

numpy only (no cv2/torch): registered LAZILY in [`muvid.footage.strategy`](muvid.footage.strategy.md#module-muvid.footage.strategy) so
`import muvid.footage` never pulls numpy.

### Module Attributes

| [`DEFAULT_WEIGHTS`](#muvid.footage.select_score.DEFAULT_WEIGHTS)          | Default per-metric weights.                                                 |
|---------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| [`PRESETS`](#muvid.footage.select_score.PRESETS)                  | Named presets — a filled-in config.                                         |
| [`UNVOUCHED_REWARD_PENALTY`](#muvid.footage.select_score.UNVOUCHED_REWARD_PENALTY) | Reward-seconds per second charged to a clip the aligner will not vouch for. |

### Functions

| [`resolve_config`](#muvid.footage.select_score.resolve_config)(\*[, preset, weights, config])     | Build a [`WeightedSelectionConfig`](#muvid.footage.select_score.WeightedSelectionConfig) from an optional preset + overrides.                         |
|----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| [`run_weighted`](#muvid.footage.select_score.run_weighted)(alignments, song_duration, context)  | Run the DP and return `(edl, meta)`; `meta` carries any fallback + its cause.                                                                 |
| [`selection_margin`](#muvid.footage.select_score.selection_margin)(alignments, tensor, \*[, ...])   | Per-frame `best_composite − 2nd_best_composite` over clips COVERING that frame.                                                               |
| [`weighted_selection`](#muvid.footage.select_score.weighted_selection)(alignments, song_duration, \*) | The `weighted` [`SelectionStrategy`](muvid.footage.strategy.md#muvid.footage.strategy.SelectionStrategy) (score-driven DP). |

### Classes

| [`SelectionContext`](#muvid.footage.select_score.SelectionContext)(tensor[, beat_times, ...])   | Everything the score-driven strategy needs beyond `(alignments, song_duration)`.   |
|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| [`WeightedSelectionConfig`](#muvid.footage.select_score.WeightedSelectionConfig)([weights, ...])       | The score-driven "strategy" as a pure config object (open-closed).                 |

### muvid.footage.select_score.DEFAULT_WEIGHTS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [float](https://docs.python.org/3/builtins/functions.html#float)]* *= {'exposure': 0.3, 'face_framing': 0.4, 'lip_sync_lse_c': 1.0, 'motion_beat_bas': 0.8, 'motion_onset_xcorr': 0.5, 'sharpness': 0.4, 'stability_shake': 0.3}*

Default per-metric weights. A metric absent from the tensor collapses to weight 0
(its column simply doesn’t exist), so a project scored without the lip-sync tier still
selects cleanly on the metrics it has.

### muvid.footage.select_score.PRESETS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [WeightedSelectionConfig](#muvid.footage.select_score.WeightedSelectionConfig)]* *= {'contemplative': WeightedSelectionConfig(weights={'lip_sync_lse_c': 1.0, 'motion_beat_bas': 0.8, 'motion_onset_xcorr': 0.5, 'sharpness': 0.6, 'exposure': 0.3, 'face_framing': 0.6, 'stability_shake': 0.3}, lambda_switch=0.6, l_min_s=3.0, l_max_s=12.0, l_max_overrun_penalty=0.05, boundary_mode='beats', beat_unit='beat'), 'energetic': WeightedSelectionConfig(weights={'lip_sync_lse_c': 1.0, 'motion_beat_bas': 1.0, 'motion_onset_xcorr': 0.8, 'sharpness': 0.4, 'exposure': 0.3, 'face_framing': 0.4, 'stability_shake': 0.3}, lambda_switch=0.2, l_min_s=0.8, l_max_s=4.0, l_max_overrun_penalty=0.25, boundary_mode='beats', beat_unit='beat')}*

Named presets — a filled-in config. “energetic” = many short cuts; “contemplative” = long
dwells, few cuts.

### *class* muvid.footage.select_score.SelectionContext(tensor, beat_times=(), downbeat_times=(), shot_boundaries=None, pins=None, config=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything the score-driven strategy needs beyond `(alignments, song_duration)`.

`tensor` is the fused [`ScoreTensor`](muvid.footage.scoring.grid.md#muvid.footage.scoring.grid.ScoreTensor) (or `None` → the
strategy raises a clear “run scoring first”). `shot_boundaries` (clip_id → song-times)
is consumed only when `config.boundary_mode == "beats+shots"`. `pins` (Phase 2) is a
list of `(song_start, song_end, clip_id)` hard constraints.

### muvid.footage.select_score.UNVOUCHED_REWARD_PENALTY *= 2.0*

Reward-seconds per second charged to a clip the aligner will not vouch for. The
composite ĝ is normalized to `[0, 1]`, so anything `> 1.0` dominates it: over the
same span a vouched clip at ĝ=0 outscores an unvouched one at ĝ=1, whatever the
weights say. It is a demotion and not a prohibition — where nothing else covers the
span the DP must still use the clip, pays the penalty on every path equally, and the
choice is unaffected.

**What it does NOT buy, measured rather than assumed.** “No weighting can buy an
unvouched clip a span another clip covers” is true of the reward INTEGRAL and false of
the DP as a whole, because two of the DP’s terms are not composite reward at all:

- `l_max_overrun_penalty` is caller-settable through `config`. At 0.9, a single
  40 s vouched clip is cut away from every 10 s: measured, `BAD` at 10-12, 20-22 and
  30-32 s, all of them spans the vouched clip covers.
- `_viterbi`’s transition window `max_seg_s` (4x `l_max` = 32 s at the defaults)
  is a PERFORMANCE bound, and consecutive segments must be different clips — so a
  vouched take longer than the cap cannot be one segment and the optimizer is forced
  to cut away and back. Measured under the default config, a 2 s opener plus one 58 s
  vouched clip gives `[(0,2,O), (2,34,V), (34,36,BAD), (36,60,V)]`.

Raising the penalty does not fix either: both are structural, not a scoring tie the
penalty is competing in. What repairs them is
`_absorb_neighbour()`, which hands such a span back to the
vouched cut on either side of it — losslessly, because that clip already covers it —
rather than letting the recovery gap footage the shoot actually has. Keep that pairing
in mind before changing either half.

Not a config field, and not a tensor column, deliberately. A column would be
*commensurate* with sharpness and lip-sync — a sharp unvouched clip could outrank a
dull vouched one, which is exactly the trade muvid#88 says must not be available —
and a config field would let the preference be tuned away silently by a caller
passing `config=` to the MCP tool.

### *class* muvid.footage.select_score.WeightedSelectionConfig(weights=<factory>, lambda_switch=0.35, l_min_s=1.2, l_max_s=8.0, l_max_overrun_penalty=0.15, boundary_mode='beats', beat_unit='beat')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The score-driven “strategy” as a pure config object (open-closed).

Adding a metric = a tensor column + a weight; no new strategy code.

`l_max_s` is a SOFT target: a shot longer than it pays `l_max_overrun_penalty` per
second of overrun (in the same “reward-seconds” units as `lambda_switch`), rather than
being forbidden. So a lone/best clip rolls past `l_max_s` when the only alternative is
materially worse (no forced cutaway to bad footage), yet comparable clips still cut around
`l_max_s` for variety. Segments are always different-clip (each = one shot).

### muvid.footage.select_score.resolve_config(, preset=None, weights=None, config=None)

Build a [`WeightedSelectionConfig`](#muvid.footage.select_score.WeightedSelectionConfig) from an optional preset + overrides.

Precedence: preset (or the default) < `config` dict fields < explicit `weights`.

* **Return type:**
  [`WeightedSelectionConfig`](#muvid.footage.select_score.WeightedSelectionConfig)

### muvid.footage.select_score.run_weighted(alignments, song_duration, context)

Run the DP and return `(edl, meta)`; `meta` carries any fallback + its cause.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry)], [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### muvid.footage.select_score.selection_margin(alignments, tensor, , weights=None)

Per-frame `best_composite − 2nd_best_composite` over clips COVERING that frame.

A LOCAL “where a human should decide” proxy (small margin = a toss-up), distinct from the
DP’s global path optimality. `NaN` where fewer than 2 clips cover the frame.

* **Return type:**
  `ndarray`

### muvid.footage.select_score.weighted_selection(alignments, song_duration, , context=None)

The `weighted` [`SelectionStrategy`](muvid.footage.strategy.md#muvid.footage.strategy.SelectionStrategy) (score-driven DP).

Requires a [`SelectionContext`](#muvid.footage.select_score.SelectionContext) carrying a score tensor. Raises a clear error if
scores are absent so the MCP layer can say “run muvid_score_footage first”.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry)]
