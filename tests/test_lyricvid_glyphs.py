"""Per-glyph timing and the glyph_pages archetype (muvid#142). No torch needed."""

import pytest

from muvid.lyricvid import glyph_align as ga
from muvid.lyricvid import spec as S
from muvid.lyricvid.scene import compile_scene, Canvas
from muvid.lyricvid.timed_text import Glyph, Line, Word, from_dict, from_word_records


def _tt():
    recs = [
        {"text": "バス", "start": 1.0, "end": 1.5, "section": "Verse"},
        {"text": "ドア", "start": 1.6, "end": 2.0, "line_end": True},
        {"text": "メロン", "start": 3.0, "end": 3.6, "line_end": True},
        {"text": "パン", "start": 6.0, "end": 6.5, "line_end": True},
    ]
    return from_word_records(recs, duration=8.0)


def _with_glyphs(tt):
    from dataclasses import replace

    def g(w):
        n = len(w.text)
        step = (w.end - w.start) / n
        return tuple(Glyph(text=c, start=w.start + i * step, end=w.start + (i + 1) * step)
                     for i, c in enumerate(w.text))

    return replace(tt, sections=tuple(
        replace(s, lines=tuple(replace(l, words=tuple(replace(w, glyphs=g(w)) for w in l.words))
                               for l in s.lines)) for s in tt.sections))


def test_word_records_keep_vendor_line_breaks_and_sections():
    tt = _tt()
    assert [l.text for l in tt.lines()] == ["バス ドア", "メロン", "パン"]
    assert tt.sections[0].label == "Verse"


def test_glyphs_round_trip_through_dict():
    tt = _with_glyphs(_tt())
    assert from_dict(tt.to_dict()) == tt


def _treatment(**params):
    return {"scenes": [{"applies_to": ["*"], "archetype": "glyph_pages",
                        "params": {"lines_on_screen": 2, "emphasis": "ハスン", **params}}],
            "direction": {"palette": {"bg": "#000000", "fg": "#ffffff",
                                      "accent": "#ffff00", "dim": "#808080"}}}


def test_glyph_pages_ignite_each_glyph_at_its_time_with_emphasis_classes():
    tt = _with_glyphs(_tt())
    sc = compile_scene(S.coerce(_treatment())[0], tt, canvas=Canvas(width=1920, height=1080))
    ink = [c for c in sc.cues if c.layer == 1]
    glyphs = [g for w in tt.words() for g in w.glyphs]
    assert sorted(c.t_in for c in ink) == pytest.approx(sorted(g.start for g in glyphs))
    by = {c.text: c.colour for c in ink}
    assert by["バ"] == "#ffff00"  # folds onto ハ: a target
    assert by["ス"] == "#ffff00"
    assert by["ド"] != "#ffff00"  # not a target: milder highlight
    ghosts = {c.text: c.colour for c in sc.cues if c.layer == 0}
    assert ghosts["バ"] == "#808080" and ghosts["ド"] != "#808080"


def test_glyph_pages_page_turns_before_the_next_page_is_sung():
    tt = _with_glyphs(_tt())
    sc = compile_scene(S.coerce(_treatment())[0], tt, canvas=Canvas(width=1920, height=1080))
    pages = {}
    for c in sc.cues:
        pages.setdefault(c.extra["page"], []).append(c)
    first_ink_p1 = min(c.t_in for c in pages[1] if c.layer == 1)
    p0_gone = max(c.t_out for c in pages[0] if c.layer == 1)
    p1_shown = min(c.t_in for c in pages[1] if c.layer == 0)
    assert p0_gone < first_ink_p1 and p1_shown < first_ink_p1


def test_glyph_pages_without_glyphs_spreads_and_marks_unmeasured():
    sc = compile_scene(S.coerce(_treatment())[0], _tt(), canvas=Canvas(width=1920, height=1080))
    assert sc.cues and all(c.extra["measured"] is False for c in sc.cues)


def test_place_silent_puts_long_vowel_between_neighbours():
    nan = float("nan")
    w = Word(text="スーパ", start=1.0, end=2.0, glyphs=(
        Glyph(text="ス", start=1.0, end=1.1), Glyph(text="ー", start=nan, end=nan, measured=False),
        Glyph(text="パ", start=1.6, end=1.7)))
    out = ga._place_silent(Line(words=(w,)))
    g = out.words[0].glyphs[1]
    assert g.start == pytest.approx(1.3) and g.measured is False


def test_reconcile_replaces_squeezed_and_disagreeing_words():
    coarse = _tt()
    fine = _with_glyphs(coarse)
    from dataclasses import replace
    words = list(fine.words())
    # squeeze メロン into 30 ms
    bad = replace(words[2], glyphs=tuple(replace(g, start=3.0 + i * 0.01, end=3.0 + i * 0.01 + 0.01)
                                         for i, g in enumerate(words[2].glyphs)))
    fine = replace(fine, sections=tuple(replace(s, lines=tuple(
        replace(l, words=tuple(bad if w.text == "メロン" else w for w in l.words)) for l in s.lines))
        for s in fine.sections))
    out, replaced = ga.reconcile(fine, coarse)
    assert [r["text"] for r in replaced] == ["メロン"]
    assert all(not g.measured for w in out.words() if w.text == "メロン" for g in w.glyphs)


def test_weights_never_downloaded_unasked(monkeypatch, tmp_path):
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchaudio")
    monkeypatch.setattr(torch.hub, "get_dir", lambda: str(tmp_path))
    monkeypatch.delenv("MUVID_ALLOW_WEIGHT_DOWNLOAD", raising=False)
    with pytest.raises(RuntimeError, match="never downloads"):
        ga._load_mms(allow_download=False)
