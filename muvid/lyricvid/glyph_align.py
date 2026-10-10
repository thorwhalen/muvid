"""Per-glyph timing: when was each written character sung? (muvid#142)

A word-level alignment says when ``バカンス`` was sung; a learner watching
katakana needs to see ``バ``, ``カ``, ``ン`` and ``ス`` light up one by one, each
at the moment its sound starts. This module refines any word-timed
:class:`~muvid.lyricvid.timed_text.TimedText` (from Suno's own timestamps, an
enhanced LRC, a whisper match...) into one with measured
:attr:`~muvid.lyricvid.timed_text.Word.glyphs`.

How: each lyric line is cut out of the separated vocal stem, widened by
``margin_s`` on both sides, and force-aligned with a CTC model (torchaudio's
``MMS_FA``, trained on romanised speech in 1,000+ languages) against the line's
romanisation, one romaji chunk per glyph, with a ``<star>`` token either side
to absorb breaths and ad-libs. Aligning per line, inside the coarse word
window, is what keeps one mis-sung line from dragging every later one.

Romanisation is a strategy slot (``romanize=``): :func:`romanize_kana` covers
katakana and hiragana, including ``ー`` (lengthens the previous vowel), ``ッ``
(doubles the next consonant) and the small ``ャュョァィゥェォ`` combinations.
Another script needs another romaniser, nothing else.

Verification lives here too, because a timing nobody checked is a guess:
:func:`onset_report` measures how close glyph starts sit to acoustic onsets in
the vocal stem, against a random-jitter baseline, and :func:`word_agreement`
lists the words where two timings disagree.

Licensing — read before shipping anything: the ``MMS_FA`` weights are
**CC-BY-NC-4.0** and htdemucs (vocal separation) is CC-BY-NC too. So this is an
opt-in extra (``muvid[lyricvid-glyphs]``), never a default, never on the prod
connector, and it never downloads weights unless the caller says
``allow_download=True`` (or ``MUVID_ALLOW_WEIGHT_DOWNLOAD=1``).
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from muvid.lyricvid.timed_text import Glyph, Line, Section, TimedText, Word

#: Seconds of audio kept either side of a line's coarse window. Vendor word
#: times run late and miss leading consonants; too wide a window lets the
#: aligner wander into the neighbouring line.
MARGIN_S = float(os.environ.get("MUVID_GLYPH_MARGIN_S", "0.5"))
#: Onset tolerance for :func:`onset_report`.
ONSET_TOL_S = 0.08
#: Two timings of a word disagree when their starts differ by more than this.
DISAGREE_S = 0.3
#: Two sounded glyphs whose STARTS are closer than this were squeezed together
#: by the aligner, not sung (a mora takes ~0.1 s or more). Start-to-start, not
#: span length: CTC spans are spiky, so a perfectly sung ``ン`` can be 20 ms.
MIN_GLYPH_S = 0.04

Romanizer = Callable[[str], list[tuple[str, str]]]

# --------------------------------------------------------------------------
# romanisation (a strategy slot; kana is the default)
# --------------------------------------------------------------------------

_KANA_ROWS = {
    "": "アイウエオ",
    "k": "カキクケコ",
    "s": "サシスセソ",
    "t": "タチツテト",
    "n": "ナニヌネノ",
    "h": "ハヒフヘホ",
    "m": "マミムメモ",
    "y": "ヤ_ユ_ヨ",
    "r": "ラリルレロ",
    "w": "ワ___ヲ",
    "g": "ガギグゲゴ",
    "z": "ザジズゼゾ",
    "d": "ダヂヅデド",
    "b": "バビブベボ",
    "p": "パピプペポ",
}
_KANA: dict[str, str] = {
    k: c + v for c, row in _KANA_ROWS.items() for v, k in zip("aiueo", row) if k != "_"
}
_KANA.update(
    {
        "シ": "shi",
        "チ": "chi",
        "ツ": "tsu",
        "フ": "fu",
        "ジ": "ji",
        "ヂ": "ji",
        "ヅ": "zu",
        "ヲ": "o",
        "ン": "n",
        "ヴ": "vu",
    }
)
#: Small kana that modify the glyph before them.
_SMALL_Y = {"ャ": "ya", "ュ": "yu", "ョ": "yo"}
_SMALL_V = {"ァ": "a", "ィ": "i", "ゥ": "u", "ェ": "e", "ォ": "o"}
_PALATAL = {"shi": "sh", "chi": "ch", "ji": "j"}


def _to_katakana(ch: str) -> str:
    """Hiragana → katakana (same sound, codepoint + 0x60); anything else as is."""
    o = ord(ch)
    return chr(o + 0x60) if 0x3041 <= o <= 0x3096 else ch


def romanize_kana(word: str) -> list[tuple[str, str]]:
    """``(glyph, romaji)`` per glyph, the romaji chunks concatenating to the word.

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

    A glyph with an empty chunk (``ー``, punctuation) has no onset of its own;
    the aligner places it between its neighbours and marks it unmeasured.
    """
    kata = [_to_katakana(c) for c in word]
    out: list[tuple[str, str]] = []
    for i, (g, k) in enumerate(zip(word, kata)):
        nxt = kata[i + 1] if i + 1 < len(kata) else ""
        if k == "ー":
            # a held vowel has no onset of its own: an empty chunk, timed
            # between its neighbours by the aligner (a CTC model places a
            # repeated vowel at the END of the held note, not where it starts)
            out.append((g, ""))
        elif k == "ッ":
            # doubles the NEXT consonant; word-final (a glottal stop) has no
            # sound of its own to align
            follow = _KANA.get(nxt, "")
            out.append((g, follow[0] if follow else ""))
        elif k in _SMALL_Y or k in _SMALL_V:
            out.append((g, (_SMALL_Y | _SMALL_V)[k]))
        elif k in _KANA:
            r = _KANA[k]
            if nxt in _SMALL_Y:  # キャ: キ keeps its consonant, ャ carries "ya"
                if r in _PALATAL:
                    r = _PALATAL[r]
                    out.append((g, r))
                    # シャ = "sha": the small kana then carries only the vowel
                    # (`zip` reads `kata` lazily, so the next step sees this)
                    kata[i + 1] = "_V" + _SMALL_Y[nxt][-1]
                    continue
                r = r[:-1] if len(r) > 1 else r
            elif nxt in _SMALL_V:  # ファ, ティ: drop the base vowel
                r = r[:-1] if len(r) > 1 else r
            out.append((g, r))
        elif k.startswith("_V"):
            out.append((g, k[2:]))
        elif k.isascii() and k.isalpha():
            out.append((g, k.lower()))
        else:
            out.append((g, ""))
    return out


# --------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------


def _load_mms(*, allow_download: bool):
    """The MMS_FA model + dictionary, refusing to fetch weights unasked."""
    import torch
    import torchaudio

    bundle = torchaudio.pipelines.MMS_FA
    cached = Path(torch.hub.get_dir()) / "checkpoints" / Path(bundle._path).name
    allow = allow_download or os.environ.get("MUVID_ALLOW_WEIGHT_DOWNLOAD") == "1"
    if not cached.exists() and not allow:
        raise RuntimeError(
            "The MMS_FA weights (CC-BY-NC-4.0, ~1.2 GB) are not cached and muvid "
            "never downloads weights unasked. Pass allow_download=True or set "
            "MUVID_ALLOW_WEIGHT_DOWNLOAD=1 — after checking the licence fits "
            "your use."
        )
    return bundle, bundle.get_model(with_star=True), bundle.get_dict()


def _read_mono(path: Path | str, sample_rate: int):
    import soundfile as sf
    import torch
    import torchaudio

    a, sr = sf.read(str(path), dtype="float32", always_2d=True)
    wav = torch.from_numpy(a.mean(1)).unsqueeze(0)
    return torchaudio.functional.resample(wav, sr, sample_rate)


def _interpolated(word: Word, chunks: Sequence[tuple[str, str]]) -> tuple[Glyph, ...]:
    """Glyphs spread evenly over the word: honest ``measured=False`` fallback."""
    n = max(1, len(chunks))
    step = (word.end - word.start) / n
    return tuple(
        Glyph(
            text=g,
            start=word.start + i * step,
            end=word.start + (i + 1) * step,
            measured=False,
        )
        for i, (g, _) in enumerate(chunks)
    )


def _align_line(
    line: Line, *, wav, sr: int, model, dic, romanize: Romanizer, margin_s: float
) -> Line:
    import torch
    import torchaudio.functional as F

    t0 = max(0.0, line.start - margin_s)
    t1 = line.end + margin_s
    seg = wav[:, int(t0 * sr) : int(t1 * sr)]
    chunks = [romanize(w.text) for w in line.words]
    letters = [c for ws in chunks for _, r in ws for c in r if c in dic]
    if not letters or seg.shape[1] < sr // 10:
        return replace(
            line,
            words=tuple(
                replace(w, glyphs=_interpolated(w, ch))
                for w, ch in zip(line.words, chunks)
            ),
        )
    with torch.inference_mode():
        emission, _ = model(seg)
    star = dic["*"]
    targets = torch.tensor(
        [[star] + [dic[c] for c in letters] + [star]], dtype=torch.int32
    )
    try:
        ali, scores = F.forced_align(emission, targets, blank=0)
    except RuntimeError:  # window too short for the targets: no honest answer
        return replace(
            line,
            words=tuple(
                replace(w, glyphs=_interpolated(w, ch))
                for w, ch in zip(line.words, chunks)
            ),
        )
    spans = [s for s in F.merge_tokens(ali[0], scores[0].exp()) if s.token != star]
    ratio = seg.shape[1] / emission.shape[1] / sr
    k = 0
    words = []
    for w, ws in zip(line.words, chunks):
        glyphs = []
        for g, r in ws:
            n = sum(1 for c in r if c in dic)
            if n == 0:  # no onset of its own: placed by _place_silent below
                glyphs.append(
                    Glyph(text=g, start=float("nan"), end=float("nan"), measured=False)
                )
                continue
            sp = spans[k : k + n]
            k += n
            glyphs.append(
                Glyph(
                    text=g,
                    start=round(t0 + sp[0].start * ratio, 3),
                    end=round(t0 + sp[-1].end * ratio, 3),
                )
            )
        words.append(replace(w, glyphs=tuple(glyphs)))
    return _place_silent(replace(line, words=tuple(words)))


def _place_silent(line: Line) -> Line:
    """Time glyphs without an onset (``ー``, punctuation) between their neighbours.

    Midway from the previous sounded glyph's start to the next one's (a held
    vowel is the second half of its syllable); at a line's end, half the
    previous glyph's span past its start. ``measured=False`` either way.
    """
    flat = [g for w in line.words for g in w.glyphs]
    times: list[tuple[float, float]] = []
    for i, g in enumerate(flat):
        if g.start == g.start:  # not NaN
            times.append((g.start, g.end))
            continue
        prev = next(
            (times[j] for j in range(i - 1, -1, -1) if flat[j].measured),
            (line.start, line.start),
        )
        nxt = next(
            (
                flat[j].start
                for j in range(i + 1, len(flat))
                if flat[j].start == flat[j].start
            ),
            None,
        )
        at = (
            (prev[0] + nxt) / 2
            if nxt is not None
            else min(line.end, prev[0] + max(0.05, (prev[1] - prev[0]) / 2))
        )
        times.append((at, at))
    it = iter(times)
    return replace(
        line,
        words=tuple(
            replace(
                w,
                glyphs=tuple(
                    g
                    if g.measured
                    else replace(g, start=round(t[0], 3), end=round(t[1], 3))
                    for g, t in ((g, next(it)) for g in w.glyphs)
                ),
            )
            for w in line.words
        ),
    )


def refine_glyphs(
    timed_text: TimedText,
    vocals: Path | str,
    *,
    romanize: Romanizer = romanize_kana,
    margin_s: float = MARGIN_S,
    allow_download: bool = False,
) -> TimedText:
    """Return ``timed_text`` with measured :attr:`Word.glyphs` on every word.

    ``vocals`` should be a separated vocal stem (see :func:`vocal_stem`): the
    accompaniment confuses a CTC model far more than it confuses a listener.
    The coarse word times are only used as per-line windows; the glyph times
    replace nothing else. A line with nothing romanisable keeps evenly spread,
    ``measured=False`` glyphs rather than invented precision.
    """
    bundle, model, dic = _load_mms(allow_download=allow_download)
    sr = bundle.sample_rate
    wav = _read_mono(vocals, sr)
    return replace(
        timed_text,
        sections=tuple(
            replace(
                s,
                lines=tuple(
                    _align_line(
                        ln,
                        wav=wav,
                        sr=sr,
                        model=model,
                        dic=dic,
                        romanize=romanize,
                        margin_s=margin_s,
                    )
                    for ln in s.lines
                ),
            )
            for s in timed_text.sections
        ),
        source=f"{timed_text.source}+glyph_align",
    )


def vocal_stem(
    audio: Path | str, *, out_dir: Path | str, allow_download: bool = False
) -> Path:
    """The song's separated vocal stem (Demucs), raising rather than degrading.

    Reuses the footage scorer's separator; that one returns ``None`` on any
    failure because lip-sync is optional there, but here no stem means no
    trustworthy glyph times, so the absence is an error.
    """
    from muvid.footage.scoring.lipsync import separate_master_vocals

    if not (allow_download or os.environ.get("MUVID_ALLOW_WEIGHT_DOWNLOAD") == "1"):
        # Demucs fetches its (CC-BY-NC) weights on first use, and offers no
        # cheap "is it cached?" probe: the caller must say yes, or hand us a stem.
        raise RuntimeError(
            "Vocal separation may download the htdemucs weights (CC-BY-NC). "
            "Pass allow_download=True (CLI: --allow-download) or supply a vocal "
            "stem yourself (CLI: --vocals)."
        )
    out = separate_master_vocals(str(audio), out_dir=str(out_dir))
    if out is None:
        raise RuntimeError(
            "Vocal separation failed or Demucs is missing "
            "(pip install 'muvid[lyricvid-glyphs]')."
        )
    return out


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------


def _glyph_starts(tt: TimedText) -> list[float]:
    return [
        g.start
        for w in tt.words()
        for g in w.glyphs
        if g.measured
        and g.end > g.start
        and unicodedata.category(g.text)[0] == "L"
        and g.text not in "ーッっ"
    ]


def onset_report(
    timed_text: TimedText,
    vocals: Path | str,
    *,
    tol_s: float = ONSET_TOL_S,
    seed: int = 0,
    trials: int = 20,
) -> dict[str, Any]:
    """How well glyph starts sit on acoustic onsets of the vocal stem.

    ``near_onset`` is the share of glyph starts within ``tol_s`` of a detected
    onset; ``random_baseline`` is the same share after jittering every start
    by up to ±0.5 s — the gap between the two is the evidence, since a dense
    onset track makes any time look "near" something.
    """
    import numpy as np
    import soundfile as sf
    import librosa

    y, sr = sf.read(str(vocals), dtype="float32", always_2d=True)
    onsets = librosa.onset.onset_detect(y=y.mean(1), sr=sr, units="time")
    starts = np.array(_glyph_starts(timed_text))
    if not len(starts) or not len(onsets):
        return {"n_glyphs": int(len(starts)), "n_onsets": int(len(onsets))}

    def share(ts) -> float:
        return float(np.mean([np.min(np.abs(onsets - t)) <= tol_s for t in ts]))

    dist = np.array([np.min(np.abs(onsets - t)) for t in starts])
    rng = np.random.default_rng(seed)
    base = np.mean(
        [share(starts + rng.uniform(-0.5, 0.5, len(starts))) for _ in range(trials)]
    )
    return {
        "n_glyphs": int(len(starts)),
        "n_onsets": int(len(onsets)),
        "near_onset": round(share(starts), 3),
        "random_baseline": round(float(base), 3),
        "median_onset_distance_ms": round(float(np.median(dist)) * 1000),
        "tol_ms": round(tol_s * 1000),
    }


def word_agreement(
    fine: TimedText, coarse: TimedText, *, disagree_s: float = DISAGREE_S
) -> dict[str, Any]:
    """Compare word starts (first glyph start when present) of two timings.

    The two must hold the same words in the same order (``fine`` is normally
    ``refine_glyphs(coarse, ...)``). Returns summary statistics and the list
    of words that disagree by more than ``disagree_s`` — the ones to look at.
    """
    import numpy as np

    def start(w: Word) -> float:
        return w.glyphs[0].start if w.glyphs else w.start

    a, b = list(fine.words()), list(coarse.words())
    if [w.text for w in a] != [w.text for w in b]:
        raise ValueError("word_agreement needs the same word sequence in both timings")
    d = np.array([start(x) - start(y) for x, y in zip(a, b)])
    bad = [
        {
            "index": i,
            "text": x.text,
            "fine": round(start(x), 3),
            "coarse": round(start(y), 3),
        }
        for i, (x, y, dd) in enumerate(zip(a, b, d))
        if abs(dd) > disagree_s
    ]
    return {
        "n_words": len(a),
        "median_abs_diff_ms": round(float(np.median(np.abs(d))) * 1000),
        "median_signed_diff_ms": round(float(np.median(d)) * 1000),
        "disagree_s": disagree_s,
        "disagreements": bad,
    }


def reconcile(
    fine: TimedText,
    coarse: TimedText,
    *,
    disagree_s: float = DISAGREE_S,
    min_glyph_s: float = MIN_GLYPH_S,
) -> tuple[TimedText, list[dict[str, Any]]]:
    """Distrust the fine timing where it is implausible; say where.

    A word is distrusted when its start disagrees with the coarse timing by
    more than ``disagree_s``, or when a sounded glyph lasts under
    ``min_glyph_s`` (CTC squeezing a word it could not hear into a few frames —
    seen on a song where the vendor's transcript listed a repeat the singer
    never sang). Such a word falls back to its COARSE window, shifted by the
    median offset between the two timings over the words they agree on (vendor
    times run late; the bias is measured, not assumed), with its glyphs spread
    evenly and marked ``measured=False``. Returns the timing and one record per
    replaced word.
    """
    import numpy as np

    def start(w: Word) -> float:
        return w.glyphs[0].start if w.glyphs else w.start

    a, b = list(fine.words()), list(coarse.words())
    # Squeezing is judged on syllabic glyphs (kana/CJK: one mora each) only —
    # letters of an alphabet are legitimately ~30 ms apart — and only between
    # neighbours in the same line (lines are aligned in overlapping windows).
    crushed: set[int] = set()
    w_idx = 0
    for ln in fine.lines():
        sounded = []
        for w in ln.words:
            sounded += [
                (w_idx, g.start)
                for g in w.glyphs
                if g.measured
                and g.text not in "ーッっ"
                and unicodedata.east_asian_width(g.text[0]) in "WF"
            ]
            w_idx += 1
        for (wa, t), (wb, u) in zip(sounded, sounded[1:]):
            if 0 <= u - t < min_glyph_s:
                crushed |= {wa, wb}

    if [w.text for w in a] != [w.text for w in b]:
        raise ValueError("reconcile needs the same word sequence in both timings")
    diffs = [start(x) - start(y) for x, y in zip(a, b)]
    bad = {i for i, d in enumerate(diffs) if abs(d) > disagree_s} | crushed
    good = [d for i, d in enumerate(diffs) if i not in bad]
    bias = float(np.median(good)) if good else 0.0
    replaced, k = [], 0
    sections = []
    for sec in fine.sections:
        lines = []
        for ln in sec.lines:
            words = []
            for w in ln.words:
                if k in bad:
                    c = b[k]
                    shifted = replace(
                        c,
                        start=c.start + bias,
                        end=c.end + bias,
                        measured=False,
                        glyphs=(),
                    )
                    glyphs = _interpolated(
                        shifted,
                        [(g.text, "") for g in w.glyphs] or [(ch, "") for ch in w.text],
                    )
                    words.append(
                        replace(
                            w,
                            start=shifted.start,
                            end=shifted.end,
                            measured=False,
                            glyphs=glyphs,
                        )
                    )
                    replaced.append(
                        {
                            "index": k,
                            "text": w.text,
                            "fine": round(start(w), 3),
                            "used": round(shifted.start, 3),
                            "why": "squeezed" if k in crushed else "disagrees",
                        }
                    )
                else:
                    words.append(w)
                k += 1
            lines.append(replace(ln, words=tuple(words)))
        sections.append(replace(sec, lines=tuple(lines)))
    return replace(fine, sections=tuple(sections)), replaced


def glyph_rows(tt: TimedText) -> Iterable[dict[str, Any]]:
    """One flat record per glyph, for a table or a quick look."""
    for li, ln in enumerate(tt.lines()):
        for wi, w in enumerate(ln.words):
            for g in w.glyphs:
                yield {
                    "line": li,
                    "word": wi,
                    "text": g.text,
                    "start": g.start,
                    "end": g.end,
                    "measured": g.measured,
                }
