"""The lyric-video timed-text layer: parsing, the aligner bridge, honesty flags.

The aligner bridge is the integration that was wrong in the first cut of #96:
``from_lyrics_and_audio`` called ``muvid.align`` functions that do not exist
(``resolve_aligner``, ``DEFAULT_ALIGNER``), so the default path — a song plus a
lyrics file — raised on every machine. These tests pin the bridge to the API
``muvid.align`` actually has, with the aligner itself stubbed so the suite does
not need whisper or a network.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from muvid.lyricvid import timed_text as tt


def _wav(tmp_path: Path, seconds: float = 2.0) -> Path:
    """A real (silent) wav so media_duration has something to probe."""
    import subprocess

    out = tmp_path / "song.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"anullsrc=r=16000:cl=mono", "-t", str(seconds), str(out)],
        check=True,
    )
    return out


@pytest.fixture
def wav(tmp_path):
    pytest.importorskip("muvid.visualize.ffmpeg")
    import shutil

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    return _wav(tmp_path)


# --- from_alignment_result: the converter --------------------------------------


def test_alignment_result_keeps_sections_and_lines():
    w1 = NS(text="we", start_s=0.0, end_s=0.3, token_index=0)
    w2 = NS(text="are", start_s=0.3, end_s=0.6, token_index=1)
    l0 = NS(line_index=0, text="we are", start_s=0.0, end_s=0.6, word_alignments=(w1, w2))
    l1 = NS(line_index=1, text="the champions", start_s=1.0, end_s=2.0, word_alignments=())
    res = NS(sections=(NS(label="chorus", lines=(l0, l1)),))
    out = tt.from_alignment_result(res, duration=2.0)
    assert [s.label for s in out.sections] == ["chorus"]
    assert [l.text for l in out.lines()] == ["we are", "the champions"]
    measured = [w.measured for w in out.words()]
    # the matched line is measured; the unmatched one was spread, and says so
    assert measured == [True, True, False, False]
    assert out.measured is False


def test_the_lyric_text_wins_over_the_transcript_text():
    """WordAlignment.text is what the ASR HEARD; the lyric is what the user wrote.

    An ASR that hears "Apple" for a lyric that says "apple" must not change the
    rendered word — the alignment contributes timing only.
    """
    heard = NS(text="Apple", start_s=0.0, end_s=0.4, token_index=0)
    ln = NS(line_index=0, text="apple pie", start_s=0.0, end_s=1.0,
            word_alignments=(heard,))
    out = tt.from_alignment_result(NS(sections=(NS(label="", lines=(ln,)),)))
    assert [w.text for w in out.words()] == ["apple", "pie"]


def test_unmatched_tokens_are_interpolated_between_measured_neighbours():
    """Half-matched line → half measured, not all-or-nothing."""
    a = NS(text="we", start_s=0.0, end_s=0.2, token_index=0)
    d = NS(text="champions", start_s=1.0, end_s=1.5, token_index=3)
    ln = NS(line_index=0, text="we are the champions", start_s=0.0, end_s=1.5,
            word_alignments=(a, d))
    out = tt.from_alignment_result(NS(sections=(NS(label="", lines=(ln,)),)))
    ws = list(out.words())
    assert [w.text for w in ws] == ["we", "are", "the", "champions"]
    assert [w.measured for w in ws] == [True, False, False, True]
    # the two guessed words sit strictly between their measured neighbours
    assert 0.2 <= ws[1].start < ws[2].end <= 1.0
    # and an out-of-range token_index is ignored rather than crashing
    bogus = NS(text="x", start_s=0.0, end_s=0.1, token_index=99)
    ln2 = NS(line_index=0, text="one two", start_s=0.0, end_s=1.0, word_alignments=(bogus,))
    out2 = tt.from_alignment_result(NS(sections=(NS(label="", lines=(ln2,)),)))
    assert [w.measured for w in out2.words()] == [False, False]


def test_alignment_result_drops_a_line_with_nothing_to_go_on():
    l0 = NS(line_index=0, text="lost", start_s=None, end_s=None, word_alignments=())
    res = NS(sections=(NS(label="", lines=(l0,)),))
    out = tt.from_alignment_result(res, duration=1.0)
    assert out.sections == ()


# --- from_lyrics_and_audio: the bridge to muvid.align ---------------------------


def test_bridge_uses_align_lyrics_with_the_offline_aligner(monkeypatch, wav, tmp_path):
    """Default path: whisperx-lite, audio handed over via ``audio_path=``."""
    from muvid import align as align_mod

    lyrics = tmp_path / "lyrics.md"
    lyrics.write_text("[verse]\nwe are the champions\nmy friends\n", encoding="utf-8")

    seen = {}

    def fake_align_lyrics(doc, transcript, *, duration_s, aligner, **kw):
        seen.update(doc=doc, transcript=transcript, duration_s=duration_s,
                    aligner=aligner, kw=kw)
        w = NS(text="we", start_s=0.1, end_s=0.4, token_index=0)
        line = NS(line_index=0, text="we are the champions", start_s=0.1, end_s=1.2,
                  word_alignments=(w,))
        return NS(sections=(NS(label="verse", lines=(line,)),))

    monkeypatch.setattr(align_mod, "align_lyrics", fake_align_lyrics)
    monkeypatch.setattr(tt, "_has", lambda m: m == "faster_whisper")

    out = tt.from_lyrics_and_audio(wav, lyrics=lyrics)

    assert seen["aligner"] == "whisperx-lite"
    assert seen["kw"]["audio_path"] == str(wav)
    assert seen["transcript"] == {}
    assert seen["duration_s"] == pytest.approx(2.0, abs=0.05)
    assert [l.text for l in seen["doc"].lines] == ["we are the champions", "my friends"]
    assert out.source == "aligner:whisperx-lite"
    assert out.sections[0].label == "verse"


def test_bridge_refuses_to_spend_silently_when_nothing_offline_exists(monkeypatch, wav, tmp_path):
    """No faster-whisper → a clear error, never a paid Scribe call by surprise."""
    lyrics = tmp_path / "lyrics.md"
    lyrics.write_text("hello world\n", encoding="utf-8")
    monkeypatch.setattr(tt, "_has", lambda m: False)
    with pytest.raises(RuntimeError, match="faster-whisper"):
        tt.from_lyrics_and_audio(wav, lyrics=lyrics)


def test_bridge_honours_an_explicit_aligner_name(monkeypatch, wav, tmp_path):
    from muvid import align as align_mod

    lyrics = tmp_path / "lyrics.md"
    lyrics.write_text("hello world\n", encoding="utf-8")
    seen = {}

    def fake_align_lyrics(doc, transcript, *, duration_s, aligner, **kw):
        seen["aligner"] = aligner
        return NS(sections=())

    monkeypatch.setattr(align_mod, "align_lyrics", fake_align_lyrics)
    monkeypatch.setattr(tt, "_has", lambda m: False)  # explicit name bypasses the probe
    out = tt.from_lyrics_and_audio(wav, lyrics=lyrics, aligner="user")
    assert seen["aligner"] == "user"
    assert out.sections == ()


def test_bridge_with_no_lyrics_transcribes_and_says_so(monkeypatch, wav):
    monkeypatch.setattr(tt, "_has", lambda m: m == "faster_whisper")
    monkeypatch.setattr(
        tt, "_transcribe_words_offline",
        lambda audio: [("la", 0.0, 0.5), ("la", 0.5, 1.0)],
    )
    out = tt.from_lyrics_and_audio(wav)
    assert out.source == "transcript"
    assert [w.text for w in out.words()] == ["la", "la"]


def test_bridge_rejects_an_empty_lyrics_file(monkeypatch, wav, tmp_path):
    lyrics = tmp_path / "lyrics.md"
    lyrics.write_text("# just a header\n(instrumental)\n", encoding="utf-8")
    monkeypatch.setattr(tt, "_has", lambda m: True)
    with pytest.raises(ValueError, match="no lyric lines"):
        tt.from_lyrics_and_audio(wav, lyrics=lyrics)


# --- subtitle parsing edge cases ------------------------------------------------


def test_srt_with_html_tags_crlf_and_multiline_cues(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_bytes(
        b"\xef\xbb\xbf1\r\n00:00:01,000 --> 00:00:02,000\r\n<i>hello</i>\r\nworld\r\n\r\n"
        b"2\r\n00:00:02,500 --> 00:00:03,000\r\nagain\r\n"
    )
    out = tt.from_subtitles(srt, duration=3.0)
    assert [l.text for l in out.lines()] == ["hello world", "again"]
    assert out.source == "srt"
    assert out.measured is False  # SRT is line-timed; words are interpolated


def test_plain_lrc_without_fraction_and_enhanced_lrc_word_stamps(tmp_path):
    plain = tmp_path / "p.lrc"
    plain.write_text("[00:01]first line\n[00:03]second\n", encoding="utf-8")
    out = tt.from_subtitles(plain, duration=5.0)
    assert [round(l.start, 2) for l in out.lines()] == [1.0, 3.0]
    assert out.measured is False

    enhanced = tmp_path / "e.lrc"
    enhanced.write_text("[00:01.00]<00:01.00>one <00:01.50>two\n", encoding="utf-8")
    out = tt.from_subtitles(enhanced, duration=3.0)
    ws = list(out.words())
    assert [(w.text, w.start) for w in ws] == [("one", 1.0), ("two", 1.5)]
    assert out.measured is True
    assert out.source == "lrc-enhanced"
