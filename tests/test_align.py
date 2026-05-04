"""Greedy token-match alignment + lacing serialization round-trip."""

from __future__ import annotations

import os

import pytest

from mtv.align import align_lyrics, write_alignment_store
from mtv.lyrics import parse_lyrics_md


SAMPLE_MD = """[intro]
(instrumental)

[verse 1]
I came down to the river
to wash my soul

[chorus]
hold my hand
when the night comes calling
"""


SAMPLE_TRANSCRIPT = {
    "words": [
        {"text": "I", "start": 12.5, "end": 12.7},
        {"text": "came", "start": 12.7, "end": 13.0},
        {"text": "down", "start": 13.0, "end": 13.3},
        {"text": "to", "start": 13.3, "end": 13.4},
        {"text": "the", "start": 13.4, "end": 13.6},
        {"text": "river", "start": 13.6, "end": 14.2},
        {"text": "to", "start": 16.2, "end": 16.3},
        {"text": "wash", "start": 16.3, "end": 16.7},
        {"text": "my", "start": 16.7, "end": 16.8},
        {"text": "soul", "start": 16.8, "end": 17.5},
        {"text": "hold", "start": 22.0, "end": 22.5},
        {"text": "my", "start": 22.5, "end": 22.7},
        {"text": "hand", "start": 22.7, "end": 23.4},
        {"text": "when", "start": 24.0, "end": 24.2},
        {"text": "the", "start": 24.2, "end": 24.4},
        {"text": "night", "start": 24.4, "end": 24.8},
        {"text": "comes", "start": 24.8, "end": 25.2},
        {"text": "calling", "start": 25.2, "end": 26.0},
    ]
}


def test_align_picks_up_word_timings():
    doc = parse_lyrics_md(SAMPLE_MD)
    al = align_lyrics(doc, SAMPLE_TRANSCRIPT, duration_s=30.0)
    # Every line in the verse + chorus should pick up a start time
    # from a matched word.
    lines_with_times = [L for L in al.lines if L.start_s is not None]
    assert len(lines_with_times) == 4
    # Verse 1, line 0 starts when "I" is sung at 12.5.
    verse_first = al.sections[1].lines[0]
    assert verse_first.start_s == pytest.approx(12.5)
    assert verse_first.end_s == pytest.approx(14.2)
    # 18 transcript tokens, all should match.
    assert len(al.words) == 18


def test_align_section_spans_inherit_from_lines():
    doc = parse_lyrics_md(SAMPLE_MD)
    al = align_lyrics(doc, SAMPLE_TRANSCRIPT, duration_s=30.0)
    chorus = al.sections[2]
    assert chorus.start_s == pytest.approx(22.0)
    assert chorus.end_s == pytest.approx(26.0)


def test_lines_in_returns_window_overlapping_lines():
    doc = parse_lyrics_md(SAMPLE_MD)
    al = align_lyrics(doc, SAMPLE_TRANSCRIPT, duration_s=30.0)
    in_window = al.lines_in(15.0, 20.0)
    texts = [L.text for L in in_window]
    assert texts == ["to wash my soul"]


def test_close_enough_tolerates_one_letter_mishears():
    md = "[v]\nblue suede shoos\n"  # whisper heard "shoes" as "shoos"
    transcript = {
        "words": [
            {"text": "blue", "start": 0.0, "end": 0.3},
            {"text": "suede", "start": 0.3, "end": 0.7},
            {"text": "shoes", "start": 0.7, "end": 1.2},
        ]
    }
    al = align_lyrics(parse_lyrics_md(md), transcript, duration_s=2.0)
    assert al.lines[0].start_s == pytest.approx(0.0)
    assert al.lines[0].end_s == pytest.approx(1.2)


def test_write_alignment_store_roundtrips_through_lacing(tmp_path):
    pytest.importorskip("lacing")
    from lacing import RationalTime, SqliteStore, TimeInterval

    doc = parse_lyrics_md(SAMPLE_MD)
    al = align_lyrics(doc, SAMPLE_TRANSCRIPT, duration_s=30.0)
    out = tmp_path / "a.annot"
    write_alignment_store(al, path=out)
    assert out.exists()
    store = SqliteStore(str(out))
    try:
        win = TimeInterval(RationalTime(0, 1000), RationalTime(30000, 1000))
        n_sections = sum(1 for a in store.intersects(win) if a.tier == "sections")
        n_lines = sum(1 for a in store.intersects(win) if a.tier == "lines")
        n_words = sum(1 for a in store.intersects(win) if a.tier == "words")
        assert n_sections == 2  # intro has no lines, so no section interval
        assert n_lines == 4
        assert n_words == 18
    finally:
        store.close()
