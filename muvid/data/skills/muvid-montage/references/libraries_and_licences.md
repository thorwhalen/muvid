# 02 — Libraries and open source for music-aligned montage

Scope: what open-source (and a few commercial) components exist for turning *a pool of personal clips and photos + one song* into a montage whose cuts and visual events land on the music. For each: licence, maturity, install weight, CPU suitability for a 3-minute song and ~30 clips, and a verdict — **USE-NOW** (v1), **LATER**, or **AVOID**. Research date 2026-10-04.

Our baseline, checked in the local tree before searching: `mixing.audio.beat_grid` (`t/mixing/mixing/audio/beats.py`) wraps librosa only and refuses `backend="madmom"` on licence grounds; `muvid.montage.analysis` (`t/muvid/muvid/montage/analysis.py`, on muvid `main`) already measures a beat grid, votes downbeats as the beat phase with the most onset energy, derives energy-based sections, and scores photo "strength" from pixel count and luma contrast; `muvid.footage.align` + `muvid.footage.edl.vouches_for` already gate song-in-clip alignment on `support > 0.5 and margin > 0` over `mixing.audio.align_clips_to_reference`. Much of what a survey would recommend is therefore already built; the gaps are a per-clip **visual** envelope and a clip-level **"does not contain the song"** verdict with a calibrated threshold.

## 1. Beat, downbeat, tempo, structure, mood

**librosa** — ISC licence, v0.11.0 released 2025-03-11 [1]. Pure Python + numpy/scipy/numba, no torch. `beat.beat_track` is a three-stage dynamic-programming tracker (onset strength → tempo from onset autocorrelation → peak picking consistent with tempo), and `beat.plp` gives a Predominant Local Pulse curve that tracks locally varying tempo [1][2]. No downbeat tracker. On CPU, a 3-minute song's onset envelope, beat track and beat-synchronous chroma take seconds. It is already the house backend. **Verdict: USE-NOW.** Its weaknesses (octave errors, no downbeats, weak on rubato) are bounded for montage material, where a fixed-tempo grid is honest.

**madmom** — source BSD, but *model and data files are CC BY-NC-SA 4.0*, with commercial use requiring a licence from the JKU group [3]. Last release 0.16.1 on 2018-11-14 [4]; it uses `np.float` and other attributes removed from modern NumPy, so it fails to import on current stacks without patches [4]. Its RNN+DBN downbeat tracker was the reference for years. **Verdict: AVOID** — non-commercial weights, unmaintained, and `mixing` already excludes it deliberately. Note that anything that depends on madmom inherits both problems (see BeatNet, allin1).

**Beat This!** (CPJKU, ISMIR 2024) — MIT for both code and published weights [5][6]. A transformer that predicts beats and downbeats as frame-wise logits *without* DBN post-processing; `pip install beat-this`, Python API `File2Beats(checkpoint_path="final0", device=...)` returning `(beats, downbeats)` [5]. Needs torch ≥ 2.0, torchaudio, einops, rotary-embedding-torch, soxr; falls back to CPU automatically; a small checkpoint (~8 MB) exists beside the ~78 MB main one [5]. ~400 stars, active. A community ONNX export runs on WebGPU [7], which matters for a browser surface later. We did not find a published CPU timing; a 3-minute song through the small model should be well under a minute on a laptop CPU but **must be measured** before any claim. **Verdict: LATER** — the strongest permissively-licensed downbeat tracker available and the right second backend behind `beat_grid(backend=...)`, but it adds torch to a path that today has none, and our closed-form downbeat vote is adequate for 4/4 pop. Adopt it as an optional extra (`mixing[beats-nn]`) the day a user's song has a wrong bar phase.

**BeatNet** — CRNN + particle filtering for online/offline joint beat, downbeat, tempo and meter tracking (ISMIR 2021); CC BY 4.0 [8]. Installation requires librosa **and madmom** (plus pyaudio for streaming) [9]. **Verdict: AVOID** — its madmom dependency imports the staleness problem, and Beat This! supersedes it offline. Its real-time mode is irrelevant to an offline render.

**Essentia** (MTG/UPF) — the library is **AGPL-3.0** [10]; the TensorFlow models (danceability, arousal/valence, mood, genre) are **CC BY-NC-ND 4.0** [11]. `pip install essentia-tensorflow` has Linux and macOS wheels [12]. The arousal/valence and danceability regressors are exactly the "energy and positiveness" descriptors a montage planner would like for matching mood [11]. **Verdict: AVOID** for anything shipped (AGPL on a hosted reelee/MCP surface triggers source-disclosure; the models forbid commercial use and derivatives). Acceptable for offline research only. Cheap substitutes: RMS energy, spectral centroid/brightness, onset density and tempo from librosa — crude but licence-clean proxies for arousal.

**aubio** — GPL-3.0; onset, pitch, tempo and beat tracking in C with numpy-view Python bindings [13]. Fast, but GPL and it adds nothing librosa lacks for offline use. **Verdict: AVOID** (licence; redundant).

**allin1 (All-In-One Music Structure Analyzer)** — MIT, ~850 stars; predicts tempo, beats, downbeats, functional segment boundaries and **labels** (intro/verse/chorus/bridge/outro) [14]. Requires PyTorch, NATTEN, **madmom** and **Demucs** (it separates sources first); the authors' benchmark is 10 songs / 33 minutes in 73 s on an RTX 4090 — CPU is supported but "GPU acceleration is recommended" [14]. Its labelled sections are the one output nothing else in this list produces. **Verdict: LATER (as an optional, paid-compute path)** — the dependency chain (torch + NATTEN + madmom + Demucs) is heavy and drags madmom's NC-licensed weights back in; check allin1's own weights licence before any use. A hosted inference (Replicate/HF Space) is the realistic way to consume it, behind the same `sections` seam our energy-based sections already fill.

**MSAF** — MIT; a framework of classical structural-segmentation algorithms (features, boundary and labelling algorithms, evaluation) [15]. Last release 0.1.80 on 2023-06-25 [15]. **Verdict: LATER/AVOID** — useful as a reference implementation list, but librosa's own recurrence-matrix and agglomerative segmentation cover the same ground with one fewer dependency.

**Demucs** — MIT; Hybrid Transformer v4 source separation. The `facebookresearch/demucs` repo was **archived on 2025-01-01**; maintenance moved to the author's fork `adefossez/demucs` [16]. Heavy (torch, large checkpoints, slow on CPU). **Verdict: AVOID unless needed** — the only montage reason to separate is drum-only onset detection on dense mixes, and percussive/harmonic separation (`librosa.effects.hpss`) is a free, CPU-cheap approximation of the same idea.

## 2. "Does this clip contain the song?" — sync and fingerprinting

**scipy / numpy cross-correlation** — what `mixing.audio.align_clips_to_reference` already does, with a consensus over windows that yields `confidence`, `support` and `margin`. The muvid calibration notes record that the raw correlation coefficient **does not separate** correct alignments from noise (worst correct 0.129 vs worst noise 0.139) and that bar-periodic music produces *alias* offsets that a coefficient cannot see (`t/muvid/muvid/footage/edl.py`). **Verdict: USE-NOW** (already in the stack), but as a *localiser* of an offset, not as the *detector* of presence.

**audalign** — Python; aligns recordings by fingerprinting first, falling back to cross-correlation, spectrogram correlation or visual alignment, then `fine_align` [17]. v1.3.1 released 2025-03-10 [17]. Results are returned as dicts with per-file match information. **Verdict: LATER** — it solves our *footage* problem (N devices, one event) more than the montage one, and we already own a calibrated aligner. Worth a read for its fingerprint parameters.

**syncstart** — computes an audio/video offset by FFT correlation over ffmpeg-extracted clips with optional z-score normalisation [18]. A small script, no confidence model. **Verdict: AVOID** (redundant with `mixing`).

**BBC audio-offset-finder** — cross-correlation of standardised MFCCs, "accuracy typically within about 0.01 s" and more robust to noise than raw-waveform correlation [19]; it reports a score with the offset. **Verdict: LATER** — the MFCC-standardisation idea is worth stealing for `mixing` if waveform correlation proves brittle on phone audio; not worth the dependency.

**Chromaprint / pyacoustid** — Chromaprint's own source is MIT but it bundles FFmpeg parts, so "as a whole" it is LGPL-2.1; the `fpcalc` binary is GPLv2+ [20]. It is designed to identify whole tracks against the AcoustID database, not to find a short excerpt at an unknown offset inside a reference. **Verdict: AVOID** for this use — wrong shape (whole-track identity, not partial-clip containment).

**dejavu** — MIT, ~6.5k stars, Python; Shazam-style peak-constellation hashing that memorises reference audio in a database and returns the matched song plus a match confidence [21]. Requires MySQL/PostgreSQL as its store, and the project is old. **Verdict: AVOID as a dependency, USE the algorithm** — the landmark-hash idea is ~100 lines of numpy and gives exactly the count-of-temporally-consistent-hashes statistic we need (section 6c).

**Panako** — AGPL-3.0, Java; fingerprints robust to time-stretch and pitch-shift [22]. Its README explicitly warns that patents US7627477 B2 and US6990453 cover techniques it implements [22]. **Verdict: AVOID** (AGPL, JVM, and pitch/tempo robustness is not our problem — phone audio of the song is not speed-shifted). Keep the patent note in mind for our own landmark code: check the status of those patents before shipping a landmark matcher commercially.

**audfprint** (Dan Ellis) — landmark fingerprinting with a CLI to build, merge and query databases [23]. Its documentation gives the most useful *metric* in this survey: of the landmark hashes shared between a query and a track, count those with a **consistent time offset**; "anything more than 5 or 6 consistently-timed matching hashes" indicates a true match, while random chance makes fewer than 1 % of raw common hashes temporally consistent [23]. **Verdict: AVOID as a dependency, USE the metric.**

## 3. Shot detection, decoding, visual features

**PySceneDetect** — BSD-3-Clause, v0.7.1 released 2026-07-22, "Production/Stable" [24]. Content, adaptive and threshold (fade) detectors; ships as `scenedetect`, `scenedetect-headless` and a library-only `scenedetect-core` [24]. OpenCV-based, CPU, fast at reduced resolution. **Verdict: USE-NOW** — personal phone clips rarely contain cuts, but screen recordings, edited exports and long clips do, and a montage must never cut *into* a source cut. Prefer `scenedetect-core` + OpenCV we already have.

**TransNetV2** — MIT; a deep network for shot transitions including gradual ones [25], with a PyTorch port and HF-hosted weights [26]. More accurate on dissolves than histogram methods. **Verdict: LATER** — torch weight for a problem personal footage barely has.

**OpenCV optical flow (Farneback)** — dense flow, CPU, in `opencv-python`, already present. On 160-px-wide frames it costs low single-digit milliseconds per frame. **Verdict: USE-NOW** — the core of the motion envelope (section 6b).

**RAFT (torchvision `raft_small`)** — BSD (torchvision), ~1 M parameters, 3.8 MB weights, 47.66 GFLOPs per pair, minimum input 128×128 [27]. Much better flow on large motions; far slower on CPU than Farneback. **Verdict: LATER** — only if Farneback's envelope proves too noisy to find motion onsets.

**decord** — PyPI 0.6.0 from October 2021, effectively unmaintained, with no Apple-silicon wheel [28]. **Verdict: AVOID.** **PyAV** (BSD, FFmpeg bindings) or a plain `ffmpeg … -vf scale=160:-2,fps=12 -pix_fmt gray -f rawvideo -` pipe into numpy is enough; the pipe is what `muvid` already does for thumbnails. **USE-NOW: ffmpeg pipe**, PyAV LATER if per-frame timestamps are needed.

**CLIP / open_clip / SigLIP** — open_clip is MIT; Google's SigLIP checkpoints loaded through it are Apache-2.0 and run on CPU [29][30]. Image embeddings give near-duplicate removal (burst photos), semantic grouping ("beach", "faces"), and the input to aesthetic heads. ViT-B on CPU is roughly tens of milliseconds per image — fine for one keyframe per clip second. Avoid the original OpenAI weights' ambiguity by preferring SigLIP. **Verdict: LATER** (v2) — adds torch; dedupe can be done in v1 with a 64-bit perceptual hash.

**LAION aesthetic predictor** — a small MLP on CLIP ViT-L/14 image embeddings, trained on "how much do you like this image, 1–10" ratings (SAC, LAION-Logos, AVA) [31]. A 2026 audit documents cultural and demographic biases in what it scores highly [32]. Licence of the improved-predictor weights was not confirmed in this search. **Verdict: LATER**, behind a seam, never as the sole ranker of a person's own photos.

**NIMA / BRISQUE / NIQE via pyiqa** — pyiqa bundles them (and MUSIQ, TOPIQ…) but is **CC BY-NC-SA 4.0** [33]. **Verdict: AVOID** (licence). For v1, **variance of the Laplacian** is the one-line blur score (`cv2.Laplacian(gray, cv2.CV_64F).var()`, below a threshold ⇒ blurry) [34] — **USE-NOW**, combined with mean luma and RMS contrast for exposure.

**Face detection** — MediaPipe Face Detector (Tasks API, `pip install mediapipe`) returns boxes plus six keypoints [35]; MediaPipe is Apache-2.0. **Verdict: USE-NOW (optional extra)** — faces are the single most useful signal for personal montages (keep them in frame, prefer clips with them on the chorus). **InsightFace**: library MIT but *all pretrained models, including `buffalo_l`, are non-commercial research only* [36]. **AVOID.**

**Saliency** — OpenCV's spectral-residual and fine-grained static saliency live in `opencv-contrib-python` [37]; CPU, milliseconds. U²-Net is Apache-2.0 with a 4.7 MB small variant [38]. `burns` already exposes `salient_box`. **Verdict: USE-NOW via `burns.salient_box`**; U²-Net LATER if spectral residual crops badly.

**Image emotion models** — EmoSet (ICCV 2023) is a 3.3 M-image dataset with 8 Mikels emotion categories and attributes such as brightness and colourfulness [39]; we found no permissively-licensed, production-ready classifier trained on it. **Verdict: LATER/AVOID** — use brightness, colourfulness and saturation (which EmoSet itself lists as emotion-bearing attributes [39]) as cheap mood proxies, and CLIP zero-shot prompts in v2.

## 4. Existing montage / auto-edit projects

**auto-editor** — Unlicense (public domain); cuts by audio loudness by default and supports `--edit motion` to drop motionless spans [40]. It is a *removal* editor (silence/stillness), not a music-driven one. **Verdict: LATER** — its motion-threshold idea validates our envelope; not a dependency.

**mugen** — MIT, ~240 stars; librosa beat analysis + MoviePy assembly, beat grouping ("every other beat"), weighted sources, onset-based events, and segment **filters** that reject segments containing a scene change, on-screen text (Tesseract) or low contrast [41]. Requires conda; old. **Verdict: AVOID as a dependency, mine for ideas** — its reject-filters (cut inside segment, text, low contrast) are a ready checklist for our segment scorer.

**synctest** — librosa beats + PySceneDetect scenes + OpenCV optical-flow motion, matching segments to beat intervals [42]. Small, unproven. **Verdict: AVOID** (confirms the same stack we are choosing).

**MediaPipe AutoFlip** — saliency-aware reframing to a new aspect ratio [43]; MediaPipe now lists it as **"support ended"** [44]. **Verdict: AVOID**; `burns.salient_box` + face boxes cover the portrait/landscape reframe for v1.

**FFmpeg `zoompan` + `xfade`** — `zoompan` evaluates per-frame zoom/x/y expressions for Ken Burns motion; `xfade` provides typed transitions with duration and offset [45][46]. LGPL/GPL depending on build, already our renderer. **Verdict: USE-NOW for transitions**; for the Ken Burns motion itself keep `burns` (≥ 0.0.11 for sub-pixel sampling — `zoompan`'s integer-pixel stepping is the jitter `burns` was fixed to avoid).

**MoviePy v2** — MIT; v2.0 introduced breaking API changes and v1 is unmaintained [47]. **Verdict: USE-NOW only where already used**; `muvid.footage.assemble` renders through ffmpeg filter graphs, which is the faster and leaner path for a 30-cut timeline.

**Remotion** — free for individuals and companies of up to 3 employees; larger for-profit organisations need a Company Licence [48]. **Verdict: LATER** — relevant only if reelee-web gains an in-browser preview of the montage.

**Video2Music** (AMAAI) — MIT; generates music *from* video, but its `script/` directory extracts per-video semantic (CLIP), motion, emotion and scene-offset features [49]. **Verdict: LATER** — reference for a feature set that is known to correlate with music; built on torch 1.12. **MVPt** (Surís et al., CVPR 2022) learns temporal music↔video correspondence contrastively for retrieval, up to 10× better than non-temporal baselines [50]; no code release found. **Verdict: LATER** (research reference for v3 "which clip suits this section").

## 5. Commercial APIs (one line each)

- **Magisto / Vimeo Create** — Vimeo bought Magisto in 2019 and folded it into Vimeo Create, later discontinued; the Vimeo–Magisto integration ended 2024-12-31 [51]. Not an option.
- **Shotstack** — JSON timeline → rendered video, credit = one rendered minute, roughly $0.045–$0.195 per minute by tier [52]. A render backend, not an editor; no music analysis.
- **Creatomate** — JSON "RenderScript" or template + modifications via `POST /v2/renders` [53]. Same category as Shotstack.
- **Cloudinary** — `e_preview` scores frame importance and assembles a short highlight with `duration`, `max_seg`, `min_seg_dur` controls [54]. Useful as a benchmark for "interesting segment" picking; not music-aware.
- **Google Video Intelligence** — `SHOT_CHANGE_DETECTION` returns shot segments [55]. Pay-per-minute; PySceneDetect suffices.
- **AWS Rekognition Segment API** — `SHOT` and `TECHNICAL_CUE` (black frames, credits, bars) segments with frame-accurate timecodes [56]. Same verdict.

None of these is music-aligned; all would add a vendor-terms perimeter. **AVOID for v1.**

## 6. Recommended v1 dependency set and the three computations

**Dependencies:** librosa (ISC), numpy/scipy, ffmpeg (pipe decoding + `xfade` + existing filter-graph assembly), `opencv-python` (Farneback, Laplacian, histograms; `-contrib` only if spectral-residual saliency is wanted beyond `burns`), `scenedetect-core` (BSD), and optionally `mediapipe` (Apache-2.0) behind an extra for faces. **No torch in v1.** Second backends named now so the seams exist: Beat This! behind `beat_grid(backend=)`, allin1 (hosted) behind `sections=`, SigLIP behind a `clip_embedder=` seam, RAFT behind `flow=`.

### (a) Music cut-point candidates — ~2–5 s CPU for a 3-minute song

1. `y` mono 22.05 kHz; `onset = librosa.onset.onset_strength(y, hop_length=512)` (23 ms frames); also the percussive part via `librosa.effects.hpss` → percussive onset envelope for denser mixes.
2. Beats from `mixing.audio.beat_grid` (librosa DP). Local pulse reliability = `librosa.beat.plp` value at each beat [2]; low PLP ⇒ the grid is guessing there, so cut on onsets rather than the grid.
3. Downbeats: keep `muvid.montage.analysis.downbeats_from_beats` (phase vote). Strengthen the vote by summing, per phase k ∈ {0..3}, onset strength *plus* low-band (< 150 Hz) energy *plus* beat-synchronous chroma change; the bar line is where harmony changes and kicks land.
4. Sections: beat-synchronous chroma + MFCC → recurrence matrix → `librosa.segment.agglomerative` (or Laplacian segmentation), snapped to the nearest downbeat; keep the existing energy-run labelling as the label source.
5. Energy: per-beat RMS (dB), smoothed over a bar; its first difference marks build-ups and drops.
6. Score every beat: `s = w_o·onset̂ + w_d·is_downbeat + w_s·is_section_start + w_e·max(0, ΔE)̂ + w_p·plp̂`, all normalised to [0, 1] by robust p99. Cut candidates = beats; "visual event" candidates (flash, punch-in, speed ramp) = onset peaks above the song's 95th percentile, including off-beat accents. Cut *density* per section follows tempo and energy: one cut per bar in quiet sections, per beat or half-bar in loud ones.

### (b) Per-clip visual impact / motion envelope — seconds per clip, decode-bound

1. Decode via ffmpeg pipe at 160 px wide, 12 fps, grey (and one RGB thumbnail per second for colour stats). 30 clips × ~20 s ≈ 7,200 small frames.
2. Per frame pair: Farneback flow on the 160-px frames. Let `m = |flow|`. Camera motion = median flow vector (global pan/shake); subject motion = `p90(|flow − median|)`. Frame difference `mean|Δgray|` as a cheaper corroborator.
3. `motion(t)` = subject motion, normalised per clip by its p99; `impact(t) = max(0, d motion/dt)`, smoothed over ~3 frames, peaks picked with a minimum separation of ~0.25 s. Those peaks are the clip's *visual events* — the frames to put on a strong beat or onset.
4. Per-second quality: Laplacian variance (blur) [34], mean luma and RMS contrast (exposure), shake = high-frequency energy of the global-motion vector, and optionally face count/area every 6th frame via MediaPipe [35]. Reject or down-weight seconds that are blurred, dark, or shaky; reject windows that straddle a PySceneDetect cut (mugen's filter [41]).
5. Segment selection: for a cut of length L on the music grid, slide over the clip and score `Σ quality + α·impact aligned to the music's event positions`; the best window wins. Photos get the existing strength score and a `burns` motion whose peak velocity is placed on the section's strongest beat.

### (c) "This clip's audio does not contain the song" — cheap, reliable, conservative

Direction of risk: in montage mode a clip whose audio *does* contain the song should be routed to the footage aligner (it can be synced); a clip that does *not* is just picture. A false "contains" is the costly error (it pins a clip to a wrong song position, the aliasing failure muvid#59 documents), so "contains" must require strong evidence and everything else defaults to "does not contain".

1. **Silence short-circuit:** no audio stream, or clip RMS below −50 dBFS for > 90 % of its length ⇒ does not contain.
2. **Landmark-hash test** (dejavu/audfprint algorithm, own ~100-line numpy implementation [21][23]): STFT at 11.025 kHz, 1024/256; local spectral maxima (≈ 20–30 peaks/s); hash pairs `(f1, f2, Δt)` with a fan-out of ~5 inside a 0–2 s target zone; index the song once. For each clip, collect all hash hits and histogram `t_song − t_clip` in 50 ms bins.
   - **Metric:** `K` = count in the best offset bin (temporally consistent matches), plus `R = K / K₂` where `K₂` is the best bin more than 0.5 s away, plus `ρ = K / N_common`.
   - **Thresholds to start from:** audfprint reports that > 5–6 consistent hashes indicates a true match and that random chance yields < 1 % consistency [23]. Proposed: **contains** iff `K ≥ 12 and ρ ≥ 0.05 and R ≥ 2`; **does not contain** iff `K ≤ 5`; otherwise **ambiguous** ⇒ treat as not contains for montage, log it, and offer the footage path.
3. **Confirm with the existing gate:** a "contains" from step 2 is passed to `mixing.audio.align_clips_to_reference` and must also pass `vouches_for` (`support > 0.5 and margin > 0`) before the clip is ever placed at its synced position. Two independent instruments must agree; either one refusing keeps the clip in montage mode.
4. **Calibrate before trusting the numbers.** Run on the muvid#59 master set: correct clips, pure-noise clips, *ambient-only* phone clips (talking, wind, a different song), and **alias** clips (same song, bar-periodic repeats) — the edl.py notes show that a set without aliases calibrates the wrong threshold. Cost: hashing a 3-minute song and 30 short clips is well under 10 s on CPU.

## REFERENCES

1. [librosa 0.11.0 package page (ISC, released 2025-03-11)](https://simple-repository.app.cern.ch/project/librosa)
2. [librosa.beat.plp — Predominant Local Pulse](https://librosa.org/doc/main/generated/librosa.beat.plp.html)
3. [madmom on PyPI (BSD code, CC BY-NC-SA 4.0 models)](https://pypi.org/project/madmom)
4. [madmom changelog — latest v0.16.1](https://data.safetycli.com/changelogs/madmom)
5. [CPJKU/beat_this — Beat This! repository](https://github.com/CPJKU/beat_this)
6. [Beat This! Zenodo record v1 (2024-10-14)](https://zenodo.org/records/13922116)
7. [Beat This! ONNX/WebGPU port on Hugging Face](https://huggingface.co/musetric/beat-this-onnx)
8. [BeatNet: CRNN and particle filtering for online joint beat, downbeat and meter tracking](https://deepai.org/publication/beatnet-crnn-and-particle-filtering-for-online-joint-beat-downbeat-and-meter-tracking)
9. [BeatNet documentation (installation and dependencies)](https://docsearch.algolia.com/mcp/docs/repo/mjhydri/beatnet)
10. [Essentia README (AGPL-3.0)](https://raw.githubusercontent.com/MTG/essentia/master/README.md)
11. [Essentia models LICENSE (CC BY-NC-ND 4.0)](https://essentia.upf.edu/models/LICENSE)
12. [Essentia — using machine learning models (essentia-tensorflow)](https://essentia.upf.edu/machine_learning.html)
13. [aubio](https://aubio.org/)
14. [mir-aidj/all-in-one — All-In-One Music Structure Analyzer](https://github.com/mir-aidj/all-in-one)
15. [MSAF 0.1.80 on PyPI](https://pypi.python.org/pypi/msaf)
16. [facebookresearch/demucs (archived)](https://github.com/facebookresearch/demucs)
17. [audalign on PyPI](https://pypi.org/project/audalign)
18. [syncstart 1.1.0 on PyPI](https://pypi.org/project/syncstart/1.1.0)
19. [BBC audio-offset-finder on PyPI](https://pypi.org/project/audio-offset-finder)
20. [fpcalc / Chromaprint licence notes](https://community.chocolatey.org/packages/fpcalc/1.4.3)
21. [worldveil/dejavu — audio fingerprinting in Python](https://github.com/worldveil/dejavu)
22. [JorenSix/Panako — acoustic fingerprinting (AGPL, patent notice)](https://github.com/JorenSix/Panako)
23. [Dan Ellis, audfprint — landmark-based audio fingerprinting](https://www.ee.columbia.edu/~dpwe/LabROSA/matlab/audfprint/)
24. [PySceneDetect (scenedetect) on PyPI — v0.7.1](https://pypi.org/project/scenedetect/)
25. [Souček & Lokoč, TransNet V2 (arXiv 2008.04838)](https://arxiv.org/pdf/2008.04838)
26. [TransNetV2 PyTorch weights README (Hugging Face)](https://huggingface.co/magnusdtd/TransNetV2/blob/main/README.md)
27. [torchvision raft_small](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.optical_flow.raft_small.html)
28. [Notes on decord 0.6.0 status and alternatives](https://aicoding.csdn.net/6a69ce2c662f9a54cb95e2f9.html)
29. [OpenCLIP introduction](https://mintlify.com/mlfoundations/open_clip/introduction)
30. [timm/ViT-B-16-SigLIP-512 model card (Apache-2.0)](https://huggingface.co/timm/ViT-B-16-SigLIP-512/blob/5ecddc904f4c25c8089e1dfc58b9e56236eacfca/README.md)
31. [LAION-Aesthetics (LAION blog)](https://laion.ai/blog/laion-aesthetics/)
32. [The Algorithmic Gaze of Image Quality Assessment: an audit of the LAION-Aesthetics Predictor (arXiv 2601.09896)](https://arxiv.org/pdf/2601.09896)
33. [pyiqa — PyTorch Toolbox for Image Quality Assessment](https://pypi.org/project/pyiqa/0.1.5/)
34. [Blur detection with OpenCV — variance of the Laplacian (PyImageSearch)](https://pyimagesearch.com/2015/09/07/blur-detection-with-opencv/)
35. [MediaPipe Face Detector](https://developers.google.com/edge/mediapipe/solutions/vision/face_detector)
36. [insightface on PyPI (model licence notice)](https://pypi.org/project/insightface/)
37. [OpenCV saliency detection (PyImageSearch)](https://www.pyimagesearch.com/2018/07/16/opencv-saliency-detection/)
38. [U²-Net ONNX model card (Apache-2.0)](https://huggingface.co/baby2008/u2net-onnx)
39. [JingyuanYY/EmoSet — large-scale visual emotion dataset](https://github.com/JingyuanYY/EmoSet)
40. [WyattBlue/auto-editor](https://github.com/wyattblue/auto-editor)
41. [scherroman/mugen — music video generator](https://github.com/scherroman/mugen)
42. [leocodeio/synctest — beat-synced video generator](https://awesome.ecosyste.ms/projects/github.com%2Fleocodeio%2Fsynctest)
43. [AutoFlip: an open source framework for intelligent video reframing (Google Research)](https://research.google/blog/autoflip-an-open-source-framework-for-intelligent-video-reframing/)
44. [MediaPipe Solutions guide (AutoFlip: support ended)](https://developers.google.com/edge/mediapipe/)
45. [Ken Burns effect slideshows with FFmpeg (zoompan)](https://el-tramo.be/blog/ken-burns-ffmpeg/)
46. [How to create a slideshow from images using FFmpeg (xfade)](https://creatomate.com/blog/how-to-create-a-slideshow-from-images-using-ffmpeg)
47. [Zulko/moviepy](https://github.com/Zulko/moviepy)
48. [Remotion licence](https://remotion.dev/license)
49. [amaai-lab/video2music](https://github.com/amaai-lab/video2music)
50. [Surís et al., It's Time for Artistic Correspondence in Music and Video (CVPR 2022)](https://arxiv.org/pdf/2206.07148)
51. [Magisto (Wikipedia)](https://en.wikipedia.org/wiki/Magisto)
52. [Shotstack pricing overview](https://www.submagic.co/blog/shotstack-pricing)
53. [Creatomate RenderScript JSON structure](https://creatomate.com/docs/api/render-script/json-structure)
54. [Cloudinary video previews and posters](https://cloudinary.com/documentation/video_previews_and_posters)
55. [Google Cloud Video Intelligence — shot change detection](https://docs.cloud.google.com/video-intelligence/docs/feature-shot-change)
56. [Amazon Rekognition Segment API](https://docs.aws.amazon.com/rekognition/latest/dg/segment-api.html)
