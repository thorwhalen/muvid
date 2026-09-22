# muvid.lyricvid.timed_text

The timed text tree — song → sections → lines → words, with measured times.

Everything downstream reads this and nothing else, which is what lets the
subgenre accept four quite different kinds of input without the renderers
caring which one arrived:

| input the caller has    | how it gets here                                                                                                                                                                                                 |
|-------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| a muvid project         | [`from_alignment_store()`](#muvid.lyricvid.timed_text.from_alignment_store) — muvid already owns<br/>the word-timing SSOT (`muvid.align` writes a<br/>three-tier lacing store); we read it, never<br/>re-transcribe. |
| an `.srt` / `.lrc` file | [`from_subtitles()`](#muvid.lyricvid.timed_text.from_subtitles) — line-level times, words<br/>spread inside a line.                                                                                            |
| lyrics text + audio     | [`from_lyrics_and_audio()`](#muvid.lyricvid.timed_text.from_lyrics_and_audio) — runs muvid’s<br/>registered aligner.                                                                                                  |
| audio only              | the aligner’s transcription path, same function.                                                                                                                                                                 |

Line-level input is not word-level input, and pretending otherwise is how lyric
videos end up subtly out of sync. When words are interpolated inside a line
rather than measured, [`Word.measured`](#muvid.lyricvid.timed_text.Word.measured) is `False` — renderers and the
quantiser can then prefer `line` quantisation, and a caller can be told the
timing is approximate instead of discovering it in the render.

Import-safe: stdlib only at module scope. The muvid alignment machinery and any
ASR are imported inside the functions that need them.

### Functions

| [`from_alignment_store`](#muvid.lyricvid.timed_text.from_alignment_store)(project_root, \*[, duration])   | Read muvid's own three-tier alignment (sections / lines / words).                                                                     |
|-------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| [`from_alignment_result`](#muvid.lyricvid.timed_text.from_alignment_result)(result, \*[, duration, ...])   | Convert a [`muvid.align.AlignmentResult`](muvid.align.md#muvid.align.AlignmentResult) into a timed tree. |
| [`from_subtitles`](#muvid.lyricvid.timed_text.from_subtitles)(path, \*[, duration])                 | Read `.srt`, `.lrc` or enhanced `.lrc` into a timed tree.                                                                             |
| [`from_lyrics_and_audio`](#muvid.lyricvid.timed_text.from_lyrics_and_audio)(audio, \*[, lyrics, ...])      | Align `lyrics` to `audio` through muvid's aligner registry.                                                                           |
| [`from_words`](#muvid.lyricvid.timed_text.from_words)(words, \*[, duration, line_gap_s, ...])   | Build from a flat `(text, start, end)` stream, splitting lines on gaps.                                                               |

### Classes

| [`Word`](#muvid.lyricvid.timed_text.Word)(\*, text, start, end[, measured])      | One sung word on the song timeline.                                     |
|----------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`Line`](#muvid.lyricvid.timed_text.Line)(\*, words[, index, text])              | One sung line.                                                          |
| [`Section`](#muvid.lyricvid.timed_text.Section)(\*, label, lines)                   | A labelled span — `verse`, `chorus`, whatever the lyrics document says. |
| [`TimedText`](#muvid.lyricvid.timed_text.TimedText)(\*, sections[, duration, source]) | The whole song's text, timed.                                           |

### *class* muvid.lyricvid.timed_text.Line(, words, index=0, text='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One sung line.

### *class* muvid.lyricvid.timed_text.Section(, label, lines)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A labelled span — `verse`, `chorus`, whatever the lyrics document says.

### *class* muvid.lyricvid.timed_text.TimedText(, sections, duration=0.0, source='unknown')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The whole song’s text, timed.

```pycon
>>> tt = from_words([('hello', 0.0, 0.5), ('world', 0.5, 1.0)], duration=1.0)
>>> [w.text for w in tt.words()]
['hello', 'world']
>>> tt.sections[0].label
'*'
```

#### *property* measured *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True when every word time was measured rather than interpolated.

False for an empty text: “all of nothing was measured” is the kind of
vacuous truth that reads as reassurance in a report.

#### source *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

Where the timing came from, for provenance and for honest reporting.

### *class* muvid.lyricvid.timed_text.Word(, text, start, end, measured=True)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One sung word on the song timeline. Times are seconds, absolute.

#### measured *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

False when the time was interpolated inside a line rather than measured.

### muvid.lyricvid.timed_text.from_alignment_result(result, , duration=0.0, source='muvid-align')

Convert a [`muvid.align.AlignmentResult`](muvid.align.md#muvid.align.AlignmentResult) into a timed tree.

Keeps the sections and lines the aligner found — which is strictly better
than flattening to words and re-splitting on gaps, because the lyrics
document already *knows* where the lines are.

A line whose words the aligner could not match still has a `[start, end]`
(interpolated by the aligner from its neighbours); its words are spread
across that span and marked `measured=False`, so downstream can see the
difference between a measured onset and a guessed one.

* **Return type:**
  [`TimedText`](#muvid.lyricvid.timed_text.TimedText)

```pycon
>>> from types import SimpleNamespace as NS
>>> w = NS(text='hi', start_s=0.0, end_s=0.4, token_index=0)
>>> ln = NS(line_index=0, text='hi there', start_s=0.0, end_s=1.0,
...         word_alignments=(w,))
>>> sec = NS(label='verse', lines=(ln,))
>>> tt = from_alignment_result(NS(sections=(sec,)), duration=1.0)
>>> [(x.text, x.measured) for x in tt.words()]
[('hi', True), ('there', False)]
>>> tt.sections[0].label
'verse'
```

### muvid.lyricvid.timed_text.from_alignment_store(project_root, , duration=0.0)

Read muvid’s own three-tier alignment (sections / lines / words).

muvid already declares itself the word-timing SSOT — `muvid.align` writes
a `lacing` store and `muvid.contracts` reads it — so this subgenre reads
that store rather than growing a second transcription path.

* **Return type:**
  [`TimedText`](#muvid.lyricvid.timed_text.TimedText)

### muvid.lyricvid.timed_text.from_lyrics_and_audio(audio, , lyrics=None, aligner=None, duration=0.0)

Align `lyrics` to `audio` through muvid’s aligner registry.

Goes through [`muvid.align.align_lyrics()`](muvid.align.md#muvid.align.align_lyrics) rather than calling an ASR
directly, so a singing-grade aligner registered later is picked up here
for free. The aligner is chosen honestly by what is installed:

* `whisperx-lite` — offline, free, needs `faster-whisper`. \*\*Default
  when available.\*\* Runs on the audio itself.
* `scribe-greedy` — needs a Scribe transcript, which costs money and an
  ElevenLabs key; only reached when asked for by name, never as a silent
  fallback that spends.

With no `lyrics` at all there is nothing to align *to*; the transcript’s
own words become the text (`source='transcript'`).

`aligner` may be any registered name; unknown names raise from
`muvid.align` with the registered list.

* **Return type:**
  [`TimedText`](#muvid.lyricvid.timed_text.TimedText)

### muvid.lyricvid.timed_text.from_subtitles(path, , duration=0.0)

Read `.srt`, `.lrc` or enhanced `.lrc` into a timed tree.

Enhanced LRC carries real per-word stamps and is used as such; plain LRC and
SRT give line times only, so their words are spread and marked
`measured=False`.

* **Return type:**
  [`TimedText`](#muvid.lyricvid.timed_text.TimedText)

### muvid.lyricvid.timed_text.from_words(words, , duration=0.0, line_gap_s=0.9, source='words')

Build from a flat `(text, start, end)` stream, splitting lines on gaps.

The gap heuristic is deliberately simple and deliberately visible: a real
lyrics document is always better, and when one exists the other builders
use it instead of guessing.

* **Return type:**
  [`TimedText`](#muvid.lyricvid.timed_text.TimedText)
