# muvid.lyricvid.glyph_align

Per-glyph timing: when was each written character sung? (muvid#142)

A word-level alignment says when `バカンス` was sung; a learner watching
katakana needs to see `バ`, `カ`, `ン` and `ス` light up one by one, each
at the moment its sound starts. This module refines any word-timed
[`TimedText`](muvid.lyricvid.timed_text.md#muvid.lyricvid.timed_text.TimedText) (from Suno’s own timestamps, an
enhanced LRC, a whisper match…) into one with measured
[`glyphs`](muvid.lyricvid.timed_text.md#muvid.lyricvid.timed_text.Word.glyphs).

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
lists the words where two timings disagree. [`reconcile()`](#muvid.lyricvid.glyph_align.reconcile) then acts on
what a list of [`Detector`](#muvid.lyricvid.glyph_align.Detector) s distrusts — each names words and a remedy
(`replace`: fall back to the vendor window; `drop`: it was never sung).
[`unsung()`](#muvid.lyricvid.glyph_align.unsung) is the one that drops: a line the aligner could not place over
which the vocal stem is silent (muvid#144).

Licensing — read before shipping anything: the `MMS_FA` weights are
**CC-BY-NC-4.0** and htdemucs (vocal separation) is CC-BY-NC too. So this is an
opt-in extra (`muvid[lyricvid-glyphs]`), never a default, never on the prod
connector, and it never downloads weights unless the caller says
`allow_download=True` (or `MUVID_ALLOW_WEIGHT_DOWNLOAD=1`).

### Module Attributes

| [`MARGIN_S`](#muvid.lyricvid.glyph_align.MARGIN_S)              | Seconds of audio kept either side of a line's coarse window.                                                                                          |
|------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`ONSET_TOL_S`](#muvid.lyricvid.glyph_align.ONSET_TOL_S)           | Onset tolerance for [`onset_report()`](#muvid.lyricvid.glyph_align.onset_report).                                                                  |
| [`DISAGREE_S`](#muvid.lyricvid.glyph_align.DISAGREE_S)            | Two timings of a word disagree when their starts differ by more than this.                                                                            |
| [`MIN_GLYPH_S`](#muvid.lyricvid.glyph_align.MIN_GLYPH_S)           | Two sounded glyphs whose STARTS are closer than this were squeezed together by the aligner, not sung (a mora takes ~0.1 s or more).                   |
| [`REMEDIES`](#muvid.lyricvid.glyph_align.REMEDIES)              | What [`reconcile()`](#muvid.lyricvid.glyph_align.reconcile) may do with a word a detector distrusts.                                            |
| [`UNSUNG_QUIET_DB`](#muvid.lyricvid.glyph_align.UNSUNG_QUIET_DB)       | a frame is quiet below this many dB under the stem's own loud level...                                                                                |
| [`UNSUNG_QUIET_SHARE`](#muvid.lyricvid.glyph_align.UNSUNG_QUIET_SHARE)    | ...and a line is unsung when at least this share of its window is quiet...                                                                            |
| [`UNSUNG_MIN_QUIET_S`](#muvid.lyricvid.glyph_align.UNSUNG_MIN_QUIET_S)    | ...in one unbroken stretch at least this long.                                                                                                        |
| [`UNSUNG_FRAME_S`](#muvid.lyricvid.glyph_align.UNSUNG_FRAME_S)        | Frame length for the stem's RMS level.                                                                                                                |
| [`UNSUNG_REF_PERCENTILE`](#muvid.lyricvid.glyph_align.UNSUNG_REF_PERCENTILE) | The stem's "loud level" is this percentile of its frame RMS, so the threshold follows the mix rather than an absolute dBFS a quieter stem would fail. |

### Functions

| [`default_detectors`](#muvid.lyricvid.glyph_align.default_detectors)(\*[, disagree_s, min_glyph_s])   | What [`reconcile()`](#muvid.lyricvid.glyph_align.reconcile) runs when given none: the two that need no audio.   |
|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| [`disagrees`](#muvid.lyricvid.glyph_align.disagrees)(\*[, disagree_s])                        | Words whose fine start is more than `disagree_s` from the coarse one.                                                 |
| [`glyph_rows`](#muvid.lyricvid.glyph_align.glyph_rows)(tt)                                     | One flat record per glyph, for a table or a quick look.                                                               |
| [`onset_report`](#muvid.lyricvid.glyph_align.onset_report)(timed_text, vocals, \*[, tol_s, ...]) | How well glyph starts sit on acoustic onsets of the vocal stem.                                                       |
| [`reconcile`](#muvid.lyricvid.glyph_align.reconcile)(fine, coarse, \*[, disagree_s, ...])     | Distrust the fine timing where a detector says so; remedy it; say where.                                              |
| [`refine_glyphs`](#muvid.lyricvid.glyph_align.refine_glyphs)(timed_text, vocals, \*[, ...])       | Return `timed_text` with measured `Word.glyphs` on every word.                                                        |
| [`romanize_kana`](#muvid.lyricvid.glyph_align.romanize_kana)(word)                                | `(glyph, romaji)` per glyph, the romaji chunks concatenating to the word.                                             |
| [`squeezed`](#muvid.lyricvid.glyph_align.squeezed)(\*[, min_glyph_s])                        | Words with two sounded glyphs starting less than `min_glyph_s` apart.                                                 |
| [`unsung`](#muvid.lyricvid.glyph_align.unsung)(vocals, \*[, among, quiet_db, ...])         | Lines a vendor transcript lists but nobody sang.                                                                      |
| [`vocal_stem`](#muvid.lyricvid.glyph_align.vocal_stem)(audio, \*, out_dir[, allow_download])   | The song's separated vocal stem (Demucs), raising rather than degrading.                                              |
| [`word_agreement`](#muvid.lyricvid.glyph_align.word_agreement)(fine, coarse, \*[, disagree_s])     | Compare word starts (first glyph start when present) of two timings.                                                  |

### Classes

| [`Detector`](#muvid.lyricvid.glyph_align.Detector)(\*, name, find[, remedy])   | Names the words of a fine timing not to trust, and what to do about them.   |
|---------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|

### muvid.lyricvid.glyph_align.DISAGREE_S *= 0.3*

Two timings of a word disagree when their starts differ by more than this.

### *class* muvid.lyricvid.glyph_align.Detector(, name, find, remedy='replace')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Names the words of a fine timing not to trust, and what to do about them.

`find(fine, coarse)` gets both timings (same word sequence) and returns
`{word_index: reason}`, indices into `list(fine.words())`. `remedy`
is one of [`REMEDIES`](#muvid.lyricvid.glyph_align.REMEDIES); when several detectors flag one word, `drop`
wins — a word nobody sang has no window worth falling back to.

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

### muvid.lyricvid.glyph_align.REMEDIES *= ('replace', 'drop')*

What [`reconcile()`](#muvid.lyricvid.glyph_align.reconcile) may do with a word a detector distrusts. `replace`:
fall back to the coarse window shifted by the measured bias, glyphs spread
evenly and marked unmeasured (the word was sung; the aligner could not place
it). `drop`: remove the word, and a line or section left empty (it was
never sung, so lighting it at ANY time is wrong).

### muvid.lyricvid.glyph_align.UNSUNG_FRAME_S *= 0.05*

Frame length for the stem’s RMS level.

### muvid.lyricvid.glyph_align.UNSUNG_MIN_QUIET_S *= 1.0*

…in one unbroken stretch at least this long. A ghost repeat is a whole
line of silence (2 s on the measured song); a short sung line whose vendor
window is merely LATE lands on a quiet tail too, but a short one.

### muvid.lyricvid.glyph_align.UNSUNG_QUIET_DB *= -40.0*

a frame is
quiet below this many dB under the stem’s own loud level…

* **Type:**
  [`unsung()`](#muvid.lyricvid.glyph_align.unsung)’s thresholds, measured on a real song (muvid#144)

### muvid.lyricvid.glyph_align.UNSUNG_QUIET_SHARE *= 0.5*

…and a line is unsung when at least this share of its window is quiet…

### muvid.lyricvid.glyph_align.UNSUNG_REF_PERCENTILE *= 95.0*

The stem’s “loud level” is this percentile of its frame RMS, so the threshold
follows the mix rather than an absolute dBFS a quieter stem would fail.

### muvid.lyricvid.glyph_align.default_detectors(, disagree_s=0.3, min_glyph_s=0.04)

What [`reconcile()`](#muvid.lyricvid.glyph_align.reconcile) runs when given none: the two that need no audio.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Detector`](#muvid.lyricvid.glyph_align.Detector), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

### muvid.lyricvid.glyph_align.disagrees(, disagree_s=0.3)

Words whose fine start is more than `disagree_s` from the coarse one.

Remedy: `replace`.

* **Return type:**
  [`Detector`](#muvid.lyricvid.glyph_align.Detector)

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

### muvid.lyricvid.glyph_align.reconcile(fine, coarse, , disagree_s=None, min_glyph_s=None, detectors=None)

Distrust the fine timing where a detector says so; remedy it; say where.

`detectors` defaults to [`default_detectors()`](#muvid.lyricvid.glyph_align.default_detectors) (`squeezed` and
`disagrees`, both `replace`; `disagree_s` and `min_glyph_s` tune
them, and are refused alongside explicit `detectors`, which they could
not reach). Add [`unsung()`](#muvid.lyricvid.glyph_align.unsung) when you have the vocal stem:

```default
reconcile(fine, coarse, detectors=(*default_detectors(), unsung(stem)))
```

`replace` puts a word back in its COARSE window, shifted by the median
offset between the two timings over the words no detector flagged (vendor
times run late; the bias is measured, not assumed), glyphs spread evenly
and marked `measured=False`. `drop` removes it, and any line or section
left empty. Returns the timing and one record per affected word: `why`
(the deciding detector), `detail`, `remedy`, `flagged_by` (every
detector that flagged it) and `used` (the start it got; `None` when
dropped).

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`TimedText`](muvid.lyricvid.timed_text.md#muvid.lyricvid.timed_text.TimedText), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]]

### muvid.lyricvid.glyph_align.refine_glyphs(timed_text, vocals, \*, romanize=<function romanize_kana>, margin_s=0.5, allow_download=False)

Return `timed_text` with measured `Word.glyphs` on every word.

`vocals` should be a separated vocal stem (see [`vocal_stem()`](#muvid.lyricvid.glyph_align.vocal_stem)): the
accompaniment confuses a CTC model far more than it confuses a listener.
The coarse word times are only used as per-line windows; the glyph times
replace nothing else. A line with nothing romanisable keeps evenly spread,
`measured=False` glyphs rather than invented precision.

* **Return type:**
  [`TimedText`](muvid.lyricvid.timed_text.md#muvid.lyricvid.timed_text.TimedText)

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

### muvid.lyricvid.glyph_align.squeezed(, min_glyph_s=0.04)

Words with two sounded glyphs starting less than `min_glyph_s` apart.

CTC squeezing a word it could not hear into a few frames. Judged on
syllabic glyphs (kana/CJK: one mora each) only — letters of an alphabet are
legitimately ~30 ms apart — and only between neighbours in the same line
(lines are aligned in overlapping windows). Remedy: `replace`.

* **Return type:**
  [`Detector`](#muvid.lyricvid.glyph_align.Detector)

### muvid.lyricvid.glyph_align.unsung(vocals, , among=None, quiet_db=-40.0, quiet_share=0.5, min_quiet_s=1.0, frame_s=0.05)

Lines a vendor transcript lists but nobody sang. Remedy: `drop`.

A LINE is unsung when both hold: `among` (default [`squeezed()`](#muvid.lyricvid.glyph_align.squeezed))
distrusts every word in it — the aligner, searching the line’s window, found
nothing to put them on — and the vocal stem is quiet (`quiet_db` under its
own loud level) over at least `quiet_share` of the line’s coarse window,
including one unbroken quiet stretch of `min_quiet_s`. The window is
shifted by the vendor’s measured bias (median fine-minus-coarse start over
the words `among` trusts), and a window running past the end of the stem
is not judged at all.

Both, because neither instrument is enough alone. Measured on the song that
motivated this (muvid#144: a Suno transcript listing a repeated タン タン タン
at 36-38 s that the stem shows nobody sang), the stem alone puts a sung,
staccato ストップ ストップ at 0.63 quiet against the ghost’s 0.75 — vendor
windows run on into the silence after a word, so a quiet window is common —
while squeeze alone also flags a sung, fast スター ダンス (0.04 quiet).
Together they separate the ghost and nothing else.

What they do NOT separate unaided is one short sung line whose vendor
window is a second late: the aligner (searching ±\`\`MARGIN_S\`\`) misses it
and squeezes, and the window lands on the silence after it — measured on
the same song, the last line (フン) shifted +1 s was dropped. The unbroken
`min_quiet_s` is what refuses that case: a short window cannot hold a
second of silence, a ghost line is silence end to end.

The unit is the line, because a transcript repeats lines, and because the
first ghost word’s own window held the decaying tail of the previous held
note: sound, but not its sound. `among` is squeeze and not disagreement
on purpose — a line the vendor MISPLACED was sung somewhere else, and
dropping it would delete a sung line.

* **Return type:**
  [`Detector`](#muvid.lyricvid.glyph_align.Detector)

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
