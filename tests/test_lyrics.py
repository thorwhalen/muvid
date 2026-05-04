"""Lyrics markdown parsing + transcript-driven lyric synthesis."""

from __future__ import annotations

from mtv.lyrics import (
    lyrics_from_transcript,
    parse_lyrics_md,
    render_lyrics_md,
    words_from_transcript,
)


SAMPLE_MD = """[intro]
(instrumental)

[verse 1]
I came down to the river  // 12.5
to wash my soul

[chorus]
hold my hand
when the night comes calling
"""


def test_parse_lyrics_md_basic_shape():
    doc = parse_lyrics_md(SAMPLE_MD)
    labels = [s.label for s in doc.sections]
    assert labels == ["intro", "verse 1", "chorus"]
    # The (instrumental) line is dropped; intro has no LyricLines.
    assert doc.sections[0].lines == ()
    assert len(doc.sections[1].lines) == 2
    assert len(doc.sections[2].lines) == 2


def test_parse_lyrics_md_picks_up_anchor():
    doc = parse_lyrics_md(SAMPLE_MD)
    first_line = doc.sections[1].lines[0]
    assert first_line.start_s == 12.5
    # The anchor token is stripped from the text.
    assert "//" not in first_line.text
    assert first_line.text == "I came down to the river"


def test_render_then_parse_is_stable():
    doc = parse_lyrics_md(SAMPLE_MD)
    rendered = render_lyrics_md(doc)
    redoc = parse_lyrics_md(rendered)
    assert [s.label for s in redoc.sections] == [s.label for s in doc.sections]
    assert [L.text for L in redoc.lines] == [L.text for L in doc.lines]


def test_words_from_transcript_filters_non_words():
    transcript = {
        "words": [
            {"text": "hello", "start": 0.0, "end": 0.4, "type": "word"},
            {"text": "(laughs)", "start": 0.4, "end": 0.7, "type": "audio_event"},
            {"text": "world", "start": 0.7, "end": 1.0, "type": "word"},
            {"text": "skip-no-time", "type": "word"},  # missing start/end
        ]
    }
    out = words_from_transcript(transcript)
    assert [w["text"] for w in out] == ["hello", "world"]


def test_lyrics_from_transcript_splits_on_punctuation_and_gaps():
    transcript = {
        "words": [
            {"text": "hello",  "start": 0.0, "end": 0.4},
            {"text": "world.", "start": 0.4, "end": 0.9},
            # Long gap → new line
            {"text": "again",  "start": 5.0, "end": 5.5},
        ]
    }
    doc = lyrics_from_transcript(transcript)
    texts = [L.text for L in doc.lines]
    assert texts == ["hello world.", "again"]
    assert doc.sections[0].label == "transcribed"
