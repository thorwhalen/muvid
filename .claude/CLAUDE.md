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
stays byte-identical) and read it defensively. `EdlEntry.transition` (muvid#34) is the
worked example, and the two read postures there are deliberately opposite —
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
  `MUVID_FOOTAGE_MIN_CONFIDENCE`, `MUVID_FFMPEG_TIMEOUT_S`, `MUVID_FETCH_TIMEOUT_S`,
  `MUVID_SCORING_*`, `MUVID_DATA_HOME`).
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
