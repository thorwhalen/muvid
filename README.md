# muvid

Tools to make music videos. Orchestrates the local
ecosystem (`falaw`, `lookbook`, `lacing`, `an`, `mixing`) into a
song-to-video pipeline. The user is the director; an agent (Claude in
the terminal, or the local web UI) drives the stages.

> **Status:** v0+. The pipeline (init → transcribe → align → cast →
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

muvid has **two independent halves**: the AI narrative pipeline above, and
[`muvid.visualize`](#muvidvisualize--audio--cover--video) — a lightweight,
deterministic, ffmpeg-only path that turns a song and a cover into an
audio-reactive visualizer video (no AI, no network).

## Install

```bash
pip install muvid              # core: CLI + muvid.visualize (needs ffmpeg + mixing)
pip install 'muvid[ai]'        # the narrative pipeline (falaw, lacing, lookbook)
pip install 'muvid[ui]'        # FastAPI + uvicorn for the web UI
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
| Audio/video editing + ElevenLabs Scribe            | `mixing` |
| **Project, pipeline, dispatcher**                  | **`muvid`** |

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
  ui/
    app.py            FastAPI app
    static/index.html single-page UI
.claude/skills/muvid/SKILL.md
misc/docs/design.md             full design rationale
misc/docs/improvement_ideas.md  v0 audit + post-audit follow-through
```
