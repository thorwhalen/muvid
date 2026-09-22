# muvid.lyrics

Lyrics — transcription and markdown round-trip.

Two surfaces for the user:

1. `transcribe(audio_path)` — calls `mixing.transcript.transcribe`
   (ElevenLabs Scribe) and writes the raw word-timestamped JSON to
   `lyrics/transcript.json`. This is the *seed*; the user is expected
   to correct it.
2. `write_lyrics_md` / `parse_lyrics_md` — the canonical, editable
   form. A simple markdown with `[section]` headers, one line per
   sung line, and an optional `// <seconds>` end-of-line anchor.

The alignment module consumes both: it reads `lyrics.md` for the
*text* the user committed to, and `transcript.json` for the *timing*
to splice in.

### Functions

| [`lyrics_from_transcript`](#muvid.lyrics.lyrics_from_transcript)(transcript)         | Build a default LyricsDoc from a transcription response.                  |
|---------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`parse_lyrics_md`](#muvid.lyrics.parse_lyrics_md)(md)                        | Parse the user-editable lyrics markdown.                                  |
| `read_lyrics_md`(path)                                                                      |                                                                           |
| `read_transcript`(path)                                                                     |                                                                           |
| [`render_lyrics_md`](#muvid.lyrics.render_lyrics_md)(doc)                      | Inverse of `parse_lyrics_md`.                                             |
| [`transcribe`](#muvid.lyrics.transcribe)(audio_path, \*[, api_key, ...]) | Run ElevenLabs Scribe on the audio and (optionally) write the JSON.       |
| [`words_from_transcript`](#muvid.lyrics.words_from_transcript)(transcript)          | Normalize Scribe's word entries: `[{text, start, end, confidence}, ...]`. |
| `write_lyrics_md`(path, doc)                                                                |                                                                           |

### Classes

| [`LyricLine`](#muvid.lyrics.LyricLine)(\*, text, line_index[, ...])         | One line of lyric text, optionally with a known start time.   |
|-------------------------------------------------------------------------------------------------|---------------------------------------------------------------|
| [`LyricSection`](#muvid.lyrics.LyricSection)(\*, label[, title, start_s, ...]) | A user-tagged section in the lyrics markdown.                 |
| [`LyricsDoc`](#muvid.lyrics.LyricsDoc)(\*, sections)                        | Full parsed view of the user's lyrics markdown.               |

### *class* muvid.lyrics.LyricLine(, text, line_index, section_label='', start_s=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One line of lyric text, optionally with a known start time.

### *class* muvid.lyrics.LyricSection(, label, title='', start_s=None, end_s=None, lines=())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A user-tagged section in the lyrics markdown.

Times are *optional* — if not present, alignment is computed from
transcripts; if present, they override.

### *class* muvid.lyrics.LyricsDoc(, sections)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Full parsed view of the user’s lyrics markdown.

### muvid.lyrics.lyrics_from_transcript(transcript)

Build a default LyricsDoc from a transcription response.

Heuristic: split lines on punctuation (`. ? !`) or on long pauses
(>0.6 s gap between consecutive words). One `[transcribed]` section
holds everything; the user is expected to re-tag with real sections.

* **Return type:**
  [`LyricsDoc`](#muvid.lyrics.LyricsDoc)

### muvid.lyrics.parse_lyrics_md(md)

Parse the user-editable lyrics markdown.

Format:

```default
[section_label] optional title
line of lyric            // 12.5
another line

[next section]
...
```

Empty lines are separators between sections. `(instrumental)` or
any line starting with `(` and ending with `)` is treated as a
non-lyric placeholder (no LyricLine emitted).

* **Return type:**
  [`LyricsDoc`](#muvid.lyrics.LyricsDoc)

### muvid.lyrics.render_lyrics_md(doc)

Inverse of `parse_lyrics_md`. Stable round-trip.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.lyrics.transcribe(audio_path, , api_key=None, out_path=None, cache=True)

Run ElevenLabs Scribe on the audio and (optionally) write the JSON.

Returns the raw response dict (which contains `words: [...]` with
per-word `text`, `start`, `end`, `confidence`).

The on-disk Scribe cache (in `mixing.transcript`) is enabled by
default, so re-running on the same audio is free. Pass
`cache=False` to force a fresh round-trip.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.lyrics.words_from_transcript(transcript)

Normalize Scribe’s word entries: `[{text, start, end, confidence}, ...]`.

Filters out non-word events (Scribe surfaces `(laughs)` etc. with
`type` ≠ `word`) and ones missing timing. `confidence` is
pass-through; absent → `None`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]
