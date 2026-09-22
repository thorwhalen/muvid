# muvid.footage.scoring

Footage scoring — per-clip score tracks on the shared song-time grid (thorwhalen/muvid#13).

Resolve every clip’s every metric onto ONE fixed-rate song-time grid → a tensor
`S[clip, frame, metric]` that BOTH the auto composer (the `weighted` Viterbi selector in
[`muvid.footage.select_score`](muvid.footage.select_score.html.md#module-muvid.footage.select_score)) and the Phase-2 multichannel editor read. This subpackage
holds the grid/normalization data model ([`grid`](muvid.footage.scoring.grid.html.md#module-muvid.footage.scoring.grid)), the per-metric
extractors, and the orchestrator that runs them.

**Import-safe.** This `__init__` imports nothing heavy — cv2/mediapipe/librosa/torch live
behind the `muvid[scoring]` (and `muvid[scoring-lipsync]`) extras and are lazy-imported
inside function bodies. The orchestrator `score_project()` is exposed lazily so
`import muvid.footage.scoring` stays light.

See `misc/docs/footage_scoring_design.md` (LOCKED decisions) for the architecture: the
torch-free core (quality + motion-beat + segment + the selector) is the default, prod-safe
tier; the lip-sync tier (Demucs + SyncNet) is opt-in and off by default (CC-BY-NC weights +
OOM risk on the memory-fragile prod box).

### Functions

| `score_project`(project, \*[, metrics, hop_s, ...])   | Score every aligned clip of `project` → persist the tensor; return a summary dict.    |
|-------------------------------------------------------|---------------------------------------------------------------------------------------|
| `list_available_extractors`()                         | Which tiers can run here (import + weight availability) — for diagnostics / the tool. |

### Modules

| [`frames`](muvid.footage.scoring.frames.html.md#module-muvid.footage.scoring.frames)             | One decode pass per clip → the shared per-frame artifacts the quality + motion extractors both consume.                                    |
|---------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| [`grid`](muvid.footage.scoring.grid.html.md#module-muvid.footage.scoring.grid)                 | The song-time score grid: `ScoreTrack`, resample-to-grid, robust normalization, the fused `ScoreTensor`, and crash-consistent persistence. |
| [`lipsync`](muvid.footage.scoring.lipsync.html.md#module-muvid.footage.scoring.lipsync)           | Lip-sync tier (OPT-IN, OFF BY DEFAULT): SyncNet LSE-C vs the master's Demucs vocal stem.                                                   |
| [`motionbeat`](muvid.footage.scoring.motionbeat.html.md#module-muvid.footage.scoring.motionbeat)     | Motion-to-beat tier: `motion_beat_bas` + `motion_onset_xcorr` → song-grid tracks.                                                          |
| [`orchestrator`](muvid.footage.scoring.orchestrator.html.md#module-muvid.footage.scoring.orchestrator) | The scoring orchestrator: a project (song + aligned clips) → the persisted score tensor.                                                   |
| [`quality`](muvid.footage.scoring.quality.html.md#module-muvid.footage.scoring.quality)           | Quality tier: sharpness / exposure / stability_shake / face_framing → song-grid tracks.                                                    |
| [`segment`](muvid.footage.scoring.segment.html.md#module-muvid.footage.scoring.segment)           | Shot boundaries (PySceneDetect) + the per-clip coverage mask.                                                                              |
