"""Lyric → audio alignment.

We have:
- a transcript (Scribe / faster-whisper) with word-level (text, start, end)
- a user-edited ``LyricsDoc`` with section labels + line text + optional
  manual line-start anchors

We want a ``lacing`` store with three tiers (sections, lines, words) so
the rest of the system can ask "which lines fall in shot X" without
re-implementing interval math.

Strategy (greedy token-match):

1. Tokenize each lyric line into normalized words.
2. Walk the transcript word stream once, assigning each transcript word
   to the next unmatched lyric word that matches (case- and
   punctuation-insensitive). Tolerate small mismatches (transcript
   word missing in lyrics, vice versa) with a small lookahead window.
3. From the matched words, derive line ``[start, end]`` as
   ``(first_matched_word.start, last_matched_word.end)``. If a line has
   *no* matched words, fall back to the user's manual anchor (if any),
   then to a linear interpolation between neighboring anchored lines.
4. Sections inherit ``[start, end]`` from the union of their lines;
   if the user provided explicit ``start_s`` / ``end_s`` on a section,
   those win.

The result is written as a ``lacing.SqliteStore`` so it round-trips and
can be edited by other tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from uuid import uuid4

from mtv.lyrics import LyricsDoc, words_from_transcript


_WORD_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _normalize(token: str) -> str:
    return token.lower().strip("'-")


def _tokenize(text: str) -> list[str]:
    """Cheap normalization: lowercase, strip punctuation."""
    return [_normalize(t) for t in _WORD_TOKEN_RE.findall(text.lower())]


@dataclass(frozen=True, slots=True, kw_only=True)
class WordAlignment:
    """One alignment between a lyric token and a transcript word."""

    line_index: int
    token_index: int  # within the line
    text: str
    start_s: float
    end_s: float
    confidence: float = 1.0


@dataclass(frozen=True, slots=True, kw_only=True)
class LineAlignment:
    line_index: int
    section_label: str
    text: str
    start_s: float | None
    end_s: float | None
    word_alignments: tuple[WordAlignment, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionAlignment:
    label: str
    title: str
    start_s: float | None
    end_s: float | None
    lines: tuple[LineAlignment, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AlignmentResult:
    sections: tuple[SectionAlignment, ...]

    @property
    def lines(self) -> tuple[LineAlignment, ...]:
        return tuple(L for s in self.sections for L in s.lines)

    @property
    def words(self) -> tuple[WordAlignment, ...]:
        return tuple(w for L in self.lines for w in L.word_alignments)

    def lines_in(self, start_s: float, end_s: float) -> list[LineAlignment]:
        """Lines that fall (at least partially) inside ``[start_s, end_s]``."""
        out: list[LineAlignment] = []
        for L in self.lines:
            if L.start_s is None or L.end_s is None:
                continue
            if L.end_s > start_s and L.start_s < end_s:
                out.append(L)
        return out

    def section_for(self, t: float) -> SectionAlignment | None:
        for s in self.sections:
            if s.start_s is None or s.end_s is None:
                continue
            if s.start_s <= t < s.end_s:
                return s
        return None


# --- core alignment -------------------------------------------------------


def align_lyrics(
    lyrics: LyricsDoc,
    transcript: dict,
    *,
    duration_s: float = 0.0,
    lookahead: int = 6,
) -> AlignmentResult:
    """Greedy token-match alignment.

    ``duration_s`` is used only when extrapolating end times for lines
    that have no matched words and no later anchor.
    """
    transcript_words = words_from_transcript(transcript)
    transcript_tokens = [_normalize(w["text"].strip("()[],.?!\"")) for w in transcript_words]

    # Build a single flat list of (line_index, token_index, normalized) for
    # every lyric token across all lines.
    flat_lyric_tokens: list[tuple[int, int, str]] = []
    for L in lyrics.lines:
        toks = _tokenize(L.text)
        for ti, tok in enumerate(toks):
            flat_lyric_tokens.append((L.line_index, ti, tok))

    # Greedy walk: for each lyric token, find the next matching transcript
    # word within ``lookahead`` of the current cursor.
    word_alignments_per_line: dict[int, list[WordAlignment]] = {}
    cursor = 0
    for line_idx, tok_idx, lyric_tok in flat_lyric_tokens:
        if not lyric_tok:
            continue
        match_at = -1
        for j in range(cursor, min(cursor + lookahead + 1, len(transcript_tokens))):
            if transcript_tokens[j] == lyric_tok:
                match_at = j
                break
        if match_at < 0:
            # tolerate a 1-character substitution (sung mishears)
            for j in range(cursor, min(cursor + lookahead + 1, len(transcript_tokens))):
                if _close_enough(transcript_tokens[j], lyric_tok):
                    match_at = j
                    break
        if match_at < 0:
            continue
        w = transcript_words[match_at]
        word_alignments_per_line.setdefault(line_idx, []).append(
            WordAlignment(
                line_index=line_idx,
                token_index=tok_idx,
                text=w["text"],
                start_s=float(w["start"]),
                end_s=float(w["end"]),
                confidence=0.9 if transcript_tokens[match_at] == lyric_tok else 0.6,
            )
        )
        cursor = match_at + 1

    # Now reduce to line / section alignments, falling back to anchors and
    # interpolation for empty lines.
    line_alignments: list[LineAlignment] = []
    for L in lyrics.lines:
        wal = tuple(word_alignments_per_line.get(L.line_index, ()))
        if wal:
            start = wal[0].start_s
            end = wal[-1].end_s
        else:
            start = L.start_s
            end = None
        line_alignments.append(
            LineAlignment(
                line_index=L.line_index,
                section_label=L.section_label,
                text=L.text,
                start_s=start,
                end_s=end,
                word_alignments=wal,
            )
        )

    line_alignments = _interpolate_line_times(line_alignments, duration_s)

    # Group back into sections.
    by_label: dict[str, list[LineAlignment]] = {}
    section_meta: dict[str, tuple[str, float | None, float | None]] = {}
    for s in lyrics.sections:
        section_meta[s.label] = (s.title, s.start_s, s.end_s)
    for la in line_alignments:
        by_label.setdefault(la.section_label, []).append(la)

    section_alignments: list[SectionAlignment] = []
    for s in lyrics.sections:
        lines_for = tuple(by_label.get(s.label, ()))
        starts = [L.start_s for L in lines_for if L.start_s is not None]
        ends = [L.end_s for L in lines_for if L.end_s is not None]
        s_start = s.start_s if s.start_s is not None else (min(starts) if starts else None)
        s_end = s.end_s if s.end_s is not None else (max(ends) if ends else None)
        section_alignments.append(
            SectionAlignment(
                label=s.label,
                title=s.title,
                start_s=s_start,
                end_s=s_end,
                lines=lines_for,
            )
        )
    return AlignmentResult(sections=tuple(section_alignments))


def _close_enough(a: str, b: str) -> bool:
    """Tolerate trivial sung-vs-said variants — same first/last char and
    differ by at most one internal char."""
    if not a or not b:
        return False
    if abs(len(a) - len(b)) > 1:
        return False
    if a[0] != b[0] or a[-1] != b[-1]:
        return False
    # Levenshtein ≤ 1, length ≥ 3
    if len(a) < 3 or len(b) < 3:
        return False
    return _lev1(a, b)


def _lev1(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) == len(b):
        diffs = sum(1 for x, y in zip(a, b) if x != y)
        return diffs <= 1
    # one-char insert/delete
    short, long = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(long)):
        if long[:i] + long[i + 1:] == short:
            return True
    return False


def _interpolate_line_times(
    lines: list[LineAlignment], duration_s: float
) -> list[LineAlignment]:
    """Fill missing line times by linear interpolation between anchors.

    Lines that still have no end after this are given a tiny
    ``end = start + 0.5s`` placeholder; lines with no start at all are
    left as-is.
    """
    if not lines:
        return lines
    starts = [L.start_s for L in lines]
    # Forward-fill ends within each line: if a line has start but no end,
    # use the next line's start (or duration_s).
    out: list[LineAlignment] = []
    for i, L in enumerate(lines):
        start = L.start_s
        end = L.end_s
        if start is None:
            # Look back for a previous start; if none, default to 0.0.
            for j in range(i - 1, -1, -1):
                if lines[j].end_s is not None:
                    start = lines[j].end_s
                    break
                if lines[j].start_s is not None:
                    start = lines[j].start_s
                    break
            if start is None:
                start = 0.0
        if end is None:
            for j in range(i + 1, len(lines)):
                if lines[j].start_s is not None:
                    end = lines[j].start_s
                    break
            if end is None:
                end = duration_s if duration_s and duration_s > start else start + 0.5
        out.append(
            LineAlignment(
                line_index=L.line_index,
                section_label=L.section_label,
                text=L.text,
                start_s=start,
                end_s=end,
                word_alignments=L.word_alignments,
            )
        )
    return out


# --- lacing serialization -------------------------------------------------


def write_alignment_store(
    alignment: AlignmentResult,
    *,
    path: str | Path,
    asset_id: str = "song:audio",
    rate: int = 1000,
) -> None:
    """Write alignment to a ``lacing.SqliteStore`` file (.annot).

    Three tiers: ``sections``, ``lines``, ``words``. Body schemas use
    custom URIs registered locally; we don't enforce them here (the
    store still validates structure).
    """
    from lacing import (
        Annotation,
        MediaRef,
        Provenance,
        RationalTime,
        SqliteStore,
        Tier,
        TimeInterval,
    )

    path = Path(path)
    if path.exists():
        path.unlink()
    store = SqliteStore(str(path))
    try:
        store.add_tier(Tier(name="sections"))
        store.add_tier(Tier(name="lines"))
        store.add_tier(Tier(name="words"))

        prov = Provenance(
            was_generated_by="mtv:align",
            was_attributed_to="mtv",
            generated_at_time=RationalTime.zero(rate),
        )

        def interval(start_s: float, end_s: float) -> TimeInterval:
            if end_s <= start_s:
                end_s = start_s + 1.0 / rate
            # Round to the integer rate-tick so we never trip lacing's
            # strict "lossy conversion" guard for decimals like 14.2 at
            # rate=1000 (14.2 → 14199.999... ticks).
            start_t = int(round(start_s * rate))
            end_t = int(round(end_s * rate))
            if end_t <= start_t:
                end_t = start_t + 1
            return TimeInterval(
                RationalTime(start_t, rate),
                RationalTime(end_t, rate),
            )

        for s in alignment.sections:
            if s.start_s is None or s.end_s is None:
                continue
            store.add(
                Annotation(
                    id=uuid4(),
                    tier="sections",
                    reference=MediaRef(
                        asset_id=asset_id,
                        interval=interval(s.start_s, s.end_s),
                    ),
                    body={"label": s.label, "title": s.title},
                    body_schema_uri="annot://schema/song-section/v1",
                    provenance=prov,
                )
            )
        for L in alignment.lines:
            if L.start_s is None or L.end_s is None:
                continue
            store.add(
                Annotation(
                    id=uuid4(),
                    tier="lines",
                    reference=MediaRef(
                        asset_id=asset_id,
                        interval=interval(L.start_s, L.end_s),
                    ),
                    body={
                        "text": L.text,
                        "line_index": L.line_index,
                        "section": L.section_label,
                    },
                    body_schema_uri="annot://schema/lyric-line/v1",
                    provenance=prov,
                )
            )
        for w in alignment.words:
            store.add(
                Annotation(
                    id=uuid4(),
                    tier="words",
                    reference=MediaRef(
                        asset_id=asset_id,
                        interval=interval(w.start_s, w.end_s),
                    ),
                    body={
                        "text": w.text,
                        "line_index": w.line_index,
                        "confidence": w.confidence,
                    },
                    body_schema_uri="annot://schema/word/v1",
                    provenance=prov,
                )
            )
    finally:
        store.close()
