# mtv

Tools to make music videos. Orchestrates the local
ecosystem (`falaw`, `lookbook`, `lacing`, `an`, `mixing`) into a
song-to-video pipeline. The user is the director; an agent (Claude in
the terminal, or the local web UI) drives the stages.

> **Status:** v0. The pipeline (init → transcribe → align → cast →
> environments → script → render → compose) works end to end. Render
> strategies: `lipsync`, `image_to_video`, `text_to_video`,
> `animation`, `still`. CLI, Claude skill (`.claude/skills/mtv/`),
> and a single-page local UI all dispatch to the same Python
> functions. See [`misc/docs/design.md`](misc/docs/design.md) for the
> full design rationale and
> [`misc/docs/alignment_references.md`](misc/docs/alignment_references.md)
> for the lyric-alignment literature mtv builds on.

## Install

```bash
pip install -e ./mtv
pip install -e ./mtv[ui]   # adds FastAPI + uvicorn for the web UI
```

This package depends on local sibling packages (`falaw`, `lookbook`,
`lacing`, `mixing`); install them editable first.

System: `ffmpeg` and `ffprobe` on `PATH`. Env: `ELEVENLABS_API_KEY`
(for transcription), `FAL_KEY` (for fal.ai generation).

## 30-second tour

```bash
# Bootstrap a project around a song.
mtv init ~/mtv/park-bench --song ~/Downloads/park_bench.mp3 --title "Park Bench"

# Transcribe to a draft lyrics.md (you'll edit it).
mtv transcribe ~/mtv/park-bench

# … you edit lyrics/lyrics.md to fix mishears and add [section] tags …

# Align lyrics.md against the transcript and write lyrics/alignment.annot.
mtv align ~/mtv/park-bench

# Cast a character: card, then images, then lookbook curation.
mtv character ~/mtv/park-bench maya --description "mid-30s, dark curly hair, wary eyes"
mtv character-generate ~/mtv/park-bench maya --n 6
mtv character-curate    ~/mtv/park-bench maya --k 8

# Establish an environment.
mtv environment ~/mtv/park-bench park_bench --description "wooden park bench at dusk"
mtv environment-render ~/mtv/park-bench park_bench

# Write/edit script/script.md (let an agent draft it from the lyrics + cast),
# then sync it back into project.json:
mtv script-apply ~/mtv/park-bench

# Render every shot, then composite.
mtv render  ~/mtv/park-bench
mtv compose ~/mtv/park-bench
# → ~/mtv/park-bench/output/final.mp4

# Or open the local UI (FastAPI + single HTML page).
mtv serve ~/mtv/park-bench
```

## How it fits the ecosystem

| Concern                          | Owner       |
|----------------------------------|-------------|
| AI media (TTS, image, video, lipsync, voice clone) | `falaw` |
| Reference image curation (LoRA-style sets)         | `lookbook` |
| Timeline / interval annotations (lyrics, sections) | `lacing` |
| Structured 2D animation (cutout characters)        | `an` |
| Audio/video editing + ElevenLabs Scribe            | `mixing` |
| **Project, pipeline, dispatcher**                  | **`mtv`** |

`mtv` is the orchestrator: a folder layout (`project.json` + `song/`,
`lyrics/`, `characters/`, `environments/`, `script/`, `shots/`,
`output/`), a content-addressed cache (re-render only what changed),
and a uniform dispatch layer with three surfaces (CLI, skill, UI)
all calling the same Python functions in `mtv.facade`.

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

`.claude/skills/mtv/SKILL.md` walks Claude (or any agent that follows
Claude Code skills) through the eight stages. It will:
- run `mtv status` first to see where you are
- pick the next stage and offer to run it
- never re-transcribe after you've edited `lyrics.md`
- never `--force` a render without asking
- offer to draft `script/script.md` from your lyrics + cast

## Layout

```
mtv/
  __init__.py         public surface (the facade)
  __main__.py         CLI (argh)
  schema.py           ProjectSpec, ShotSpec, SectionSpec, …
  project.py          MusicVideoProject (folder facade)
  song.py             (probing via ffprobe lives in project.py)
  lyrics.py           transcribe + parse/render lyrics.md
  align.py            greedy token-match → lacing SqliteStore
  characters.py       cards + ref images + lookbook curation
  environments.py     cards + establishing-image generation
  script.py           script.md ↔ ShotSpec list
  render/
    __init__.py       dispatcher + RenderContext + caching
    lipsync.py        falaw.animate_face
    image_to_video.py falaw.image_to_video
    text_to_video.py  falaw.text_to_video
    still.py          ffmpeg single-image loop
    animation.py      handoff to `an.orchestrate`
  compose.py          ffmpeg concat + overlay song audio
  facade.py           top-level verbs the CLI/skill/UI call
  ui/
    app.py            FastAPI app
    static/index.html single-page UI
.claude/skills/mtv/SKILL.md
misc/docs/design.md   full design rationale
```
