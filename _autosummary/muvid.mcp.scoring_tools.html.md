# muvid.mcp.scoring_tools

MCP tools for the footage SCORING layer (thorwhalen/muvid#13).

A background scoring job (via `nw.jobs` — the federation’s durable/cancellable async
facade, reused rather than a second system) computes per-clip score tracks; the editor +
`assemble_music_video(strategy='weighted')` read them. All FREE (no AI/keys).

Key design decisions (LOCKED, see `misc/docs/footage_scoring_design.md`):

- **Scoring is keyed on INPUTS ONLY** (`song_hash` + an alignment fingerprint + the metric
  set + hop). Weights/preset are NOT here — they enter at `assemble` time, so ONE tensor is
  reused across every preset for free, and a re-align mid-flight yields a NEW job (not a dedup
  to the stale run).
- **Bounded long-poll** status so an agent needs ~1 poll, not ~15.
- **NaN never hits the wire** — masked entries serialize as `null`; the `mask` array is
  authoritative.

### Functions

| [`footage_score_status`](#muvid.mcp.scoring_tools.footage_score_status)(project_id, \*[, ...])     | The scoring job's status (bounded long-poll).                                    |
|--------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`footage_scores`](#muvid.mcp.scoring_tools.footage_scores)(project_id, \*[, clip_id, ...])  | The persisted score tracks — for the multichannel editor + inspection.           |
| [`score_footage`](#muvid.mcp.scoring_tools.score_footage)(project_id, \*[, hop_s, metrics]) | Kick a BACKGROUND job that scores every aligned clip (quality + motion-to-beat). |

### muvid.mcp.scoring_tools.footage_score_status(project_id, , job_id='', wait_s=0)

The scoring job’s status (bounded long-poll). Free.

Pass the `job_id` from `score_footage` (or omit for the newest scoring job). With
`wait_s` > 0 this blocks up to ~\`\`wait_s\`\` seconds (capped), returning early on a
terminal state — so an agent needs ~1 poll, not many.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.scoring_tools.footage_scores(project_id, , clip_id='', metrics=None, max_points=1500)

The persisted score tracks — for the multichannel editor + inspection. Free.

- No `clip_id` → a SUMMARY (metrics, per-clip coverage %, beats, tempo, the decimated
  `selection_margin`, grid geometry) — bounded, safe as the default.
- `clip_id` → that clip’s tracks (values as `null`-masked arrays, decimated to
  `max_points` per metric), for the editor’s lanes.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.scoring_tools.score_footage(project_id, , hop_s=0.1, metrics=None)

Kick a BACKGROUND job that scores every aligned clip (quality + motion-to-beat). Free.

Returns immediately with a `job_id`; poll `footage_score_status`. Scoring extracts ALL
core metrics (re-weight later at assemble time — no re-scoring needed). Requires a song +
a run of `align_footage` first. The heavy lip-sync tier is OFF by default (opt-in,
off-prod).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
