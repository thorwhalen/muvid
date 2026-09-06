"""Lyrics markdown parsing + transcript-driven lyric synthesis."""

from __future__ import annotations

import json

from muvid.lyrics import (
    lyrics_from_transcript,
    parse_lyrics_md,
    render_lyrics_md,
    words_from_transcript,
)
from tests.ascii_locale_support import needs_ascii_locale, run_under_ascii_locale


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
            {"text": "hello", "start": 0.0, "end": 0.4},
            {"text": "world.", "start": 0.4, "end": 0.9},
            # Long gap → new line
            {"text": "again", "start": 5.0, "end": 5.5},
        ]
    }
    doc = lyrics_from_transcript(transcript)
    texts = [L.text for L in doc.lines]
    assert texts == ["hello world.", "again"]
    assert doc.sections[0].label == "transcribed"


# --- UTF-8 on disk, regardless of the ambient locale ----------------------

#: A Spanish dembow lyric — the kind of text lyrics.md exists to hold.
NON_ASCII_LINES = ("Qué calor, cómo está el ambiente", "ñandú — «dembow»")

_ROUND_TRIP = """
import json, sys
from muvid.lyrics import (
    LyricLine, LyricSection, LyricsDoc, read_lyrics_md, write_lyrics_md,
)

path, texts = sys.argv[1], json.loads(sys.argv[2])
doc = LyricsDoc(
    sections=(
        LyricSection(
            label="verso 1",
            lines=tuple(
                LyricLine(text=t, line_index=i, section_label="verso 1")
                for i, t in enumerate(texts)
            ),
        ),
    )
)
write_lyrics_md(path, doc)
# ensure_ascii keeps stdout readable under the ASCII codec too.
print(json.dumps([L.text for L in read_lyrics_md(path).lines]))
"""


@needs_ascii_locale
def test_lyrics_md_round_trips_non_ascii_under_ascii_locale(tmp_path):
    """``lyrics.md`` is UTF-8 by contract, not by ambient locale.

    Where the process locale is ``C`` (a container with no locale, cron, a
    systemd unit), an unqualified ``write_text`` picks the ASCII codec and
    raises on the first accented character, so a Spanish lyric cannot be saved
    at all.
    """
    target = tmp_path / "lyrics.md"
    proc = run_under_ascii_locale(
        _ROUND_TRIP, str(target), json.dumps(list(NON_ASCII_LINES))
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == list(NON_ASCII_LINES)
    # ...and the bytes that landed on disk are UTF-8, not the locale's codec.
    on_disk = target.read_bytes().decode("utf-8")
    assert all(line in on_disk for line in NON_ASCII_LINES)


@needs_ascii_locale
def test_read_transcript_decodes_utf8_under_ascii_locale(tmp_path):
    """``transcript.json`` may hold non-ASCII words even though we write it ASCII.

    Scribe's own output goes through ``json.dump`` (ASCII-escaped), but a user
    may hand-correct the file in a UTF-8 editor, and that must still read back.
    """
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps(
            {"words": [{"text": "ñandú", "start": 0.0, "end": 0.4}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    proc = run_under_ascii_locale(
        "import json, sys\n"
        "from muvid.lyrics import read_transcript\n"
        "print(json.dumps(read_transcript(sys.argv[1])['words'][0]['text']))\n",
        str(transcript),
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == "ñandú"
