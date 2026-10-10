# muvid.lyricvid.glyph_align

Per-glyph timing: when was each written character sung? (muvid#142)

A word-level alignment says when `バカンス` was sung; a learner watching
katakana needs to see `バ`, `カ`, `ン` and `ス` light up one by one, each
at the moment its sound starts. This module refines any word-timed
[`TimedText`](muvid.lyricvid.timed_text.html.md#muvid.lyricvid.timed_text.TimedText) (from Suno’s own timestamps, an
enhanced LRC, a whisper match…) into one with measured
[`glyphs`](muvid.lyricvid.timed_text.html.md#muvid.lyricvid.timed_text.Word.glyphs).

How: each lyric line is cut out of the separated vocal stem, widened by
`margin_s` on both sides, and force-aligned with a CTC model (torchaudio’s
`MMS_FA`, trained on romanised speech in 1,000+ languages) against the line’s
romanisation, one romaji chunk per glyph, with a `<star>` token either side
to absorb breaths and ad-libs. Aligning per line, inside the coarse word
window, is what keeps one mis-sung line from dragging every later one.

Romanisation is a strategy slot (`romanize=`): [`romanize_kana()`](#muvid.lyricvid.glyph_align.romanize_kana) covers
katakana and hiragana, including `ー` (lengthens the previous vowel), `ッ`
(doubles the next consonant) and the small `ャュョァィゥェォ` combinations.
Another script needs another romaniser, nothing else.

Verification lives here too, because a timing nobody checked is a guess:
[`onset_report()`](#muvid.lyricvid.glyph_align.onset_report) measures how close glyph starts sit to acoustic onsets in
the vocal stem, against a random-jitter baseline, and [`word_agreement()`](#muvid.lyricvid.glyph_align.word_agreement)
lists the words where two timings disagree.

Licensing — read before shipping anything: the `MMS_FA` weights are
**CC-BY-NC-4.0** and htdemucs (vocal separation) is CC-BY-NC too. So this is an
opt-in extra (`muvid[lyricvid-glyphs]`), never a default, never on the prod
connector, and it never downloads weights unless the caller says
`allow_download=True` (or `MUVID_ALLOW_WEIGHT_DOWNLOAD=1`).

### Module Attributes

| [`MARGIN_S`](#muvid.lyricvid.glyph_align.MARGIN_S)    | Seconds of audio kept either side of a line's coarse window.                                                                        |
|--------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| [`ONSET_TOL_S`](#muvid.lyricvid.glyph_align.ONSET_TOL_S) | Onset tolerance for [`onset_report()`](#muvid.lyricvid.glyph_align.onset_report).                                                |
| [`DISAGREE_S`](#muvid.lyricvid.glyph_align.DISAGREE_S)  | Two timings of a word disagree when their starts differ by more than this.                                                          |
| [`MIN_GLYPH_S`](#muvid.lyricvid.glyph_align.MIN_GLYPH_S) | Two sounded glyphs whose STARTS are closer than this were squeezed together by the aligner, not sung (a mora takes ~0.1 s or more). |

### Functions

| [`glyph_rows`](#muvid.lyricvid.glyph_align.glyph_rows)(tt)                                     | One flat record per glyph, for a table or a quick look.                   |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`onset_report`](#muvid.lyricvid.glyph_align.onset_report)(timed_text, vocals, \*[, tol_s, ...]) | How well glyph starts sit on acoustic onsets of the vocal stem.           |
| [`reconcile`](#muvid.lyricvid.glyph_align.reconcile)(fine, coarse, \*[, disagree_s, ...])     | Distrust the fine timing where it is implausible; say where.              |
| [`refine_glyphs`](#muvid.lyricvid.glyph_align.refine_glyphs)(timed_text, vocals, \*[, ...])       | Return `timed_text` with measured `Word.glyphs` on every word.            |
| [`romanize_kana`](#muvid.lyricvid.glyph_align.romanize_kana)(word)                                | `(glyph, romaji)` per glyph, the romaji chunks concatenating to the word. |
| [`vocal_stem`](#muvid.lyricvid.glyph_align.vocal_stem)(audio, \*, out_dir[, allow_download])   | The song's separated vocal stem (Demucs), raising rather than degrading.  |
| [`word_agreement`](#muvid.lyricvid.glyph_align.word_agreement)(fine, coarse, \*[, disagree_s])     | Compare word starts (first glyph start when present) of two timings.      |

### muvid.lyricvid.glyph_align.DISAGREE_S *= 0.3*

Two timings of a word disagree when their starts differ by more than this.

### muvid.lyricvid.glyph_align.MARGIN_S *= 0.5*

Seconds of audio kept either side of a line’s coarse window. Vendor word
times run late and miss leading consonants; too wide a window lets the
aligner wander into the neighbouring line.

### muvid.lyricvid.glyph_align.MIN_GLYPH_S *= 0.04*

Two sounded glyphs whose STARTS are closer than this were squeezed together
by the aligner, not sung (a mora takes ~0.1 s or more). Start-to-start, not
span length: CTC spans are spiky, so a perfectly sung `ン` can be 20 ms.

### muvid.lyricvid.glyph_align.ONSET_TOL_S *= 0.08*

Onset tolerance for [`onset_report()`](#muvid.lyricvid.glyph_align.onset_report).

### muvid.lyricvid.glyph_align.glyph_rows(tt)

One flat record per glyph, for a table or a quick look.

* **Return type:**
  [`Iterable`](https://docs.python.org/3/library/typing.html#typing.Iterable)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

### muvid.lyricvid.glyph_align.onset_report(timed_text, vocals, , tol_s=0.08, seed=0, trials=20)

How well glyph starts sit on acoustic onsets of the vocal stem.

`near_onset` is the share of glyph starts within `tol_s` of a detected
onset; `random_baseline` is the same share after jittering every start
by up to ±0.5 s — the gap between the two is the evidence, since a dense
onset track makes any time look “near” something.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.lyricvid.glyph_align.reconcile(fine, coarse, , disagree_s=0.3, min_glyph_s=0.04)

Distrust the fine timing where it is implausible; say where.

A word is distrusted when its start disagrees with the coarse timing by
more than `disagree_s`, or when a sounded glyph lasts under
`min_glyph_s` (CTC squeezing a word it could not hear into a few frames —
seen on a song where the vendor’s transcript listed a repeat the singer
never sang). Such a word falls back to its COARSE window, shifted by the
median offset between the two timings over the words they agree on (vendor
times run late; the bias is measured, not assumed), with its glyphs spread
evenly and marked `measured=False`. Returns the timing and one record per
replaced word.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`TimedText`](muvid.lyricvid.timed_text.html.md#muvid.lyricvid.timed_text.TimedText), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]]

### muvid.lyricvid.glyph_align.refine_glyphs(timed_text, vocals, \*, romanize=<function romanize_kana>, margin_s=0.5, allow_download=False)

Return `timed_text` with measured `Word.glyphs` on every word.

`vocals` should be a separated vocal stem (see [`vocal_stem()`](#muvid.lyricvid.glyph_align.vocal_stem)): the
accompaniment confuses a CTC model far more than it confuses a listener.
The coarse word times are only used as per-line windows; the glyph times
replace nothing else. A line with nothing romanisable keeps evenly spread,
`measured=False` glyphs rather than invented precision.

* **Return type:**
  [`TimedText`](muvid.lyricvid.timed_text.html.md#muvid.lyricvid.timed_text.TimedText)

### muvid.lyricvid.glyph_align.romanize_kana(word)

`(glyph, romaji)` per glyph, the romaji chunks concatenating to the word.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

```pycon
>>> romanize_kana("バカンス")
[('バ', 'ba'), ('カ', 'ka'), ('ン', 'n'), ('ス', 'su')]
>>> romanize_kana("スーパー")
[('ス', 'su'), ('ー', ''), ('パ', 'pa'), ('ー', '')]
>>> romanize_kana("カップ")
[('カ', 'ka'), ('ッ', 'p'), ('プ', 'pu')]
>>> romanize_kana("シャツ")
[('シ', 'sh'), ('ャ', 'a'), ('ツ', 'tsu')]
>>> romanize_kana("キャンディー")
[('キ', 'k'), ('ャ', 'ya'), ('ン', 'n'), ('デ', 'd'), ('ィ', 'i'), ('ー', '')]
>>> romanize_kana("ok!")
[('o', 'o'), ('k', 'k'), ('!', '')]
```

A glyph with an empty chunk (`ー`, punctuation) has no onset of its own;
the aligner places it between its neighbours and marks it unmeasured.

### muvid.lyricvid.glyph_align.vocal_stem(audio, , out_dir, allow_download=False)

The song’s separated vocal stem (Demucs), raising rather than degrading.

Reuses the footage scorer’s separator; that one returns `None` on any
failure because lip-sync is optional there, but here no stem means no
trustworthy glyph times, so the absence is an error.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### muvid.lyricvid.glyph_align.word_agreement(fine, coarse, , disagree_s=0.3)

Compare word starts (first glyph start when present) of two timings.

The two must hold the same words in the same order (`fine` is normally
`refine_glyphs(coarse, ...)`). Returns summary statistics and the list
of words that disagree by more than `disagree_s` — the ones to look at.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]
