# muvid — agent & contributor guide

`muvid` makes music videos. It is **three parts**, not two — the "two independent
halves" line in `README.md` and `muvid/__init__.py` predates the footage genre and is
stale. Read this table before touching anything:

| # | part | entry points | state | status |
|---|---|---|---|---|
| 1 | **Visualizer** — ffmpeg-only, deterministic, no AI, no network | `muvid/visualize/`, nw genre `music-visualizer` (`muvid/genre.py`) | stateless | shipped; `yb` publishes through it |
| 2 | **Footage assembly** — align N phone recordings of ONE song, score, select, EDL, assemble | `muvid/footage/`, `muvid/genre_music_video.py`, `muvid/mcp/footage_tools.py` + `scoring_tools.py` | **stateful, on disk** | shipped; **the active workstream**; serves live connector traffic |
| 3 | **Full-AI narrative pipeline** — transcribe → align → cast → environments → script → render → compose | `muvid/facade.py`, `muvid/renderers/`, `muvid/lyrics.py`, `muvid/align.py`, `muvid/script.py`, `muvid/compose.py` | project folder | works end to end; **not** an nw genre; the shrink-toward-nw candidate (issue #4) |

Parts 1 and 2 are registered nw genres served over MCP. Part 3 is standalone (CLI +
`.claude/skills/muvid/` + the local FastAPI UI).

## Start here (AI-first)

Skills in `.claude/skills/` (there are no agents in this repo):

- [`muvid`](skills/muvid/SKILL.md) — drives **part 3**, the eight-stage narrative
  pipeline, interactively.
- [`muvid-visualize`](skills/muvid-visualize/SKILL.md) — **part 1**: render/tune a
  visualizer, add a visual, verify a render.
- [`muvid-choose-footage-segments`](skills/muvid-choose-footage-segments/SKILL.md) —
  **part 2**: the score MENU, the song-time data model, the two-consumer principle,
  the selection strategy. Read this first for footage work.
- [`muvid-score-footage`](skills/muvid-score-footage/SKILL.md) — **part 2**: the
  per-metric scoring recipes and the licence boundary.

The rest of this file is the contract those skills rely on.

## Part 2 — the footage genre (where the work is)

One fixed clean song + N clips, each a *different-device* recording of that same song.
Pipeline: `align → score → select → EDL → assemble`.

| module | owns |
|---|---|
| `muvid/footage/align.py` | thin over `mixing.audio.align_clips_to_reference`; per-clip `offset_s`, `confidence`, `coverage`, `overlaps` |
| `muvid/footage/edl.py` | `FootageAlignment`, `EdlEntry`, `AssemblyCut`; `fill_gaps`, **`validate_edl`** (the ONE gate), `derive_cuts` (the ONE place `clip_in = song_start - offset_s` is derived) |
| `muvid/footage/strategy.py` | the `SelectionStrategy` registry — `best_confidence` (`DEFAULT_STRATEGY`), `longest_take`, `fewest_cuts`, plus `weighted` registered **lazily** so `import muvid.footage` stays numpy-free |
| `muvid/footage/select_score.py` | the `weighted` strategy: beat-snapped semi-Markov Viterbi over the score tensor |
| `muvid/footage/scoring/` | `grid.py` (`ScoreTrack`/`ScoreTensor` + resample + robust-normalize + `.npz` persistence), `frames.py` (ONE decode pass per clip), `quality.py`, `motionbeat.py`, `segment.py`, `lipsync.py`, `orchestrator.py` (`score_project`) |
| `muvid/footage/assemble.py` | three bounded stages (one ffmpeg per cut → concat demuxer stream-copy → mux) — memory O(1) in cut count |
| `muvid/footage/workspace.py` | `FootageWorkspace` / `MusicVideoFootageProject` — the stateful on-disk project |
| `muvid/footage/lacing_bridge.py` | project ⇄ lacing standoff annotations for the multichannel editor |
| `muvid/downloads.py` | `claim()` / `resolve()` — muvid owns *resolution*, the connector owns *transport* |

**Invariants that already cost us a bug each — do not relax them:**

- **A source is never removed by a measurement.** A clip that does not overlap the song
  is recorded with `overlaps=False`, never omitted (`footage/align.py`, `edl.py:40-43`).
- **`validate_edl` is the single gate.** Both the explicit-`edl` path and every strategy
  pass through it before any cutting. Never validate somewhere else.
- **A hole is an explicit gap entry, not an absence.** `EdlEntry.clip_id == ""`
  (`null` over JSON) is a gap; `fill_gaps` makes the edit span `[0, song_duration]`.
  An *implicit* hole is an error.
- **Gate, don't zero.** "no face / no data" is a `mask`=NA, never a 0 score.
- **Compute once on the master.** Beat grid and vocal stem come from the clean song and
  map to each clip through its offset. Never per clip.
- **Scoring is keyed on INPUTS ONLY** — `song_hash` + alignment fingerprint + metric set
  + hop. Weights enter at *assemble* time, so one tensor serves every preset.

## The slug trap (issue #4) — read before renaming anything

`music_video` is **taken by the footage genre** and is a **persisted on-disk path
segment**. Three facts, each verified:

1. `muvid/genre_music_video.py:20` sets `MUSIC_VIDEO_SLUG = "music_video"` and line 38
   claims it via `register_genre(...)`.
2. `nw.genres` is `Registry(name="nw.genres", on_conflict="error")` (`nw/genres.py:289`)
   and `nw.register_genre` has **no** `replace=`/`force=`. Re-registering the slug raises
   at import time.
3. The slug is a directory name, not just an identifier:
   `{root}/music_video/projects/{email}/{project_id}/manifest.json`
   (`muvid/footage/workspace.py:11`, built at line 250). It is also the value callers
   pass to `create_project(genre='music_video')` on the deployed connector.

So renaming it is **a data migration plus a connector-contract break**, not a rename.

**When issue #4 lands, the full-AI pipeline gets its OWN slug.** The recommended one,
per #4's own restatement, is **`music-video-ai`** — it reads as a sibling of the other
two and names the thing that distinguishes it (generation, keys, cost). Do not attempt
to reuse or reclaim `music_video`. Note the two existing slugs disagree on separator
(`music-visualizer` hyphen vs `music_video` underscore); pick knowingly for the new one,
and leave both existing ones alone for the reason above.

Also from #4: the new genre must set `cost_profile` **honestly**. The two existing
genres are `cost_profile=None` (genuinely free). An unknown cost must force approval and
must never encode as `0.0`.

## Licence boundary — the scoring tiers

Two tiers, and the line between them is a **licensing** line, not a performance one.

| tier | extra | contents | licence | prod? |
|---|---|---|---|---|
| **core** | `muvid[scoring]` (+ optional `muvid[scoring-shots]`) | quality gates, motion-to-beat, segment boundaries, the Viterbi selector, editor score-tracks | MIT / BSD / Apache / ISC only | **yes** — this is what the connector installs |
| **lip-sync** | `muvid[scoring-lipsync]` | Demucs vocal separation + SyncNet LSE-C | **htdemucs weights are CC-BY-NC — research-only, NOT commercial-clean** | **no** |

Rules:

- The lip-sync tier is **off by default** and **off-prod**. Two independent reasons:
  the CC-BY-NC weights, and Demucs+SyncNet peaking ~2–3 GB on CPU (OOM on the
  memory-fragile box).
- Enabling it takes **three** things, all opt-in:
  `pip install 'muvid[scoring-lipsync]'`, `MUVID_SCORING_ENABLE_LIPSYNC=1`
  (`footage/scoring/orchestrator.py:52`), **and** operator-supplied weights at
  `MUVID_SYNCNET_S3FD_WEIGHTS` + `MUVID_SYNCNET_WEIGHTS`
  (`footage/scoring/lipsync.py:51-54`). muvid **never downloads weights at runtime**.
- Missing any of the three → `lipsync_available()` returns `(False, reason)` and the
  extractor **skips** (returns `[]`) — never a crash, never a silent 0.
- **Do not add** to the core tier: `madmom` (academic-licensed beat models — dropped;
  librosa via `mixing[beats]` is the sole beat backend), Wav2Lip expert weights,
  `pyiqa` NIMA/MUSIQ/TOPIQ, DOVER, VocaLiST. All non-commercial.
- A new core-tier dependency must be MIT/BSD/Apache/ISC. If it is not, it belongs behind
  its own opt-in extra with the same three-gate treatment, and the reason goes in the
  module docstring.

## Cross-package duties

### It is an nw plugin

`muvid/genre.py` registers `music-visualizer`; line 37 imports `muvid.genre_music_video`
so **one** `import muvid.genre` surfaces both genres and both project factories.

- Both genre modules must stay **import-safe**: `nw` at module top, everything heavy
  (workspace, align, assemble, numpy, fastmcp) lazy-imported inside function bodies.
  A host imports these to build a catalog; it must not pay for the engine.
- Both genres are deliberately **engine-less at the nw level** — `transform_names=()`,
  `strategy_names=()`, `projection_entrypoint=None`. That combination is what makes
  `is_ready()` True for an `available` genre. Listing muvid's internal strategies in
  `strategy_names` is a wiring bug, not a feature.
- `muvid` CORE never imports `nw`. The floor is pinned in the `[mcp]` extra
  (`nw>=0.0.15`, which is where `register_genre_project_factory` exists — the import
  doubles as the version guard).
- `muvid/mcp/scoring_tools.py:80` uses `nw.jobs` as the durable/cancellable async job
  substrate. Reuse it; do not grow a second job system.

### It ships three lacing body schemas — and what that does NOT mean

`muvid/footage/lacing_bridge.py:34-36` is the SSOT for the names:

```python
CLIP_ALIGNMENT_SCHEMA = "annot://schema/clip-alignment/v1"
CLIP_SCORE_TRACK_SCHEMA = "annot://schema/clip-score-track/v1"
MUSIC_VIDEO_EDL_SCHEMA = "annot://schema/music-video-edl/v1"
```

**Correct these three facts before reasoning about them; an earlier version of this
section got them wrong and the error propagated into issue #34, whose body and its
comment both mis-scoped a one-day change as a federation migration:**

1. **These URIs are bare string literals.** `lacing.schema.register_body_schema` is
   called nowhere in muvid; `lacing/bodies/` registers five schemas and none is
   muvid's. There is no registration, no migration ladder, and no version to bump.
2. **Nothing validates these bodies.** `lacing.schema.validate` is never called from
   muvid, `Annotation.body` is a free `dict` whose only validator requires string keys,
   and no lacing store consults the body registry. A body carrying an unknown key is
   accepted today.
3. **Adding a field to `EdlEntry` does NOT change the body.** `edl_annotations`
   serialises `{"clip_id": ...}` and nothing else — `song_start`/`song_end` live on the
   `MediaRef` interval. A field reaches the body only if someone puts it there.

So the compatibility surface is **not** a schema version. It is two concrete things:

- **`renders/{render_id}/meta.json["edl"]`** — the only place an EDL is persisted
  (`workspace.py`'s `write_render_meta`, fed by `footage_tools._edl_json`). `_as_entry`
  reads three keys by name and ignores the rest, so an old build reading a newer record
  renders hard cuts — degraded, never wrong, in both directions.
- **The live MCP wire.** `footage_editor_document` and `footage_edl_from_annotations` are
  registered tools, and the deployed reelee connector installs `lacing` as a *top-level*
  requirement — so the `editor`-extra guard succeeds in production and these bodies cross
  a real multi-tenant surface. reelee-web#203's editor is still unimplemented, so there
  is no browser consumer yet; the connector is the surface that matters.

The rule for adding a field, therefore, is not "bump the schema" but **omit-when-None +
read-by-name**: emit the key only when it is set (so every existing document and body
stays byte-identical) and read it defensively.

**Emitting is the half that gets forgotten, so it is now a table rather than an `if`.**
`transition` was hand-written into `footage_tools._edl_json` and survived; `crop`,
`crop_end` and `look` were each added to `EdlEntry` and *not* — so a caller's framing
and grade were accepted, honoured by the renderer, and absent from both the returned
`edl` and the `meta.json` beside the file they styled, while the tool's own note says
that list "must feed straight back as the edl= argument and reproduce the same render".
`_EDL_OPTIONAL_FIELDS` is the emit table and `lacing_bridge._edl_body` is its editor
twin; the guard is parameterised over `EdlEntry`'s **own dataclass fields** (never over
the tables, which are the things that forget) plus a literal list, so a new field fails
the suite until someone classifies it. **The rule is omit-when-ABSENT, not
omit-when-`None`** — `look_time_varying` (muvid#73) is a `bool` whose absent value is
`False`, and `False is not None`, so the emit table carries the absent value as a column
rather than testing `is not None`. Getting that wrong puts the key in every record ever
written, for a field almost none of them use; it is measured rather than reasoned about
(render the corpus under both revisions and diff the JSON). `EdlEntry.transition`
(muvid#34) is the worked example, and the two read postures there are deliberately opposite —
`edl.py`'s `_as_entry` **raises** on a malformed transition because it reads a caller's
*request*, while `edl_from_annotations` **skips** because it reads a browser's *output*.

- `edl_from_annotations` treats annotations as **untrusted editor input** — anything
  shaped wrong is skipped, never crashed on. Keep it that way.
- Round-trip is a contract: EDL → annotations → EDL must be identity — which is *why*
  a new `EdlEntry` field has to reach the body. A field the bridge does not carry is a
  field the editor silently DROPS on the way back. Times quantize at
  `TIME_RATE = 1_000_000` (µs), far finer than `validate_edl`'s 1 ms tolerance, so
  annotate → edit → export → render reproduces the same cuts. Do not coarsen it.
- Score arrays are inlined today. Moving them behind a `ContentRef` is a body-schema
  major bump, not a quiet optimisation.

### It depends on `mixing`

Core dependency, floor `mixing>=0.0.34` (below that, `align_clips_to_reference` scores on
the raw waveform instead of the onset envelope and **every** clip of a real multi-device
shoot falls under the confidence gate — muvid#15). `mixing[beats]>=0.0.30` supplies
`beat_grid` in the `scoring` extra.

**Never wrap a `mixing` call in a broad `except`.** A `mixing` regression that reaches
muvid must surface as a traceback, not as a silent fallback: muvid#15 (confidence measured
on the wrong feature) and mixing#25 (confidence halved by the decode path) were both
*scoring* bugs whose only symptom was a plausible-looking number. A swallowed exception
turns that class of bug into "the pipeline quietly produced nothing".

- `muvid/footage/align.py:34` is the model: lazy import, no try/except, let it raise.
- **The rule has a live scar.** `trim_video_to_duration` in
  `muvid/renderers/_common.py` used to wrap `from mixing.video import Video` and the
  whole trim/pad body in `except Exception:` and `shutil.copy2` the untrimmed source —
  so any `mixing` failure returned a video at the *source's* length under a filename
  promising `target_s`, reported as success. Nothing downstream re-measures a shot
  (`facade.status` reports the *spec's* `duration_s`; `verify_video` only sees the
  composed master), so the drift only surfaced once the whole video was out of sync
  with the song. That is how an 8s clip stayed 5.87s for months. Removed in muvid#38,
  with regression tests. Do not reintroduce the shape.
- Where a broad except is genuinely correct (an un-probeable song, an unreadable
  intermediate), it carries `# noqa: BLE001` **and a reason** — see
  `footage/assemble.py:158,181`. That is the bar.

### It bridges to `an` for animation

`muvid/renderers/animation.py` hands a synthesized scene to `an.orchestrate`, passing an
`an.audio.WordTimingsLipSync` built from muvid's own `lacing` alignment store so `an`
does **not** re-transcribe the same audio with whisper — muvid owns the word-timing SSOT.
`an` is **not** a declared dependency in any extra; the import is soft and falls back to
the `still` strategy when `an` is unusable.

**An `an` that never ran and an `an` that refused are different facts, and the
renderer no longer decides what to do about either.** `an` states a refusal as
*data* — `OrchestratorReport.success is False` (`an/orchestrate.py`) — not as an
exception, so swallowing it was a single `if`, and that `if` returned a still
image under the shot's own filename (muvid#46). Three things made it worse than a
missing output: the result was *wrong* rather than absent, so a freeze frame read
as a deliberate creative choice; `renderers/__init__.py` journalled
`strategy=shot.render_strategy` — the **requested** one — so the record could not
be used to find the affected shots afterwards; and `still` reaches
`falaw.generate_image`, so the degradation could **bill** while `cost.py` prices
`animation` at nothing and the budget gate had already cleared the shot at $0.00.

The split now:

- **Absent** — `an` is declared in no extra and carries no floor, so a machine
  without it is a *supported* machine. `render_animation` raises
  `RendererUnavailable`; the **dispatcher** degrades to `still`, warns, and
  journals `strategy="still"` plus `requested_strategy` and `fallback_reason`
  (present if and only if a fallback happened, so finding every degraded shot is
  a grep for one key). The fallback is caught **inside** the handler, which is
  what caps it at exactly one level — structurally, not by a check.
- **Refused** — `render_animation` raises `AnimationRenderError`, built by
  `_format_an_failure`, which reads **three** fields because no single one is
  populated in every shape: `an` has five ways to return `success=False` and two
  of them leave `error` as `None` with the only diagnosis in `verifications`.
  Reading `report.error` alone renders those two as an empty string — including
  every `LayoutLintVerifier` refusal, which is the shape muvid can actually
  provoke by synthesizing a bad `scene.md`.
- **A degraded render is not cached, and declining to write the hash is not
  enough to make that true.** `_shot_hash` is computed from the shot alone, so a
  recorded hash would make the still satisfy that shot forever — installing `an`
  later would keep returning the freeze frame without ever retrying. The hash is
  written only when the render did what was asked **and actively unlinked when it
  was not**: `--force` bypasses the cache check, so a shot that rendered once
  successfully still holds a *matching* hash, and a forced re-render after `an`
  disappeared would overwrite `output.mp4` with the freeze frame and leave that
  hash standing. Withholding closes the fresh-project case; unlinking is what
  makes the invariant unconditional.
- The import catch is `except ImportError`, never bare `Exception`. The broad
  form meant an `an` that is *installed but broken* was indistinguishable from an
  absent one and degraded just as quietly.

The same reasoning as the `mixing` rule above, and the same scar: a sibling
package's failure that returns a plausible artifact instead of surfacing is a bug
whose only symptom is a wrong output nobody re-measures.

### The budget gate is conjunctive — a number alone is not a decision

`muvid render --budget=X` refuses on **two** conditions, and the second is the one
muvid#47 was filed about. `muvid/cost.py` is careful: a price it cannot determine goes
into `_Rollup.skipped` and contributes **nothing** to `total_amount`. That is correct
arithmetic and a trap — `facade.render`'s gate compared only the number, so an
unpriceable shot read as free and cleared **any** budget.

`CostRollup.has_unknown_costs` is the flag every gate must read; it is the same
encoding as reelee's `OperationEstimate(estimated_cost_usd, has_unknown_costs)` and the
same rule: **unknown is not zero, and unknown must force approval.**

Three ways the total under-reported, all now routed through `skipped`:

- a model with no `cost_estimate`, or no model in the category (`_price_one`);
- **`falaw` not installed at all** — the worst shape, because the old code returned a
  bare `$0.00` with an **empty** `skipped`, leaving a gate nothing to fail on;
- an **`animation`** shot where `an` is absent. After muvid#46 the renderer degrades
  that to a `still`, and `still` reaches `falaw.generate_image` — so it is now priced as
  the still it will become. `cost.an_available()` is that seam, named rather than
  inlined precisely because the estimate legitimately depends on the environment (the
  *render* does), and a test must be able to pin both branches instead of the answer
  changing with the runner.

Two things adversarial review caught in the first pass at this, both worth keeping in
mind for any future gate:

- **Fail-closed must not mean fail-always.** Seeding the falaw-missing skip *before*
  reading the project could not tell "could not price things" from "there was nothing to
  price", so a project with no pending shots — or an `an`-only animation project, a
  supported state on a machine without the `ai` extra — was refused despite provably
  spending nothing. It now falls through to the normal walk with a `pick_model` that
  explains itself, so a skip is recorded per shot that would actually have reached falaw.
- **`an_available()` must ask the question the RENDERER asks.** It probes
  `an.orchestrate`, not `an`, because that is the module `renderers/animation.py`
  imports: a package that is findable but whose submodule is not importable degrades to a
  paid `still` while the estimate calls it free — the same $0.00-then-bill, one level
  down.

Two rules for anything added here:

- **A threshold is not an approval.** The gate fails closed, and `allow_unpriced=True`
  (CLI `--allow-unpriced`) is the deliberate escape — because a hard refusal with no way
  past it makes `--budget` unusable for a project holding one exotic model. What it must
  never become is a silent default.
- **`--budget=0` is a $0 cap, not an off switch.** The abort message used to advise it,
  so following the advice made the abort repeat. `-1` disables.
- **`muvid render --shot X` REFUSES the budget flags rather than ignoring them.**
  `facade.render_shot` takes no budget and never has, so silently accepting `--budget`
  there would let a caller believe a cap applied to a render that is not capped — worse
  than the gate not existing, and worse still now that the command's own `--help`
  promises it. Gating a single shot needs a per-shot rollup; that is not built.

Still open, deliberately: a **cumulative** bound. A per-run cap does not bound spend
across runs, and no non-monetary destructive gate exists.

**The two packages do not share a camera vocabulary, and the boundary translates.**
`ShotSpec.camera` is free prose a director writes into the script (`**camera**: slow
push-in`); `an`'s `camera.move` is a closed set of named moves, and a name outside it is
a hard refusal at **both** validate and compile. So `animation.an_camera_move` maps
prose → move at the boundary. Never pass the prose through: muvid#44 emitted
`move: static`, which `an` has never implemented, so every animation render failed
validate and fell back to `still` — silently, because nothing in the suite compiled the
synthesized `scene.md`.

**That closed set has two eras, and muvid pins neither.** `hold`, `push_in`, `pull_out`,
`zoom_in` and `zoom_out` have always been there. The four *translating* moves —
`pan_left`, `pan_right`, `tilt_up`, `tilt_down` — arrived with **an#109** and shipped in
**`an` 0.1.65**; on 0.1.64 and below `an` refuses them exactly as it refused `static`.
muvid declares `an` in no extra and no floor (the import is soft by design), so
`an_camera_move` reads the vocabulary the **installed** `an` reports
(`an.ir.camera.CAMERA_MOVES`, the SSOT) and degrades an unavailable move to `hold` with
a warning. That is the floor, enforced where the answer is actually knowable. Do not
write a version number into a doc without checking `git log -S<move> ` in `an` — an
earlier draft of this paragraph said 0.1.58, which is the issue's five-move era.

**A direction the table cannot name warns.** `an`'s strictness exists because a camera
move that silently no-ops is a bug; the translation boundary must not commit that bug on
the way in. `an_camera_move("truck left")` returns `hold` **and** warns. A move the
director explicitly *refused* ("static, no push-in") is obeyed silently — matching is by
word, clause-scoped, and the director's first-written move wins over table order.

**The vocabulary guard runs in CI, and had to be made to.** A guard that imports `an`
skips on every runner (CI installs `ai,mcp`; `an` is in neither), which left the exact
drift muvid#44 was un-gated in the only environment that blocks a merge — measured:
adding a bogus phrase→move pair kept the suite green. So `tests/data/an_camera_moves.json`
**records** `an.ir.camera.CAMERA_MOVES` (generated, never hand-typed), the table is pinned
against the recording in CI, and the imported-`an` tests are the recording's *freshness*
check on a developer machine. Same shape as reelee-web's `schemas/destructive-tools.json`.
Refresh with `tests.test_animation_camera._refresh_snapshot()`.

## Connector duty — `muvid_*` tools are live

`muvid/mcp/__init__.py` declares the tool surface a host connector aggregates via
`register_tools(server, prefix="muvid_")`:

- `VISUALIZER_TOOLS` — `muvid/mcp/tools.py`
- `FOOTAGE_TOOLS` — `muvid/mcp/footage_tools.py`
- `SCORING_TOOLS` — `muvid/mcp/scoring_tools.py`

These run on the deployed reelee AV connector against real user projects on disk.
**The package's declared surface and the connector's live surface are two different
numbers** — a server-side `tools/list` returned 14 `muvid_*` tools in Aug 2026 while the
package declared more, because the connector installs muvid from an unversioned git pin
and only picks up changes on a redeploy. Read the current declared count from
`len(muvid.mcp.TOOL_NAMES)`; assume anything in it is, or shortly will be, live.

A test asserts each tool module's public functions equal its declared list, so a tool
cannot be implemented-and-tested yet unreachable — which is what happened to the editor
bridge before muvid#37.

- **A tool signature is a production contract.** Renaming a tool, renaming/removing a
  parameter, or changing a response key breaks live callers. The federation renames
  freely but keeps **no aliases** — so a rename is a deliberate, announced break, and
  `assemble_music_video` keeps its deployed name for exactly that reason (issue #21).
- Every tool is **free** (`FREE_TOOLS == TOOL_NAMES`, `COSTED_TOOLS == []`). If a costed
  tool ever appears here, that fact has to change with it.
- **A free-form tool argument is a trust boundary, and `assemble_music_video`'s `edl`
  is the one that matters.** It is `list | None` — arbitrary dicts a remote OAuth
  caller writes — and `edl.py`'s `_as_entry` reads every field out of them by name. Two
  of those fields (`crop`, `look`) were added after the tool shipped. `look` is
  **executable ffmpeg**, so it is gated by an **allowlist of filter names**
  (`edl.LOOK_FILTERS`), not by refusing characters: measured on the muvid#66 branch
  before the allowlist landed, `look="metadata=mode=print:file=<path>"` passed the gate,
  rendered, returned `ok`, and truncated that file to zero bytes — outside the caller's
  own project tree, so workspace scoping did not contain it. The rule to carry forward:
  **when you widen `_as_entry`, you widen the remote input surface**, and a field whose
  value is interpreted rather than measured needs a curated vocabulary the way
  `TRANSITION_CURVES` and the `an` camera table do.
- **A vocabulary is not a bound on its PARAMETERS, and the second half took a separate
  pass** (muvid#75). `LOOK_FILTERS` closed the filters that write the host's disk; it did
  not stop an allowlisted one being asked for a 64-megapixel frame. Measured from a 64x48
  source: `scale=8000:8000` 328 MB, `zoompan=d=1:s=8000x8000` 313 MB, **`pad=8000:8000`
  307 MB** (the lever the issue itself did not name — `pad` is allowlisted because
  `looks`' `fit` letterboxes with it), against 19 MB at canvas size. So `validate_edl`
  now takes the delivery `canvas` and `_validate_look_size` bounds every declared
  dimension at `MAX_LOOK_SCALE` (4) times it. Two things generalise past this field:
  **the bound has to be relative to something the gate is given** — `_resolve_canvas` had
  to move *above* the validation in `assemble_music_video`, or a `canvas=` override would
  be bounded against the project's canvas — and **an expression is refused rather than
  bounded**, because `iw*80` evaluates against whatever is underneath, `-1` derives from
  an aspect a preceding `crop` can make extreme, and a size *name* resolves through
  ffmpeg's own table (`s=whuxga` is 7680x4800). That costs nothing only because every
  size muvid's own compilers emit is already a literal — swept, not assumed.
- **That first pass listed the options that SET a size and read every other option as
  nothing — a blocklist wearing an allowlist's clothes — and it leaked twice, both
  bigger than the case it refused.** Measured on the production 1920x1080 canvas with
  the exact `-vf` the assembler builds: `pad=w=1920:h=1080:aspect=1/30` renders a
  1920x57600 frame at **590 MB** with w and h sitting AT canvas size, and
  `crop=w=1920:h=200,scale=w=7680:h=4320:force_original_aspect_ratio=increase` renders
  41472x4320 at **941 MB** with both sizes sitting exactly ON the bound — against 110 MB
  at canvas size and 403 MB for the `scale=8000:8000` the bound does refuse. Both were
  accepted end-to-end by the live tool. A third, `force_divisible_by`, does nothing
  *without* `force_original_aspect_ratio`, which is why a one-option-at-a-time sweep
  cannot find it. Three rules carry the fix, and the ordering is the lesson:
  - **The four filters that can change the output geometry (`scale`/`pad`/`crop`/
    `zoompan`) are allowlisted per OPTION and per positional SLOT**
    (`_LOOK_GEOMETRY_FILTERS`). An option muvid has not measured is refused, so the next
    lever nobody thought of is refused by default rather than read as nothing.
  - **A positional argument past the classified prefix is refused, never dropped.**
    `pad`'s `aspect` is its SEVENTH slot, so `pad=1920:1080:0:0:black:init:1/30` reaches
    it without naming it — silently dropping slot 7 was the second half of the leak. The
    prefix stops at what muvid's compilers emit rather than carrying the full order,
    because the two ffmpeg builds this fleet runs do not declare the same option list for
    `scale`.
  - **The census read from the binary is an instrument, not the guard.** ffmpeg 6.1.6's
    `-h filter=scale` omits `s`/`size` while `scale=s=320x240` works there — a table that
    classified only what the help prints would have had a hole on that binary. So the
    recorded census (`tests/data/ffmpeg_filter_options.json`) exists to make a NEW ffmpeg
    option a decision someone records, and `tests/test_edl_look_options.py` drives every
    option of every allowlisted filter through the real binary asserting the gate never
    under-reads a growth. The hand corpus that missed both leaks had exactly the right
    shape and simply did not contain them.
- **The two ffmpeg builds disagree about what a bare argument after a `key=value` one
  MEANS, and only CI could see it.** `scale=w=8000:100` exits 234 on 9.0.1 ("No option
  name near '100'" — the shorthand is discarded) and renders **8000x100** on 6.1.6, which
  fills the next slot. Both measured; CI installs an ffmpeg 6, so a claim pinned only
  against the local 9.0.1 went red there. Two consequences worth carrying: `_link_options`
  **refuses** such a fragment rather than reading it, because no single reading is right on
  both binaries; and dropping that rule is a **hole**, not an over-refusal — the trailing
  bare value refills slot 0 and *overwrites* the named `w`, so the gate reads 100 and
  accepts what 6.1.6 renders 8000 px wide. More generally: **run the ffmpeg-dependent
  suite against both binaries before believing a measurement about the binary**
  (`PATH=/…/ffmpeg@6/bin:$PATH pytest -q`); a one-build measurement stated as a fact is how
  this one got in.
- **The seam is a bare string, so anything muvid must know about a look has to be its own
  field.** `EdlEntry.look_time_varying` (muvid#73) is the worked example: a moving look
  restarts its ramp on a transitioned boundary (the blend is a separate input-side-seeked
  invocation, so the filter clock returns to 0 — a 1.12x punch reaching zoom 1.109 on the
  solo part is redrawn at 1.000 on the blend), and muvid cannot tell a punch from a grade
  by looking at the fragment. Rebasing it is refused for `looks`' rule-27 reason. The
  compilers answer instead — `punch_in`/`motion`/`stylize` return a `LookFragment`, a
  `str` that also carries `.time_varying`, so `punch_in_cuts` *copies* the answer rather
  than hardcoding it. `stylize` derives it from the compiled plan rather than declaring
  itself static: `looks`' `motion` effect and any step with an `at` span both reach the
  clock. Do **not** reach for `ImplRef.timeline` for this — it means "supports `enable=`"
  and classifies every row backwards (`motion` is `False`, the static grades are `True`).
- Tools resolve the caller through `muvid.mcp.identity.current_email()` and scope every
  path by email. Workspace scoping is the authorization model — `downloads.resolve`
  raises `KeyError` (not `PermissionError`) for another user's render, because saying
  which would leak existence.
- Errors reaching a caller should be a clean `fastmcp` `ToolError` with a next action
  ("no song set — call set_song first"), not a raw pydantic or ffmpeg traceback.

**A gap worth remembering, now closed:** `footage_editor_document` and
`footage_edl_from_annotations` were implemented and tested but listed nowhere, so
`register_tools()` never exposed them and the lacing editor bridge had no transport at
all. The tests reached them by direct import, which is exactly why it stayed invisible.
Fixed in muvid#37 along with the drift test above. When you add a tool, adding the
function is half the job.

## On-disk state — treat it as a migration surface

Default root `~/.local/share/muvid`, overridable via `MUVID_DATA_HOME`
(`muvid/footage/workspace.py:28,39-41`). Never inside the app/deploy tree.

```
{root}/music_video/projects/{email}/{project_id}/manifest.json   title, canvas, song, song_hash, clips
                                              .../song/song.<ext>
                                              .../clips/{clip_id}.<ext>
                                              .../alignments.json
                                              .../scores/                (ScoreTensor .npz + manifest)
                                              .../renders/{render_id}/   (final.mp4 + meta.json)
```

Anything that changes the shape of `manifest.json`, `alignments.json`, the score
manifest, or the `music_video` path segment is a **migration**, and existing users'
projects are the thing being migrated. `FootageAlignment.from_dict` already carries one
such compatibility read (`overlaps` defaults True for records written before the field
existed) — that is the pattern when a field is added.

Invalidation is deliberate: `set_song` drops `alignments.json` and every score track
(the song is the alignment reference); `align_footage` only invalidates scores when
`align_fingerprint` actually changed, because the tensor is the most expensive artifact
in the pipeline.

## Optional extras

`pip install 'muvid[<extra>]'`:

| extra | for | notes |
|---|---|---|
| `ai` | part 3 | `falaw`, `lacing`, `lookbook` |
| `mcp` | serving genres/tools | `fastmcp`, `py2mcp>=0.1.9`, `nw>=0.0.15` |
| `scoring` | part 2 core tier | `mediapipe` (also brings the `cv2` the CV kernels use), `mixing[beats]>=0.0.30` |
| `scoring-shots` | `boundary_mode="beats+shots"` | `scenedetect`; absent → degrades to beats-only |
| `scoring-lipsync` | **opt-in, off-prod** | see the licence boundary above |
| `editor` | the lacing bridge | `lacing` |
| `ui` | the local single-page UI | `fastapi`, `uvicorn`, `pydantic` |

Core (`pip install muvid`) is `argh`, `mixing>=0.0.34`, `numpy`, `graze>=0.1.44`.
**`import muvid` must never pull any extra** — `muvid/__init__.py` is a PEP 562 lazy
facade, `muvid.footage.scoring.__init__` is lazy the same way, and the genre modules
import only `nw`. This is tested by subprocess import-safety checks that assert the
heavy modules are absent from `sys.modules` afterwards (`tests/test_footage.py:129`
for `moviepy`/`fastmcp`/`cv2`/`numpy`, `tests/test_scoring_extractors.py:35` for
`cv2`/`mediapipe`/`librosa`/`torch`/`numpy`). Keep them green: never add an eager
top-of-module heavy import to anything on an import-safe path.

System requirement: **`ffmpeg` and `ffprobe` on PATH** for essentially all real work.

## Tests

Run `pytest -q` from the repo root. Everything ffmpeg-dependent guards itself through
`tests/ffmpeg_support.py` (`needs_ffmpeg`, `needs_ffmpeg_filter("drawtext", …)`), which
composes the binary check with `muvid.visualize.ffmpeg.has_filter` — so a build missing
`drawtext`/`showcqt` **skips** rather than fails. Locally, missing extras also skip.

**The skip guards are a trap in CI, and there is a canary for it.**
`tests/test_ci_extras_canary.py` has no skip guard: inside CI a missing `nw`/`fastmcp`/
`mixing` is a hard failure, because the footage suite's `pytest.importorskip` once let
`align.py` and `footage_tools.py` sit at 0% coverage for months on a green run
(muvid#24 B5). If you change the installed extras, **three sites must agree**:
`[tool.wads.ci.install] extras` in `pyproject.toml`, and the `extras:` input on **both**
`install-deps-uv` steps in `.github/workflows/ci.yml`.

Do not weaken an assertion to make a refactor pass. If behavior must change, change the
assertion deliberately and say why in the commit.

## Conventions

- Favor functional style; small focused helpers (`_underscore` module-private, inner
  functions for single-use). `dataclasses` (frozen where they are records) for data.
- Arguments beyond the 2nd–3rd position are **keyword-only**.
- No magic numbers. Tunables are module constants, and the operational ones read an
  env var with a documented default (`MUVID_FOOTAGE_MAX_EDL_ENTRIES`,
  `MUVID_FOOTAGE_MAX_LOOK_SCALE`, `MUVID_FOOTAGE_MIN_CONFIDENCE`,
  `MUVID_FFMPEG_TIMEOUT_S`, `MUVID_FETCH_TIMEOUT_S`, `MUVID_SCORING_*`,
  `MUVID_DATA_HOME`).
- **Every module needs a top-level docstring** (auto-extracted for docs; `D100` is the
  one ruff rule selected). muvid's docstrings carry the *why* and the issue number —
  match that; they are the real design record.
- Registries over conditionals: `register_visual`, `register_selection_strategy`,
  `register_lazy_strategy`, `register_aligner`. Adding a metric is a column plus a
  weight, not a branch.
- Time is **seconds** (`float`) on the song timeline everywhere, except lacing
  annotations, which are rational at `TIME_RATE` µs.
- Demos/notes live in `misc/` — never inside the importable package. The LOCKED design
  decisions for part 2 are `misc/docs/footage_scoring_design.md`, with the citations in
  `misc/docs/footage_scoring_research.md`; part 3's rationale is `misc/docs/design.md`.
