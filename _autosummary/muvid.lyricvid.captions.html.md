# muvid.lyricvid.captions

Caption tracks: one subtitle file per language, timed per sung line (muvid#145).

A lyric video for learners wants toggleable caption tracks on YouTube — a
translation, a transliteration (Hepburn romaji for kana) — timed from the same
[`TimedText`](muvid.lyricvid.timed_text.html.md#muvid.lyricvid.timed_text.TimedText) that drives the picture, so the
words on screen and the caption under them change together.

Each language is a **transform** of a line’s text:

- a mapping `{line text: caption}` — a hand-written translation. A sung line
  the mapping does not cover is an ERROR naming every such line: a caption track
  with silent holes is a plausible artifact nobody re-checks;
- a callable `line text -> caption`;
- a name from [`CAPTION_TRANSFORMS`](#muvid.lyricvid.captions.CAPTION_TRANSFORMS) (`"hepburn"`, `"original"`) — the
  form that survives JSON, so a render request or the CLI can ask for it.

A transform returning `""` omits that line from its track, on purpose.
Every check happens before any track is built — a remote render request
reaches this through the `captions` param, and the subgenre schema validator
does not read `anyOf` — so a malformed request fails as a `ValueError`
before the render, never after it.

Timing, per line: the caption appears `lead_s` before the line’s first sung
glyph (or word) — a reader needs the head start — stays at least `min_s`, and
leaves when the next line’s caption arrives (the last one `tail_s` after its
line ends). `offset_s` shifts everything, e.g. by a title card prepended to
the video. Lines are captioned in the order they are SUNG, whatever order the
timing lists them in; lines sung together get overlapping captions rather
than one of them a few milliseconds long. No caption outlasts the song. The
files hand straight to `yb.youtube.CaptionTrack(path, language)`.

`pysubs2` (the `lyricvid` extra, which the default renderer already needs)
is imported only when a track is built.

### Module Attributes

| [`CAPTION_TRANSFORMS`](#muvid.lyricvid.captions.CAPTION_TRANSFORMS)   | Named transforms — what a JSON caller (a render request, the CLI) can ask for.   |
|-----------------------------------------------------------------------|----------------------------------------------------------------------------------|

### Functions

| [`caption_tracks`](#muvid.lyricvid.captions.caption_tracks)(timed_text, transforms, \*[, ...])   | One subtitle track per language, a caption per sung line.                  |
|------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|
| [`write_caption_tracks`](#muvid.lyricvid.captions.write_caption_tracks)(tracks, out_dir, \*[, ...])    | Save each track as `{out_dir}/{stem}.{lang}.{fmt}`; return `{lang: path}`. |

### muvid.lyricvid.captions.CAPTION_TRANSFORMS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Callable](https://docs.python.org/3/library/typing.html#typing.Callable)[[[str](https://docs.python.org/3/builtins/stdtypes.html#str)], [str](https://docs.python.org/3/builtins/stdtypes.html#str)]]* *= {'hepburn': <function \_hepburn>, 'original': <function <lambda>>}*

Named transforms — what a JSON caller (a render request, the CLI) can ask for.

### muvid.lyricvid.captions.caption_tracks(timed_text, transforms, , lead_s=0.15, min_s=1.2, tail_s=1.5, offset_s=0.0)

One subtitle track per language, a caption per sung line.

`transforms` maps a BCP-47 language tag to a `LineTransform`.
Every transform is checked (unknown names, uncovered lines) before any
track is built, so a bad request fails whole rather than half-written.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), `SSAFile`]

```pycon
>>> from muvid.lyricvid.timed_text import from_word_records
>>> tt = from_word_records([
...     {"text": "バス", "start": 1.0, "end": 1.5, "line_end": True},
...     {"text": "スタート", "start": 2.0, "end": 3.0, "line_end": True}])
>>> tracks = caption_tracks(tt, {"en": {"バス": "Bus", "スタート": "Start"},
...                              "ja-Latn": "hepburn"})
>>> [(e.start, e.end, e.text) for e in tracks["ja-Latn"]]
[(850, 1850, 'basu'), (1850, 3050, 'sutāto')]
```

### muvid.lyricvid.captions.write_caption_tracks(tracks, out_dir, , stem='captions', fmt='srt')

Save each track as `{out_dir}/{stem}.{lang}.{fmt}`; return `{lang: path}`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]
