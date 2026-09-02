---
name: muvid-score-footage
description: >
  Concrete recipes for computing per-clip score curves for muvid's `music_video` footage
  selection — the lip-sync score, the motion-to-beat / dance-match score, and the cheap
  CPU quality tier — each resolved onto the shared song-time grid. Use when IMPLEMENTING a
  footage score. Triggers on "compute lip-sync score", "SyncNet / LSE-C", "motion beat
  alignment / BAS", "optical flow motion envelope", "sharpness / blur / shake / exposure
  score", "face framing score", "beat grid on the master". The overall menu + selection +
  data model is `muvid-choose-footage-segments`.
---

# muvid-score-footage — per-metric scoring recipes

Every recipe emits a score track on the shared song-time grid (`hop≈0.1s`; values
higher=better; a coverage `mask`). Compute audio beats + the vocal stem **once on the
master**; map to each clip via its known offset.

## Decided constraints (v1 — implemented, see `misc/docs/footage_scoring_design.md` LOCKED)

- **Two tiers.** The **core tier** (`muvid[scoring]`, torch-free) = quality gates +
  motion-to-beat + the selector + editor tracks; this is what prod installs. The
  **lip-sync tier** (`muvid[scoring-lipsync]`: Demucs + `syncnet-python`) is **OPT-IN,
  OFF BY DEFAULT, off-prod** — the htdemucs weights are **CC-BY-NC (research-only, NOT
  commercial-clean)** and Demucs+SyncNet on CPU peak ~2-3 GB (OOM risk on the fragile box).
  Enable only on a local/worker box (`MUVID_SCORING_ENABLE_LIPSYNC=1`) with operator-provided
  licensed weights (`MUVID_SYNCNET_*_WEIGHTS`).
- **Commercial → MIT/BSD/Apache/ISC ONLY** for the core: OpenCV, MediaPipe, `librosa` (ISC),
  optional PySceneDetect (BSD-3, `muvid[scoring-shots]`). **`madmom` is DROPPED** (its beat
  models are academic-licensed) — librosa is the sole beat backend. **Do NOT** use Wav2Lip
  expert weights, `pyiqa` NIMA/MUSIQ/TOPIQ, DOVER, or VocaLiST (all non-commercial).
- **Async, CPU-first.** Scoring is a background job (`nw.jobs`) after align, NOT the render
  path — a process-wide concurrency=1 semaphore, `should_cancel` between clips. Farneback
  flow (not RAFT), heavy extractors out-of-process when enabled.

## Implemented module map (v1)

- `mixing.audio.beat_grid` (mixing[beats], librosa) — the master beat/onset grid, computed ONCE.
- `muvid/footage/scoring/`: `grid.py` (ScoreTrack + resample + robust-normalize + tensor +
  atomic/NaN-safe persistence), `frames.py` (ONE decode pass → shared per-clip artifacts),
  `quality.py`, `motionbeat.py`, `segment.py`, `lipsync.py` (opt-in), `orchestrator.py`
  (`score_project`). The selector is `muvid/footage/select_score.py` (`weighted` strategy).

## Lip-sync (`lip_sync_lse_c` + `lse_d_offset` + `face_gate`) — OPT-IN TIER (off-prod)

Implemented in `muvid/footage/scoring/lipsync.py`, gated: skips cleanly (returns `[]`) unless
`muvid[scoring-lipsync]` is installed AND `MUVID_SYNCNET_*_WEIGHTS` are set. ⚠ its glue is
untestable without the deps → needs a live validation pass before first real use.
Pipeline: face-detect → track → mouth-crop → SyncNet score vs **master vocal stem**.
- Separate the master's vocals once: **Demucs** (`pip install demucs`, MIT code — but the
  htdemucs **weights are CC-BY-NC**, so this tier is off-prod / local-worker only).
- Per clip: `syncnet-python` (`pip install syncnet-python`, **MIT**) runs S3FD face detect
  → track → mouth crop → per-window LSE-C (confidence, higher=better) + LSE-D (distance) +
  offset. Feed the co-temporal **master vocal** window (offset already known) so SyncNet
  becomes a *validation*, not an offset search.
- **Gate** with `TalkNet-ASD` (MIT) per-frame speaking prob + face coverage → set the
  score's `mask` to NA where no one is singing (never 0).
- Mouth ROI front-end: **MediaPipe Face Landmarker** (Apache-2.0) or `face-alignment`
  (BSD) for profile/low-light phone footage.
- **Singing caveat:** SyncNet/Wav2Lip are speech-trained; sustained vowels degrade them.
  **VocaLiST** (research license — verify) is the v2 accuracy upgrade for vocal spans.
- **Commercial note:** the Wav2Lip *expert-discriminator weights* are non-commercial; the
  2016 MIT `syncnet-python` weights are the commercial-clean choice.
- Refs: SyncNet [2], Wav2Lip/LSE-C/LSE-D [3], TalkNet [5], VocaLiST [4] (report §2).

## Motion-to-beat (`motion_beat_bas` + `motion_onset_xcorr`)

- **Audio beats/onsets (master, once):** `mixing.audio.beat_grid` (librosa, ISC) — beats +
  onset envelope in one call, computed ONCE on the master. (madmom is DROPPED — academic
  models; librosa is the sole backend.)
- **Motion envelope (per clip):** optical-flow magnitude (OpenCV Farneback CPU, or RAFT
  on GPU) or frame-difference energy → 1-D; when a person is tracked, **pose-keypoint**
  velocity/acceleration (MediaPipe) gives cleaner "body beats". Abe Davis's
  *directogram / visual impacts / visual beats* (`visbeat`) is the reference method [10].
  **Compensate camera motion** (reuse the `stability_shake` flow) so shake ≠ subject motion.
- **The score:** **Beat Alignment Score (BAS)** — mean over detected motion beats of the
  proximity to the nearest audio beat (AIST++ [11]) — when a person is present; plus
  **cross-correlation** of the motion envelope vs the onset envelope (strength + lead/lag;
  the argmax lag also refines per-clip A/V latency). A **phase-locking value** (numpy
  circular stats) captures groove even when syncopated. All MIT/BSD/ISC → commercial-clean.
- Refs: Visual Rhythm & Beat [10], BAS/AIST++ [11], madmom [8], BeatNet [9] (report §3).

### Measured, on real crowd footage: none of this detects a beat lock — and why that is not the last word

Run on three phone recordings of a dancing crowd, a camera-compensated whole-frame flow
envelope showed **no phase concentration at all** — |z| < 2 against a circular-shift null
at beat, 2-beat, bar and 2-bar periods, and spatially resolved into 48 cells, **0/48**
reached z > 3. One clip's elevated beat-frequency SNR was most plausibly disco lights
pulsing, not dancers. (Detail: thorwhalen/muvid#61.)

**Read that as a result about the METHOD, not about the footage.** Perceptually meaningful
timing lives at 1–20 ms; a frame period is 16.7–33 ms. Literal frame-to-onset mapping
therefore cannot resolve rhythmic placement, and a negative result from it only confirms
that premise. Scoring a clip on "is it lively here" works fine — that is what these
metrics are actually good for, and it is what the selector uses them for. Concluding
"the dancers are not on the beat" from them does not follow.

Recovering the timing needs **inference against a musical prior**, not measurement:
extract sparse high-confidence anchors (velocity zero-crossings, acceleration peaks — beat
*candidates with confidences*, never hard onsets) and filter them through a state space
over (phase-in-bar, tempo). The signal-theoretic licence is finite rate of innovation. See
`thoremin/docs/research/rhythm-from-gesture-research-map.md`; thorwhalen/thoremin#178
proposes extracting that engine as a shared package (`ictus`), with muvid as a consumer —
and muvid is the right place to *validate* it, because here the ground truth is known.

**Do not build an automatic time-warp driver on BAS or envelope cross-correlation.** They
are selection scores. The driver is the inference engine.

## Quality tier (cheap CPU; also HARD GATES) — all OpenCV, Apache-2.0

- `sharpness` = variance of the Laplacian (`cv2.Laplacian(gray).var()`), <1 ms/frame.
- `exposure` = luma-histogram stats (clipping at 0/255, contrast), sub-ms.
- `stability_shake` = LK optical-flow global-motion jitter energy (also the camera-motion
  estimate the motion-beat recipe needs).
- `face_framing` = MediaPipe BlazeFace presence/size/centering/frontality (200–1000 fps).
- Use these as **gates**: prune frames below thresholds to `mask`=NA *before* the weighted
  vote. Sample ~2–8 Hz, resample to the grid.
- **Segment boundaries:** **PySceneDetect** (BSD-3) once per clip → the aggregation unit.
- **Learned aesthetics (v2, licensing-gated):** NIMA/MUSIQ/TOPIQ (`pyiqa`) + DOVER are
  best-correlated but **non-commercial weights + GPU**; BRISQUE/NIQE (opencv-contrib) is
  the commercial fallback. Refs: BRISQUE [12], NIQE [13], DOVER [20] (report §4).

## Output contract (every recipe)

Return `{clip_id, metric, t0, hop_s, values[], mask[], raw_values[], direction:"higher_better",
norm:{median, iqr, p5, p95}}` — dense arrays on the song grid, robustly normalized across
clips (median/IQR, percentile-clipped). See `muvid-choose-footage-segments` for how the
fused tensor drives the Viterbi selector AND the multichannel editor lanes.
