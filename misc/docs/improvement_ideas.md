# muvid — improvement ideas & ecosystem roadmap

Companion to [`design.md`](design.md) and
[`alignment_references.md`](alignment_references.md). Captures the
**what's-next** that came out of a v0 audit (after the
mtv → muvid rename). Reading order is: thesis → friction inventory →
ranked ideas → incremental rollout plan with a per-step test strategy.

> **Status (as of the v0 audit follow-through):** I9, I8, I5, I6, I10,
> I2, I4, S4, and a fal-events-into-muvid bridge have all landed.
> Next leverage moves: I1 (lacing as the SSOT through `an`), I3
> (cost rollup), I7 (interactive curate), I11 (shared contracts).
> See the per-section "Status" lines below.

## Thesis

`muvid` is a **timeline-locked orchestrator** sitting on top of a
deliberately-thin facade over five sibling packages:

| Concern                          | Owner       | Maturity | Main friction with `muvid`                                       |
|----------------------------------|-------------|----------|------------------------------------------------------------------|
| AI media gen (fal.ai)            | `falaw`     | strong   | sync-only, no progress events, no cost rollup, no batching       |
| Reference curation               | `lookbook`  | strong   | returns `ImageRef` not paths, sync GPU, no interactive loop      |
| Interval annotation              | `lacing`    | strong   | verbose builder, no `at_tier(name, window)` convenience          |
| 2D animation (cutout / lipsync)  | `an`        | strong   | re-transcribes with whisper; ignores muvid's alignment store     |
| Audio/video edit + Scribe        | `mixing`    | strong   | no transcript caching; throws away word-confidence               |

The ecosystem itself is healthy. The leverage is in **the seams** —
where two packages meet and one of them doesn't quite trust the other's
SSOT.

## Inventory of concrete friction (from the v0 audit)

Ground truth (file:line) lives below. These are the items every
later idea references.

### S1 — Two transcribers, one song
- `muvid/lyrics.py` calls `mixing.transcript.transcribe` (Scribe) and
  writes `lyrics/alignment.annot` via `lacing`.
- For animation shots, `muvid/renderers/animation.py:6-9` writes a
  pre-baked audio slice into the `an` scene; `an.audio.pipeline`
  then **re-transcribes that audio with `WhisperLipSync`** to produce
  visemes (`an/audio/whisper_lipsync.py:103-150`).
- `WhisperLipSync.align()` returns `VisemeTrack` only — word
  boundaries are computed internally and discarded.
- Net effect: two independent word-timing sources for the same audio,
  no coherence guarantee, double the latency, and `lacing`'s store
  isn't actually the SSOT it claims to be.

### S2 — falaw is sync, opaque, and uncosted
- `falaw.core.call_fal` (sync, no retry, no timeout knobs).
- `on_log` in `call_fal` defaults to `print()`; no structured progress
  events (phase / pct / ETA) for an orchestrator to surface.
- `ModelRecord.cost_hint` is a free-form string. No quantitative
  `estimate_cost(scene) -> dict[currency, float]`.
- `render_scene` is sequential — no concurrency knob, no yield-as-done.
- `materialize_asset` infers extension from URL path, falling back to
  `.bin` (`falaw/cache.py:152-158`); should sniff `Content-Type`.
- `Scene` IR has `quality_tier` but no per-beat `model_id` override.

### S3 — lacing's builder is hand-rolled in muvid
- `muvid/align.py:200+` constructs `Annotation` + `MediaRef` +
  `Provenance` + `body_schema_uri` per word/line/section by hand.
- `MemoryStore.at_tier(name, window)` exists but is buried; muvid
  rolls its own `intersects(window) → filter by tier` in places
  (`tests/test_align.py:153-155`).
- No "muvid-shaped" convenience: a `lines_in(window)`,
  `words_in(window)`, `sections_covering(t)` API.

### S4 — lookbook's `RunResult.kept` is `ImageRef`, not paths
- `muvid/characters.py:163` does
  `getattr(r, "path", None) or r.image_id`. Works for `PathImageRef`,
  fails for `BytesImageRef` / `UrlImageRef` (returns the id, not a
  usable path). Fragile if `ingest()` strategy ever changes.
- InsightFace + 6DRepNet run **serially per image** — no GPU batching.
- `pyyaml` is a soft-required dep for recipe loading; failure surfaces
  late.

### S5 — mixing throws away word confidence + no transcript cache
- `muvid/lyrics.py:95-111` parses `{text, start, end}` only.
  Scribe returns `confidence` and event-type (`word` vs `audio_event`);
  both dropped on the floor.
- Each `transcribe(audio_path)` round-trips Scribe; no on-disk cache
  keyed by `(audio_hash, model_id, lang)`.

### S6 — muvid's own rough edges
- `align.py:261` — line `end = start + 0.5s` placeholder when no end
  found.
- `render/_common.py:84` — pads tail by *copying the last frame*.
- `__init__.py` only re-exports the facade; no `muvid.cost` /
  `muvid.preview` / `muvid.queue` modules yet.
- Project-level content-hash caching is in the design doc but only
  partially wired (shots cache; alignment doesn't).

## Ranked ideas

Ordered by **impact ÷ effort**. Each item: **what**, **where**,
**how to test automatically**.

### I1 — Make lacing the actual SSOT for word timings  *(highest leverage)*

Solves S1.

**What.** Add a `WordTimingProvider` protocol to `an` that returns
a list of `(text, start, end)` tuples. Default implementation =
current `WhisperLipSync`. Add a `LacingWordTimingProvider` that reads
from a `lacing` store (any tier whose body schema has a `text` field).
Wire it through `an.audio.pipeline.produce_audio_for_dialogue` so
muvid can pass in pre-computed timings and skip whisper.

**Where.**
- `an/audio/whisper_lipsync.py` — extract `_visemes_for_words`'s word
  boundary input into a `WordTimingProvider` interface.
- `an/audio/pipeline.py:130-131` — accept `word_timing_provider=`.
- `muvid/renderers/animation.py` — when alignment store exists, pass a
  `LacingWordTimingProvider(store)` instead of letting `an` re-run.

**How to test.**
- In `an`: a property test — for any list of `(text, start, end)`,
  the resulting `VisemeTrack` has visemes only inside those word
  intervals.
- In `muvid`: a fixture with synthetic audio + canned alignment store;
  assert `WhisperLipSync.align` is **not called** when a provider is
  injected (use `unittest.mock`).

### I2 — Structured progress events in falaw

Solves the visible half of S2.

**What.** Replace `on_log: Callable[[str], None]` with `on_event:
Callable[[ProgressEvent], None]`, where `ProgressEvent` is a small
discriminated union: `{kind: "queued"|"running"|"progress"|"done"|
"error", call_id, app, pct?, message?}`. Default impl prints; add an
`EventBus` that muvid (or any UI) subscribes to.

**Where.** `falaw/core.py:54-59` (`call_fal`).

**How to test.**
- `falaw`: monkeypatch `fal_client.subscribe` to yield a sequence of
  fake `Queued / InProgress / Done` events; assert the bus receives
  the expected shape.
- `muvid`: assert the FastAPI UI's SSE stream emits a `ProgressEvent`
  per stage transition.

### I3 — Cost rollup: `estimate_cost(scene)` and `--budget` gate

Solves the rest of S2.

**What.** Replace `cost_hint: str` with `cost_estimate:
CostEstimate` on `ModelRecord`, where `CostEstimate` is `{kind:
"per_image"|"per_second"|"per_token", amount, currency, notes}`.
Add `falaw.estimate_cost(scene) -> CostRollup`. Add `muvid render
--budget=$X` that aborts if the rollup exceeds X, and a `muvid
status` line that shows estimated remaining cost.

**Where.**
- `falaw/registry.py` (or wherever `ModelRecord` lives).
- New `falaw/cost.py` for the rollup function.
- `muvid/__main__.py` — add `--budget`.

**How to test.**
- `falaw`: golden-test the cost rollup of a fixture `Scene` against
  a hand-computed expected value.
- Catch model-record drift: a CI test that asserts every model in
  `models.json` has a non-default `cost_estimate`. Failing CI on new
  un-priced models keeps the catalog honest.

### I4 — Concurrent `render_scene` with yield-as-done

Solves the throughput half of S2.

**What.** `render_scene(scene, *, concurrency=4) -> Iterator[Result]`
runs shots through a thread pool (most fal calls are HTTP-bound).
Yield as each completes. Falaw stays sync internally; concurrency
sits at the orchestrator boundary.

**Where.** `falaw/operations/render.py` (or equivalent).

**How to test.**
- `falaw`: monkeypatch each render to sleep a known duration; assert
  total wall time ≈ `sum / concurrency`, and that yielded order matches
  completion order, not submission order.
- `muvid`: assert `--concurrency=4` finishes 4 fixture shots in <2×
  the longest shot's wall time (use deterministic mock).

### I5 — A `lacing.muvid` (or `lacing.tracks.subtitle`) helpers module

Solves S3.

**What.** A small builder that knows the canonical `(sections, lines,
words)` triple plus convenience queries. Belongs in `lacing` (it's
generic — any subtitle/caption-shaped data wants this), but
muvid-shape names work fine. API sketch:

```python
from lacing.tracks.subtitle import SubtitleBuilder

with SubtitleBuilder(store, asset_id="song/audio.mp3") as b:
    with b.section("intro", 0.0, 12.5):
        b.line("I came down to the river", 12.5, 16.2)
        # words optional; auto-emitted if you pass per-word timings

# queries
b.lines_in(window=(35.0, 55.0))
b.sections_covering(42.0)
```

**Where.** New `lacing/tracks/subtitle.py`; `muvid/align.py` migrates
to it.

**How to test.**
- `lacing`: round-trip — build a store with the builder, query it,
  assert results match an Allen-relation hand reference.
- `muvid`: the existing `tests/test_align.py` should shrink by ~30
  lines after migration; that's the regression signal.

### I6 — Singing-grade alignment as a pluggable aligner

Already designed in `alignment_references.md`. Concretely:

**What.** Expose `align_lyrics(..., aligner: AlignerName)` with three
choices today (`"scribe-greedy"`, `"whisperx-lite"`,
`"user-provided"`) and a stub for `"stars"`. `whisperx-lite` re-uses
the already-installed `faster-whisper` from `an`. Each aligner
returns the same shape (`list[WordTiming]`); the rest of the pipeline
is aligner-agnostic.

**Where.** `muvid/align.py` — split into `aligners/{scribe,
whisperx_lite, user}.py`; `muvid/__main__.py:align` grows
`--aligner=...`.

**How to test.**
- A canned 10-second audio fixture + canonical lyrics. Each aligner
  must produce timings within ±200ms of ground truth on every word.
  Run all aligners in CI; mark `stars` as `xfail` until implemented.

### I7 — Interactive curate loop in lookbook

Solves part of S4.

**What.** A `lookbook.curate_interactive(refs, recipe, *,
present=k, on_decision)` that yields the top candidates, accepts
keep/reject decisions, and re-scores. Lets the user "weight" their
own taste in. Headless variant: a deterministic decision callable
for tests.

**Where.** `lookbook/runtime.py` (or wherever `curate` lives).

**How to test.**
- `lookbook`: a deterministic `on_decision` that always rejects images
  with `face_area < 0.1`; assert the second-pass `kept` set has all
  faces ≥ 0.1.
- `muvid`: a CLI smoke test that reads decisions from a JSON file —
  fully automatable.

### I8 — Transcript cache + word-confidence in mixing

Solves S5.

**What.** Wrap `mixing.transcript.transcribe` in a content-hash
cache keyed by `(blake3(audio_bytes), model_id, lang,
timestamps_granularity)`. Surface `confidence` per word in the
returned shape. Don't drop `audio_event` rows; expose them in a
parallel field so callers can ignore or use them.

**Where.** `mixing/transcript/scribe.py` — add a `cache_dir` param
defaulting to `$XDG_CACHE_HOME/mixing/scribe`. Update the docstring
to document `confidence` and `audio_events`.

**How to test.**
- `mixing`: monkeypatch the HTTP client; first call hits, second call
  with same inputs returns cached and asserts `0` HTTP calls.
- `muvid`: an alignment regression test that asserts low-confidence
  words bubble up in `lyrics.md` as TODO markers (a feature this
  unlocks).

### I9 — End-to-end smoke fixture for every render strategy

Solves the lack of integration coverage in muvid. This is the safety
net that makes everything else above safer.

**What.** A `tests/fixtures/tiny_song/` with: a 5-second synthetic
audio, a 2-line lyrics.md, a single character (one stub PNG), a
single environment (one stub PNG), a script with **five shots —
one per render strategy**. A pytest fixture that monkeypatches
`falaw.call_fal` and `an.orchestrate.orchestrate` with deterministic
stubs that produce solid-color MP4s. The test asserts:
- the pipeline runs end-to-end,
- the alignment store is populated correctly,
- each shot's `output.mp4` exists with the right duration,
- `compose` produces a final.mp4 whose duration matches the song.

**Where.** `muvid/tests/test_smoke_pipeline.py` + a `conftest.py`
fixture pack.

**How to test.** It IS the test. Runs in <10s. Goes in CI. Every
ecosystem-touching change must keep it green.

### I10 — `muvid status --json` and SSE for the UI

Quality-of-life. Solves part of S6.

**What.** `muvid status --json` prints a structured report (stages
done/pending, per-shot cost estimate, alignment-confidence histogram,
est. wall time at current concurrency). The FastAPI UI subscribes
to a `/api/events` SSE that streams the same shape during a run.

**Where.** `muvid/project.py` (compute), `muvid/__main__.py:status`
(CLI), `muvid/ui/app.py` (SSE).

**How to test.**
- `muvid`: snapshot test on the `--json` output for the smoke fixture.
- `muvid`: SSE test using `httpx.AsyncClient` + the FastAPI test
  client; assert the right event shapes arrive in order.

### I11 — A shared contracts package (`mvtypes` or fold into `lacing`)

Long-tail cleanup. Solves the typing inconsistency where each
package has its own `Character` / word-timing shape.

**What.** Extract `WordTiming`, `IntervalAlignment`, `Character`,
`Environment`, `ProgressEvent`, `CostEstimate` into a tiny
zero-dep package. Every other package depends on this only. The
runtime cost is one import; the design cost is keeping the package
small (no logic, only frozen dataclasses + JSON-schema).

**Where.** New repo. Or fold into `lacing` since it's already
the typed-data hub.

**How to test.** Reference equality across siblings: assert
`isinstance(falaw_char, mvtypes.Character) is True`. JSON-roundtrip
parametrized by every dataclass.

## Incremental rollout plan

The ideas above are **independently mergeable**. Suggested order
(each step is one or two PRs; each PR ships green tests):

1. **I9 (smoke fixture)** — *first*, before anything else. Without it
   you can't safely change ecosystem seams.
2. **I8 (transcript cache + confidence)** — small, in `mixing`, no
   downstream surprises.
3. **I5 (lacing subtitle helpers)** — pure addition; muvid migrates
   in a follow-up.
4. **I6 (pluggable aligners)** — locally scoped to muvid; new
   aligners gated by optional deps.
5. **I1 (lacing-as-SSOT through `an`)** — the highest-leverage
   ecosystem change. Needs I5 in place to be ergonomic.
6. **I2 (progress events)** then **I3 (cost rollup)** then
   **I4 (concurrency)** in `falaw` — these are independent within
   `falaw` but cleanest done in this order (you want events before
   you parallelize).
7. **I10 (status --json + SSE)** — pulls I2/I3 into the UI.
8. **I7 (interactive curate)** — independent of everything above.
9. **I11 (shared contracts)** — extract once everything else has
   stabilized; trying to do it earlier just chases a moving target.

## Testing strategy across the ecosystem

The premise: **changes in any sibling package should fail fast in
`muvid`**, because muvid is the integration layer. Three layers of
defense:

### a. Per-package tests (already in place)
Each sibling has a non-trivial test suite. Don't degrade these.

### b. The muvid smoke fixture (I9)
Runs every render strategy through every seam. Uses deterministic
stubs for fal and `an.orchestrate`. Catches:
- type drift (someone renamed `kept` → `selected` in `lookbook`),
- contract drift (`mixing.transcript.transcribe` changes return
  shape),
- pipeline drift (a new field appears in `Scene` that muvid doesn't
  populate).

### c. Downstream-consumer CI in muvid
A CI job that pip-installs the **siblings from the local-package
ecosystem path** rather than from PyPI, then runs the smoke fixture.
This catches breakage before sibling PRs land.

```yaml
# .github/workflows/ecosystem-compat.yml (sketch)
- name: install siblings from main
  run: |
    for pkg in falaw lookbook lacing an mixing; do
      pip install --no-deps "git+https://github.com/thorwhalen/$pkg@main"
    done
- name: muvid smoke
  run: pytest tests/test_smoke_pipeline.py
```

### d. Property tests where math matters
- Allen-relation queries in `lacing`.
- Word-timing → viseme conversion in `an`.
- Content-hash idempotency in `falaw.cache`.

### e. Real-API regressions (low frequency, opt-in)
A `--real-api` pytest marker that hits fal and ElevenLabs with tiny
inputs, gated by env vars in CI. Run nightly, not per-PR. Output:
"the cheapest end-to-end run still produces a valid mp4 of the
expected duration."

## Open questions worth answering before committing

1. Do we want a real `mvtypes` package, or do we just elevate `lacing`
   to be the typed-data hub? (`lacing` already owns intervals; a
   `lacing.contracts` submodule is half the work.)
2. Is the `an` lipsync provider abstraction worth pushing back into
   `an`, or do we wrap it in a `muvid.adapters.an` shim? (The shim is
   safer; the upstream change is cleaner.)
3. Cost-rollup: do we want token-level breakdowns (`text_to_video
   = $0.18 image + $0.42 video`) or just total? Token-level is
   strictly better for debugging.
4. Should the smoke fixture also produce a *visible* artifact (a 5s
   solid-colour mp4) we eyeball, or stay headless? (Cheap to do both.)
