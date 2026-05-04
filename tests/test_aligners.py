"""Pluggable aligner registry + user-provided aligner."""

from __future__ import annotations

import pytest

from muvid.align import (
    AlignmentResult,
    align_lyrics,
    aligner_info,
    list_aligners,
    register_aligner,
)
from muvid.lyrics import parse_lyrics_md


SAMPLE_MD = """[verse]
hello hello hello
goodbye goodbye goodbye
"""


def test_default_aligner_is_scribe_greedy():
    transcript = {
        "words": [
            {"text": "hello", "start": 0.5, "end": 1.0, "type": "word"},
            {"text": "hello", "start": 1.0, "end": 1.5, "type": "word"},
            {"text": "hello", "start": 1.5, "end": 2.0, "type": "word"},
            {"text": "goodbye", "start": 6.0, "end": 6.5, "type": "word"},
            {"text": "goodbye", "start": 6.5, "end": 7.0, "type": "word"},
            {"text": "goodbye", "start": 7.0, "end": 7.5, "type": "word"},
        ]
    }
    result = align_lyrics(parse_lyrics_md(SAMPLE_MD), transcript, duration_s=10.0)
    assert isinstance(result, AlignmentResult)
    assert result.lines[0].start_s == pytest.approx(0.5)


def test_list_aligners_includes_builtins():
    names = list_aligners()
    assert "scribe-greedy" in names
    assert "user" in names
    assert "whisperx-lite" in names
    assert "stars" in names


def test_aligner_info_has_description():
    info = aligner_info("scribe-greedy")
    assert info.description
    assert info.fn is not None


def test_unknown_aligner_raises():
    with pytest.raises(KeyError):
        align_lyrics(parse_lyrics_md(SAMPLE_MD), {"words": []}, aligner="nope")


def test_user_aligner_uses_supplied_line_timings():
    timings = [
        {"line_index": 0, "start_s": 1.5, "end_s": 3.0},
        {"line_index": 1, "start_s": 5.0, "end_s": 7.5},
    ]
    result = align_lyrics(
        parse_lyrics_md(SAMPLE_MD),
        {},
        aligner="user",
        user_line_timings=timings,
    )
    assert result.lines[0].start_s == pytest.approx(1.5)
    assert result.lines[0].end_s == pytest.approx(3.0)
    assert result.lines[1].start_s == pytest.approx(5.0)
    assert result.lines[1].end_s == pytest.approx(7.5)
    # The user aligner doesn't produce per-word alignments.
    assert result.lines[0].word_alignments == ()


def test_user_aligner_falls_back_to_anchors_in_lyrics_md():
    md = "[v]\nfirst line  // 2.0\nsecond line  // 4.0\n"
    result = align_lyrics(parse_lyrics_md(md), {}, aligner="user", duration_s=8.0)
    assert result.lines[0].start_s == pytest.approx(2.0)
    assert result.lines[1].start_s == pytest.approx(4.0)


def test_stars_aligner_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        align_lyrics(parse_lyrics_md(SAMPLE_MD), {}, aligner="stars")


def test_register_aligner_round_trips():
    """Custom aligners can be registered + dispatched."""

    def _trivial(lyrics, transcript, *, duration_s=0.0):
        # Returns empty alignment.
        return AlignmentResult(sections=())

    register_aligner("test-trivial", _trivial, description="for tests")
    assert "test-trivial" in list_aligners()
    result = align_lyrics(parse_lyrics_md(SAMPLE_MD), {}, aligner="test-trivial")
    assert isinstance(result, AlignmentResult)
    assert result.sections == ()


def test_whisperx_lite_falls_back_to_transcript_when_no_audio_path():
    """Without an audio_path, whisperx-lite degrades to scribe-greedy."""
    transcript = {
        "words": [
            {"text": "hello", "start": 0.5, "end": 1.0, "type": "word"},
            {"text": "hello", "start": 1.0, "end": 1.5, "type": "word"},
            {"text": "hello", "start": 1.5, "end": 2.0, "type": "word"},
            {"text": "goodbye", "start": 6.0, "end": 6.5, "type": "word"},
            {"text": "goodbye", "start": 6.5, "end": 7.0, "type": "word"},
            {"text": "goodbye", "start": 7.0, "end": 7.5, "type": "word"},
        ]
    }
    # Skip the requires=("faster_whisper",) check by calling the function
    # directly — when audio_path is None, faster_whisper isn't actually used.
    from muvid.align import align_whisperx_lite

    result = align_whisperx_lite(
        parse_lyrics_md(SAMPLE_MD), transcript, duration_s=10.0
    )
    assert result.lines[0].start_s == pytest.approx(0.5)
