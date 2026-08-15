# muvid

Tools to make music videos — three ways, from a song and a cover, from a pile of
phone recordings of one gig, or from an AI narrative pipeline. See the table
below for which is which; the rest of this section describes the third.

The AI pipeline orchestrates the local ecosystem (`falaw`, `lookbook`, `lacing`,
`an`, `mixing`) into a song-to-video pipeline. The user is the director; an agent
(Claude in the terminal, or the local web UI) drives the stages.

> **Status (the AI pipeline):** v0+. The pipeline (init → transcribe → align → cast →
> environments → script → render → compose) works end to end. Render
> strategies: `lipsync`, `image_to_video`, `text_to_video`,
> `animation`, `still`. CLI, Claude skill (`.claude/skills/muvid/`),
> and a single-page local UI all dispatch to the same Python
> functions. The v0 audit follow-up ([`improvement_ideas.md`](misc/docs/improvement_ideas.md))
> shipped pluggable aligners, cost rollups + `--budget`, structured
> falaw progress events streamed to `.muvid/fal_events.jsonl`,
> end-to-end smoke fixture, lacing as the SSOT for word timings (no
> redundant whisper passes inside `an`), and a `muvid.contracts`
> adapter layer to sibling-package shapes. See
> [`misc/docs/design.md`](misc/docs/design.md) for the design
> rationale and
> [`misc/docs/alignment_references.md`](misc/docs/alignment_references.md)
> for the lyric-alignment literature muvid builds on.

muvid is **three independent parts**:

| part | what it does | needs |
|---|---|---|
| [`muvid.visualize`](#muvidvisualize--audio--cover--video) | a song + a cover → a 16:9 audio-reactive visualizer video, deterministic and ffmpeg-only | `ffmpeg` |
| [the `music_video` genre](#the-music_video-genre--footage-assembly) | N phone recordings of ONE song → aligned, scored, cut and assembled into a music video | `ffmpeg` |
| the AI narrative pipeline (above) | a song → a cast, a script and generated shots | API keys, `muvid[ai]` |

The first two are free, deterministic and key-free, and both are registered
[`nw`](https://github.com/thorwhalen/nw) genres — so a host connector serves them
directly. The third is the generative one, and is the only part that spends money.

## Install

```bash
pip install muvid                  # core: CLI + muvid.visualize + muvid.footage (needs ffmpeg)
pip install 'muvid[scoring]'       # footage scoring: quality + motion-to-beat + the weighted selector
pip install 'muvid[editor]'        # export a footage project as lacing annotations
pip install 'muvid[mcp]'           # serve the two nw genres over MCP (fastmcp, py2mcp, nw)
pip install 'muvid[ai]'            # the narrative pipeline (falaw, lacing, lookbook)
pip install 'muvid[ui]'            # FastAPI + uvicorn for the web UI
```

The narrative pipeline depends on local sibling packages (`falaw`, `lookbook`,
`lacing`); with editable installs, install them first and use `pip install -e
./muvid[ai]`. `muvid.visualize` needs only `mixing`.

System: `ffmpeg` and `ffprobe` on `PATH`. Env (pipeline only):
`ELEVENLABS_API_KEY` (transcription), `FAL_KEY` (fal.ai generation).

## `muvid.visualize` — audio + cover → video

Turn a song into a publishable music video, without the AI pipeline:

```python
from muvid.visualize import render_audio_video, list_visuals, verify_video, report

# Simplest: the cover on a 16:9 canvas, held for the song, loudness-normalized.
result = render_audio_video("song.wav", image="cover.png")

# Pick a visualizer (list_visuals() -> still, ken_burns, cqt, spectrum, waves,
# bars, scope), or pass "auto" / your own callable.
render_audio_video("song.wav", image="cover.png", visual="cqt", normalize=True)

# Check what you produced before shipping it.
print(report(verify_video(result.path, audio="song.wav")))
```

What it does, by default:

- **16:9, never pillarboxed** — square/portrait art is composed onto 1080p filled
  with a blurred, darkened copy of itself, sharp cover centred on top.
- **Loudness −14 LUFS** (two-pass EBU R128), so a set of songs plays level.
- **Video exactly as long as the song**, H.264 High / yuv420p / AAC 48 kHz,
  `+faststart`, no edit lists — what YouTube asks for.
- **A teal accent** across the reactive visualizers (one tunable tint) over a
  muted background, so an album reads as one release whatever each cover's colours.
- **A matching thumbnail** derivable from the same composition
  (`thumbnail_image`, 1280×720, under YouTube's 2 MiB cap).

Every knob is overridable (`visual`, `size`, `fps`, `normalize`,
`CoverLayout(...)`, `options={...}`); add your own look with
`register_visual`. Needs only **ffmpeg** — every built-in visual is ffmpeg-native
except Ken Burns (via `burns`, which comes with `mixing`).

Publishing these to YouTube (single song or a whole folder as an album) is the
[`yb`](https://github.com/thorwhalen/yb) package's job — it renders through
`muvid.visualize` and uploads.

## The `music_video` genre — footage assembly

Several people filmed the same gig on their phones. Each recording caught the *same
song* through a different mic, from a different seat, starting at a different moment.
`muvid.footage` puts them all on the clean studio master's timeline and cuts a music
video out of them — no AI, no keys, no cost, just `ffmpeg` and arithmetic.

```python
from muvid.footage import select_edl, validate_edl, derive_cuts
from muvid.footage.align import align_footage
from muvid.footage.edl import fill_gaps
from muvid.footage.assemble import assemble_music_video

song, dur = "master.wav", 205.0
clips = [("c1", "phone_a.mov"), ("c2", "phone_b.mp4"), ("c3", "tripod.mov")]

# 1. Where does each clip sit on the song? (audio cross-correlation, via mixing.audio)
aligns = align_footage(song, clips, song_duration=dur)

# 2. Who is on air over each span? A strategy turns alignments into an EDL,
#    fill_gaps makes it span the whole song, validate_edl is the one gate.
edl = validate_edl(fill_gaps(select_edl("best_confidence", aligns, dur), dur), aligns, dur)

# 3. Cut it, over the clean master audio.
cuts = derive_cuts(edl, aligns, dict(clips))
assemble_music_video(cuts, song, "out.mp4", canvas=(1920, 1080))
```

**The five stages**

| stage | module | what comes out |
|---|---|---|
| align | `muvid/footage/align.py` | per clip: `offset_s`, a confidence, its `coverage` of the song, `overlaps` |
| score | `muvid/footage/scoring/` | a tensor `S[clip, frame, metric]` on ONE shared song-time grid (`hop≈0.1 s`) |
| select | `muvid/footage/strategy.py`, `select_score.py` | an EDL — which clip covers which span |
| validate | `muvid/footage/edl.py` | the one gate: ordered, non-overlapping, gapless, inside each clip's coverage |
| assemble | `muvid/footage/assemble.py` | one ffmpeg pass per cut → concat by stream copy → mux the master |

**What the design commits to**

- **Nothing vanishes because it measured badly.** A clip that doesn't overlap the song
  is still recorded, still listed, still addressable — it just carries `overlaps=False`.
- **A hole is an explicit gap entry**, not an absence. `EdlEntry(clip_id="")` (`null` over
  JSON) means "no footage here, fill it", so the render is always exactly the song's
  length and every span is accounted for by exactly one entry.
- **Bounded memory.** One ffmpeg invocation per cut, then a stream-copy concat — peak
  memory is O(1) in the cut count, not O(n). A 70-cut score-driven edit renders on the
  same box as a 3-cut one.
- **Mixed phones are the normal case.** Clips are scaled and padded onto a fixed canvas
  (`landscape` / `portrait` / `square`), rotation metadata is honoured, and mismatched
  frame rates are unified — so a portrait iPhone clip and a 29.97 fps camera cut together
  without drift.
- **The strategy is a config object, not a branch.** `best_confidence` (default),
  `longest_take` and `fewest_cuts` work from the alignments alone; `weighted` runs a
  beat-snapped Viterbi over the score tensor. Add your own with
  `register_selection_strategy`.

**Scoring** (`pip install 'muvid[scoring]'`) resolves every clip's every metric onto that
one song-time grid, so the same numbers drive both the automatic cut and a human editor's
lanes. The **core tier** — sharpness, exposure, shake, face framing, motion-to-beat, the
selector — is torch-free and MIT/BSD/Apache/ISC only, and is what a deployment installs.
The **lip-sync tier** (`muvid[scoring-lipsync]`: Demucs + SyncNet) is **opt-in and off by
default**: the htdemucs weights are CC-BY-NC (research-only, not commercial-clean), and
it peaks at 2–3 GB on CPU. Turning it on takes the extra, `MUVID_SCORING_ENABLE_LIPSYNC=1`,
*and* weights you point at yourself via `MUVID_SYNCNET_S3FD_WEIGHTS` /
`MUVID_SYNCNET_WEIGHTS` — muvid never downloads model weights at runtime. Without all
three it skips cleanly rather than scoring zero.

**As a hosted genre.** `muvid.genre_music_video` registers `music_video` as an `nw.Genre`
(canvas presets as its Templates) with a project factory backed by
`muvid.footage.workspace.FootageWorkspace` — a stateful per-user project (one song, N
clips, a persisted alignment, score tracks, renders) under `~/.local/share/muvid`
(`MUVID_DATA_HOME` to relocate). `muvid.mcp.footage_tools` and `muvid.mcp.scoring_tools`
expose it as MCP tools (`set_song`, `add_footage`, `align_footage`, `propose_edit`,
`footage_timeline`, `score_footage`, `assemble_music_video`, …), all free.

**As an editor document.** `pip install 'muvid[editor]'` adds
`muvid.footage.lacing_bridge`, which exports a project as
[`lacing`](https://github.com/thorwhalen/lacing) standoff annotations — three published
body schemas, `clip-alignment/v1`, `clip-score-track/v1` and `music-video-edl/v1`, all in
song time on one axis — and reads an edited `DECISION` tier back into an EDL. Annotate →
edit → export → render round-trips to the same cuts.

## 30-second tour

```bash
# Bootstrap a project around a song.
muvid init ~/muvid/park-bench --song ~/Downloads/park_bench.mp3 --title "Park Bench"

# Transcribe to a draft lyrics.md (you'll edit it).
muvid transcribe ~/muvid/park-bench

# … you edit lyrics/lyrics.md to fix mishears and add [section] tags …

# Align lyrics.md against the transcript and write lyrics/alignment.annot.
muvid align ~/muvid/park-bench

# Cast a character: card, then images, then lookbook curation.
muvid character ~/muvid/park-bench maya --description "mid-30s, dark curly hair, wary eyes"
muvid character-generate ~/muvid/park-bench maya --n 6
muvid character-curate    ~/muvid/park-bench maya --k 8

# Establish an environment.
muvid environment ~/muvid/park-bench park_bench --description "wooden park bench at dusk"
muvid environment-render ~/muvid/park-bench park_bench

# Write/edit script/script.md (let an agent draft it from the lyrics + cast),
# then sync it back into project.json:
muvid script-apply ~/muvid/park-bench

# Estimate cost before committing fal calls.
muvid estimate-cost ~/muvid/park-bench

# Render every shot (optionally gated on a USD budget), then composite.
muvid render  ~/muvid/park-bench --budget=2.50
muvid compose ~/muvid/park-bench
# → ~/muvid/park-bench/output/final.mp4

# Inspect progress.
muvid status        ~/muvid/park-bench           # human-readable
muvid status --json ~/muvid/park-bench           # structured shape

# Or open the local UI (FastAPI + single HTML page).
muvid serve ~/muvid/park-bench
```

### Pluggable aligners

`muvid align --aligner=...` accepts:

- `scribe-greedy` (default) — Scribe transcript + greedy token-match.
- `user` — caller-supplied `line_index → (start, end)` timings.
- `whisperx-lite` — local faster-whisper, falls back to scribe-greedy
  if no `audio_path=` is given.
- `stars` — singing-grade joint inference (stub; `NotImplementedError`).

Plug your own with `muvid.align.register_aligner(name, fn, ...)`.

### Interactive character curation

When a recipe's automatic top-k isn't quite right, replay a JSON
of decisions:

```bash
# decisions.json:
# [{"keep": ["<image_id>"], "reject": [...], "stop": false}, ...]
muvid character-curate-interactive ~/muvid/park-bench maya \
    --decisions decisions.json --k 8 --present 6
```

## How it fits the ecosystem

| Concern                          | Owner       |
|----------------------------------|-------------|
| AI media (TTS, image, video, lipsync, voice clone) | `falaw` |
| Reference image curation (LoRA-style sets)         | `lookbook` |
| Timeline / interval annotations (lyrics, sections) | `lacing` |
| Structured 2D animation (cutout characters)        | `an` |
| Audio/video editing, alignment + ElevenLabs Scribe | `mixing` |
| Genre / Template registry, durable async jobs      | `nw` |
| **Visualizer, footage assembly, pipeline, dispatcher** | **`muvid`** |

`muvid` is the orchestrator: a folder layout (`project.json` + `song/`,
`lyrics/`, `characters/`, `environments/`, `script/`, `shots/`,
`output/`), a content-addressed cache (re-render only what changed),
and a uniform dispatch layer with three surfaces (CLI, skill, UI)
all calling the same Python functions in `muvid.facade`.

## Render strategies

Each shot picks one. The dispatcher resolves shared inputs (audio
slice, lyric lines that fall in the shot interval, character / env
anchor images) once and hands them to the strategy:

| strategy        | use it for                                     | calls |
|-----------------|-------------------------------------------------|-------|
| `lipsync`       | character singing on screen                     | `falaw.animate_face` |
| `image_to_video`| cinematic shot, env anchor as i2v seed         | `falaw.image_to_video` |
| `text_to_video` | no anchor, pure prompt                          | `falaw.text_to_video` |
| `animation`     | stylized 2D cutout                              | `an.orchestrate` |
| `still`         | single image held for the duration              | `ffmpeg` |

## The Claude skill

`.claude/skills/muvid/SKILL.md` walks Claude (or any agent that follows
Claude Code skills) through the eight stages. It will:
- run `muvid status` first to see where you are
- pick the next stage and offer to run it
- never re-transcribe after you've edited `lyrics.md`
- never `--force` a render without asking
- offer to draft `script/script.md` from your lyrics + cast

## Layout

```
muvid/
  __init__.py         public surface (the facade)
  __main__.py         CLI (argh)
  schema.py           ProjectSpec, ShotSpec, SectionSpec, …
  project.py          MusicVideoProject (folder facade)
  lyrics.py           transcribe + parse/render lyrics.md
  align.py            pluggable aligners + lacing SqliteStore writer
  characters.py       cards + ref images + lookbook curation (incl. interactive)
  environments.py     cards + establishing-image generation
  script.py           script.md ↔ ShotSpec list
  cost.py             render-cost rollup over pending shots
  events.py           pipe falaw progress events → .muvid/fal_events.jsonl
  contracts.py        adapters: muvid SSOT ↔ falaw / an / lacing shapes
  renderers/
    __init__.py       dispatcher + RenderContext + caching
    lipsync.py        falaw.animate_face
    image_to_video.py falaw.image_to_video
    text_to_video.py  falaw.text_to_video
    still.py          ffmpeg single-image loop
    animation.py      handoff to `an.orchestrate` with lacing-driven lipsync
  compose.py          ffmpeg concat + overlay song audio
  facade.py           top-level verbs the CLI/skill/UI call
  downloads.py        claim()/resolve() — muvid owns resolution, the host owns transport
  visualize/          part 1: the ffmpeg-only audio visualizer (+ its visual registry)
  footage/            part 2: align, edl, strategy, select_score, assemble, workspace
    scoring/          the score tensor: grid, frames, quality, motionbeat, segment, lipsync
    lacing_bridge.py  project ⇄ lacing standoff annotations (the editor document)
  genre.py            registers the `music-visualizer` nw genre (and imports the next)
  genre_music_video.py  registers the `music_video` footage genre
  mcp/                the MCP tool surface: tools, footage_tools, scoring_tools
  ui/
    app.py            FastAPI app
    static/index.html single-page UI
.claude/CLAUDE.md               agent & contributor guide (start here)
.claude/skills/                 muvid, muvid-visualize, muvid-score-footage,
                                muvid-choose-footage-segments
misc/docs/design.md             part 3's design rationale
misc/docs/footage_scoring_design.md    part 2's LOCKED design decisions
misc/docs/footage_scoring_research.md  the citations behind them
misc/docs/improvement_ideas.md  v0 audit + post-audit follow-through
```
