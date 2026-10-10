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


def test_reconcile_does_not_flag_alphabet_letters():
    w = Word(text="love", start=1.0, end=1.4, glyphs=tuple(
        Glyph(text=c, start=1.0 + i * 0.02, end=1.0 + i * 0.02 + 0.02) for i, c in enumerate("love")))
    from muvid.lyricvid.timed_text import Section, TimedText
    fine = TimedText(sections=(Section(label="*", lines=(Line(words=(w,)),)),), duration=3.0)
    coarse = TimedText(sections=(Section(label="*", lines=(Line(words=(replace_glyphs(w),)),)),),
                       duration=3.0)
    _, replaced = ga.reconcile(fine, coarse)
    assert replaced == []


def replace_glyphs(w):
    from dataclasses import replace
    return replace(w, glyphs=())


def test_glyph_pages_skips_empty_pages_and_previews_next_page():
    recs = [{"text": "カス", "start": 1.0, "end": 2.6, "line_end": True},
            {"text": "タト", "start": 2.5, "end": 3.0, "line_end": True}]
    tt = _with_glyphs(from_word_records(recs, duration=5.0))
    sc = compile_scene(S.coerce(_treatment(lines_on_screen=1))[0], tt, canvas=Canvas(width=1920, height=1080))
    ta_ghost = [c for c in sc.cues if c.text == "タ" and c.layer == 0][0]
    assert ta_ghost.t_out - ta_ghost.t_in >= 0.2  # seen grey before it is sung


def test_romanize_word_final_sokuon_is_silent():
    assert ga.romanize_kana("カッ") == [("カ", "ka"), ("ッ", "")]


# --------------------------------------------------------------------------
# reconcile's detector seam, and the unsung detector (muvid#144)
# --------------------------------------------------------------------------

_SR = 8000


def _song_with_a_ghost_line():
    """Four lines: sung; a vendor-listed repeat nobody sang; sung but squeezed
    by the aligner; sung staccato with a long silence after it (not squeezed)."""
    recs = [
        {"text": "カス", "start": 1.0, "end": 2.0, "line_end": True},
        {"text": "タン", "start": 3.0, "end": 4.0},
        {"text": "タン", "start": 4.0, "end": 5.0, "line_end": True},
        {"text": "スター", "start": 6.0, "end": 7.0, "line_end": True},
        {"text": "ストップ", "start": 8.0, "end": 10.0, "line_end": True},
    ]
    coarse = from_word_records(recs, duration=11.0)
    fine = _with_glyphs(coarse)

    def squeeze(w):
        from dataclasses import replace
        t = w.start + 0.6  # crammed together, later than the vendor said
        return replace(w, glyphs=tuple(replace(g, start=t + i * 0.01, end=t + i * 0.01 + 0.01)
                                       for i, g in enumerate(w.glyphs)))

    from dataclasses import replace
    fine = replace(fine, sections=tuple(replace(s, lines=tuple(
        replace(l, words=tuple(squeeze(w) if w.text in ("タン", "スター") else w
                               for w in l.words)) for l in s.lines)) for s in fine.sections))
    return fine, coarse


def _stem(*spans, duration=11.0):
    np = pytest.importorskip("numpy")
    t = np.arange(int(duration * _SR)) / _SR
    y = np.zeros_like(t, dtype="float32")
    for a, b in spans:
        m = (t >= a) & (t < b)
        y[m] = 0.5 * np.sin(2 * np.pi * 220 * t[m])
    return y


@pytest.fixture
def stem_of(monkeypatch):
    def use(y):
        monkeypatch.setattr(ga, "_read_stem", lambda vocals: (y, _SR))
        return "vocals.wav"
    return use


def test_unsung_drops_the_silent_squeezed_line_and_nothing_else(stem_of):
    fine, coarse = _song_with_a_ghost_line()
    stem = stem_of(_stem((1.0, 2.0), (6.0, 7.0), (8.0, 8.3)))
    out, records = ga.reconcile(fine, coarse,
                                detectors=(*ga.default_detectors(), ga.unsung(stem)))
    assert [l.text for l in out.lines()] == ["カス", "スター", "ストップ"]
    by = {(r["index"], r["text"]): r for r in records}
    assert {k for k, r in by.items() if r["remedy"] == "drop"} == {(1, "タン"), (2, "タン")}
    assert all(r["why"] == "unsung" and r["used"] is None
               for r in records if r["remedy"] == "drop")
    # squeezed but SUNG: falls back to its vendor window, it is not deleted
    star = by[(3, "スター")]
    assert star["remedy"] == "replace" and star["why"] == "squeezed"
    # a quiet window alone is not evidence: ストップ is untouched
    assert all(r["text"] != "ストップ" for r in records)


def test_unsung_needs_the_aligner_to_have_failed_too(stem_of):
    """A silent stem over a line the aligner placed fine drops nothing."""
    fine, coarse = _song_with_a_ghost_line()
    fine = _with_glyphs(coarse)  # nothing squeezed
    stem = stem_of(_stem((1.0, 2.0)))  # every later line is quiet
    _, records = ga.reconcile(fine, coarse,
                              detectors=(*ga.default_detectors(), ga.unsung(stem)))
    assert records == []


def test_unsung_ignores_a_digitally_silent_stem(stem_of):
    """No loud level to be quiet against: never a reason to delete words."""
    fine, coarse = _song_with_a_ghost_line()
    stem = stem_of(_stem())
    _, records = ga.reconcile(fine, coarse,
                              detectors=(*ga.default_detectors(), ga.unsung(stem)))
    assert all(r["remedy"] == "replace" for r in records)


def test_default_reconcile_still_replaces_the_ghost_line():
    """Without a stem, the behaviour before muvid#144 is unchanged."""
    fine, coarse = _song_with_a_ghost_line()
    out, records = ga.reconcile(fine, coarse)
    assert [l.text for l in out.lines()] == [l.text for l in coarse.lines()]
    assert {r["text"] for r in records} == {"タン", "スター"}
    assert all(r["remedy"] == "replace" for r in records)


def test_a_custom_detector_plugs_in_and_drop_beats_replace():
    fine, coarse = _song_with_a_ghost_line()
    first = ga.Detector(name="first", find=lambda f, c: {0: "test"}, remedy="drop")
    also = ga.Detector(name="also", find=lambda f, c: {0: "test"})
    out, records = ga.reconcile(fine, coarse, detectors=(also, first))
    assert [l.text for l in out.lines()][0] == "タン タン"  # line 0 left empty: gone
    assert records == [{"index": 0, "text": "カス", "fine": 1.0, "why": "first",
                        "detail": "test", "remedy": "drop",
                        "flagged_by": ["also", "first"], "used": None}]


def test_an_unknown_remedy_is_refused():
    with pytest.raises(ValueError, match="remedy"):
        ga.Detector(name="x", find=lambda f, c: {}, remedy="mark")


def test_read_stem_reads_a_real_file(tmp_path):
    sf = pytest.importorskip("soundfile")
    y = _stem((0.0, 0.5), duration=1.0)
    sf.write(tmp_path / "v.wav", y, _SR)
    got, sr = ga._read_stem(tmp_path / "v.wav")
    assert sr == _SR and len(got) == len(y)
