---
name: muvid-lyric-video
description: >-
  Make a lyric video — a typographic music video where the words appear in time
  with the singing — from a song, using muvid. Use when the user has an audio
  file and wants the lyrics animated, a karaoke video, kinetic typography, a
  "words on screen" music video, or wants a poem/lyric laid out in a shape that
  fills in as it is sung. Also use to inspect or hand-edit an existing
  treatment, to choose between treatment options, or when a lyric video came
  out badly and you need to work out why (usually: the word timings were
  interpolated rather than measured). Triggers on "lyric video", "karaoke
  video", "animate these lyrics", "words in time with the music", "kinetic
  typography", "put the lyrics on screen".
metadata:
  audience: users
---

# Making a lyric video with muvid

You are art-directing, not writing a renderer. muvid computes every position
and every time from measurement; your job is to choose a **treatment** and to
notice when the inputs are not good enough for the treatment you chose.

Everything below runs through one command surface:

```bash
python -m muvid.lyricvid --help
```

Install: `pip install 'muvid[lyricvid]'` (add `muvid[lyricvid-web]` only if you
need the browser renderer — see *Choosing a renderer*).

## The workflow

**1. Measure before you decide.**

```bash
python -m muvid.lyricvid analyze song.wav --lyrics lyrics.md
```

Read three fields before anything else:

- `timing_measured` — **if this is `false`, stop and reconsider.** It means the
  word times were interpolated inside lines rather than measured, and any
  treatment that quantises to `word` will look subtly, unfixably out of sync.
  Either get better input (an enhanced `.lrc`, or let muvid align real lyrics
  to the audio) or set `timing.quantize_to` to `line`. Choosing `line` on
  purpose looks deliberate; choosing `word` on bad data looks broken.
- `words_per_second` — above roughly 4, `one_word_centred` becomes a strobe.
  Prefer `karaoke_wipe` or `stacked_lines`.
- `sections` — if the song has real section labels, you can give the chorus a
  different scene from the verses. If it has one `*` section, write one scene.

**2. Get options, and read the rationales.**

```bash
python -m muvid.lyricvid propose song.wav --lyrics lyrics.md --n 3
```

Free and instant — no model is called. Each option comes with a `score`, a
`why`, and the treatment itself. Show the user the *rationales*, not the JSON.
Add `--use-llm` only when the heuristic options are all wrong for the song, and
tell the user first: that call costs money and its price cannot be known ahead
of time.

**3. Render.**

```bash
python -m muvid.lyricvid render song.wav out.mp4 \
    --lyrics lyrics.md --treatment treatment.json
```

Omit `--treatment` to have one chosen. The result carries an `artifacts` map
including the `.ass` subtitle file and the `treatment.json` actually used —
hand those to the user; they are what makes the video editable rather than
opaque.

**4. Iterate on the treatment, not the code.** A treatment is a small JSON file.
Change one field, re-render, look. Validate before rendering if you hand-edited:

```bash
python -m muvid.lyricvid validate treatment.json
```

It repairs near-misses rather than refusing, and lists every substitution it
made — read that list, because a silent repair means you wrote something the
vocabulary does not contain.

## Choosing an archetype

Run `python -m muvid.lyricvid vocabulary` for the authoritative list with
descriptions — it is generated from the code, so it cannot drift from what the
renderer actually supports. In practice:

| the song is… | use |
|---|---|
| anything, and you want it to just work | `one_word_centred` |
| fast, wordy, or you want it singable | `karaoke_wipe` |
| narrative — the words are worth re-reading | `stacked_lines` |
| built on one strong image (a river, an apple, a heart) | `shape_fill` with that shape |
| a poem whose *layout is the point* | `concrete_page` |
| one repeated hook | `text_on_path` |
| chaotic, crowded, instrumental-heavy | `scatter` |

`concrete_page` is the one worth understanding: the whole lyric is typeset once
as a fixed page and each word ignites in reading order as it is sung. Combine
it with `persistence: "dim"` and the page is visible from the first frame in a
faint colour, darkening word by word — that is how you animate a concrete poem
without the layout ever moving.

## Rules you must not break

These exist because breaking them produces a video that is wrong in a way that
is expensive to diagnose later:

- **Never write coordinates.** There is no field for them, on purpose. If you
  want something in a particular place, that is an archetype (or a new one),
  not a number.
- **Never write timestamps.** You choose `timing.quantize_to`; muvid applies it
  to measured onsets. A time you typed is a time that will drift.
- **Only use values from the closed vocabularies.** Anything else gets silently
  repaired to a default, and you will wonder why your motion did nothing.

## Choosing a renderer

`ass` (the default) is frame-exact, needs no browser, renders in seconds, and
leaves a `.ass` file the user can open in Aegisub and hand-fix. Use it unless
you have a specific reason not to.

`web` runs headless Chromium and exists for effects ASS cannot express — CSS
filters, blend modes, arbitrary easing. It is 10–100× slower and needs
`playwright install chromium`. Reach for it only when the treatment genuinely
needs it, and say why.

## When it looks wrong

| symptom | cause | fix |
|---|---|---|
| words consistently early or late | perceived-lag mismatch | `timing.lead_s`, ±0.05 at a time |
| words drift progressively | timings interpolated, not measured | check `timing_measured`; quantise to `line` or get better input |
| text overflows the frame | a very long line | lower `typography.max_line_chars`, or `stacked_lines` |
| a motion does nothing | not in the vocabulary; silently repaired | run `validate` and read `repairs` |
| shape_fill drops words | they did not fit the outline | fewer words per scene, smaller `params.size`, or a rounder shape |

## Adding a new look

The archetypes are a registry (`muvid.lyricvid.scene.register_archetype`) and
the renderers are a registry (`muvid.lyricvid.pipeline.register_renderer`). A
new look is a function plus a vocabulary entry — not a fork. If the user wants
something the vocabulary cannot express, that is the honest answer: name the
archetype that would be needed and offer to add it.

A whole new *kind* of video is a subgenre plugin — see
`python -m muvid.lyricvid subgenres` and `muvid.subgenres`.
