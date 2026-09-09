---
name: muvid-lyric-video
description: >-
  Make a lyric video — a typographic music video where the words appear in time
  with the singing — from a song, using muvid. Use when the user has an audio
  file and wants the lyrics animated, a karaoke video, kinetic typography, a
  "words on screen" music video, or wants a poem/lyric laid out in a shape that
  fills in as it is sung. Covers CONCRETE POETRY and CALLIGRAMS — a poem whose
  shape on the page is the point (Apollinaire's "Il pleut", shaped text, word
  art, ASCII-art-style layouts) — via the `calligram` and `shape_fill`
  archetypes. Also use to inspect or hand-edit an existing treatment, to choose
  between treatment options, or when a lyric video came out badly and you need
  to work out why (usually: the word timings were interpolated rather than
  measured). Triggers on "lyric video", "karaoke video", "animate these
  lyrics", "words in time with the music", "kinetic typography", "put the
  lyrics on screen", "calligram", "concrete poem", "concrete poetry", "shaped
  text", "word art", "make a video of this poem".
  If the user has a poem but NO audio, muvid cannot make the song — generate it
  first with `arioso` (see "You need a song first" below), then come back here.
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

## You need a song first — muvid does not make one

`audio` is the only **required** input, and muvid has no way to produce it. If
the user brings a poem, lyrics, or just a subject and no audio file, that is
not a muvid problem — generate the song with **`arioso`** (`$PP/t/arioso`), a
facade over 14 AI music backends, then come back:

```python
import arioso
songs = arioso.generate_many(
    "slow French chanson, sparse piano, breathy female voice, 62 bpm",
    platform="sunoapi",              # Suno. also: elevenlabs, udio, yue
    lyrics=poem_text,                # sung VERBATIM — not a prompt
    genre="french chanson, ambient", # required alongside lyrics on Suno
    title="Il pleut",                # ditto — omitting it can 400
    model="V5", wait_for_completion=True, timeout=600,
)
open("song.mp3", "wb").write(arioso.fetch_audio(songs[0]).audio_bytes)
```

Only four backends sing **given** lyrics — `sunoapi`, `elevenlabs`, `udio`,
`yue`. On the other ten `lyrics=` is dropped **silently** — every adapter ends
in `**kwargs` and the "warn on unsupported param" path is never reached — so you
get a tune with nothing of the poem in it and nothing tells you. Two more traps:
`instrumental=True` discards `lyrics`, and `SUNO_DEFAULT_MODEL` defaults to `V4`,
whose lyric cap is 3,000 chars rather than V4_5+'s 5,000 — pass `model=`.
This **spends money** (Suno is a paid subscription, and arioso has no cost gate
and no `estimate()`); say so and get agreement before you call it.
The fuller version of all this is the **`arioso`** skill.

## Matching words to a printed layout — use `ocracy`

To reproduce a real printed page you need its geometry, not just its words.
**`ocracy`** (`$PP/t/ocracy`) is the fleet's OCR facade over ~17 engines and
returns per-word **bounding boxes** — enough to measure a scan's line angles
and letter pitch and feed them to `calligram`'s `slants` / `head_offsets`, or
to threshold the scan into a `shape_fill` `mask_image`.

Two traps: each backend emits exactly **one** granularity, so `.words` is empty
on `easyocr`/`rapidocr`/`paddleocr`/`ocrmac` and `.lines` is empty on
`tesseract`; and a per-letter calligram is close to worst-case for OCR — for a
slanting one-letter-per-row layout, blob detection on the thresholded scan is
more reliable than asking an engine to read it.

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
| a poem laid out as lines on a page | `concrete_page` |
| a **calligram** — slanting streaks, the run of the text makes the picture | `calligram` |
| one repeated hook | `text_on_path` |
| chaotic, crowded, instrumental-heavy | `scatter` |

Three of these hold the layout still while it fills in, and choosing between
them is the whole art direction. They are **not** interchangeable:

- **`concrete_page`** typesets the lyric as ordinary lines — one **centred
  horizontal row per line** — and ignites each word in reading order. Use it
  when the poem is lines on a page. It does **not** make a picture: it cannot
  slant, indent or shape anything, and leading whitespace in your lyrics file
  is stripped before it ever gets here, so a hand-drawn ASCII layout pasted
  into `lyrics.md` collapses to centred rows.
- **`calligram`** turns each line into a **slanting streak of upright letters**,
  one letter per grid slot, the streaks fanning open as they descend — the
  Apollinaire *Il pleut* construction. The picture is made by the run of the
  text itself. This is the one to reach for on "calligram" or "concrete poem".
- **`shape_fill`** pours whole words into an outline you supply. Use it when
  the shape is a *picture* (an apple, a heart, a map) rather than something the
  text's own flow draws. It **drops** any word that will not fit, so it is a
  poor choice for thin or spiky outlines.

Combine any of them with `persistence: "dim"` and the page is visible from the
first frame in a faint colour, taking full ink as each word is sung — that is
how you animate a concrete poem without the layout ever moving.

### The `calligram` archetype

All layout is in *row-pitch units* and only fitted to the frame at the end, so
the shape is identical in portrait, landscape and 4K. For streak `k`, slot `i`:
`u = head_offsets[k] + i * slants[k]`, `v = i`. Words are separated by exactly
one empty slot.

| `params` key | default | what it does |
|---|---|---|
| `slant` | `0.19` | `dx/dy` (a tangent) of the first streak |
| `slant_step` | `0.042` | added per streak, so the group fans open |
| `head_gap` | `5.0` | distance between streak heads, in slots |
| `slants` | — | explicit per-streak tangents (list), overrides the two above |
| `head_offsets` | — | explicit per-streak head positions (list), in slots |
| `size_ratio` | `0.62` | cap height as a fraction of the row pitch |
| `stagger` | `1.0` | 0..1 — spread a word's letters over its own duration |
| `top`/`bottom`/`left`/`right` | `.05/.97/.04/.96` | the box to fit inside |

`slants` and `head_offsets` are **shape parameters, not coordinates** — they
describe the fan, not where any letter goes — so passing them does not break
the no-coordinates rule. Use them to reproduce a specific printed page; measure
the angles off a scan rather than guessing. For the 1918 Mercure de France
setting of *Il pleut*: `slants` `[0.186, 0.220, 0.257, 0.298, 0.353]`,
`head_offsets` `[0, 7.0, 11.7, 16.4, 19.3]`, `size_ratio` `0.86`.

A calligram wants a **portrait** frame: ~80 stacked slots against ~40 across is
roughly 1:2, so `--width 1440 --height 2560` gives far larger letters than
1920x1080 would.

### Shapes, for `shape_fill`

`shape.kind` is one of three, and only the first is documented in most places:

| kind | `value` | notes |
|---|---|---|
| `named` | `apple`, `circle`, `heart`, `square`, `star` | extend via `muvid.lyricvid.shape.register_shape` |
| `svg_path` | an inline SVG path (`M/L/H/V/C/S/Q/T/Z`) | arcs (`A`) are refused by name |
| `mask_image` | a path to an image whose dark ink / alpha is the outline | **refused over MCP** (path-oracle risk); local callers only |

`mask_image` is how you pour words into a shape traced from a real picture —
including a scan of a printed page.

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

**`ffmpeg` must have libass, and yours may not.** The `ass` renderer needs the
`subtitles` filter; a build without it fails, and there is usually more than
one ffmpeg on a machine. Check with
`ffmpeg -filters | grep -w subtitles` and, if it is empty, put one that has it
first on `PATH` (on this Mac: `/opt/homebrew/opt/ffmpeg@6/bin`).

**Known gap — `concrete_page` + `persistence: "dim"` does nothing under `ass`.**
That combination signals the dim→bright handover through `Cue.extra["ignite_at"]`,
which only `render_web` reads; ASS implements the opposite ramp (`dim_from`,
bright→dim). Under the default renderer the page therefore comes up fully
bright at frame 1 and never ignites. `calligram` is unaffected — it emits the
handover as an overlap of two cues, which both backends already understand. If
you need a dimming `concrete_page`, pass `--renderer web`.

## When it looks wrong

| symptom | cause | fix |
|---|---|---|
| words consistently early or late | perceived-lag mismatch | `timing.lead_s`, ±0.05 at a time |
| words drift progressively | timings interpolated, not measured | check `timing_measured`; quantise to `line` or get better input |
| text overflows the frame | a very long line | lower `typography.max_line_chars`, or `stacked_lines` |
| a motion does nothing | not in the vocabulary; silently repaired | run `validate` and read `repairs` |
| shape_fill drops words | they did not fit the outline | fewer words per scene, smaller `params.size`, or a rounder shape |
| accented words split or vanish (`même` → `m`,`me`; `ô` gone) | muvid ≤ 0.0.61 tokenised ASCII-only | fixed in `muvid.align` / `lyricvid.timed_text`; upgrade muvid |
| the page is bright from frame 1 and never ignites | `concrete_page` + `dim` under `ass` | `--renderer web`, or use `calligram` |
| a calligram is a stack of centred rows | you used `concrete_page` | use `calligram` |
| letters tiny | a calligram in landscape | render portrait, e.g. `--width 1440 --height 2560` |

## Adding a new look

The archetypes are a registry (`muvid.lyricvid.scene.register_archetype`) and
the renderers are a registry (`muvid.lyricvid.pipeline.register_renderer`). A
new look is a function plus a vocabulary entry — not a fork. If the user wants
something the vocabulary cannot express, that is the honest answer: name the
archetype that would be needed and offer to add it.

A whole new *kind* of video is a subgenre plugin — see
`python -m muvid.lyricvid subgenres` and `muvid.subgenres`.
