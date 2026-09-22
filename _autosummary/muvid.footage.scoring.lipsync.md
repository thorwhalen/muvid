# muvid.footage.scoring.lipsync

Lip-sync tier (OPT-IN, OFF BY DEFAULT): SyncNet LSE-C vs the master’s Demucs vocal stem.

**Not on the prod path.** The design’s LOCKED decision: this tier is behind the
`muvid[scoring-lipsync]` extra and disabled by default because (a) Demucs + SyncNet on CPU
peak ~2–3 GB and would OOM the memory-fragile connector, and (b) the htdemucs weights are
**CC-BY-NC (research-only)** — not commercial-clean. So it runs only on a local/worker box the
operator opts into, and it requires the operator to POINT AT weights via env vars (rather than
this package downloading questionable weights at runtime):

- `MUVID_SYNCNET_S3FD_WEIGHTS` — the S3FD face-detector weights.
- `MUVID_SYNCNET_WEIGHTS` — the SyncNet model weights.

If either is unset, or `demucs`/`syncnet-python` is not installed, the extractor is
**skipped** (returns `[]` + a reason) — never a crash, never a silent 0.

Pipeline (design §3c): separate the master vocal stem ONCE (orchestrator), then per clip run
SyncNet’s face-detect → track → mouth-crop → per-window LSE-C \*\*against the master vocal stem
at the known offset\*\* (a validation, not a search). Emit `lip_sync_lse_c` + `lse_d_offset`,
gated to NA where no singing face is present (never 0).

⚠ This module’s heavy glue (Demucs + SyncNetPipeline) cannot run in CI or on the dev box
(deps absent) — it needs a live validation pass on a machine with the extra + weights before
first real use. It is structured to fail safe (skip) everywhere else.

### Functions

| [`lipsync_available`](#muvid.footage.scoring.lipsync.lipsync_available)()                                | `(ok, reason)` — whether the opt-in lip-sync tier can run here.                 |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| [`lipsync_tracks`](#muvid.footage.scoring.lipsync.lipsync_tracks)(clip_path, \*, clip_id, ...[, ...]) | Per-clip LSE-C / LSE-D tracks vs the OFFSET-ALIGNED master vocal stem, or `[]`. |
| [`separate_master_vocals`](#muvid.footage.scoring.lipsync.separate_master_vocals)(song_path, \*, out_dir)     | Demucs → the master vocal stem as a wav (`out_dir/vocals.wav`), or `None`.      |

### muvid.footage.scoring.lipsync.lipsync_available()

`(ok, reason)` — whether the opt-in lip-sync tier can run here.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`bool`](https://docs.python.org/3/builtins/functions.html#bool), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.footage.scoring.lipsync.lipsync_tracks(clip_path, , clip_id, offset_s, duration_s, coverage, vocal_stem_path, t0, hop_s, n, device='cpu')

Per-clip LSE-C / LSE-D tracks vs the OFFSET-ALIGNED master vocal stem, or `[]`.

Feeds SyncNet the co-temporal slice of the master vocals (`[offset_s, offset_s+dur]`) so
the score is meaningful for clips whose offset exceeds SyncNet’s tiny internal search;
holds the per-face-track LSE-C over that track’s span, clamped to the clip’s coverage
(never fabricates lip-sync beyond the clip); spans with no detected face are NA.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ScoreTrack`](muvid.footage.scoring.grid.md#muvid.footage.scoring.grid.ScoreTrack)]

### muvid.footage.scoring.lipsync.separate_master_vocals(song_path, , out_dir)

Demucs → the master vocal stem as a wav (`out_dir/vocals.wav`), or `None`.

Computed ONCE on the clean master; the resulting stem is passed to every clip’s SyncNet
call as the co-temporal reference audio. Returns `None` (never raises) if Demucs is
absent — the caller then skips lip-sync.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)
