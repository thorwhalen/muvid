"""Lyrics — transcription and markdown round-trip.

Two surfaces for the user:

1. ``transcribe(audio_path)`` — calls ``mixing.transcript.transcribe``
   (ElevenLabs Scribe) and writes the raw word-timestamped JSON to
   ``lyrics/transcript.json``. This is the *seed*; the user is expected
   to correct it.
2. ``write_lyrics_md`` / ``parse_lyrics_md`` — the canonical, editable
   form. A simple markdown with ``[section]`` headers, one line per
   sung line, and an optional ``// <seconds>`` end-of-line anchor.

The alignment module consumes both: it reads ``lyrics.md`` for the
*text* the user committed to, and ``transcript.json`` for the *timing*
to splice in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
ANCHOR_RE = re.compile(r"//\s*([0-9]+(?:\.[0-9]+)?)\s*$")


@dataclass(frozen=True, slots=True, kw_only=True)
class LyricLine:
    """One line of lyric text, optionally with a known start time."""

    text: str
    line_index: int  # 0-based, contiguous across the whole song
    section_label: str = ""
    start_s: float | None = None  # the user's manual anchor, if any


@dataclass(frozen=True, slots=True, kw_only=True)
class LyricSection:
    """A user-tagged section in the lyrics markdown.

    Times are *optional* — if not present, alignment is computed from
    transcripts; if present, they override.
    """

    label: str
    title: str = ""  # free-form e.g. "verse 1"
    start_s: float | None = None
    end_s: float | None = None
    lines: tuple[LyricLine, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class LyricsDoc:
    """Full parsed view of the user's lyrics markdown."""

    sections: tuple[LyricSection, ...]

    @property
    def lines(self) -> tuple[LyricLine, ...]:
        out: list[LyricLine] = []
        for s in self.sections:
            out.extend(s.lines)
        return tuple(out)


# --- transcription --------------------------------------------------------


def transcribe(
    audio_path: str | Path,
    *,
    api_key: str | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run ElevenLabs Scribe on the audio and (optionally) cache the JSON.

    Returns the raw response dict (which contains ``words: [...]`` with
    per-word ``text``, ``start``, ``end``).
    """
    from mixing.transcript import transcribe as _transcribe

    response = _transcribe(audio_path, api_key=api_key)
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            json.dump(response, f, indent=2)
    return response


def words_from_transcript(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize Scribe's word entries: ``[{text, start, end}, ...]``.

    Filters out non-word events (Scribe surfaces ``(laughs)`` etc. with
    ``type`` ≠ ``word``) and ones missing timing.
    """
    out: list[dict[str, Any]] = []
    for w in transcript.get("words", ()):
        if w.get("type") and w.get("type") != "word":
            continue
        text = (w.get("text") or "").strip()
        start = w.get("start")
        end = w.get("end")
        if not text or start is None or end is None:
            continue
        out.append({"text": text, "start": float(start), "end": float(end)})
    return out


# --- markdown round-trip --------------------------------------------------


def parse_lyrics_md(md: str) -> LyricsDoc:
    """Parse the user-editable lyrics markdown.

    Format::

        [section_label] optional title
        line of lyric            // 12.5
        another line

        [next section]
        ...

    Empty lines are separators between sections. ``(instrumental)`` or
    any line starting with ``(`` and ending with ``)`` is treated as a
    non-lyric placeholder (no LyricLine emitted).
    """
    sections: list[LyricSection] = []
    current_label: str | None = None
    current_title = ""
    current_lines: list[LyricLine] = []
    line_index = 0

    def flush():
        nonlocal current_label, current_title, current_lines
        if current_label is None and not current_lines:
            return
        sections.append(
            LyricSection(
                label=current_label or "",
                title=current_title,
                lines=tuple(current_lines),
            )
        )
        current_label = None
        current_title = ""
        current_lines = []

    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            # Blank line — keep accumulating in the current section. We
            # only flush on a new section header.
            continue
        m = SECTION_RE.match(line)
        if m:
            flush()
            current_label = m.group(1).strip()
            current_title = m.group(2).strip()
            continue
        if line.startswith("(") and line.endswith(")"):
            # Non-lyric placeholder; ignored.
            continue
        anchor = ANCHOR_RE.search(line)
        start_s: float | None = None
        if anchor:
            start_s = float(anchor.group(1))
            line = ANCHOR_RE.sub("", line).strip()
        if line.startswith("#"):
            # Markdown headers other than [section] tags are ignored.
            continue
        current_lines.append(
            LyricLine(
                text=line,
                line_index=line_index,
                section_label=current_label or "",
                start_s=start_s,
            )
        )
        line_index += 1
    flush()
    return LyricsDoc(sections=tuple(sections))


def render_lyrics_md(doc: LyricsDoc) -> str:
    """Inverse of ``parse_lyrics_md``. Stable round-trip."""
    parts: list[str] = []
    for section in doc.sections:
        header = f"[{section.label}]"
        if section.title:
            header += f" {section.title}"
        parts.append(header)
        for line in section.lines:
            text = line.text
            if line.start_s is not None:
                text = f"{text}  // {line.start_s:.2f}"
            parts.append(text)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def lyrics_from_transcript(transcript: dict[str, Any]) -> LyricsDoc:
    """Build a default LyricsDoc from a transcription response.

    Heuristic: split lines on punctuation (``. ? !``) or on long pauses
    (>0.6 s gap between consecutive words). One ``[transcribed]`` section
    holds everything; the user is expected to re-tag with real sections.
    """
    words = words_from_transcript(transcript)
    if not words:
        return LyricsDoc(sections=())
    lines: list[list[dict[str, Any]]] = [[]]
    GAP = 0.6
    for i, w in enumerate(words):
        if i > 0 and w["start"] - words[i - 1]["end"] > GAP:
            if lines[-1]:
                lines.append([])
        lines[-1].append(w)
        if w["text"].endswith((".", "?", "!")):
            lines.append([])
    lines = [grp for grp in lines if grp]

    out_lines: list[LyricLine] = []
    for idx, grp in enumerate(lines):
        text = " ".join(w["text"] for w in grp).strip()
        start_s = float(grp[0]["start"])
        out_lines.append(
            LyricLine(
                text=text,
                line_index=idx,
                section_label="transcribed",
                start_s=start_s,
            )
        )
    return LyricsDoc(
        sections=(
            LyricSection(
                label="transcribed",
                title="auto",
                lines=tuple(out_lines),
            ),
        )
    )


# --- file helpers ---------------------------------------------------------


def write_lyrics_md(path: str | Path, doc: LyricsDoc) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_lyrics_md(doc))


def read_lyrics_md(path: str | Path) -> LyricsDoc:
    return parse_lyrics_md(Path(path).read_text())


def read_transcript(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())
