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

## Decided constraints (2026-08-03)

- **Commercial → MIT/BSD/Apache ONLY.** Use `syncnet-python` (MIT weights), `demucs`,
  `madmom`/`librosa`, OpenCV, MediaPipe, PySceneDetect, and **BRISQUE/NIQE** for any
  aesthetic term. **Do NOT** use the Wav2Lip expert-discriminator weights, `pyiqa`
  NIMA/MUSIQ/TOPIQ, or DOVER (all non-commercial). VocaLiST is out for now (research
  license); revisit only if relicensed.
- **Async, CPU-first.** Scoring is a background job after align (NOT the synchronous
  render path). Use **Farneback** optical flow (not RAFT) and `syncnet-python` on CPU so
  it runs on the current keyless box; keep a GPU fast-path optional behind a flag.

## Lip-sync (`lip_sync_lse_c` + `lse_d_offset` + `face_gate`)

Pipeline: face-detect → track → mouth-crop → SyncNet score vs **master vocal stem**.
- Separate the master's vocals once: **Demucs** (`pip install demucs`, MIT).
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

- **Audio beats/onsets (master, once):** `madmom` DBNBeatTracker (BSD-2; models academic)
  or `librosa.beat` (ISC) / `BeatNet` (MIT). Also the onset-strength envelope.
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
