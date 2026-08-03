# Scoring & choosing footage segments for the `music_video` genre — research report

**Context.** muvid's `music_video` genre already aligns several different-device
recordings of one fixed song to the clean master's timeline (audio cross-correlation →
a per-clip offset, so any clip time maps to a song time). This report surveys **scoring
criteria** that rate footage segments so that (A) an **auto composer** can choose which
clip is on-air over each span of the song, and (B) a **human editor** can see, in a
multichannel view, where to cut and aggregate. The two consumers read the **same
scores** — that symmetry is the design keystone.

The two scores the user named — **lip-sync quality** and **motion-to-beat** — are both
buildable now as moderate glue over off-the-shelf parts. This report also proposes a
broader menu, an architecture, and the key decisions to settle before building.

---

## 1. The keystone: one song-time grid, two consumers

Every raw feature from every clip is resolved onto **one fixed-rate song-time grid**
(`t0 = 0`, `hop ≈ 0.1 s` → ~10 Hz — ample for a UI and for beat-level selection),
decoupled from any clip's fps. Each `(clip, metric)` becomes a **score track**: parallel
arrays `values[]` (robustly normalized, higher = better), `raw_values[]` (tooltips), and
a per-frame `coverage mask[]`; grid frame *k* ↔ song time `t0 + k·hop`, identical across
clips so they stack without per-point timestamps. This is the JAMS "dense observation"
idiom [1] stored as arrays for O(1) tensor access.

- **Auto** deserializes to a tensor `S[clip, frame, metric]` + mask `M[clip, frame]`;
  `composite[c,k] = (Σ_m w_m · S[c,k,m]) · M[c,k]`. A **beat-snapped Viterbi / semi-Markov
  DP** over the trellis (columns = frames, rows = clips) picks the on-air path: node
  reward = composite, edge cost = a Potts switch penalty, transitions allowed only at
  beats, dwell states enforce min/max shot length. This is Arev et al.'s multi-social-
  camera editor [15] and Leake et al.'s take-selection editor [16] specialized to score
  curves. **The "strategy" is a pure config object** — `(weights w, λ_switch, L_min,
  L_max, boundary set)` — so adding a metric is adding a column + a weight (open-closed).
- **Editor** draws the same curves as stacked lanes over the shared song-time axis; a
  human's manual pin FIXES those DP states and a constrained re-solve optimally fills the
  rest. One set of curves, one objective — the editor steers the same optimizer the auto
  composer runs headless.

**Normalization** is the crux (see §7): a robust per-metric-global transform (subtract
median, divide by IQR, clip to [p5, p95], squash to [0,1]) keeps scores comparable
*across clips* (which the editor's "which camera is sharper" needs) while resisting one
outlier clip skewing the scale.

---

## 2. Lip-sync quality

**State of the art.** The **SyncNet** family [2] is the mature, off-the-shelf way to
score "do the on-screen singer's lips match the audio here": a two-stream CNN embeds a
0.2 s mouth crop and the co-temporal audio into a shared space; per sliding window it
yields a confidence **LSE-C** (higher = better sync), a distance **LSE-D** (lower =
better), and the audio-visual offset [3]. `syncnet-python` (MIT, pip) runs the full
face-detect → track → mouth-crop → score pipeline, so a per-window curve is glue, not
research.

**The muvid domain win.** Because we already know each clip's offset to the *clean
master*, we should score the mouth crop against the master's **separated vocal stem**
(Demucs [6]) at the known song-time — not the clip's noisy phone audio. This removes
SyncNet's own offset search (it becomes a validation) and feeds the model clean,
in-domain audio. Gate the score with **active-speaker detection** (TalkNet [5]) + face
coverage so segments with no singing face are **NA** (excluded), never 0 — otherwise the
term biases selection toward any face on screen.

**Caveat.** SyncNet/Wav2Lip [3] were trained on *speech*; sustained sung vowels degrade
them. **VocaLiST** [4] is a cross-modal transformer built for lips-and-*voices* including
singing — more accurate but heavier and research-licensed; hold it as a v2 upgrade on
high-value vocal spans. **Perfect Match** [17] and **AV-HuBERT** [18] are stronger
embeddings but overkill for a per-window score.

**Proposed scores.** `lip_sync_lse_c` (sliding LSE-C vs master vocals), its free
`lse_d_offset_curve` (the offset trace shows where a clip's sync breaks — a cut cue), and
the `lip_sync_face_gate` mask.

---

## 3. Motion-to-beat ("dance match")

Splits into three sub-problems, two solved and one the real work:

1. **Audio beats** — mature: `librosa.beat` (PLP) [7], `madmom` DBNBeatTracker [8],
   `BeatNet` [9], Essentia. Computed **once on the master** and reused via each clip's
   offset.
2. **Video "motion rhythm"** — a 1-D envelope from optical-flow magnitude / frame-diff
   energy (OpenCV, RAFT), or **pose-keypoint** velocity/acceleration (MediaPipe) when a
   person is tracked. Abe Davis's **"Visual Rhythm and Beat"** [10] formalizes a
   *directogram*, *visual impacts*, and *visual beats* (ref impl `visbeat`).
3. **The alignment score** — the canonical number is the **Beat Alignment Score (BAS)**
   from AIST++ dance-generation eval [11]: mean over motion beats of the nearest audio
   beat's proximity. A content-agnostic partner is **cross-correlation** of the motion
   envelope vs the onset envelope (strength + lead/lag; the lag also auto-estimates
   per-clip A/V capture latency). A **phase-locking value** captures groove even when
   motion is syncopated-but-tight.

**Camera-motion compensation** matters: hand-held shake must not be read as subject
motion (share the stabilization pass, §4).

**Proposed scores.** `motion_beat_bas` (person present) + `motion_onset_xcorr`
(content-agnostic; covers scene/camera motion) — together they cover every clip.

---

## 4. Footage quality & content (cheap CPU tier)

Because every frame maps to a song time, "how good does this look right now" is a set of
near-free per-frame signals sampled at ~2–8 Hz and resampled onto the grid — ideal as
**hard gates** that prune unusable footage *before* the weighted vote, and as editor
lanes:

- **Sharpness** — variance of Laplacian (OpenCV, <1 ms/frame): in-focus / no motion blur.
- **Exposure** — luma-histogram stats: not blown-out/crushed.
- **Stability/shake** — LK-flow jitter energy: usable vs jittery (also supplies the
  camera-motion estimate §3 needs).
- **Face framing** — MediaPipe BlazeFace presence/size/centering/frontality (200–1000 fps
  CPU): the hero-shot signal for a performance video.

Richer content signals (v2+): expression/smile (FER/DeepFace), saliency (cv2.saliency),
shot-boundary segmentation (**PySceneDetect** [19] — the shared aggregation unit).
**Learned aesthetic VQA** — NIMA/MUSIQ/TOPIQ (pyiqa) and DOVER [20] — correlates best with
"watchability" but ships under **non-commercial** weights and prefers GPU; the
commercial-clean fallback is BRISQUE/NIQE (opencv-contrib) [12,13].

---

## 5. Editing theory → criteria

Good music-video editing is well-codified: **cut on the beat** (ideally the downbeat);
let cutting **rate** track tempo/section (fast in the chorus/drop, slow in the
intro/bridge); match **visual energy** to musical energy; keep **shot variety** high;
preserve **continuity**; follow the song's **emotional arc** — with Walter Murch's *Rule
of Six* [14] ordering the priorities (emotion > story > rhythm > eye-trace > 2-D plane >
3-D space). Automatic-MV research operationalizes these: **audeosynth** (music-driven
montage) [21], automatic-MV-generation [22], emotion-aware montage [23]. Song structure
(`allin1`/MSAF) + a per-section energy target turn "match the chorus" into a score;
average-shot-length distributions (Cinemetrics) turn "pacing" into a target curve.

Most of these become **per-segment scores** (energy_match, section_fit) or **global
constraints** (cut-on-beat, diversity, pacing) rather than standalone curves.

---

## 6. Score → selection algorithm

Our setup — several clips over one timeline, each with per-metric curves, choose who's
on-air — is almost exactly **Arev et al.** [15] (trellis DP: node coverage reward + edge
switching/cinematography penalties) and **Leake et al.** [16] (multiple takes of one
scene, HMM/DP clip-per-line with composable cost terms — the closest analog). The v1
recommendation: a **beat-snapped semi-Markov Viterbi DP** (`O(T·K²)`, milliseconds) whose
node reward is the fused score, edge cost is a switch penalty, transitions only at beats,
states enforce shot-length limits. Diversity/fairness (each clip ≥ X% screen time) is a
v2 escape hatch: re-express the same objective as an OR-tools knapsack or a sequential-DPP
re-ranker (seqDPP [24], DPP [25], apricot [26]).

---

## 7. Data model & the multichannel editor

The UI convention is a **shared horizontal time axis, one lane per source** — exactly how
NLE multicam tools (PluralEyes/DaVinci) align device recordings by audio cross-correlation
(which `mixing.audio` already computes). Feature tracks render as curves/heat-strips
sharing the waveform x-scale (wavesurfer.js / peaks.js [27], Sonic Visualiser convention).
Persistence fits reelee-web's `annot://schema/<name>/vN` model as two standoff types:
`clip-alignment/v1` (offset, duration, optional time-warp) and `clip-score-track/v1`
(clip_id, metric, t0, hop_s, values[], mask[], raw_values[], norm params). Large float
arrays likely carry a `dol`/ContentRef into the blob layer rather than inlining. The JAMS
[1] / ELAN / OpenTimelineIO ecosystem is the standoff-annotation precedent; exporting the
auto path to **OpenTimelineIO** lets it open in a real NLE.

---

## 8. Recommended v1 (for discussion)

A small, mostly-off-the-shelf core that still delivers both named signals:

| Group | Score | Feasibility |
|---|---|---|
| sync | `lip_sync_lse_c` vs master vocal stem + `lse_d_offset` + `face_gate` (TalkNet) | moderate glue |
| sync | `motion_beat_bas` + `motion_onset_xcorr` (camera-compensated) | moderate glue |
| quality | sharpness, exposure, stability, face_framing (hard gates) | off-the-shelf |
| structural | master beat/downbeat grid, PySceneDetect boundaries, coverage_mask, weighted fusion, beat-snapped Viterbi path, selection_margin | off-the-shelf |

**Deferred:** VocaLiST (heavier/licensed), learned aesthetic VQA/DOVER (non-commercial +
GPU), emotion, diversity/pacing/ILP, section_fit/energy_match refinements.

**Decisions that most shape the build:** (a) commercial vs non-commercial deployment
(gates pyiqa/DOVER/Wav2Lip weights → stick to MIT syncnet-python + BRISQUE/NIQE); (b)
per-beat vs fixed-hop decision grid, and switch-between-clips-only vs cut-within-a-clip;
(c) per-clip vs global normalization (the editor/auto tension); (d) sync vs async
compute + GPU availability.

---

## REFERENCES

[1] Humphrey EJ, Salamon J, Nieto O, et al. JAMS: A JSON Annotated Music Specification for Reproducible MIR Research. ISMIR; 2014. [https://archives.ismir.net/ismir2014/paper/000355.pdf](https://archives.ismir.net/ismir2014/paper/000355.pdf)
[2] Chung JS, Zisserman A. Out of time: automated lip sync in the wild (SyncNet). ACCV Workshops; 2016. [https://www.robots.ox.ac.uk/~vgg/publications/2016/Chung16a/chung16a.pdf](https://www.robots.ox.ac.uk/~vgg/publications/2016/Chung16a/chung16a.pdf)
[3] Prajwal KR, Mukhopadhyay R, Namboodiri VP, Jawahar CV. A Lip Sync Expert Is All You Need for Speech to Lip Generation In The Wild (Wav2Lip; LSE-C/LSE-D). ACM MM; 2020. [https://arxiv.org/abs/2008.10010](https://arxiv.org/abs/2008.10010)
[4] Kadandale VS, Montesinos JF, Haro G. VocaLiST: An Audio-Visual Synchronisation Model for Lips and Voices. Interspeech; 2022. [https://arxiv.org/pdf/2204.02090](https://arxiv.org/pdf/2204.02090)
[5] Tao R, Pan Z, Das RK, et al. Is Someone Speaking? Long-term Temporal Features for AV Active Speaker Detection (TalkNet). ACM MM; 2021. [https://arxiv.org/abs/2107.06592](https://arxiv.org/abs/2107.06592)
[6] Défossez A. Hybrid Transformers for Music Source Separation (Demucs). [https://github.com/facebookresearch/demucs](https://github.com/facebookresearch/demucs)
[7] McFee B, et al. librosa.beat.plp — Predominant Local Pulse (Grosche & Müller 2011). [https://librosa.org/doc/main/generated/librosa.beat.plp.html](https://librosa.org/doc/main/generated/librosa.beat.plp.html)
[8] Böck S, Korzeniowski F, Schlüter J, et al. madmom: A New Python Audio and Music Signal Processing Library. ACM MM; 2016. [https://github.com/CPJKU/madmom](https://github.com/CPJKU/madmom)
[9] Heydari M, Cwitkowitz F, Duan Z. BeatNet: CRNN and Particle Filtering for Online Joint Beat, Downbeat and Meter Tracking. ISMIR; 2021. [https://arxiv.org/pdf/2108.03576](https://arxiv.org/pdf/2108.03576)
[10] Davis A, Agrawala M. Visual Rhythm and Beat. SIGGRAPH / ACM TOG; 2018. [https://www.abedavis.com/files/papers/VisualRhythm_Davis18.pdf](https://www.abedavis.com/files/papers/VisualRhythm_Davis18.pdf)
[11] Li R, Yang S, Ross DA, Kanazawa A. AI Choreographer: Music Conditioned 3D Dance Generation with AIST++ (Beat Alignment Score). ICCV; 2021. [https://arxiv.org/abs/2101.08779](https://arxiv.org/abs/2101.08779)
[12] Mittal A, Moorthy AK, Bovik AC. No-Reference Image Quality Assessment in the Spatial Domain (BRISQUE). IEEE TIP; 2012. [https://ieeexplore.ieee.org/document/6272356](https://ieeexplore.ieee.org/document/6272356)
[13] Mittal A, Soundararajan R, Bovik AC. Making a Completely Blind Image Quality Analyzer (NIQE). IEEE SPL; 2013. [https://ieeexplore.ieee.org/document/6353522](https://ieeexplore.ieee.org/document/6353522)
[14] Murch W. In the Blink of an Eye — the Rule of Six. 1995/2001. [https://www.studiobinder.com/blog/walter-murch-rule-of-six/](https://www.studiobinder.com/blog/walter-murch-rule-of-six/)
[15] Arev I, Park HS, Sheikh Y, et al. Automatic Editing of Footage from Multiple Social Cameras. SIGGRAPH; 2014. [https://studios.disneyresearch.com/2014/07/27/automatic-editing-of-footage-from-multiple-social-cameras/](https://studios.disneyresearch.com/2014/07/27/automatic-editing-of-footage-from-multiple-social-cameras/)
[16] Leake M, Davis A, Truong A, Agrawala M. Computational Video Editing for Dialogue-Driven Scenes. SIGGRAPH; 2017. [https://graphics.stanford.edu/papers/roughcut/files/roughcut-small.pdf](https://graphics.stanford.edu/papers/roughcut/files/roughcut-small.pdf)
[17] Chung SW, Chung JS, Kang HG. Perfect Match: Improved Cross-modal Embeddings for AV Synchronisation. ICASSP; 2019. [https://arxiv.org/pdf/1809.08001](https://arxiv.org/pdf/1809.08001)
[18] Shi B, Hsu WN, Lakhotia K, Mohamed A. Learning AV Speech Representation by Masked Multimodal Cluster Prediction (AV-HuBERT). ICLR; 2022. [https://arxiv.org/abs/2201.02184](https://arxiv.org/abs/2201.02184)
[19] Castellano B. PySceneDetect. [https://www.scenedetect.com/](https://www.scenedetect.com/)
[20] Wu H, Zhang E, Liao L, et al. Exploring Video Quality Assessment on UGC from Aesthetic and Technical Perspectives (DOVER). ICCV; 2023. [https://arxiv.org/abs/2211.04894](https://arxiv.org/abs/2211.04894)
[21] Liao Z, Yu Y, Chen B, et al. audeosynth: Music-Driven Video Montage. SIGGRAPH; 2015. [https://i.cs.hku.hk/~yzyu/publication/audeosynth-sig2015.pdf](https://i.cs.hku.hk/~yzyu/publication/audeosynth-sig2015.pdf)
[22] Lin JC, Wei WL, Wang HM, et al. Automatic Music Video Generation Based on Simultaneous Soundtrack Recommendation and Video Editing. ACM MM; 2017. [https://dl.acm.org/doi/abs/10.1145/3123266.3123399](https://dl.acm.org/doi/abs/10.1145/3123266.3123399)
[23] Emotion-Aware Music Driven Movie Montage. J Comput Sci Technol; 2023. [https://link.springer.com/article/10.1007/s11390-023-3064-6](https://link.springer.com/article/10.1007/s11390-023-3064-6)
[24] Gong B, Chao WL, Grauman K, Sha F. Diverse Sequential Subset Selection for Supervised Video Summarization (seqDPP). NeurIPS; 2014. [https://www.cs.utexas.edu/~grauman/papers/nips14_seqdpp.pdf](https://www.cs.utexas.edu/~grauman/papers/nips14_seqdpp.pdf)
[25] Kulesza A, Taskar B. Determinantal Point Processes for Machine Learning. FTML; 2012. [https://arxiv.org/abs/1207.6083](https://arxiv.org/abs/1207.6083)
[26] Schreiber J, Bilmes J, Noble WS. apricot: Submodular Selection for Data Summarization in Python. JMLR; 2020. [https://www.jmlr.org/papers/v21/19-467.html](https://www.jmlr.org/papers/v21/19-467.html)
[27] BBC R&D. peaks.js — audio waveform UI component. [https://github.com/bbc/peaks.js](https://github.com/bbc/peaks.js)
