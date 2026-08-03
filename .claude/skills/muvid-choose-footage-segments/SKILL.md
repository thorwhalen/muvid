---
name: muvid-choose-footage-segments
description: >
  Use when choosing WHICH uploaded footage clip to show over each span of a fixed song in
  muvid's `music_video` genre — the scoring + selection layer on top of audio alignment.
  Triggers on "which clip should be on-air", "score footage segments", "auto-choose
  footage", "pick the best take", "footage selection strategy", "multichannel editor
  view", "lip-sync score", "motion-to-beat / dance-match score", or building the auto
  composer / human-editor timeline for aligned footage. The concrete per-metric scoring
  recipes live in `muvid-score-footage`; this skill owns the score MENU, the song-time
  data model, the two-consumer principle, and the selection strategy.
---

# muvid-choose-footage-segments — score & select footage over a song

The `music_video` genre aligns N different-device recordings of one fixed song to the
clean master's timeline (`mixing.audio`, per-clip offset). This skill is the layer above:
**score** footage segments and **select** which clip is on-air over each span. Full
design + citations: `muvid/docs/footage_scoring_research.md`.

## The one idea to hold onto: one grid, two consumers

Resolve every clip's every metric onto **one fixed-rate song-time grid** (`t0=0`,
`hop≈0.1s` → ~10 Hz). Each `(clip, metric)` → a **score track**: `values[]` (normalized,
higher = better) + `raw_values[]` + a coverage `mask[]`; grid frame *k* ↔ song time
`t0 + k·hop`, identical across clips so they stack. That single tensor
`S[clip, frame, metric]` feeds BOTH:

- **Auto composer:** `composite = (Σ_m w_m·S[...,m])·mask`; a **beat-snapped Viterbi /
  semi-Markov DP** picks the on-air clip per span (node reward = composite, edge cost = a
  Potts switch penalty λ, transitions only on beats, dwell states enforce L_min/L_max).
  **The "strategy" is a config object** `(weights w, λ_switch, L_min, L_max, boundaries)`
  — adding a metric = a column + a weight (open-closed; this generalizes the existing
  `SelectionStrategy` in `muvid/footage/strategy.py`).
- **Human editor:** draw the same curves as stacked lanes over the shared song axis; a
  manual pin FIXES those DP states and a constrained re-solve fills the rest. **Never
  build a second pipeline** — the editor steers the same optimizer.

## The score menu (grouped; see `muvid-score-footage` for recipes)

- **sync** — `lip_sync_lse_c` (SyncNet mouth-crop vs the master's Demucs-separated VOCAL
  stem at the known offset; gated NA by TalkNet, never 0), `lse_d_offset` (free; its
  offset trace flags where sync breaks); `motion_beat_bas` (Beat Alignment Score) +
  `motion_onset_xcorr` (content-agnostic, covers no-person clips).
- **quality (cheap CPU, double as HARD GATES)** — `sharpness` (var-of-Laplacian),
  `exposure`, `stability_shake`, `face_framing` (MediaPipe).
- **content (v2)** — smile/expression, saliency, `section_fit`, `energy_match`.
- **structural (backbone)** — the master **beat/downbeat grid** (compute ONCE, reuse via
  offsets), **PySceneDetect** segment boundaries (the shared aggregation unit),
  `coverage_mask`, the fused `composite`, the Viterbi path, and `selection_margin`
  (best − 2nd-best → "where a human should decide").

## Decided v1 (2026-08-03)

- **In scope:** all four — lip-sync + motion-to-beat + the quality gates + the
  selector/editor-tracks machinery. (Everything else in the menu stays v2.)
- **Commercial → MIT/BSD/Apache only** (see `muvid-score-footage`).
- **Async, CPU-first** scoring (a background job after align, not the synchronous render).
- **Cut grid:** the DP DEFAULTS to switching **between clips, on beats** (the clean,
  musically-safe path) — but cutting **within a clip** at its own shot boundaries is a
  supported, easily-reverted option (a boundary-set/config flag on the strategy), not
  disallowed. The clean default must always be one flag away.

## Rules

- **Compute-once-on-master:** beats/downbeats and the Demucs vocal stem are the master's;
  everything maps to song time through each clip's offset. Never recompute per clip.
- **Gate, don't zero:** "no face / no data" is a `mask` = NA, not a 0 score — else
  selection biases toward any-face-on-screen or penalizes valid instrumental footage.
- **Normalize robustly across clips** (median/IQR, percentile-clipped) so a "motion" peak
  in clip A is comparable to clip B and to A's "sharpness" — but know this is the
  editor/auto tension (per-clip contrast vs cross-clip truth); it's a settled decision, so
  check `docs/footage_scoring_research.md` §7 before changing it.
- **Cut on the beat:** the DP only switches at beat/downbeat grid points; shot-length
  limits and the switch penalty are the strategy's tunables, not hardcoded.
- **Feasibility first:** ship the off-the-shelf tier (quality gates + master beat grid +
  PySceneDetect + the Viterbi selector) and the two named sync scores as moderate glue;
  defer VocaLiST, learned aesthetic VQA (non-commercial + GPU), emotion, and
  diversity/pacing ILP to v2 (see the report's phasing).

## Where this plugs into the existing code

- `muvid/footage/strategy.py` — the `SelectionStrategy` registry. A score-driven strategy
  is `select_edl(...)` implemented as the weighted-DP over the score tensor; the built-in
  `best_confidence`/`longest_take`/`fewest_cuts` remain the alignment-only fallbacks.
- `muvid/footage/edl.py` — `validate_edl` still gates the produced EDL; the DP produces a
  gapless, in-order, within-coverage EDL by construction.
- New (once scope is agreed): `muvid/footage/scoring/` (per-metric extractors → grid) +
  a `clip-score-track` persistence type.
