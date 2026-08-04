# Footage scoring layer — implementation design (muvid#13, Phase 1 backend)

Companion to the research report (`footage_scoring_research.md`) and the two skills
(`muvid-choose-footage-segments`, `muvid-score-footage`). The report is the *why*; this
doc is the *what/where/how* that gets built. Scope is decided (see the issue); this design
resolves the open engineering questions and is the target of the adversarial design
critique before implementation.

---

## LOCKED DECISIONS (post adversarial critique, 2026-08-04)

A 7-lens adversarial critique + a focused algorithm review revised this design. The deltas
below **supersede** the original sections wherever they conflict. The prose sections are
kept for rationale; where a section contradicts a locked decision, the locked decision wins.

**Memory & licensing (the dominant findings).** The prod box is 3.7 GB, OOM-fragile. Demucs
+ SyncNet on CPU peak ~2–3 GB and would OOM the connector. AND the htdemucs weights are
**CC-BY-NC (research-only)** — not commercial-clean, contra the owner's MIT-only rule
(independently verified: demucs *code* is MIT, *weights* are not). **Owner decision: lip-sync
is an OPT-IN tier, OFF by default, NOT on the prod connector.**

1. **Two extras.** `muvid[scoring]` = the **torch-free core** (numpy, opencv-contrib-python,
   mediapipe, `mixing[beats]`→librosa, scenedetect) → quality + motion-beat + segment +
   the selector + editor tracks. This is what the prod connector installs. `muvid[scoring-lipsync]`
   = adds `demucs` + `syncnet-python` (torch) → the lip-sync tier, **local/worker only, off by
   default**, with a license warning. Use `opencv-contrib-python` (NOT `opencv-python`) to
   avoid the cv2 double-install with syncnet/mediapipe.
2. **Federation split (coherent rule):** push a primitive down only when it's reusable AND
   doesn't expand the target's dependency *class*. → `beat_grid` (librosa) → **mixing.audio**
   (`mixing[beats]`). `separate_vocals` (torch) and the CV kernels **stay in muvid** (torch is
   a new dep class for mixing; CV kernels have no 2nd consumer yet — promote later).
3. **Drop madmom entirely** for v1 (librosa/ISC is the sole beat backend — no academic models).
   TalkNet is GitHub-only/not-in-extras; the **face + mouth-motion gate is the tested primary**.
4. **Score-job is keyed on INPUTS ONLY** — `(song_hash, sorted metric set, hop_s)`. Weights/
   preset/λ/L_min/L_max are **NOT** in the score job; they enter only at **assemble** time via
   `SelectionContext`. → one tensor is reused across every preset for free; fixes the
   concurrent-job race and the "re-score to change weights" trap. `muvid_score_footage`
   extracts ALL available metrics; `assemble_music_video` does the (cheap, iterable) selection.
5. **Idempotency key** folds in `song_hash` + an alignment fingerprint, so a mid-flight
   re-align + resubmit yields a NEW job (not a dedup to the stale run).
6. **Persistence is crash-consistent + NaN-safe.** Write each `.npz` to a temp then
   `os.replace`; write `manifest.json` LAST via tmp+rename. `set_song`/`align_footage`
   `rmtree scores/` (primary invalidation). `song_hash` is computed ONCE at `set_song` and
   stored in the manifest — never re-hashed per read. **NaN never reaches a serializer**:
   the manifest stores `null` norm params for an all-masked metric; the MCP wire maps masked
   entries to `null` and relies on the `mask` array. Manifest is the SSOT for grid geometry
   (`t0/hop_s/n`); per-track geometry is a load-time assertion.
7. **Memory safety on prod:** heavy extractors run **out-of-process** (`au.ProcessBackend`
   with `resource.setrlimit(RLIMIT_AS)` so the OS kills the child, not the connector) — this
   also reclaims torch/cv2 RSS on worker exit; a **process-wide concurrency=1 semaphore**
   (`MUVID_SCORING_MAX_CONCURRENT`) serializes heavy work; heavy extractors default OFF on
   prod (`MUVID_SCORING_ENABLE_LIPSYNC=0`). `should_cancel` is checked between every clip and
   every extractor (cancel latency ≈ one step). Provision pre-warms nothing torch (core is
   torch-free); if the lipsync tier is ever enabled, weights are pre-warmed at provision into
   a persistent bind-mounted cache.
8. **Registry & dispatch:** `select_edl` gains a keyword-only `context=None` (a real signature
   change — it is 3-arg today). `weighted` is registered via a **lazy loader** so
   `list_strategies()` shows it WITHOUT importing numpy (the import-light test is extended to
   assert this). Dispatch reuses the exact `nw.jobs._call_dispatch` shape: try `inspect.signature`,
   on `(ValueError, TypeError)` fall back to the 2-arg call, pass `context` if the strategy
   declares `**kwargs` OR a `context` param. The magic param name `context` is documented in
   `SelectionStrategy`'s docstring + the skills.
9. **Async UX:** `muvid_footage_score_status(project_id, *, job_id=None, wait_s=0)` — a bounded
   long-poll (≤25 s, under the connector timeout) so an agent gets ~1 poll not ~15. Progress
   is reported as explicit `stage_index`/`stage_count` dict events (NOT a learned ETA, whose
   keys would be empty for a compute job); the `on_event` contract is `{'kind':'progress',
   'stage_index':i,'stage_count':n,'current_transform':name}` and is round-trip tested.
10. **The corrected DP** (§4c is superseded by "§4c′ — corrected recurrence" below).

---

## 0. The keystone (unchanged) and what this doc adds

Resolve every clip's every metric onto ONE fixed-rate song-time grid → a tensor
`S[clip, frame, metric]` that BOTH the auto composer (argmax via a beat-snapped
semi-Markov Viterbi DP) and the Phase-2 editor read. One set of curves, one objective.

This doc pins down: (1) the data model + persistence, (2) the federation split (what goes
to `mixing`, what stays in `muvid`), (3) each extractor's contract, (4) the exact selector
algorithm, (5) how the score-driven strategy fits the existing `SelectionStrategy`
registry, (6) the async job, (7) the MCP surface + resource caps, (8) the testing +
license gating.

---

## 1. Package layout & federation split

**Principle applied:** work *from* muvid (the app), push genuinely-reusable substance
*down*. The task statement names the federation as `muvid → nw + mixing (audio/video
editing incl. the audio-alignment primitives)`. So:

### 1a. Push DOWN to `mixing.audio` (reusable audio-analysis primitives)

Both are pure audio analysis, obviously reused beyond footage (braidio, foley, reelee
music features), and fit `mixing.audio`'s existing charter (it already owns
alignment + segmentation). Heavy deps behind **new mixing extras**, lazy-imported:

- `mixing.audio.beat_grid(audio, *, sample_rate=22050, backend="librosa") -> BeatGrid`
  — beats, downbeats (best-effort), and the onset-strength envelope on the song grid.
  `BeatGrid{beat_times[], downbeat_times[], onset_env[], onset_hop_s, sr, tempo_bpm}`.
  Default backend `librosa` (**ISC** — fully permissive). Optional `backend="madmom"`
  (BSD-2 code, but its DBN *models* are academic-license → **not** default, opt-in only).
  Extra: `mixing[beats] = ["librosa"]`.
- `mixing.audio.separate_vocals(audio, *, model="htdemucs", stems=("vocals",)) -> dict[str, np.ndarray] | dict[str, Path]`
  — Demucs source separation returning the vocal stem (MIT code + MIT model weights —
  verify in the license audit). Extra: `mixing[stems] = ["demucs"]`.

These become `mixing >= <next>`; muvid pins that floor. The reelee connector already
installs `mixing @ git+…main`, so the deploy path is unchanged.

### 1b. STAYS in muvid (footage-selection-specific)

Everything that only makes sense for "which take is on-air over this song span":
the grid/normalization data model, the four extractors' grid-resampling + gating logic,
the orchestrator, and the selector. Placed under `muvid/footage/scoring/`.

The **CV frame-metric kernels** (var-of-Laplacian sharpness, luma-exposure stats,
Farneback flow-magnitude) are written as small standalone functions in
`muvid/footage/scoring/_frame_metrics.py`. They are *promotion candidates* for
`mixing.video` the moment a second consumer appears — but kept in muvid for v1 to hold
the cross-repo surface to one clean audio PR. Noted in the module docstring.

```
muvid/footage/scoring/
  __init__.py        # orchestrator: project → tensor + persisted tracks (import-safe)
  grid.py            # ScoreTrack + resample-to-grid + robust-normalize + tensor assembly + persistence
  _frame_metrics.py  # pure OpenCV frame kernels (sharpness/exposure/flow) — promotion candidates
  quality.py         # sharpness/exposure/stability_shake/face_framing (+ gates → mask NA)
  motionbeat.py      # motion envelope → BAS + onset xcorr, over mixing.audio.beat_grid
  lipsync.py         # SyncNet LSE-C vs mixing.audio.separate_vocals stem, TalkNet/face gate
  segment.py         # PySceneDetect boundaries + coverage mask
muvid/footage/select_score.py   # the semi-Markov beat-snapped Viterbi selector + config + registration
```

`import muvid`, `import muvid.footage`, and `import muvid.genre_music_video` MUST stay
light (test `test_import_genre_is_light` already asserts no cv2/numpy leak). Every heavy
import lives inside function bodies. `muvid/footage/scoring/__init__.py` imports nothing
heavy at module load — it exposes the orchestrator whose body lazy-imports the extractors.

---

## 2. Data model — `ScoreTrack` and the tensor (grid.py)

```python
@dataclass(frozen=True)
class ScoreTrack:
    clip_id: str
    metric: str                 # e.g. "sharpness", "lip_sync_lse_c", "motion_beat_bas"
    t0: float                   # song time of frame 0 (always 0.0 in v1)
    hop_s: float                # grid step (default 0.1 → 10 Hz)
    values: np.ndarray          # float32[n], robustly-normalized, higher=better, NaN where masked
    mask: np.ndarray            # bool[n], True = valid/covered, False = NA (gap or gated out)
    raw_values: np.ndarray      # float32[n], pre-normalization (tooltips/debug), NaN where masked
    direction: str              # "higher_better" (post-normalization always higher_better)
    norm: dict                  # {"median","iqr","p5","p95"} the transform used (editor needs this)
    def to_meta(self) -> dict   # everything except the arrays (for the JSON manifest)
```

**Grid contract.** Frame `k` ↔ song time `t0 + k*hop_s`, IDENTICAL across clips, so tracks
stack into `S[clip, frame, metric]` with a companion `M[clip, frame]` mask (True where
ALL required-for-composite metrics are valid — see the selector). `n = ceil(song_duration/hop_s)`.

**`resample_to_grid(sample_times, sample_values, *, t0, hop_s, n, agg="mean") -> (values, mask)`**
— each extractor produces irregular `(song_time, value)` samples (a clip's frames mapped
to song time via its offset); this bins them onto the grid. Frames outside a clip's
coverage → `mask=False`. Uses last-value-hold / nearest within a tolerance, mean-aggregates
multiple samples per bin.

**Robust normalization (`robust_normalize`).** Per-metric-**global** across all clips &
valid frames: `z = clip((x - median)/IQR, at p5..p95)` then min-max to [0,1]. Masked frames
excluded from stat estimation. Distance-type metrics (LSE-D) are inverted first so higher
is always better. `norm` params stored so the editor can render raw + normalized. IQR==0
(constant metric) → all-valid frames map to 0.5 (neutral), never divide-by-zero.

**Persistence (muvid-local; the lacing `clip-score-track/v1` is the reelee-web concern).**
Under `{project.root}/scores/`:
- `scores/{clip_id}.npz` — the arrays for all metrics of one clip (compressed).
- `scores/manifest.json` — `{song_hash, hop_s, t0, n, clips:[...], metrics:[...],
  beats:{beat_times, downbeat_times}, generated_at, extractors_run, versions}`.
- The `song_hash` (sha256 of the song file) invalidates scores when the song changes
  (mirrors how `set_song` drops `alignments.json`). `align_footage`/re-align also
  invalidates (offsets moved → every clip's grid mapping is stale).

Arrays as `.npz`, NOT inlined JSON: a 12-min song at 10 Hz = 7200 frames; 8 clips × 6
metrics × 7200 × float32 ≈ 1.4 MB compressed vs ~10 MB+ as JSON. The MCP
`muvid_footage_scores` tool serializes bounded, rounded, optionally-decimated arrays.

---

## 3. Extractor contract

Every extractor is `extract(project, *, config, progress_cb=None, should_cancel=None) ->
list[ScoreTrack]` (or a per-clip variant the orchestrator maps). Rules:

- **Compute-once-on-master** (beats, onset env, vocal stem) is the orchestrator's job; it
  passes the shared artifacts into each extractor. Extractors never recompute them.
- **Sample sparsely, resample to grid.** Quality/face ~4–8 Hz; motion per-frame-pair
  decimated; lip-sync per SyncNet window (~5 Hz). Then `resample_to_grid`.
- **Gate → mask NA, never 0.** Sub-threshold quality frames and "no singing face" spans
  are `mask=False`. A 0 would bias selection.
- **Hard resource caps** (env-tunable, mirror the footage tools): max frames sampled per
  clip, per-extractor wall-clock timeout, max clip duration already enforced upstream.
- **Import-safe & degrade gracefully.** Missing optional dep (syncnet/scenedetect/demucs)
  → the extractor is skipped with a recorded reason, not a crash; the orchestrator runs
  the extractors whose deps are present.

### 3a. quality.py (cv2 + mediapipe; the cheap CPU tier + gates)
- `sharpness` = var-of-Laplacian (grayscale). `exposure` = luma-histogram score (penalize
  clipping at 0/255 + low contrast). `stability_shake` = LK global-motion jitter energy
  (also EXPORTS the per-frame camera-motion estimate motionbeat consumes → computed here
  once, shared). `face_framing` = MediaPipe BlazeFace presence×size×centering×frontality.
- Gates: frames below sharpness/exposure thresholds → `mask=False` for a `quality_ok`
  meta-mask the orchestrator ANDs into `M`. face_framing sub-threshold does NOT gate the
  frame globally (a valid instrumental shot has no face) — it only lowers that metric.

### 3b. motionbeat.py (cv2 Farneback + mixing.audio.beat_grid)
- Motion envelope: Farneback flow magnitude per decimated frame pair, camera-compensated
  by subtracting the `stability_shake` global-motion estimate; when a person is tracked,
  add MediaPipe pose-keypoint velocity as a cleaner "body beat". → a 1-D envelope on the
  clip's own frames → mapped to song time.
- `motion_beat_bas` = Beat Alignment Score (AIST++): mean over detected motion beats of
  `exp(-Δt²/2σ²)` to the nearest master audio beat. NA where no motion beats detected.
- `motion_onset_xcorr` = normalized cross-correlation of the motion envelope vs the master
  onset envelope (strength; the argmax lag also refines per-clip A/V latency, logged).
  Content-agnostic (covers no-person clips).

### 3c. lipsync.py (syncnet-python + mixing.audio.separate_vocals; all gated)
- Orchestrator separates the master vocal stem ONCE (`separate_vocals`). Per clip:
  face-detect→track→mouth-crop (syncnet-python's S3FD pipeline), score each 0.2 s window's
  LSE-C against the co-temporal MASTER VOCAL window (offset already known → validation, not
  search). Emit `lip_sync_lse_c` + `lse_d_offset` (the free offset trace).
- **Gate** (`mask=NA`, never 0): TalkNet active-speaker prob × face-coverage below
  threshold ⇒ no one is singing here ⇒ NA. TalkNet is optional; if absent, gate on
  face-presence + mouth-region motion variance (a weaker but permissive fallback).
- Singing caveat (SyncNet is speech-trained) is documented; VocaLiST is a v2 upgrade.

### 3d. segment.py (PySceneDetect; the aggregation unit + coverage)
- `coverage_mask`: from the offset + clip duration, the boolean of song frames this clip
  actually covers (this IS the base `mask` every other metric ANDs with).
- Shot boundaries: PySceneDetect ContentDetector per clip → boundary song-times, used only
  when the selector's `boundary_mode="beats+shots"`. Optional dep; absent → shots empty,
  boundary_mode silently falls back to beats-only.

---

## 4. The selector (select_score.py) — beat-snapped semi-Markov Viterbi

### 4a. Config object (the "strategy")

```python
@dataclass(frozen=True)
class WeightedSelectionConfig:
    weights: dict[str, float]      # per-metric weight; missing metric ⇒ weight 0
    lambda_switch: float = 0.35    # Potts penalty per clip switch (in composite units)
    l_min_s: float = 1.2           # min shot length (relaxed on the terminal segment)
    l_max_s: float = 8.0           # max shot length (a dwell cap)
    boundary_mode: str = "beats"   # "beats" | "beats+shots"
    beat_unit: str = "beat"        # "beat" | "downbeat" (which grid the DP may switch on)

PRESETS = {
  "energetic":     WeightedSelectionConfig(weights={...}, lambda_switch=0.2, l_min_s=0.8, l_max_s=4.0),
  "contemplative": WeightedSelectionConfig(weights={...}, lambda_switch=0.6, l_min_s=3.0, l_max_s=12.0),
}
DEFAULT_WEIGHTS = {"lip_sync_lse_c":1.0,"motion_beat_bas":0.8,"motion_onset_xcorr":0.5,
                   "sharpness":0.4,"exposure":0.3,"face_framing":0.4,"stability_shake":0.3}
```

### 4b. Fitting into the existing registry (open-closed, no compat shims)

The built-ins are `f(alignments, song_duration) -> [EdlEntry]`. The weighted selector needs
more (the tensor, beats, config). We **widen by progressive disclosure**, the established
federation idiom (`nw.jobs._call_dispatch`): `select_edl` builds a `SelectionContext` and
passes `context=` ONLY to strategies whose signature declares it. Old 2-arg built-ins are
untouched.

```python
@dataclass(frozen=True)
class SelectionContext:
    alignments: Sequence[FootageAlignment]
    song_duration: float
    tensor: ScoreTensor | None      # S[clip,frame,metric] + M + clip_ids + metrics + t0 + hop
    beats: BeatSet | None           # beat_times, downbeat_times (song time)
    config: WeightedSelectionConfig

# select_edl(strategy, alignments, song_duration, *, context=None):
#   fn = resolve_strategy(strategy)
#   pass context only if `context` in signature(fn).parameters  (else call the 2-arg form)
```

`weighted_selection(alignments, song_duration, *, context)` is registered under
`"weighted"`. If `context.tensor is None` (no scores yet) it raises a clear error the MCP
layer turns into "run muvid_score_footage first" (or the caller falls back to
`best_confidence`).

### 4c. The DP (the algorithmic heart — the critique's focus)

**Boundaries.** `B` = sorted candidate cut times within the covered timeline:
`{cover_start, cover_end} ∪ (beats ∩ [cover_start,cover_end])` for `boundary_mode="beats"`;
`∪ shot_boundaries` for `"beats+shots"`. `beat_unit="downbeat"` uses downbeats only.
Deduplicate within `hop_s/2`. The DP decides which clip is on-air over each `[B_i, B_{i+1})`.

**Node reward.** For clip `c` over interval `[B_i, B_j)` (a candidate *segment*):
`R(c,i,j) = Σ_{k in frames(i..j)} ( Σ_m w_m · S[c,k,m] ) · M[c,k]` — the integral of the
weighted composite over valid frames. `R = -inf` if clip `c` does not fully cover
`[B_i,B_j)` (containment: every frame in the span has `coverage_mask[c,k]` True and the
derived `clip_in` is within the clip) — so the emitted EDL passes `validate_edl` by
construction.

**Semi-Markov recurrence over segments.** State = "a segment on clip `c` ends exactly at
boundary `B_j`". `best[j][c]` = max total reward of a valid cover of `[B_0, B_j)` whose last
segment is on `c`.
```
best[j][c] = max over i<j, over c' (previous clip) of:
    best[i][c'] + R(c,i,j) - (lambda_switch * switch_scale if c' != c else 0)
subject to:  L_min ≤ (B_j - B_i) ≤ L_max     (dwell / shot-length)
             clip c fully covers [B_i, B_j)
base case:   best[i0][c] = R(c, 0, i) for the first segment (no switch cost, L_min relaxed)
```
`switch_scale` normalizes λ to composite units (λ is "penalty per switch" in the same
scale as one frame's max composite × a reference shot length, so it's tempo-agnostic; the
exact scaling is documented and unit-tested). The inner `i` loop is bounded by
`B_j - B_i ≤ L_max`, so the DP is `O(|B| · (L_max/min_beat_gap) · K²)` — milliseconds for
`|B|≤~1500, K≤8`.

**Feasibility & terminal relaxation.** L_min is relaxed for the FIRST and LAST segment
(the song rarely starts/ends on a full-length shot). If no feasible full cover exists
(e.g. an interior span no single clip covers for ≥ L_min, or a coverage gap), the DP marks
those boundaries infeasible; the resulting EDL will have a gap → `validate_edl` raises the
exact-uncovered-span error, IDENTICAL UX to the built-ins (gap-fill is muvid#10). A final
guard: if the whole DP is infeasible, `weighted_selection` falls back to `best_confidence`
and records `fallback="best_confidence"` in the result meta (never silently returns junk).

**Outputs.** (1) the EDL (coalesced, gapless-within-cover, passes `validate_edl`); (2) a
`selection_margin` track = per-frame `(best_composite − 2nd_best_composite)` → the editor's
"where a human should decide" lane; (3) the resolved config + any fallback, in meta.

**Determinism.** Ties broken by (higher confidence, lower clip index) so the path is
reproducible (needed for tests and for the editor's re-solve to be stable).

---

## 5. Async job (nw.jobs) + MCP surface

### 5a. The job — reuse nw.jobs, do NOT build a second async system
Scoring is CPU-bound and minutes-long → a background job with durable state, progress,
cancel. `nw.jobs` already provides exactly that (ThreadBackend, durable index, cancel,
ETA) and only needs `project.root` — which `MusicVideoFootageProject` has. No fal cost, so
the cost/estimate machinery is inert (`estimated_usd=0`). The dispatch callable is the
orchestrator, adapted:
```python
nw.jobs.enqueue(proj, kind="footage.score",
    params={"metrics": [...], "config": {...}, "estimated_usd": 0.0},
    dispatch={"footage.score": _run_scoring},   # (project, params, *, job_id, on_event, should_cancel)
    label="Score footage")
```
`_run_scoring` calls the orchestrator with `progress_cb` mapped to `on_event`
(stage_index/stage_count/current_transform → the tray) and `should_cancel` threaded in so a
cancel stops at the next clip/extractor boundary. Idempotency key defaults to
`sha256(root:footage.score:params)` → a re-submit while running dedups (no duplicate job).

Note: this writes to `{muvid_project.root}/.nw/jobs`, a DIFFERENT store than reelee's
Project jobs, so the reelee task tray won't surface it in v1 — muvid exposes its own
poll tool (`footage_score_status`). Cross-surfacing into reelee's tray is a follow-up.

### 5b. MCP tools (muvid/mcp/scoring_tools.py; registered in muvid.mcp, all FREE)
- `muvid_score_footage(project_id, *, preset="", weights=None, config=None)` → enqueue the
  job, return `{job_id, status}`. Requires song + alignments (clear ToolError otherwise).
- `muvid_footage_score_status(project_id)` → the job's `{status, pct, stage, error}` via
  `nw.jobs.get_job` (newest footage.score job).
- `muvid_footage_scores(project_id, *, clip_id=None, metrics=None, max_points=1500)` →
  the persisted tracks as bounded JSON (rounded to 3 dp, decimated to ≤ max_points/clip),
  plus beats + selection_margin + the current auto EDL. For the editor + inspection.
- `assemble_music_video(... strategy="weighted", preset="", weights=None, config=None)` —
  the weighted strategy loads the persisted tensor + beats into a `SelectionContext`.
  Absent scores → ToolError "run muvid_score_footage first" (or, if `strategy` unset,
  the existing default `best_confidence` still works with zero scores).

Resource caps (env-tunable, new): `MUVID_SCORING_MAX_FRAMES_PER_CLIP`,
`MUVID_SCORING_PER_EXTRACTOR_TIMEOUT_S`, `MUVID_SCORING_MAX_POINTS_WIRE`,
`MUVID_SCORING_HOP_S`. Existing clip-count/duration caps already bound the input.

### 5c. pyproject + provision
```toml
[project.optional-dependencies]
scoring = [
  "numpy", "opencv-python", "mediapipe",
  "mixing[beats,stems]>=<next>",     # librosa (ISC) + demucs (MIT) via mixing
  "scenedetect",                     # BSD-3
  "syncnet-python",                  # MIT (+ its 2016 MIT weights)
  # TalkNet optional / installed separately; lipsync degrades gracefully without it
]
```
`nw.jobs` is already covered by `muvid[mcp]` (nw dep). The scoring tools import
`nw.jobs` only inside the tool body (mcp context). Provision: append
`muvid[mcp,scoring]` (was `muvid[mcp]`) in `provision-reelee-connector.sh`, and add a
deploy-time assertion that the scoring extractors import (or degrade) cleanly.

---

## 6. Licenses (commercial → MIT/BSD/Apache/ISC only)

| Dep | Use | License | Verdict |
|---|---|---|---|
| librosa | beats/onset (mixing) | ISC | ✅ |
| madmom | optional beats | BSD-2 code, **academic models** | ⚠ opt-in only, models NOT default |
| demucs | vocal stem (mixing) | MIT code + weights | ✅ (verify weights in audit) |
| opencv-python | quality/motion | Apache-2.0 | ✅ |
| mediapipe | face/pose | Apache-2.0 | ✅ |
| scenedetect | shot boundaries | BSD-3 | ✅ |
| syncnet-python | lip-sync | MIT (+2016 MIT weights) | ✅ |
| TalkNet-ASD | speaking gate (optional) | MIT | ✅ (verify weights) |
| **Excluded** | Wav2Lip expert wts, pyiqa NIMA/MUSIQ/TOPIQ, DOVER, VocaLiST | non-commercial | ❌ v1 |

Verify the actual LICENSE file (not the metadata field) for demucs weights + TalkNet in
the license-audit part of the review (per the "verify the LICENSE file" rule).

---

## 7. Testing

**Pure (always run, no extra):**
- grid: `resample_to_grid` (binning, coverage→mask, hold/nearest), `robust_normalize`
  (median/IQR/clip/min-max, IQR==0 neutral, distance inversion, masked frames excluded).
- selector: the Viterbi on SYNTHETIC tensors — asserts (a) argmax path picks the
  higher-composite clip per span, (b) switches land ONLY on beat boundaries, (c) L_min/L_max
  respected, (d) λ_switch reduces cut count monotonically, (e) gapless EDL passes
  `validate_edl`, (f) `boundary_mode="beats+shots"` admits within-clip cuts, (g) infeasible
  → clean `best_confidence` fallback, (h) `selection_margin` computed, (i) determinism.
- SelectionContext dispatch: a 2-arg built-in still works; the weighted strategy receives
  context; `select_edl` passes context only when declared.
- gate→NA: a masked frame never contributes to composite / never wins.

**Gated (skip cleanly without the extra / model / ffmpeg):**
- `motionbeat` end-to-end on a tiny synthetic clip using librosa beats (librosa is
  present locally → this one runs) — asserts a BAS/xcorr track shape on the grid.
- `quality` on a tiny synthetic clip (cv2 + mediapipe present) — track shapes + a gate.
- `lipsync`/`segment`/`separate_vocals` — `importorskip` syncnet_python/scenedetect/demucs.
- orchestrator import-safety: a clean subprocess imports `muvid.footage.scoring` and asserts
  no cv2/mediapipe/librosa leaked into `sys.modules` at import.

Follow `tests/test_footage.py` conventions (`pytest.importorskip`, `needs_pipeline`-style
skip guards, synthetic-media builders).

---

## §4c′ — Corrected DP recurrence (supersedes §4c; from the algorithm review)

**Inputs.** Alignments `a_c` (`offset_c`, `duration_c`, `confidence_c`); tensor `S[c,k,m]`,
composite mask `M[c,k]`; weights `w_m`, `W = Σ w_m > 0`; `hop_s`; `L_min`, `L_max`,
`λ_switch`; `_EPS = 1e-3` (identical to edl.py).

**Boundaries.** `cover_start = min_c coverage_c[0]`, `cover_end = max_c coverage_c[1]`.
`B = sort(dedup_{<hop_s/2}({cover_start,cover_end} ∪ (beats ∩ [cover_start,cover_end]) [∪ shots if beats+shots] [∪ pin_edges]))`,
indexed `b_0=cover_start … b_m=cover_end`. `B` is **injectable** (pin edges appended + re-dedup)
so the Phase-2 manual-pin re-solve is a boundary augmentation, not a reformulation.

**Reward — time-integral, normalized, prefix-summed (O(1) per segment).**
`ĝ(c,k) = (Σ_m w_m·S[c,k,m])·M[c,k] / W ∈ [0,1]`. Precompute `P[c][k] = Σ_{k'<k} ĝ(c,k')`.
`κ(b)` = first grid index `≥ b/hop_s` (frames half-open `[b_i,b_j)`).
`R(c,i,j) = hop_s·(P[c][κ(b_j)] − P[c][κ(b_i)])` — units: **seconds of perfect-footage-equivalent**,
hop- and tempo-independent. A switch then costs `λ_switch` in those same seconds
(`switch_scale ≡ 1`); `λ_switch=0.35` = "a cut must earn 0.35 s of perfect-footage reward."

**Feasibility — CONTINUOUS containment (the authority, matches validate_edl exactly).**
`CONTAINS(c,i,j) ≡ b_i ≥ offset_c − _EPS  AND  b_j ≤ offset_c + duration_c + _EPS`  (uses
`offset_c`/`duration_c`, NOT the clamped `coverage`, so a sub-hop overhang can't slip past a
grid mask). `M[c,k]` is used for reward weighting ONLY, never as the containment oracle.
`L_min_eff(i,j) = 0 if (i==0 or j==m) else L_min` (first/last relaxed).
`allowed(i,j) ⊆ clips` — a per-segment clip domain (all clips by default; `{c*}` inside a pin;
the Phase-2 hook). `FEAS(c,i,j) ≡ CONTAINS(c,i,j) AND c ∈ allowed(i,j) AND L_min_eff ≤ (b_j−b_i) ≤ L_max AND (b_j−b_i) > _EPS`.

**State / recurrence.** `best[j][c]` = max reward covering `[b_0,b_j)` with last segment on `c`
ending at `b_j`; `back[j][c]=(i,c')`. Init `−∞`. Base (i=0): `if FEAS(c,0,j): best[j][c]=R(c,0,j); back=(0,None)`.
Keep `maxAll[i]=max_{c'} best[i][c']` (reduces the inner clip loop from K² to K):
```
for j in 1..m:
  for i in (0<i<j with b_j−b_i ≤ L_max):     # break when b_j−b_i > L_max
    for c in clips with FEAS(c,i,j):
      prev = max(best[i][c],  maxAll[i] − λ_switch)   # stay (no penalty) vs best-other minus one cut
      cand = prev + R(c,i,j)
      update best[j][c] on STRICT improvement under key (round(cand,9), −#switches, c_index, i)
  maxAll[j] = max_c best[j][c]
```

**Output.** `c* = argmax_c best[m][c]`.
- `best[m][c*] = −∞` → **classify, don't silently fall back**: if some `[b_p,b_{p+1})` has no
  clip with `CONTAINS` → `coverage_gap` (add footage; best_confidence fails identically). Else
  `dwell_infeasible` → re-solve with `L_min→0` first; only then `best_confidence` fallback,
  recording the cause in meta.
- Else reconstruct segments via `back`, emit one `EdlEntry(b_i,b_j,c)` per segment, **coalesce**
  adjacent same-clip, assert `≤ MAX_EDL_ENTRIES` (else re-solve with a larger effective
  `λ_switch` — binary-search — or raise the specific cap error), then `validate_edl` (now a
  genuine tautology since FEAS is the continuous check).

**L_max is SOFT (revised post find→verify).** The original "hard L_max + coalesce same-clip"
model forced a cutaway to *inferior* footage whenever a lone/best clip's shot would exceed
L_max but an alternate covered the span — a materially worse auto-edit tagged "optimal". The
implemented model instead: (a) **forbids same-clip adjacency** (each segment is one shot, so
every cut pays λ and L_max is a real per-shot length), AND (b) makes L_max a **soft penalty** —
`seg_reward(c,i,j) = R(c,i,j) − l_max_overrun_penalty · max(0, (b_j−b_i) − L_max)` — instead of
a hard feasibility cap. So a lone/best clip rolls past L_max (paying a mild overrun) when the
only alternative is much worse (no forced cutaway), yet comparable clips still cut around L_max
for variety. The base case (i=0) is uncapped so a single clip can cover the whole span; the
transition window is bounded at `max(4·L_max, L_max+8)s` for performance. Infeasibility can now
only come from L_min (a forced sub-L_min mid-song segment) → the retry drops L_min only.

**`selection_margin`** — a separate O(n·K) per-frame pass (a LOCAL diagnostic, not the DP's path
margin): `margin[k] = ĝ_sorted[-1] − ĝ_sorted[-2]` over clips with `M[c,k]`, else `NaN` (<2 valid).

**`boundary_mode="beats+shots"`** adds **inter-clip** cut candidates at shot-boundary times (a
true *within-single-clip* jump cut is unrepresentable in the single-`offset_s` EDL model → a
deferred data-model change, not this flag). Rename accordingly in code + skill.

**Determinism:** fixed frame-summation order + `round(cand,9)` + the strict lexicographic key →
bit-reproducible auto path AND editor re-solve.

*(The three "open decisions" that were here are resolved in the LOCKED DECISIONS section above:
federation = beat_grid→mixing only; madmom = dropped; TalkNet = optional, face+mouth-motion primary.)*
```
