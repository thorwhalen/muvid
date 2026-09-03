---
name: muvid-choose-footage-segments
description: >
  Use when choosing WHICH uploaded footage clip to show over each span of a fixed song in
  muvid's `music_video` genre — the scoring + selection layer on top of audio alignment.
  Triggers on "which clip should be on-air", "score footage segments", "auto-choose
  footage", "pick the best take", "footage selection strategy", "multichannel editor
  view", "lip-sync score", "motion-to-beat / dance-match score", or building the auto
  composer / human-editor timeline for aligned footage. ALSO covers what happens to the
  pixels once a clip is chosen — the per-cut crop window and the `look` seam onto the
  `looks` package: "punch in", "in-shot zoom", "push-in", "Ken Burns on footage", "colour
  grade a cut", "apply a LUT", "stylize the edit". The concrete per-metric scoring recipes
  live in `muvid-score-footage`; this skill owns the score MENU, the song-time data model,
  the two-consumer principle, the selection strategy, and the crop/look spatial half.
---

# muvid-choose-footage-segments — score & select footage over a song

The `music_video` genre aligns N different-device recordings of one fixed song to the
clean master's timeline (`mixing.audio`, per-clip offset). This skill is the layer above:
**score** footage segments and **select** which clip is on-air over each span. Full
design + citations: `misc/docs/footage_scoring_research.md`.

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

## Decided v1 (implemented; LOCKED post-critique — see `misc/docs/footage_scoring_design.md`)

- **Core tier (default, prod-safe, torch-free):** motion-to-beat + quality gates + the
  selector/editor-tracks machinery. **Lip-sync is an OPT-IN tier, OFF by default, off-prod**
  (htdemucs weights are CC-BY-NC + Demucs/SyncNet OOM the fragile box) — present as a
  `lip_sync_lse_c` column when enabled, absent (collapses to weight 0) otherwise.
- **Commercial → MIT/BSD/Apache/ISC only** for the core; madmom dropped (see `muvid-score-footage`).
- **Async, CPU-first** scoring (`nw.jobs` background job, concurrency=1) after align.
- **Cut grid:** the DP DEFAULTS to switching **between clips, on beats**.
  `boundary_mode="beats+shots"` adds **inter-clip** cut candidates at each clip's PySceneDetect
  shot boundaries (needs `muvid[scoring-shots]`; degrades to beats-only without it). NB a true
  *within-single-clip jump cut* is unrepresentable in the single-`offset_s` EDL model → a
  deferred data-model change, NOT this flag.

## Rules

- **Compute-once-on-master:** beats/downbeats and the Demucs vocal stem are the master's;
  everything maps to song time through each clip's offset. Never recompute per clip.
- **Gate, don't zero:** "no face / no data" is a `mask` = NA, not a 0 score — else
  selection biases toward any-face-on-screen or penalizes valid instrumental footage.
- **Normalize robustly across clips** (median/IQR, percentile-clipped) so a "motion" peak
  in clip A is comparable to clip B and to A's "sharpness" — but know this is the
  editor/auto tension (per-clip contrast vs cross-clip truth); it's a settled decision, so
  check `misc/docs/footage_scoring_research.md` §7 before changing it.
- **Cut on the beat:** the DP only switches at beat/downbeat grid points; shot-length
  limits and the switch penalty are the strategy's tunables, not hardcoded.
- **A transition is CENTRED on the cut, for the same reason** (muvid#34). An EDL entry may
  carry `transition: {duration_s, curve}` — a blend IN from its predecessor. Each side
  supplies `duration_s/2` of source beyond its own span, so the perceptual midpoint lands
  exactly on the authored boundary; a trailing blend would put the perceived cut
  `duration_s/2` late on every transition, defeating the beat-snapping above. `validate_edl`
  refuses a transition on the first entry, an unknown curve, anything under 0.04 s, and one
  that does not fit in either side's aligned coverage — a hard cut is the default and
  needs no key. Curves: `fade`, `fadeblack`, `fadewhite`, `dissolve`, `wipe{left,right,up,
  down}`, `slide{left,right,up,down}`, `smooth{left,right}`, `circleopen`, `circleclose`.
- **Feasibility first:** ship the off-the-shelf tier (quality gates + master beat grid +
  PySceneDetect + the Viterbi selector) and the two named sync scores as moderate glue;
  defer VocaLiST, learned aesthetic VQA (non-commercial + GPU), emotion, and
  diversity/pacing ILP to v2 (see the report's phasing).

## WHERE in the frame, not just WHICH clip (muvid#60)

Selection answers *which clip is on-air*. `EdlEntry.crop` answers *which rectangle of
it* — the EDL's spatial half, added because without it every source is letterboxed onto
the canvas and a portrait clip in a landscape edit is ~68% black bars.

```python
from muvid.footage import CropWindow, EdlEntry

EdlEntry(0.0, 3.8, "c01", crop=CropWindow(x=0.0, y=0.42, w=1.0, h=0.32))  # static
EdlEntry(
    3.8,
    7.6,
    "c01",
    crop=CropWindow(0.0, 0.30, 1.0, 0.32),
    crop_end=CropWindow(0.0, 0.52, 1.0, 0.32),
)  # a pan
```

Four things to know:

- **Fractions, not pixels**, on `burns.Rect`'s convention (top-left origin, window
  fraction) — so one window is valid for every clip in a multi-device edit whatever its
  resolution, and a Ken Burns path computed in `burns` drops in with no rename table.
- **`crop_end` pans; it does not resize.** Same `w`/`h` as `crop`, enforced by
  `validate_edl`. `crop` fixes its output size ONCE, at configure time — it cannot
  vary `w`/`h` at all — so a resizing window would either refuse to configure or
  silently render at one wrong size. An in-shot **push-in** is therefore not a crop
  at all: it is a `look` (see the next section), which compiles to `zoompan`.
- **Choosing the window is editorial, and on real footage it has to be.** Measured on a
  478×850 clip of dancers under a concrete overhang: the top ~35–40% of every frame is
  dead ceiling (so a centre crop is the wrong crop), and a standing body occupies
  315–380 px against a full-width 16:9 window of 269 px — **a whole body does not fit**.
  "Heads or feet" is a per-cut decision, not a default. A motion-saliency-weighted
  vertical profile put the best centre at 0.58 of the allowed range, measurably low.
- **Absent means what it always meant.** No crop emits no filter at all, so every EDL
  written before this field renders byte-identically, and the persisted
  `music-video-edl/v1` body omits the key unless set — additive, no lacing migration.

`crop` with a `t` expression is the filter **for a pan at constant window size** —
which is the only shape this EDL can express. It is not a verdict on `zoompan`.
Measured on ffmpeg 8.1, in case you need the other shape:

- `crop` cannot vary `w`/`h` at all: those expressions are evaluated once, at
  configure time, with `t` still NAN. Depending on the expression you either get a
  refusal (`Error when evaluating the expression` / `Failed to configure input pad`)
  or — worse — a clean exit 0 and a plausible video frozen at a single wrong size.
- `zoompan` genuinely has no `t` — but it is not time-blind: `in_time` and its
  alias `it` are the elapsed input time, alongside the frame counters `on`/`in`.
  Reach for one of those, and let ffmpeg — not a remembered variable name — tell
  you whether it exists.
- `zoompan` does not duplicate frames on video input by nature — that is its default
  `d=90` (20 frames in, 1800 out). `d=1` is exactly 1:1.

So a *resizing* window is a `zoompan` job. This one is not, and `crop` is simpler.
Reaching for the other shape is `EdlEntry.look` — the next section.

## WHAT the pixels become: the `look` seam (muvid#66, `looks` build-order item 10)

`crop` says which rectangle. **`look` says what the pixels in it become** — a grade, a
LUT, a posterise, or an in-shot punch-in. It is one compiled ffmpeg filter chain carried
on the entry and spliced into the per-cut chain the assembler already builds.

muvid never authors that string. `muvid.footage.look` compiles it from
[`looks`](https://pypi.org/project/looks/), which owns the effect vocabulary, the
licence tier of every filter it emits, and the choice of which filter expresses a given
camera path. muvid keeps `-c:v`, the frame counts and the process shape.

```python
from muvid.footage import EdlEntry, punch_in, punch_in_cuts, stylize, chain

# The in-shot punch-in the design partner asked for: stays on the SAME shot.
EdlEntry(0.0, 3.8, "c01", look=punch_in(canvas=(1920, 1080), fps=30, duration_s=3.8))

# ~2N of them, evenly redistributed over an edit rather than appended.
entries = punch_in_cuts(entries, canvas=(1920, 1080), fps=30, every=2)

# A whole looks.Look — compiled against the binary muvid will actually run.
import looks

muted = looks.Look(steps=(looks.Effect(name="saturation", params={"amount": 0.35}),))
grade = stylize(muted, canvas=(1920, 1080), fps=30)

# One cut carries one look, so two effects are one chain.
EdlEntry(3.8, 7.6, "c01", look=chain(punch_in(...), grade))
```

Five things to know:

- **Absent means what it always meant.** No look emits no filter at all — verified by
  rendering the same look-less EDL before and after the field existed and comparing
  DECODED frames (identical framemd5, 200 frames, through both render sites).
- **The windows a `look` moves are fractions of the CANVAS, not of the source.** The
  fragment is spliced *after* `scale`/`pad`/`fps`, so it sees the delivery canvas at the
  delivery rate. That is what lets a punch-in be compiled with exact numbers instead of
  a per-clip probe, and what makes the same punch read the same over a portrait phone
  clip and a landscape one. `crop` is still the source-relative decision; they compose,
  crop first.
- **It lands identically on both sides of a blended boundary.** The assembler renders a
  transition as a two-input `xfade` whose sides are normalised separately, so a look
  reaching one side and not the other would be a visible seam. One template now serves
  both sites.
- **A `-vf` fragment adds no decoder**, which is why this is the seam. A look naming a
  container input (`[1:v]`) or carrying a graph separator is **refused by
  `validate_edl`**, because either would reintroduce the shape that was OOM-killed at
  30 cuts (muvid#21/#24).
- **The filters are an ALLOWLIST, because `assemble_music_video` is a live per-caller
  MCP tool and `look` arrives over that wire.** `validate_edl` accepts only
  `muvid.footage.edl.LOOK_FILTERS` — what `muvid.footage.look` and `looks` actually
  emit, plus `hue`. Everything else is refused **by name**, which is what closes the
  whole class rather than one filter: before the allowlist,
  `look="metadata=mode=print:file=<any writable path>"` passed the gate, rendered
  normally, returned a success payload and truncated that file to zero bytes (measured;
  `deshake=filename=` is a second, structurally different write primitive, and
  `movie=`/`amovie=` open an unaccounted container). The name is resolved the way
  ffmpeg resolves it — `'metadata'` and `\m\e\t\a\d\a\t\a` both reach the same
  filter — so this is not string matching. Compile the look; don't hand-write one.
- **A second SOURCE is not reachable from a look at all.** An earlier version of this
  section (and of `_validate_look`'s error text) said to use `movie=`. That advice does
  not work: `movie=` is a zero-input source, so the solo site's *simple* filtergraph is
  structurally invalid (`had 1 input(s) and 2 output(s)`, measured on ffmpeg 9.0.1) and
  the transition site opens a second container from inside the fragment. Compositing
  needs a splice site the assembler does not have.
- **One sharp edge, measured: a *moving* look restarts its ramp on a transitioned
  boundary** — the blend is a separate invocation whose clock starts at 0 again. A grade
  or a LUT is unaffected. muvid#73 has the numbers and the options.

## Where this plugs into the existing code (IMPLEMENTED v1 — muvid#13)

- `muvid/footage/scoring/` — the layer: `grid.py` (`ScoreTrack` + resample + robust-normalize
  + `ScoreTensor` + atomic/NaN-safe `.npz`+manifest persistence under `{project}/scores/`),
  `frames.py` (ONE decode pass → shared per-clip artifacts), `quality.py`, `motionbeat.py`,
  `segment.py`, `lipsync.py` (opt-in tier), `orchestrator.py` (`score_project`).
- `muvid/footage/select_score.py` — the **`weighted`** strategy: the beat-snapped semi-Markov
  Viterbi DP over the tensor. **The "strategy" is `WeightedSelectionConfig`** (weights,
  `lambda_switch`, `l_min_s`, `l_max_s`, `boundary_mode`) passed via a `SelectionContext`.
  Registered LAZILY in `muvid/footage/strategy.py` (numpy-free import). λ_switch is in
  "seconds of perfect-footage reward a cut must earn"; feasibility is CONTINUOUS containment
  (matches `validate_edl` exactly → valid EDL by construction); segments are different-clip
  (so `l_max_s` is a true max-shot-length).
- `muvid/footage/strategy.py` — `select_edl(strategy, aligns, dur, *, context=None)`; the
  built-in `best_confidence`/`longest_take`/`fewest_cuts` remain the alignment-only fallbacks
  (context is passed only to strategies that declare it — the `nw.jobs._call_dispatch` idiom).
- **Scoring is keyed on INPUTS ONLY** (`song_hash` + offsets + metrics + hop) — weights enter
  at ASSEMBLE, so one tensor serves every preset. MCP: `score_footage` (background `nw.jobs`),
  `footage_score_status` (bounded long-poll), `footage_scores` (editor reads), and
  `assemble_music_video(strategy='weighted', preset=…, weights=…, config=…)`.
- `muvid/footage/look.py` — the `looks` seam: `punch_in`, `motion`, `stylize`, `chain`,
  `punch_in_cuts`. Compiles only; the assembler splices. `muvid/footage/assemble.py`'s
  `_part_filter` is THE per-cut chain, shared by the solo and the transition sites.
- `mixing.audio.beat_grid` (mixing[beats]) supplies the master beat/onset grid.
