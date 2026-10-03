# muvid.align

Lyric → audio alignment.

We have:

- a transcript (Scribe / faster-whisper) with word-level (text, start, end)
- a user-edited `LyricsDoc` with section labels + line text + optional
  manual line-start anchors

We want a `lacing` store with three tiers (sections, lines, words) so
the rest of the system can ask “which lines fall in shot X” without
re-implementing interval math.

Strategy (greedy token-match):

1. Tokenize each lyric line into normalized words.
2. Walk the transcript word stream once, assigning each transcript word
   to the next unmatched lyric word that matches (case- and
   punctuation-insensitive). Tolerate small mismatches (transcript
   word missing in lyrics, vice versa) with a small lookahead window.
3. From the matched words, derive line `[start, end]` as
   `(first_matched_word.start, last_matched_word.end)`. If a line has
   *no* matched words, fall back to the user’s manual anchor (if any),
   then to a linear interpolation between neighboring anchored lines.
4. Sections inherit `[start, end]` from the union of their lines;
   if the user provided explicit `start_s` / `end_s` on a section,
   those win.

The result is written as a `lacing.SqliteStore` so it round-trips and
can be edited by other tools.

### Module Attributes

| [`AlignerName`](#muvid.align.AlignerName)              | Built-in aligner names.                                                                                                                                                                                                    |
|---------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`WHISPERX_LITE_MODEL_SIZE`](#muvid.align.WHISPERX_LITE_MODEL_SIZE) | `tiny` is fast but, on sung/repetitive audio, produces a transcript so garbled that the greedy matcher walks past whole minutes before finding a plausible match (muvid#101: measured 53 s late on a repeated-word lyric). |

### Functions

| [`align_lyrics`](#muvid.align.align_lyrics)(lyrics, transcript, \*[, ...])        | Align a `LyricsDoc` to a transcript.                       |
|-----------------------------------------------------------------------------------------------------|------------------------------------------------------------|
| [`align_scribe_greedy`](#muvid.align.align_scribe_greedy)(lyrics, transcript, \*[, ...]) | Greedy token-match against a word-timestamped transcript.  |
| [`align_stars`](#muvid.align.align_stars)(lyrics, transcript, \*[, duration_s])  | Singing-specific alignment (STARS / similar).              |
| [`align_user_provided`](#muvid.align.align_user_provided)(lyrics, transcript, \*[, ...]) | Use line-level timings the caller has already determined.  |
| [`align_whisperx_lite`](#muvid.align.align_whisperx_lite)(lyrics, transcript, \*[, ...]) | Local-only aligner that re-uses `faster-whisper` (no API). |
| `aligner_info`(name)                                                                                |                                                            |
| [`list_aligners`](#muvid.align.list_aligners)()                                    | Return all registered aligner names, sorted.               |
| [`register_aligner`](#muvid.align.register_aligner)(name, fn, \*, description[, ...]) | Register an aligner under `name`.                          |
| [`write_alignment_store`](#muvid.align.write_alignment_store)(alignment, \*, path[, ...])  | Write alignment to a `lacing.SqliteStore` file (.annot).   |

### Classes

| [`AlignerSpec`](#muvid.align.AlignerSpec)(\*, name, description, fn[, requires])   | One row in the aligner registry.                           |
|-------------------------------------------------------------------------------------------------------|------------------------------------------------------------|
| [`AlignmentResult`](#muvid.align.AlignmentResult)(\*, sections)                        |                                                            |
| [`LineAlignment`](#muvid.align.LineAlignment)(\*, line_index, section_label, ...)    |                                                            |
| [`SectionAlignment`](#muvid.align.SectionAlignment)(\*, label, title, start_s, ...)     |                                                            |
| [`WordAlignment`](#muvid.align.WordAlignment)(\*, line_index, token_index, ...)      | One alignment between a lyric token and a transcript word. |

### muvid.align.AlignerName

Built-in aligner names. `align_lyrics(aligner=...)` accepts these
plus any name added later via [`register_aligner()`](#muvid.align.register_aligner).

### *class* muvid.align.AlignerSpec(, name, description, fn, requires=())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One row in the aligner registry.

### *class* muvid.align.AlignmentResult(, sections)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

#### lines_in(start_s, end_s)

Lines that fall (at least partially) inside `[start_s, end_s]`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`LineAlignment`](#muvid.align.LineAlignment)]

### *class* muvid.align.LineAlignment(, line_index, section_label, text, start_s, end_s, word_alignments)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* muvid.align.SectionAlignment(, label, title, start_s, end_s, lines)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### muvid.align.WHISPERX_LITE_MODEL_SIZE *= 'small'*

`tiny` is fast but, on sung/repetitive audio, produces a transcript so
garbled that the greedy matcher walks past whole minutes before finding a
plausible match (muvid#101: measured 53 s late on a repeated-word lyric).
The lyric-video path is happy to wait, so default to a materially more
accurate size; override per call or via this env var.

### *class* muvid.align.WordAlignment(, line_index, token_index, text, start_s, end_s, confidence=1.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One alignment between a lyric token and a transcript word.

### muvid.align.align_lyrics(lyrics, transcript, , duration_s=0.0, aligner='scribe-greedy', \*\*aligner_kwargs)

Align a `LyricsDoc` to a transcript.

* **Parameters:**
  * **lyrics** ([`LyricsDoc`](muvid.lyrics.html.md#muvid.lyrics.LyricsDoc)) – User-edited lyrics document (the ground-truth text).
  * **transcript** ([`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)) – Aligner-specific input. For `"scribe-greedy"`
    this is a Scribe / faster-whisper response with
    `words: [...]`. For `"user"` this can be empty if you
    pass `user_line_timings=...`. For `"whisperx-lite"` the
    transcript is ignored — the aligner runs on the audio
    directly (path passed via `audio_path=`).
  * **duration_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Used to extrapolate end times for lines with no
    matched words and no later anchor.
  * **aligner** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Name of a registered aligner. See [`list_aligners()`](#muvid.align.list_aligners).
  * **\*\*aligner_kwargs** – Forwarded to the aligner.
* **Return type:**
  [`AlignmentResult`](#muvid.align.AlignmentResult)
* **Returns:**
  [`AlignmentResult`](#muvid.align.AlignmentResult).

### muvid.align.align_scribe_greedy(lyrics, transcript, , duration_s=0.0, lookahead=6)

Greedy token-match against a word-timestamped transcript.

Cheap, network-only (assumes the transcript came from Scribe or
similar). Tolerates 1-character mishears between sung and written
text. `duration_s` is used only when extrapolating end times.

* **Return type:**
  [`AlignmentResult`](#muvid.align.AlignmentResult)

### muvid.align.align_stars(lyrics, transcript, , duration_s=0.0, \*\*kwargs)

Singing-specific alignment (STARS / similar). Not yet implemented.

See `misc/docs/alignment_references.md` for the literature
motivating this aligner. The slot exists so callers can already
write `aligner="stars"` and get a clear NotImplementedError.

* **Return type:**
  [`AlignmentResult`](#muvid.align.AlignmentResult)

### muvid.align.align_user_provided(lyrics, transcript, , duration_s=0.0, user_line_timings=None)

Use line-level timings the caller has already determined.

Useful when the user has hand-anchored every line, or when an
external aligner has produced `line_index → (start, end)`
timings and you don’t want any token-matching.

`user_line_timings` is a list of `{"line_index": int,
"start_s": float, "end_s": float}`. If omitted, this aligner
falls back to the manual anchors already on `lyrics` (the
`// 12.5` end-of-line markers in `lyrics.md`).

* **Return type:**
  [`AlignmentResult`](#muvid.align.AlignmentResult)

### muvid.align.align_whisperx_lite(lyrics, transcript, , duration_s=0.0, audio_path=None, model_size='small', lookahead=6)

Local-only aligner that re-uses `faster-whisper` (no API).

The `transcript` argument is ignored when `audio_path` is given:
we re-transcribe locally with faster-whisper and then run the same
greedy match used by `scribe-greedy`. When `audio_path` is not
given, we fall through and just use the supplied `transcript`.

Trade-offs vs `scribe-greedy`: free + offline; slower; less
accurate on singing; needs torch + faster-whisper installed.

`model_size` defaults to [`WHISPERX_LITE_MODEL_SIZE`](#muvid.align.WHISPERX_LITE_MODEL_SIZE) (`"small"`,
overridable via `MUVID_WHISPERX_LITE_MODEL_SIZE`) rather than
faster-whisper’s own `"tiny"` default — see muvid#101.

* **Return type:**
  [`AlignmentResult`](#muvid.align.AlignmentResult)

### muvid.align.list_aligners()

Return all registered aligner names, sorted.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.align.register_aligner(name, fn, , description, requires=())

Register an aligner under `name`.

The function should accept `(lyrics, transcript, *, duration_s,
\*\*kw) -> AlignmentResult`. Extra keyword arguments forwarded by
[`align_lyrics()`](#muvid.align.align_lyrics) are passed through.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.align.write_alignment_store(alignment, , path, asset_id='song:audio', rate=1000)

Write alignment to a `lacing.SqliteStore` file (.annot).

Uses `lacing.tracks.subtitle.SubtitleBuilder` for the
standard `(sections, lines, words)` tier set.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)
