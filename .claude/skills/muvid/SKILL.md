---
name: muvid
description: Use when the user wants to make a music video from a song. Triggers on "make a music video", "turn this song into a video", "/muvid", or any work inside an muvid project folder (look for project.json with muvid schema_version). Walks the user through getting lyrics, aligning to audio, casting characters, picking environments, writing the script, and rendering shots.
---

# muvid — guide a user from song to music video

You are operating an `muvid` project: a folder containing a `project.json`,
a song under `song/`, and progressively-filled-in artifacts under
`lyrics/`, `characters/`, `environments/`, `script/`, `shots/`,
`output/`. The Python package `muvid` exposes every step as a function;
the CLI `muvid <verb>` is a thin dispatcher.

Your job is to **drive that pipeline interactively** — pick the right
next step based on the project's current state, run the command, show
the user what came out, and ask just enough questions to keep moving.

## The pipeline (eight stages, each idempotent)

1. **init** — `muvid init <root> --song <audio>`. Fresh project, song
   probed.
2. **transcribe** — `muvid transcribe <root>`. ElevenLabs Scribe writes
   `lyrics/transcript.json` and a draft `lyrics/lyrics.md`.
3. **edit lyrics** — *user task*. The user opens `lyrics/lyrics.md`,
   fixes mishears (singing transcripts always have some), and adds
   real `[section]` headers like `[verse 1]`, `[chorus]`, `[bridge]`.
4. **align** — `muvid align <root>`. Builds `lyrics/alignment.annot` (a
   `lacing` SqliteStore) with three tiers: sections / lines / words.
   Also syncs the parsed sections into `project.json`.
   *Aligner choice:* `--aligner=scribe-greedy` (default), `user`,
   `whisperx-lite`, or `stars` (stub). The default is right for most
   cases; switch to `whisperx-lite` for offline runs and `user` when
   you've authored line timings yourself. The alignment store is the
   SSOT for word timings — animation renders read it directly instead
   of letting `an` re-transcribe.
5. **cast characters** — for each character:
   - `muvid character <root> <name> --description "..."`
   - `muvid character-images <root> <name> path1 path2 ...` (drop user
     photos) or `muvid character-generate <root> <name> --n 6` (use fal
     to generate variants).
   - `muvid character-curate <root> <name> --k 8` (lookbook picks the
     best diverse subset). When the user wants a say in which
     specific images survive, use
     `muvid character-curate-interactive <root> <name> --decisions <file>`
     where the decisions file is a JSON list of
     `{"keep": ["<image_id>"], "reject": [...], "stop": false}`
     records (one per round).
6. **establish environments** — `muvid environment <root> <name>
   --description "..." --time-of-day "..."` then
   `muvid environment-render <root> <name>`.
7. **write the script** — author `script/script.md` (you can draft this
   from lyrics + characters + environments; the user edits). Then
   `muvid script-apply <root>` to upsert sections+shots into the SSOT.
8. **render** — `muvid render <root>` (all shots) or `muvid render <root>
   --shot s02`. Per-shot strategies: `lipsync`, `image_to_video`,
   `text_to_video`, `animation`, `still`. *Cost gate:* pass
   `--budget 2.50` to refuse to start if the estimate exceeds the
   given USD budget. Use `muvid estimate-cost <root>` to preview the
   rollup before committing.
9. **compose** — `muvid compose <root>`. Concatenates shots, overlays
   the original song.

## Always start by reading state

Before doing anything, run `muvid status <root>` (or
`muvid status --json <root>` when you want to grep into it). The
output shows stage progression, per-shot render state, alignment
quality (lines / words / confidence histogram), and an estimated USD
remaining-render cost. Pick the next stage where a flag is False or
a count is zero. **Never** re-run a stage that's already done unless
the user asks — every stage is idempotent but they cost API calls
(Scribe, fal).

## Lyric editing — the part that needs you most

Step 3 (editing `lyrics/lyrics.md`) is the highest-leverage human
input. The format:

```
[intro]
(instrumental)

[verse 1]
I came down to the river       // 12.5
to wash my soul                // 16.2

[chorus]
hold my hand
when the night comes calling
```

- `[label]` headers tag sections. Use what fits the song
  (intro/verse/chorus/bridge/outro/breakdown/etc.).
- `(parenthesized)` lines are non-vocal placeholders.
- `// 12.5` after a line is an optional manual start anchor in
  seconds. Useful for sections where Scribe got the timing wrong;
  not required.
- The order of lines must match the song.

After editing, run `muvid align` and read the resulting alignment
quickly: lines whose `start_s` look obviously wrong (out of order,
or in the middle of an instrumental break) are usually mishears
that need a fix. Re-edit, re-align.

> **Background**: the trade-offs around different alignment
> approaches (Scribe + greedy match vs. WhisperX CTC vs. singing-grade
> systems like STARS) are summarized in
> `misc/docs/alignment_references.md`. v0 deliberately uses the
> cheapest path because the user is the source of truth for the
> lyric text anyway.

## Choosing render strategies

For each shot, pick:

- `lipsync` — character is singing on screen. Needs a curated
  character image. Calls `falaw.animate_face` (image+audio →
  talking-head). Best for verses/choruses with the singer in frame.
- `image_to_video` — cinematic shot. Uses the environment image as
  the i2v seed (or generates a fresh storyboard still). Great for
  intro/outro establishing shots and instrumental passages.
- `text_to_video` — pure prompt → video, no anchor image. Use
  sparingly (no character/location consistency).
- `animation` — hand off to the `an` package for stylized 2D cutout
  animation. Best for lyric-video passages or surreal sequences.
- `still` — single image held for the duration. Cheapest. Good for
  title cards, transitions.

When proposing a script, default to a sensible mix:
- 1 establishing `image_to_video` per major section
- `lipsync` for verses/chorus when a character is featured
- `still` or `animation` for short bridges and outros

## When you draft the script for the user

Read the project: `muvid status`, then read `lyrics/lyrics.md`,
character cards (`characters/<name>/card.json`), environment cards
(`environments/<name>/card.json`). Write `script/script.md` in this
shape:

```markdown
# <project title> — script

## [intro] 0.00 → 12.50

### s01 | 0.00-12.50 | image_to_video
**env**: park_bench  **camera**: slow push-in
A wide of the empty park bench at golden hour. Leaves drifting.

## [verse 1] 12.50 → 35.00

### s02 | 12.50-22.00 | lipsync
**env**: park_bench  **chars**: maya
Medium close on Maya. She begins to sing, looking off-camera.

### s03 | 22.00-35.00 | image_to_video
**env**: park_bench  **chars**: maya
Push in to a tight close-up. She closes her eyes on the last word.
```

Rules:
- Shot ids are `s01`, `s02`, ... in timeline order.
- Each shot's `[start-end]` MUST fit inside the section that contains
  it. Shots within a section MUST cover its span without gaps or
  overlap (or there will be silent / black gaps in the final video).
- Never propose a `lipsync` shot that doesn't reference a character
  with at least one curated image — check before suggesting it.

After writing the markdown, **show the user the diff** (or just the
content) and ask "edit anything before I apply this?" before running
`muvid script-apply`.

## Render walk

When the user says "render it", default to:
1. Confirm with `muvid status` that all stages 1–7 are done.
2. Render shot-by-shot (`muvid render --shot s01`, then `s02`, ...) so
   if one fails the others aren't lost. Show the user each output as
   it lands.
3. After all shots succeed: `muvid compose`.
4. Print the final mp4 path and recommend they open it.

If a shot's render strategy fails (fal API error, no character
image), explain the failure and offer the smallest fix (different
strategy, generate the missing image, etc.) — don't just retry.

## Things to never do

- Never re-run `muvid transcribe` after the user has edited
  `lyrics/lyrics.md` — it would clobber the transcript that the
  alignment depends on. (The lyrics.md only gets clobbered if it
  doesn't already exist, but transcript.json gets overwritten.)
- Never auto-edit `lyrics/lyrics.md`. The user is the source of
  truth for what the song actually says.
- Never call `muvid render` with `--force` unless the user explicitly
  asks; renders cost real money on fal.
- Never compose before all shots have rendered outputs.

## Useful inspection commands

- `muvid status <root>` — human-readable summary (stages, render bar,
  alignment quality, estimated remaining cost).
- `muvid status --json <root>` — same data structured. Pipe through
  `jq '.stages.render'` etc.
- `muvid estimate-cost <root>` — preview the per-shot USD rollup
  before committing to `muvid render`.
- `cat <root>/project.json | jq` — the SSOT.
- `cat <root>/lyrics/lyrics.md` — the user's lyrics.
- `ls <root>/shots/` — see which shots have been rendered (each one
  is a folder with `output.mp4` if done).
- `cat <root>/.muvid/decisions.jsonl` — append-only log of every
  pipeline action.
- `tail -f <root>/.muvid/fal_events.jsonl` — live falaw progress
  events (one JSON per line) emitted during any fal-touching run.

## When the user starts from scratch

If the user just says "make a music video from `~/Downloads/song.mp3`"
and there's no project yet:

1. Pick a sensible project root (ask if unsure). Default to
   `~/muvid/<song-stem>`.
2. `muvid init <root> --song <path>` — show the resulting folder.
3. `muvid transcribe <root>` — show the draft `lyrics.md`.
4. Pause. Tell the user: "Open `<root>/lyrics/lyrics.md`, fix any
   mishears, and add real section tags. Tell me when it's done."
5. When they confirm, `muvid align <root>` → show them how many
   sections/lines/words got aligned.
6. Walk them through casting (one character at a time), then
   environments, then drafting the script, then rendering.

Move at the user's pace; offer the next step but never run more
than one stage of the pipeline without checking in.
