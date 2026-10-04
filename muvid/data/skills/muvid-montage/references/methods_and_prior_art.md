# Montage mode — methods and prior art

*Research section 01, 2026-10-04. Scope: making a music-synchronised montage from a pool of personal clips and stills (whose own audio is unrelated to the song) plus one song. Citations are Vancouver-style; licences were checked against each repository's LICENSE file or official model page on the date above.*

## 0. Framing in one paragraph

The problem has been studied for over twenty years under the names *automatic music video generation*, *music-driven video montage*, *audio beat matching* and, recently, *agentic music-synchronised editing*. Every serious system splits it into the same four stages: (a) analyse the song into a hierarchy of time points (onsets ⊂ beats ⊂ downbeats/bars ⊂ sections), (b) analyse each clip into time-varying visual descriptors (motion, quality, semantics), (c) solve an assignment problem that maps clip sub-segments to the intervals between chosen cut points while minimising a sum of cost terms, and (d) render, sometimes time-warping each segment so its visual accents land on musical ones. What changes across the literature is how rich (b) is and which solver handles (c). Commercial products (Apple Memories, Google Photos movies) publish curation signals but nothing about audiovisual alignment, which matches the observation that motivated this work.

## 1. Prior art: music-driven video editing

### 1.1 The classical line (2002–2006)

**Foote, Cooper & Girgensohn (FXPAL, ACM MM 2002), "Creating music videos using automatic media analysis".** This is the founding paper. It takes home video plus a music track, segments the music at beat-detected/novelty boundaries, scores video segments by a camera-motion/brightness "unsuitability" heuristic, then truncates and concatenates segments to fill the music intervals [1]. *Maturity:* conceptual only; no code. *For us:* it already uses the v1 recipe: cut on audio novelty peaks and pick the steadiest, best-exposed footage to fill each slot.

**Hua, Lu & Zhang (Microsoft Research Asia, ACM MM 2004), "Automatic music video generation based on temporal pattern analysis".** This paper extracts the temporal structure of both home video (shots, sub-shots, highlights) and music (beats, sentences, *repetitive patterns*), then selects highlight segments so that repetition in the music maps to visual pattern [2]. The same group's **Photo2Video** applied the idea to stills: per-photo key-frames from an attention model, a camera-motion (pan/zoom) pattern per photo, and clips aligned to music content [3]. *For us:* Photo2Video is the direct academic ancestor of "stills with Ken Burns, timed to the music". Its use of an attention model to choose the start and end frames is what `burns.salient_box` already provides.

**Chen et al., "Tiling Slideshow" (ACM MM 2006, best paper).** Clusters similar photos and shows them as tiled layouts whose changes follow the pace of the background music [4]. *For us:* this is a later "multi-still per beat" visual idiom. It is not needed for v1.

### 1.2 The optimisation line (2015–2018)

**Liao, Yu, Gong & Cheng, "audeosynth: music-driven video montage" (SIGGRAPH 2015).** This is the closest academic precedent to our use case: a set of personal clips (gatherings, adventures) plus a piece of music produces a montage that is "cut to the beat" and "synchronised" [5]. Details worth copying:

- *Music input is MIDI*, not audio. Note onsets, pitch, tracks and bars come from the score, and segments are bottom-up merges of bars [5]. That shortcut is not available to us. We must estimate the same hierarchy from audio (§2).
- *Video features* come from dense optical flow. **Motion Change Rate** (MCR) is the per-pixel temporal difference of flow, which measures acceleration rather than speed, saliency-weighted. **Flow peak** is the 99.9th percentile of flow magnitude. **Dynamism** is the fraction of pixels above a flow threshold. **Peak frequency** is the PSD peak of the MCR profile [5].
- *Music saliency* Ω(t) is a sum of Gaussians centred on note onsets, weighted by rules: start of bar, pitch peak, before a long interval, and so on [5].
- *Energy* = matching cost (mean-subtracted correlation of MCR with Ω, plus a pace↔peak-frequency mismatch term) + transition cost (pace change vs mean-flow-velocity change, and number of tracks vs change in dynamism across the cut) + higher-order terms [5].
- *Solver:* precompute, for every (music segment, clip) pair, the best subsequence (start frame, end frame, scale) with a sliding window. Then use MCMC over assignments, and finally "snap" local MCR peaks to the exact onsets with dynamic programming. 30 segments × 60 clips solved in 10–15 s [5].
- *Maturity:* no code was released. The paper is complete enough to reimplement. *For us:* this is the reference design for later work. v1 should copy its *factorisation* (precompute per-pair best fit, then a global assignment), but use DP instead of MCMC, because our cost is chain-structured (§4).

**Davis & Agrawala, "Visual Rhythm and Beat" (SIGGRAPH 2018 / CVPRW 2018).** Defines **visual beats** from motion and shows that warping them onto musical beats creates the appearance of dance [6]. From the released code: Farneback dense flow per frame, then a 128-bin *directogram* (flow-angle histogram weighted by magnitude, excluding a 5% border dead zone and a noise-floor percentile), then a **visual impact envelope**: the decrease in directional flux (sudden deceleration), mean-pooled, Butterworth high-passed at about 0.8 s, and percentile-clipped. Visual beats are its peaks [6, 7]. Applications include changing the song a person dances to, and *searching a collection for moments of accidental dance-like motion*. That second application is our use case in miniature [6]. *Code and licence:* `visbeat`/`visbeat3` (Python port) is under a **Stanford non-commercial research licence**. It forbids commercial use of derivatives and requires keeping the visbeat logo on outputs [7]. *For us:* **do not depend on visbeat.** The algorithm is simple: about 50 lines of NumPy over OpenCV Farneback or RAFT flow. Reimplement it from the paper (ideas are not licensed; code is). It is the best single visual-accent channel for a later version.

**Bellini, Kleiman & Cohen-Or, "Dance to the beat: synchronizing motion to audio" (CVM 2018).** Extracts motion beats as frames where motion direction changes sharply or stops. It matches them to music beats so that "as many beats as possible are matched with as little time-warping distortion as possible", and re-times the video accordingly [8]. *For us:* this is the speed-ramp formulation (§4.3) in its simplest form.

### 1.3 Industrial and benchmark work (2023)

**Pei et al. (ByteDance), "AutoMatch: audio beat matching benchmark" (2023).** Defines *audio beat matching* (ABM) as predicting the transition timestamps that a professional editor would use for a given background track. The dataset has 87k tracks with transition times mined from published short videos. The model, BeatX (mel and energy features plus attention), reaches 61.6% F1 [9]. A key finding is that "changing boundaries of music are not necessarily leading to transitions" [9]. Editors cut on a subset of beats chosen by context, not on every beat. *For us:* the main design lesson is that the candidate cut set must be a *ranked* set (beat strength × downbeat × section boundary × energy), not "every beat". A learned cut-point predictor is a later option. The dataset does not appear to be openly licensed.

### 1.4 The LLM/agentic line (2024–2026)

- **LAVE** (IUI 2024): an LLM agent over automatically generated language descriptions of the user's footage. It supports semantic retrieval, storyboarding and trimming. It has no music alignment [10].
- **ExpressEdit** (IUI 2024): natural language plus sketch commands parsed into temporal, spatial and operational edit references. Also no music alignment [11].
- **AutoMV** (Dec 2025): a multi-agent *generated* music-video pipeline. It uses SongFormer for section structure, htdemucs plus Whisper for lyrics, and Qwen2.5-Omni for a mood and genre caption. Segments are 3–15 s, cut at lyric and section boundaries, with 1/24 s quantisation to avoid drift [12]. Code is Apache-2.0 per its GitHub repository; the paper says MIT.
- **CutClaw** (Mar 2026): agentic editing of *hours-long* raw footage into short music-synchronised videos. Agents decompose the audio and video hierarchically and a "playwriter" anchors scenes to musical shifts, followed by editor and reviewer agents [13]. Code is on GitHub but released under CC BY-NC-SA 4.0 per the paper page [13].
- **BEAT** (May 2026): music-guided trailer generation. Its **Bar-DP** is a beam search over bars with a *many-to-one elastic* shot→bar mapping. The cost has three parts: a learned music–visual compatibility score (MuVA, on CLAP audio embeddings), a smoothness penalty on consecutive-shot similarity, and an **energy-adaptive cut bonus** λ·(2ē−0.3) that encourages cuts in high-RMS bars and discourages them in quiet ones. It also enforces neighbour exclusion (cosine > 0.8 is rejected) and a duration constraint allowing mild slow motion (η = 0.9) [14]. No code was found [14].

*For us:* the 2025–26 agentic systems agree on a split. The **LLM does semantics and narrative**: what goes with the chorus, chronology, which faces. A **numeric DP does the timing**. That matches the ecosystem rule that "agents compose typed capabilities; they never emit topology": the LLM may propose an ordered pool or per-section intent, and the DP places cuts. BEAT's energy-adaptive cut bonus and neighbour exclusion are cheap, effective terms to adopt in v1.

### 1.5 Products

Apple's Photos technical brief describes the curation signals: "interesting" content (frequent people, new locations), *representation* of the day, *composition*, *liveliness* (mixing Live Photos, video and stills), user activity (edited, shared, favourited), an attention-based saliency engine trained on eye-tracking with faces as the strongest cue, and an on-device knowledge graph of events [15]. It says nothing about cutting to music. Google Photos has produced themed movies "synced to music" since 2016, with no published method [16]. *For us:* Apple's signal list is a good v1 *selection* prior. Alignment is where we can be clearly better.

### 1.6 The reverse and the matching directions (for placement only)

**Video→music generation:** CMT (ACM MM 2021 best paper) links video *timing, motion speed and motion saliency* to music *beat, note density and note strength* [17]. V2Meow (AAAI 2024) [18] and VidMuse (2024) [19] generate audio conditioned on frames. These matter to us only because CMT's three-way pairing (cut timing↔beat, motion speed↔density, motion saliency↔accent strength) is a clean statement of the *feature correspondences* our cost should reward. **Cross-modal video↔music retrieval:** CBVMR (2017) [20], Prétet et al.'s self-supervised recommendation (IJCNN 2021) [21], and MVPt (CVPR 2022), which models long-range temporal context in both modalities and improves retrieval accuracy up to 10× [22]. These are the basis for the later "choose the best song for this pool" feature. **Emotion matching:** IMEMNet learns a shared image–music embedding in valence–arousal (VA) space from over 140k pairs [23]. MMVA (2025) extends it with music captions [24].

## 2. Music-side features (cut-point candidates and energy)

| Feature | Best open tool | Licence | Notes |
|---|---|---|---|
| Beats + downbeats | **Beat This!** (Foscarin, Schlüter & Widmer, ISMIR 2024) [25] | **MIT, code and weights** [25] | SOTA *without* the DBN post-processor; plain peak-picking; handles time-signature changes and tempo drift. CPU fallback. |
| Beats + downbeats (legacy) | madmom RNN+DBN [26] | Code BSD, **model files CC BY-NC-SA 4.0** [26]; the DBN is reported as patented for non-commercial use [25] | Avoid for anything commercial. |
| Beats (classical DP) | librosa `beat_track` (Ellis 2007 DP tracker) [27, 28] | ISC [28] | One global tempo; no downbeats. Already in the fleet (`mixing.audio.beat_grid`). |
| Beats + downbeats + **sections with labels** | **allin1** (Kim & Nam, WASPAA 2023) [29] | MIT, but it **imports madmom's DBN** for metrical post-processing (in `postprocessing/metrical.py`, which loads `DBNDownBeatTrackingProcessor`), and it needs NATTEN plus Demucs | Gives intro/verse/chorus/bridge/outro. Use its sections, and take beats from Beat This! if licence-clean output matters. |
| Sections (2025) | **SongFormer** [30] | CC BY 4.0 (repository LICENSE) | Newer SOTA structure analyser; AutoMV uses it [12]. |
| Sections (classical) | MSAF (Foote novelty, spectral clustering, etc.) [31] | MIT | No deep model; robust fallback. |
| Onset strength / novelty / RMS energy / spectral flux | librosa [28] | ISC | The continuous "music saliency" Ω(t) in Audeosynth terms. |
| Valence/arousal, mood | Essentia models (DEAM/emoMusic/MuSe regressors; happy/sad/aggressive/relaxed/party classifiers) [32, 33] | **CC BY-NC-SA 4.0**, proprietary licence on request [32] | Fine for research and personal use; blocks commercial use unless licensed. Essentia itself is AGPL-3.0. |
| General audio embedding | LAION CLAP [34] | CC0 repository | For song↔image semantic matching and the BEAT-style MuVA idea [14]. |

*What this means for v1:* use Beat This! for beats and downbeats, librosa for the onset-strength and RMS envelopes, and SongFormer or MSAF for sections. That stack is commercially clean end to end. Build a **cut-candidate score** c(t) = w_b·beat + w_d·downbeat + w_s·section-boundary + w_o·onset-strength, then let the optimiser choose a subset. *Later:* local tempo curves (Beat This! is DBN-free, so it already follows tempo drift), per-section energy profiles, and VA curves (with licensed models, or an in-house regressor on CLAP embeddings).

## 3. Visual-side features

| Channel | Tool | Licence | Use |
|---|---|---|---|
| Shot boundaries inside a clip | **TransNetV2** [35] | MIT | A personal clip can contain in-camera cuts; never let a montage segment straddle one. |
| | PySceneDetect (content/adaptive/hash detectors) [36] | BSD-3 | Cheap CPU fallback. |
| Dense motion | OpenCV Farneback (used by visbeat [7]); **RAFT** [37] | BSD-3 (RAFT) | Motion magnitude → "energy" match; directional flux → visual impact envelope (§1.2). |
| Visual beats / impact envelope | Reimplement Davis & Agrawala [6] | (do not reuse visbeat code [7]) | Accent points for snapping and speed-ramps. |
| Camera motion vs subject motion | Global affine/homography fit on flow; residual = subject motion | — | Pans read as calm and shakes read as bad; subject motion gives the accents. |
| Technical quality | Laplacian-variance blur, exposure histogram, shake (already in `muvid.footage.scoring.quality`) | Apache-2.0 (OpenCV, MediaPipe) | Hard floors for unusable frames. |
| Aesthetics | **LAION aesthetic predictor** (MLP on CLIP ViT-L/14) [38] | Apache-2.0 | Ranking stills and keyframes, 0–10. |
| | NIMA [39] | Paper; Apache-2.0 reimplementation by idealo | Distribution-of-scores alternative. |
| | Q-Align (LMM; quality, aesthetics, video quality) [40] | **S-Lab licence, non-commercial** | Research only. |
| | pyiqa / IQA-PyTorch toolbox [41] | **PolyForm Noncommercial** | Research only, despite its convenience. |
| Faces / people | MediaPipe, InsightFace or deepface; Apple notes faces dominate saliency [15] | Varies (check model weights) | Selection prior; Ken Burns targets. |
| Saliency | `burns.salient_box` (in-fleet) | — | Ken Burns start and end boxes; Photo2Video's idea [3]. |
| Semantics, grouping, diversity | open_clip / CLIP embeddings [42] | MIT-style | Clustering into "moments", a diversity penalty between neighbours (BEAT uses cosine > 0.8 exclusion [14]), and text queries ("the beach part"). |
| Near-duplicates | imagededup (pHash, CNN) [43] | Apache-2.0 | Burst shots and Live Photo stills; Apple removes "similar photos" first [15]. |
| Highlights | Moment-DETR / QVHighlights (query-conditioned saliency per clip) [44] | MIT | Later: "best 3 s of this clip", optionally conditioned on a text intent. |
| Image affect | EmoSet (3.3M images, 118k labelled with emotion plus attributes such as brightness, colourfulness, facial expression) [45] | Dataset licence (research) | Later: VA per still or keyframe for mood matching [23, 24]. |

*What this means for v1:* four channels are enough. They are **quality** (gate), **motion magnitude** with camera/subject split (energy match), **CLIP embedding** (diversity and grouping), and **faces/saliency** (selection prior and Ken Burns targets). Add **shot boundaries** as a hard constraint. Visual-impact accents, aesthetics, highlights and affect come later as additional cost terms. Each is just another per-segment feature plugged into the same DP.

## 4. Alignment and optimisation formulations

### 4.1 The canonical decomposition

Let the song give a ranked candidate-cut set T = {t₀ < t₁ < … < t_N} with strengths c(tᵢ). Let the pool give candidate segments s = (clip, in, out) with features. The edit is a path that chooses a subset of cut points and assigns a segment to each slot [t_a, t_b). Its total cost is

E = Σ_slots [ λ_cut·(−c(t_a)) + λ_dur·D(t_b−t_a | section, tempo) + λ_fit·F(s, slot) ] + Σ_adjacent [ λ_div·sim(s, s′) + λ_chron·chrono_violation(s, s′) + λ_trans·T(s, s′, music change) ] + global terms (coverage, reuse limits).

- **Cut reward** −c(t): prefer downbeats and section boundaries. Add BEAT's energy-adaptive bonus so loud passages cut more often [14].
- **Duration prior** D: a log-normal on slot length measured in *beats*, with a section-dependent mean (for example, verse 4–8 beats, chorus 2–4). This encodes "cut on a subset of beats", which AutoMatch observed in professional edits [9].
- **Fit** F: the motion-energy vs music-energy mismatch (Audeosynth's pace↔peak-frequency term [5]; CMT's speed↔density pairing [17]), quality, and accent alignment (§4.3).
- **Transition** T: Audeosynth's pace-change↔velocity-change and track-count↔dynamism-change terms [5]. A section change should bring a visual change (new moment cluster), and a within-section cut should keep continuity.
- **Diversity / chronology:** CLIP cosine penalty between neighbours [14]. EXIF time order as a soft prior (holiday montages read better chronologically).

### 4.2 Solvers

- **DP / Viterbi.** If the pairwise terms involve only adjacent slots, E is chain-structured. A Viterbi pass over states (cut index, segment-or-cluster id) is exact and runs in O(N·K²). With N ≈ 200 candidate cuts for a 3-minute song and K pruned to about 50 candidates per slot, it runs in milliseconds to seconds. The "each clip used at most once" constraint breaks exactness. Standard fixes are beam search (BEAT's Bar-DP keeps a used set in the state [14]), Lagrangian penalties, or a greedy repair pass.
- **Two-stage factorisation (Audeosynth).** Precompute the best (in, out, rate) of every clip for every slot length, then run the global assignment [5]. This is the key to speed, and it lets F be arbitrarily expensive.
- **MCMC** [5] is only needed when higher-order or global terms dominate. It is not needed for v1.
- **LLM in the loop:** the LLM proposes section-level intent (which clusters go in which section, in what order), and the DP fills the timing. This is the CutClaw/AutoMV division of labour [12, 13].

### 4.3 Making the picture land on the beat: snap, slip, ramp

A segment can be fitted to its slot in three increasingly strong ways:

1. **Slip** (choose the in-point): pick the offset so the segment's strongest visual accents fall on beats. This is a 1-D search maximising Σ w·cos(2π(t−φ)/P). The fleet already has this scorer, with a block-rotation null for confidence, in `muvid.footage.beat_fit`.
2. **Uniform rate** (0.8–1.25×): Audeosynth's scale parameter [5]; BEAT's η = 0.9 slow-motion allowance [14].
3. **Piecewise-linear time warp** (speed ramp): match visual beats to music beats by DP over monotone correspondences, penalising warp curvature. This is Davis & Agrawala [6] and Bellini et al. [8]. It is visible on people (it can look like dancing, intended or not), so cap it at about ±20% and use it on high-motion clips only.

### 4.4 Stills

Photo2Video's recipe [3] still holds: choose start and end framing from saliency or faces, choose a motion pattern, and time it to the music. Two tempo-aware refinements follow from the cost structure above. (i) A still's slot length should be an integer number of beats (typically 2–4 beats, or 1 bar), and its pan/zoom *velocity* should scale with the section's energy or tempo. That is the stills analogue of the motion↔pace term [5]. (ii) Beat-synchronous micro-accents (a small push or zoom ease landing on the downbeat) give stills the visual accents they otherwise lack. The fleet's `burns ≥ 0.0.11` sub-pixel sampling matters here, because slow pushes otherwise stutter.

## 5. Detecting that a clip does not contain the song

In montage mode the clip audio is *expected* to be unrelated. The detector exists to route a mixed pool: clips that *do* contain the song (a concert recording, someone filming the speaker) should be *sync-placed* at their true offset, and everything else should be montage-placed. The failure to avoid is a confident-looking offset on a clip that does not contain the song.

**Methods, with their decision statistics:**

- **Landmark fingerprinting** (Shazam [46]; Ellis's audfprint [47]; dejavu [48], MIT; Panako [49], AGPL-3.0, which tolerates pitch and tempo change; Chromaprint [50], LGPL-2.1 as distributed). The score is the count of hash matches in the peak bin of the time-offset histogram. Wang's prescription for the threshold is the right statistical practice: collect the score distribution for *known non-matching* items, model the maximum score among wrong candidates, and choose the threshold that meets a target false-positive rate (for example 0.1% or 0.01%) [46]. Bryan, Smaragdis & Mysore showed that landmark matching is an efficient form of cross-correlation on a non-linearly transformed signal. They used it to cluster and synchronise multi-camera recordings of the same event, with a refinement step that resolves inconsistent pairwise estimates [51]. Kennedy & Naaman used fingerprints to synchronise community concert clips [52]. *Best fit for "is the song in this clip at all"*, because it is robust to crowd noise and handles partial overlap.
- **Waveform or onset-envelope cross-correlation** (PluralEyes style; Shrestha, Barbieri & Weda, ACM MM 2007 [53]). Decision statistics are the normalised peak height, the **peak-to-sidelobe ratio** (peak minus mean of the correlation outside a ±guard window, divided by its standard deviation), and the ratio of the first to the second peak outside the guard window. Any single global xcorr peak is weak on its own: unrelated signals always produce *some* maximum.
- **Windowed consistency.** Split the clip into overlapping windows, estimate an offset per window, and require that a majority agree on the same offset (and drift). This is the most discriminative cheap test in practice. The fleet already measured it: `mixing.audio.align_clips_to_reference` returns a per-window **support** and a **margin** (tally at the chosen offset minus the best tally elsewhere). On muvid's calibration master, `margin > 0` separated 24 correct alignments from 6 pure-noise clips, and five of the six noise clips had *negative* margin. A single confidence number at a short window wrongly accepted four of the six noise clips (`t/muvid/muvid/footage/edl.py`, muvid#59/#91).
- **Chroma/CENS subsequence DTW** (Müller's FMP audio matching: CENS at 2 Hz, matching function Δ, local minima under τ = 0.2 [54]). This approach is tolerant of timbre and tempo differences. It is useful when the clip is a *cover or live version* of the song rather than a capture of the master; for a capture, fingerprinting is better.
- **Audio-visual events** (Llagostera Casanovas & Cavallaro): co-occurring sharp audio onsets and localised visual changes, plus confidence estimates that "allow automatic rejection of recordings not reliable for alignment" [55].

**Recommended practice:** (1) a cheap prefilter: if the clip's audio is mostly speech, wind or silence by an audio classifier, skip it. (2) Fingerprint-match against the song. Accept only if the matched hash count clears a threshold *calibrated on a null set of the user's own non-matching clips* (Wang's procedure [46]), *and* windowed offsets agree (support above the ballot ceiling and margin > 0, the fleet's existing gate). (3) Report "could not decide" as its own outcome, never as a pass. That matches the ecosystem rule that could-not-run is not a pass.

## 6. Recommended v1 recipe

1. **Song analysis (once, cached):** Beat This! beats and downbeats [25]; librosa onset-strength and RMS envelopes [28]; SongFormer or MSAF sections [30, 31]. Output a ranked cut-candidate list c(t) and a per-beat energy curve.
2. **Pool analysis (per item, cached by content hash):** shot boundaries (TransNetV2 or PySceneDetect) [35, 36]; per-frame quality (existing muvid quality tier); motion magnitude with camera/subject split (Farneback, every 2nd frame at 256 px); CLIP embedding per 1 s and per still [42]; faces and saliency; EXIF time; near-duplicate collapse [43]. Song-presence check (§5) routes clips to sync placement or montage placement.
3. **Candidate segments:** sliding windows inside shot boundaries, with the quality gate applied. Stills become candidates of any beat-quantised length.
4. **Assignment:** Viterbi/beam DP over cut candidates with the cost of §4.1. The terms are the cut reward (with the energy-adaptive bonus [14]), a beat-length duration prior per section, motion-energy↔music-energy fit, a CLIP diversity penalty, a soft chronology prior, and a no-reuse beam constraint.
5. **Per-slot fit:** slip-to-beat (reusing `muvid.footage.beat_fit`), optionally a uniform rate of 0.9–1.1×. Stills get beat-length Ken Burns with velocity scaled to section energy, using `burns.salient_box` targets.
6. **Render:** song audio as the master clock; frame-accurate cut quantisation (AutoMV's 1/24 s discipline [12]); hard cuts on the beat, short dissolves only at section boundaries in calm sections.
7. **Validate:** a check that cut times fall within one frame of chosen beats, a coverage check, and a duplicate check, registered on `nw.validation`.

## 7. Later extensions, mapped to the user's ideas

- **More visual channels:** the visual impact envelope [6] (accent snapping, then speed ramps [8]), aesthetics [38, 39], query-conditioned highlights [44], an object/action vocabulary from CLIP text prompts, and subject tracking for reframing.
- **More music channels:** per-instrument stems (Demucs), so a drum hit can drive cuts while vocals drive face shots; lyric word times (the fleet's `muvid.align`) for "show X when the lyric says X"; tempo-curve following.
- **Emotion/affect matching:** per-still or per-segment VA (an EmoSet-trained or CLIP-probe regressor [45]) against a per-section music VA curve (Essentia models [32] if licensed, otherwise a CLAP-probe regressor), added as a fit term. A shared-space model in the style of IMEMNet or MMVA can follow [23, 24].
- **Choosing the best song among several:** score each candidate song by the optimum value of the same DP (how well the pool *can* fill it), plus a global pool↔song affinity from cross-modal retrieval (MVPt-style [22], or CLAP↔CLIP similarity). The DP optimum is the honest, explainable signal.
- **Multi-song with transitions:** concatenate songs at section boundaries with beat-matched crossfades, and partition the pool by chronology or clusters across songs. The DP runs per song with a shared no-reuse constraint.
- **Learned cut placement:** an AutoMatch-style model [9] trained on the user's accepted edits, which replaces the hand-weighted c(t).
- **LLM director:** section-level intent and ordering from captions (LAVE/CutClaw style [10, 13]), with the DP retained for all timing.

## REFERENCES

1. Foote J, Cooper M, Girgensohn A. Creating music videos using automatic media analysis. Proc. ACM Multimedia 2002. Method also disclosed in [US7027124B2, Method for automatically producing music videos](https://patents.google.com/patent/US7027124).
2. Hua X-S, Lu L, Zhang H-J. [Automatic music video generation based on temporal pattern analysis](https://www.microsoft.com/en-us/research/?p=151644). Proc. ACM Multimedia 2004.
3. Hua X-S, Lu L, Zhang H-J. [Automatically converting photographic series into video (Photo2Video)](https://www.microsoft.com/en-us/research/?p=151643). Microsoft Research.
4. Chen J-C, Chu W-T, Kuo J-H, Weng C-Y, Wu J-L. [Tiling Slideshow](https://www.cmlab.csie.ntu.edu.tw/TilingSlideshow). Proc. ACM Multimedia 2006.
5. Liao Z, Yu Y, Gong B, Cheng L. [audeosynth: music-driven video montage](https://i.cs.hku.hk/%7Eyzyu/publication/audeosynth-sig2015.pdf). ACM Trans. Graph. (SIGGRAPH) 2015;34(4).
6. Davis A, Agrawala M. [Visual Rhythm and Beat](https://openaccess.thecvf.com/content_cvpr_2018_workshops/w49/html/Davis_Visual_Rhythm_and_CVPR_2018_paper.html). CVPR Workshops / SIGGRAPH 2018.
7. Wang H (port). [visbeat3: Python 3 implementation of Visual Rhythm and Beat](https://pypi.org/project/visbeat3/0.0.5), including the Stanford non-commercial LICENSE (docket S18-164), inspected 2026-10-04.
8. Bellini R, Kleiman Y, Cohen-Or D. [Dance to the beat: synchronizing motion to audio](https://sciopen.com/article/10.1007/s41095-018-0115-y). Computational Visual Media 2018.
9. Pei S, Yu J, Chen Q, He W. [AutoMatch: a large-scale audio beat matching benchmark for boosting deep learning assistant video editing](https://arxiv.org/abs/2303.01884). arXiv 2023.
10. Wang B, Li Y, Lv Z, Xia H, Xu Y, Sodhi R. [LAVE: LLM-powered agent assistance and language augmentation for video editing](https://arxiv.org/abs/2402.10294). IUI 2024.
11. Tilekbay B, Yang S, Lewkowicz M, Suryapranata A, Kim J. [ExpressEdit: video editing with natural language and sketching](https://arxiv.org/abs/2403.17693). IUI 2024.
12. [AutoMV: an automatic multi-agent system for music video generation](https://arxiv.org/abs/2512.12196). arXiv 2025; [code](https://github.com/multimodal-art-projection/AutoMV).
13. Zhao S, Hu Y, Shan Y, Wei Y, Cun X. [CutClaw: agentic hours-long video editing via music synchronization](https://arxiv.org/abs/2603.29664). arXiv 2026; [code](https://github.com/GVCLab/CutClaw).
14. [BEAT: rhythm-elastic alignment for agentic music-guided movie trailer generation](https://arxiv.org/abs/2605.27067). arXiv 2026.
15. Apple. [Photos: private, on-device technologies to browse and edit photos and videos (technical brief)](https://apple.com/ph/ios/photos/pdf/Photos_Tech_Brief_Sept_2019.pdf). September 2019.
16. TechCrunch. [Google Photos can now automatically create movies around themes](https://techcrunch.com/2016/09/19/google-photos-can-now-automatically-create-movies-around-themes-makes-sharing-easier). 2016.
17. Di S, et al. [Video background music generation with controllable music transformer](https://arxiv.org/abs/2111.08380). ACM Multimedia 2021.
18. Su K, et al. [V2Meow: meowing to the visual beat via video-to-music generation](https://ojs.aaai.org/index.php/AAAI/article/view/28299). AAAI 2024.
19. Tian Z, et al. [VidMuse: a simple video-to-music generation framework with long-short-term modeling](https://arxiv.org/abs/2406.04321). arXiv 2024.
20. Hong S, Im W, Yang HS. [Content-based video–music retrieval using soft intra-modal structure constraint](https://arxiv.org/abs/1704.06761). arXiv 2017 / ICMR 2018.
21. Prétet L, Richard G, Peeters G. [Cross-modal music-video recommendation: a study of design choices](https://researchportal.ip-paris.fr/fr/publications/cross-modal-music-video-recommendation-a-study-of-design-choices/). IJCNN 2021.
22. Surís D, Vondrick C, Russell B, Salamon J. [It's time for artistic correspondence in music and video](https://arxiv.org/abs/2206.07148). CVPR 2022.
23. Zhao S, et al. [Emotion-based end-to-end matching between image and music in valence-arousal space](https://arxiv.org/abs/2009.05103). ACM Multimedia 2020.
24. [MMVA: multimodal matching based on valence and arousal across images, music, and musical captions](https://arxiv.org/abs/2501.01094). arXiv 2025.
25. Foscarin F, Schlüter J, Widmer G. [Beat this! Accurate beat tracking without DBN postprocessing](https://arxiv.org/abs/2407.21658). ISMIR 2024; [code and weights, MIT](https://github.com/CPJKU/beat_this).
26. Böck S, et al. [madmom](https://pypi.org/project/madmom). Code BSD; model and data files CC BY-NC-SA 4.0.
27. Ellis DPW. [Beat tracking by dynamic programming](https://www.ee.columbia.edu/~dpwe/LabROSA/projects/beattrack/). J. New Music Research 2007;36(1):51–60.
28. McFee B, et al. [librosa](https://github.com/librosa/librosa) (ISC).
29. Kim T, Nam J. [All-in-one metrical and functional structure analysis with neighborhood attentions on demixed audio (allin1)](https://pypi.org/project/allin1/). WASPAA 2023; [code](https://github.com/mir-aidj/all-in-one).
30. [SongFormer: scaling music structure analysis with heterogeneous supervision](https://arxiv.org/abs/2510.02797). arXiv 2025; [code, CC BY 4.0](https://github.com/ASLP-lab/SongFormer).
31. Nieto O, Bello JP. [MSAF: Music Structure Analysis Framework](https://github.com/urinieto/msaf) (MIT).
32. MTG-UPF. [Essentia models](https://essentia.upf.edu/models.html) (CC BY-NC-SA 4.0; proprietary licence on request).
33. Aljanaki A, Yang Y-H, Soleymani M. [DEAM: MediaEval Database for Emotional Analysis in Music](https://cvml.unige.ch/databases/DEAM/).
34. LAION. [CLAP: contrastive language-audio pretraining](https://github.com/LAION-AI/CLAP) (CC0 repository).
35. Souček T, Lokoč J. [TransNet V2: an effective deep network architecture for fast shot transition detection](https://arxiv.org/abs/2008.04838); [code, MIT](https://github.com/soCzech/TransNetV2).
36. Castellano B. [PySceneDetect](https://www.scenedetect.com/docs/api.html) (BSD-3).
37. Teed Z, Deng J. [RAFT: recurrent all-pairs field transforms for optical flow](https://github.com/princeton-vl/RAFT). ECCV 2020 (BSD-3).
38. LAION. [LAION-Aesthetics](https://laion.ai/blog/laion-aesthetics/); [improved-aesthetic-predictor](https://github.com/christophschuhmann/improved-aesthetic-predictor) (Apache-2.0).
39. Talebi H, Milanfar P. [NIMA: Neural Image Assessment](https://arxiv.org/abs/1709.05424). IEEE TIP 2018; [idealo implementation](https://github.com/idealo/image-quality-assessment) (Apache-2.0).
40. Wu H, et al. [Q-Align: teaching LMMs for visual scoring via discrete text-defined levels](https://arxiv.org/abs/2312.17090). ICML 2024; [code, S-Lab non-commercial licence](https://github.com/Q-Future/Q-Align).
41. Chen C. [IQA-PyTorch (pyiqa)](https://github.com/chaofengc/IQA-PyTorch) (PolyForm Noncommercial 1.0.0).
42. Ilharco G, et al. [OpenCLIP](https://github.com/mlfoundations/open_clip).
43. idealo. [imagededup](https://github.com/idealo/imagededup) (Apache-2.0).
44. Lei J, Berg TL, Bansal M. [Detecting moments and highlights in videos via natural language queries (QVHighlights, Moment-DETR)](https://arxiv.org/abs/2107.09609). NeurIPS 2021; [code, MIT](https://github.com/jayleicn/moment_detr).
45. Yang J, et al. [EmoSet: a large-scale visual emotion dataset with rich attributes](https://openaccess.thecvf.com/content/ICCV2023/html/Yang_EmoSet_A_Large-scale_Visual_Emotion_Dataset_with_Rich_Attributes_ICCV_2023_paper.html). ICCV 2023.
46. Wang A. [An industrial-strength audio search algorithm](https://www.princeton.edu/~cuff/ele201/files/Wang03-shazam.pdf). ISMIR 2003.
47. Ellis DPW. [Robust landmark-based audio fingerprinting](https://www.ee.columbia.edu/~dpwe/LabROSA/matlab/fingerprint/).
48. Drevo W. [dejavu: audio fingerprinting and recognition in Python](https://github.com/worldveil/dejavu) (MIT).
49. Six J. [Panako: acoustic fingerprinting robust to pitch shift and time stretch](https://github.com/JorenSix/Panako) (AGPL-3.0).
50. AcoustID. [Chromaprint](https://github.com/acoustid/chromaprint) (MIT source; LGPL-2.1 as a whole due to bundled FFmpeg code).
51. Bryan NJ, Smaragdis P, Mysore GJ. [Clustering and synchronizing multi-camera video via landmark cross-correlation](https://research.adobe.com/publication/clustering-and-synchronizing-multi-camera-video-via-landmark-cross-correlation). ICASSP 2012.
52. Kennedy L, Naaman M. [Less talk, more rock: automated organization of community-contributed collections of concert videos](https://web-archive.southampton.ac.uk/www2009.eprints.org/32/index.html). WWW 2009.
53. Shrestha P, Barbieri M, Weda H. [Synchronization of multi-camera video recordings based on audio](https://scholar.google.com/scholar?q=%22Synchronization+of+multi-camera+video+recordings+based+on+audio%22). Proc. ACM Multimedia 2007:545–548.
54. Müller M. [FMP notebooks C7S2: audio matching (CENS + subsequence DTW)](https://www.audiolabs-erlangen.de/resources/MIR/FMP/C7/C7S2_AudioMatching.html). AudioLabs Erlangen.
55. Llagostera Casanovas A, Cavallaro A. [Audio-visual events for multi-camera synchronization](https://graphsearch.epfl.ch/en/publication/146437). Multimedia Tools and Applications 2015.
