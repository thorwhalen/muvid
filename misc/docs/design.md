# muvid design — song → music video

## Thesis

A music video is a **timeline-locked Scene**: every visual decision is
anchored to a position in the song. The director's job is to populate
that timeline with intent (lyrics, sections, characters, environments,
shots, animation actions); the renderer's job is to turn intent into
frames synced to the audio.

`muvid` is the orchestrator. It does **not** re-implement TTS, lip-sync,
image generation, video generation, character curation, audio editing,
or interval annotation. Those are owned by sibling packages already in
the local ecosystem:

| Concern                           | Owner       | Surface used |
|-----------------------------------|-------------|--------------|
| AI media generation (fal.ai)      | `falaw`     | `generate_image`, `image_to_video`, `text_to_video`, `animate_face`, `lipsync`, `voice_clone`, `text_to_speech`, `Scene`/`Beat`/`Character`/`Environment` |
| Character reference curation      | `lookbook`  | `curate(...)` with the `person` recipe |
| Timeline / interval annotation    | `lacing`    | `MemoryStore` / `SqliteStore`, `JAMS` adapter, body schema registry |
| Structured 2D animation           | `an`        | Scene IR, cutout renderer, whisper lip-sync, character authoring |
| Audio/video editing               | `mixing`    | `Audio`, `Video`, `transcribe` (ElevenLabs Scribe), `concatenate_audio`, `extract_audio`, `apply_keeps` |

`muvid` adds: the **MusicVideoProject** (a directory layout + a SSOT JSON
+ a `dol`-backed mall), the **song timeline** (sections + lyrics +
shots), the **alignment pipeline** (audio → words → user-edited),
and the **render dispatch** (one of: AI generative video, structured
animation, lip-sync over a still, or hybrid).

The Claude skill and the minimal UI are both thin clients that drive
the same Python functions.

## The artifacts (what a project is)

A project lives in a directory. Plain files; everything is inspectable
and Git-trackable.

```
my-video/
├── project.json              # SSOT: everything that defines the video
├── song/
│   ├── audio.<ext>           # the master audio (mp3 / wav / m4a)
│   └── audio.info.json       # duration, sample_rate, bitrate
├── lyrics/
│   ├── transcript.json       # ElevenLabs Scribe output (word-timestamped)
│   ├── lyrics.md             # human-edited lyrics with [section] tags
│   └── alignment.annot       # lacing SqliteStore: words/lines/sections tiers
├── characters/
│   └── <name>/
│       ├── card.json         # falaw.Character (name, description, voice)
│       ├── refs/             # raw image dump (input to lookbook)
│       └── selected/         # lookbook curate output (the LoRA set)
├── environments/
│   └── <name>/
│       └── card.json         # falaw.Environment
├── script/
│   └── script.md             # human-editable screenplay (sections & shots)
├── shots/
│   └── <shot_id>/
│       ├── shot.json         # falaw.Shot + start_s/end_s + render_strategy
│       ├── storyboard.png    # storyboard still (cached)
│       └── output.mp4        # rendered shot
├── output/
│   └── final.mp4             # composited final
└── .muvid/
    └── decisions.jsonl       # decisions log (why each choice was made)
```

### Why a folder, not a database

The user creates the artifacts incrementally with the help of an
agent (Claude in the terminal, or the future web UI). Plain folders
are diffable, versionable, and let any tool — lookbook, lacing,
falaw, an, mixing, ffmpeg — operate on the right slice without
pulling the whole project in.

### `project.json` — the SSOT

```json
{
  "schema_version": 1,
  "title": "Park Bench Blues",
  "song": {
    "audio_path": "song/audio.mp3",
    "duration_s": 198.4,
    "bpm": 96
  },
  "characters": ["maya", "charlie"],
  "environments": ["park_bench", "diner"],
  "sections": [
    {"id": "intro",   "start_s": 0.0,   "end_s": 12.5,  "label": "intro"},
    {"id": "verse_1", "start_s": 12.5,  "end_s": 35.0,  "label": "verse"},
    {"id": "chorus_1","start_s": 35.0,  "end_s": 55.0,  "label": "chorus"}
  ],
  "shots": [
    {"id": "s01", "start_s": 0.0,  "end_s": 12.5,
     "section_id": "intro", "render_strategy": "image_to_video",
     "environment": "park_bench", "characters": [], "description": "..."},
    {"id": "s02", "start_s": 12.5, "end_s": 22.0,
     "section_id": "verse_1", "render_strategy": "lipsync",
     "environment": "park_bench", "characters": ["maya"], "description": "..."}
  ],
  "global_style": "70s 35mm film grain, warm golden hour, slight halation"
}
```

## The pipeline (`song → music video`)

Eight stages. Each stage is **idempotent** (re-running with the same
inputs is a no-op via content-hash caching) and produces a distinct
artifact the user can inspect/edit.

### Stage 1 — Bootstrap project

`muvid init <name> --song <audio>` creates the directory, copies the
audio in, probes its duration with ffprobe, writes a stub
`project.json`. No network calls.

### Stage 2 — Get lyrics

Three modes the user can mix:

1. **Auto-transcribe**: `mixing.transcript.transcribe(audio_path)` →
   ElevenLabs Scribe → word-timestamped JSON. Cheap, fast, surprisingly
   good even on sung audio (Scribe handles music). Save as
   `lyrics/transcript.json`.
2. **User-provided**: user pastes lyrics into `lyrics/lyrics.md` with
   `[section]` markers. We then *align* by running Scribe and
   matching the user's text against the transcribed words (Levenshtein
   on collapsed-tokens) to import accurate timestamps.
3. **Hybrid**: Scribe gives a draft; user edits the markdown to fix
   mishears (very common with singing).

The Claude skill walks the user through this and writes the canonical
`lyrics.md` with section tags like:

```
[intro]
(instrumental)

[verse 1]
I came down to the river  // 12.5
to wash my soul          // 16.2
```

The `// <seconds>` comments are line-start anchors the user can type
or drag to adjust.

### Stage 3 — Build the alignment

`muvid align` produces `lyrics/alignment.annot` — a `lacing` SqliteStore
with three tiers:

- `sections` (stereotype `NONE`) — non-overlapping song sections
- `lines` (stereotype `INCLUDED_IN(sections)`) — lyric lines
- `words` (stereotype `INCLUDED_IN(lines)`) — individual words

Body schemas:

- `annot://schema/song-section/v1` — `{label, energy, mood}`
- `annot://schema/lyric-line/v1` — `{text, line_index}`
- `annot://schema/lyric-word/v1` — `{text, confidence}` (already in
  lacing as the built-in `word` schema; we extend with `confidence`)

Why lacing: we need overlap queries ("which lines are inside this
chorus?", "which words are in shot s05?"), validation (no overlapping
sections), and round-tripping (export to JAMS for later musicology).

### Stage 4 — Cast characters

For each character the user wants in the video:

1. `muvid character new <name>` writes a stub `card.json` (description,
   voice slot).
2. **Reference acquisition**: user drops images into
   `characters/<name>/refs/` *or* `muvid character generate <name>` runs
   `falaw.generate_image` N times with style variants and saves them
   into `refs/`.
3. **Curation**: `muvid character curate <name>` runs
   `lookbook.curate(refs_dir, recipe="person", k=20)` and copies the
   selected images to `selected/`. The first selected image becomes
   the `Character.reference_image_url` (used as the lipsync anchor).
4. **Voice**: optional. `muvid character voice <name> <ref_audio>` saves
   a voice id (ElevenLabs preset) or a reference audio for cloning.

A `falaw.Character` is then materialized from the card and is what
downstream stages consume.

### Stage 5 — Establish environments

Same pattern: `muvid env new <name>` → user provides a description →
`muvid env render <name>` calls `falaw.establish_environment(...)` to
generate the canonical establishing image. Stored as
`environments/<name>/card.json` with a `reference_image_url`.

### Stage 6 — Write the script (shots)

The script is a markdown screenplay anchored to the song timeline.
The agent helps the user produce it; the canonical form looks like:

```markdown
# Park Bench Blues — script

## [intro] 0.0 → 12.5

### s01 | 0.0–12.5 | image_to_video
**env**: park_bench  **camera**: slow push-in
A wide of the empty park bench at golden hour. Leaves drifting.

## [verse 1] 12.5 → 35.0

### s02 | 12.5–22.0 | lipsync
**env**: park_bench  **chars**: maya
Medium close on Maya. She begins to sing, looking off-camera.
**lyrics**: lines 1–2

### s03 | 22.0–35.0 | image_to_video
**env**: park_bench  **chars**: maya
Push in to a tight close-up. She closes her eyes on the last word.
```

`muvid script parse` parses this into a list of `Shot` records and
slots them into `project.json`. The agent can also generate a draft
script from `(lyrics, characters, environments, style)` via
`falaw.parse_screenplay`, then the user edits the markdown.

The render strategy per shot is one of:

- `lipsync` — render a short lip-synced clip of a character singing.
  Uses `falaw.lipsync` if we have a video clip of the character, or
  `falaw.animate_face` if we only have a still.
- `image_to_video` — `falaw.image_to_video(storyboard, prompt)` for
  cinematic shots without a singing performance.
- `text_to_video` — `falaw.text_to_video(prompt)` when no anchor.
- `animation` — hand off to `an` for structured 2D animation
  (cutout characters, scripted actions). Best for stylized lyric-video
  passages and animated skits.
- `still` — a static image (Ken Burns optional). Cheapest.

### Stage 7 — Render shots

`muvid render <shot_id>` (or `muvid render --all`) walks shots in order:

1. Compute a content hash of `(shot.json, dependencies)` — skip if
   `output.mp4` exists and matches.
2. Resolve dependencies: pull lyrics for this shot's interval from
   `alignment.annot`, look up character `reference_image_url`,
   environment `reference_image_url`.
3. Build a storyboard still via `falaw.storyboard_shot` (cached).
4. Dispatch on `render_strategy`:
   - `lipsync`: extract the audio slice (`mixing.audio.crop_audio` on
     `[start_s, end_s]`), call `falaw.animate_face(character_image,
     audio_slice)` if still, else `falaw.lipsync(video, audio_slice)`.
   - `image_to_video`: `falaw.image_to_video(storyboard, prompt,
     extra={"duration": end_s - start_s})`.
   - `text_to_video`: `falaw.text_to_video(prompt, extra={...})`.
   - `animation`: write an `an` `scene.md` for this shot's interval
     (lyrics → dialogue blocks, characters → entities) and call
     `an.orchestrate.orchestrate(shot_dir)`.
5. Download the result, trim/pad to exact `[start_s, end_s]` length
   with `mixing.video`, save to `output.mp4`.

### Stage 8 — Composite

`muvid compose` concatenates all shots with `mixing.video.concatenate`,
then `mixing.video.replace_audio` to overlay the original song over
the visual track. Writes `output/final.mp4`.

## Lyric → audio alignment (the hard problem)

Singing alignment is genuinely harder than speech (phoneme durations
vary wildly with held notes and melisma; rhythmic structure breaks
the priors speech-aligners assume). See
[`alignment_references.md`](alignment_references.md) for the
literature muvid builds on (WhisperX's CTC forced-alignment system,
and the STARS unified singing-annotation framework). We have three
acceptable solutions and pick the best per case:

1. **ElevenLabs Scribe** (default): handles sung English well enough
   for prototyping. Gives us word-level timestamps in one call, no
   model download. Already wrapped in `mixing.transcript.transcribe`.
   Confidence is encoded per word.
2. **WhisperX / faster-whisper** (offline, free): the `an` package
   already ships `WhisperLipSync` which uses `faster-whisper` for
   word-timestamped transcription. We re-use it for offline mode.
   Less accurate on singing but free and local.
3. **User-corrected**: the canonical surface for the user is
   `lyrics.md`. Auto-alignment is the *seed*, not the truth. The user
   edits text and drags line-start anchors; the alignment store is
   re-derived.

The skill nudges the user to listen-and-correct critical sections
(the chorus you'll repeat 4×, the bridge with the trickiest
phrasing). Internal validation: after the user edits, we re-run
Scribe over each section and warn if the user's text and the
re-transcription diverge by more than a token-edit threshold.

## Lip-sync (per shot)

For each shot rendered with `render_strategy: "lipsync"`:

1. Extract the song's audio slice for `[start_s, end_s]` (the part of
   the song the character is "singing on screen").
2. If we have a still image of the character (the lookbook-selected
   anchor): `falaw.animate_face(image, audio_slice)` — fal.ai's
   ai-avatar / omnihuman models handle the lip motion.
3. If we have a video clip (the user dropped in a video of the
   character): `falaw.lipsync(video, audio_slice)`.
4. For stylized animation (cartoon characters in `an`):
   `an` already runs `WhisperLipSync` on synthesized TTS. For an
   `muvid` integration we **re-use** the existing audio slice — same
   audio path, same word timestamps from our alignment store —
   and feed those into the cutout renderer's viseme track. The
   alignment store IS the lip-sync source of truth across modes.

## What `muvid` is *not* doing in v0

- No song generation (we accept an audio file; could later wire
  `falaw.text_to_speech` → DiffRhythm/Lyria-2 for AI music).
- No multi-character split-screen lip-sync. v0 lip-syncs one character
  per shot.
- No real-time preview. Renders are batch.
- No SaaS UI. The "frontend" in v0 is a small local web app
  (FastAPI + a single HTML page) that wraps the same Python
  functions the CLI and skill use.

## Public surface (what the user calls)

Every stage is callable three ways from the same Python function:

1. **Python**: `from muvid import init, align, cast_character, render`
2. **CLI** (`muvid ...`): argh dispatch over the same functions.
3. **Skill** (Claude Code): `.claude/skills/muvid/SKILL.md` walks Claude
   through the orchestration so a user can sit in a terminal and say
   "make a music video from `song.mp3`" and get walked through the
   stages.

Later, a fourth surface (HTTP / web UI) lights up via the same
dispatch pattern (the `argh`-style introspection feeds a small
FastAPI app — `qh` already does this in this ecosystem).

## File layout for the package

```
muvid/
  __init__.py            # public surface
  __main__.py            # CLI (argh)
  project.py             # MusicVideoProject (dol-backed mall + SSOT)
  schema.py              # ProjectSchema, SectionSpec, ShotSpec, ...
  song.py                # song probing (duration, bpm if librosa avail)
  lyrics.py              # transcribe + parse_lyrics_md + write_lyrics_md
  align.py               # build alignment.annot from transcript + lyrics.md
  characters.py          # character cards + curate via lookbook
  environments.py        # environment cards + render via falaw
  script.py              # parse/write the script markdown ↔ shots
  render/
    __init__.py
    dispatch.py          # render_shot dispatcher
    lipsync.py           # render_strategy="lipsync"
    image_to_video.py    # render_strategy="image_to_video"
    text_to_video.py     # render_strategy="text_to_video"
    animation.py         # render_strategy="animation" (handoff to `an`)
    still.py             # render_strategy="still"
  compose.py             # concat shots + overlay song audio
  ui/
    app.py               # FastAPI app
    static/
      index.html         # single-page UI
  data/
    skills/muvid/SKILL.md  # the Claude skill (also linked to .claude/)
.claude/skills/muvid/SKILL.md  # symlink/copy of the above
```

## Status / phasing

- **Phase 0** — this design doc.
- **Phase 1** — `project.py`, `schema.py`, `song.py`, `lyrics.py`,
  `align.py`, CLI for these. Skill describes the workflow.
- **Phase 2** — `characters.py`, `environments.py`, `script.py`. CLI
  + skill steps for cast & shoot prep.
- **Phase 3** — `render/` — at minimum `image_to_video` and `lipsync`.
- **Phase 4** — `compose.py`, end-to-end `make_music_video` facade.
- **Phase 5** — UI (FastAPI + single HTML page) reusing the same
  Python functions.
