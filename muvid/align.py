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

from muvid.lyrics import LyricsDoc, words_from_transcript


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


# --- aligner registry -----------------------------------------------------

#: Built-in aligner names. ``align_lyrics(aligner=...)`` accepts these
#: plus any name added later via :func:`register_aligner`.
AlignerName = str  # kept loose so user-registered names work too

_ALIGNERS: dict[str, "AlignerSpec"] = {}


@dataclass(frozen=True, slots=True, kw_only=True)
class AlignerSpec:
    """One row in the aligner registry."""

    name: str
    description: str
    fn: "object"  # Callable[..., AlignmentResult]
    requires: tuple[str, ...] = ()


def register_aligner(
    name: str,
    fn,
    *,
    description: str,
    requires: tuple[str, ...] = (),
) -> None:
    """Register an aligner under ``name``.

    The function should accept ``(lyrics, transcript, *, duration_s,
    **kw) -> AlignmentResult``. Extra keyword arguments forwarded by
    :func:`align_lyrics` are passed through.
    """
    _ALIGNERS[name] = AlignerSpec(
        name=name, description=description, fn=fn, requires=tuple(requires)
    )


def list_aligners() -> list[str]:
    """Return all registered aligner names, sorted."""
    return sorted(_ALIGNERS)


def aligner_info(name: str) -> AlignerSpec:
    if name not in _ALIGNERS:
        raise KeyError(
            f"Unknown aligner {name!r}. Registered: {list_aligners()}"
        )
    return _ALIGNERS[name]


# --- public dispatch ------------------------------------------------------


def align_lyrics(
    lyrics: LyricsDoc,
    transcript: dict,
    *,
    duration_s: float = 0.0,
    aligner: str = "scribe-greedy",
    **aligner_kwargs,
) -> AlignmentResult:
    """Align a ``LyricsDoc`` to a transcript.

    Args:
        lyrics: User-edited lyrics document (the ground-truth text).
        transcript: Aligner-specific input. For ``"scribe-greedy"``
            this is a Scribe / faster-whisper response with
            ``words: [...]``. For ``"user"`` this can be empty if you
            pass ``user_line_timings=...``. For ``"whisperx-lite"`` the
            transcript is ignored — the aligner runs on the audio
            directly (path passed via ``audio_path=``).
        duration_s: Used to extrapolate end times for lines with no
            matched words and no later anchor.
        aligner: Name of a registered aligner. See :func:`list_aligners`.
        **aligner_kwargs: Forwarded to the aligner.

    Returns:
        :class:`AlignmentResult`.
    """
    spec = aligner_info(aligner)
    for pkg in spec.requires:
        try:
            __import__(pkg)
        except ImportError as e:
            raise RuntimeError(
                f"aligner {aligner!r} requires {pkg!r}; install it first "
                f"(pip install {pkg})"
            ) from e
    return spec.fn(lyrics, transcript, duration_s=duration_s, **aligner_kwargs)


# --- aligner: scribe-greedy ----------------------------------------------


def align_scribe_greedy(
    lyrics: LyricsDoc,
    transcript: dict,
    *,
    duration_s: float = 0.0,
    lookahead: int = 6,
) -> AlignmentResult:
    """Greedy token-match against a word-timestamped transcript.

    Cheap, network-only (assumes the transcript came from Scribe or
    similar). Tolerates 1-character mishears between sung and written
    text. ``duration_s`` is used only when extrapolating end times.
    """
    transcript_words = words_from_transcript(transcript)
    transcript_tokens = [
        _normalize(w["text"].strip('()[],.?!"')) for w in transcript_words
    ]

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
        s_start = (
            s.start_s if s.start_s is not None else (min(starts) if starts else None)
        )
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
        if long[:i] + long[i + 1 :] == short:
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


# --- aligner: user-provided -----------------------------------------------


def align_user_provided(
    lyrics: LyricsDoc,
    transcript: dict,
    *,
    duration_s: float = 0.0,
    user_line_timings: Optional[list[dict]] = None,
) -> AlignmentResult:
    """Use line-level timings the caller has already determined.

    Useful when the user has hand-anchored every line, or when an
    external aligner has produced ``line_index → (start, end)``
    timings and you don't want any token-matching.

    ``user_line_timings`` is a list of ``{"line_index": int,
    "start_s": float, "end_s": float}``. If omitted, this aligner
    falls back to the manual anchors already on ``lyrics`` (the
    ``// 12.5`` end-of-line markers in ``lyrics.md``).
    """
    user_line_timings = user_line_timings or []
    timings_by_line = {int(t["line_index"]): t for t in user_line_timings}

    line_alignments: list[LineAlignment] = []
    for L in lyrics.lines:
        t = timings_by_line.get(L.line_index)
        if t is not None:
            start = float(t["start_s"])
            end = float(t["end_s"])
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
                word_alignments=(),
            )
        )
    line_alignments = _interpolate_line_times(line_alignments, duration_s)

    # Re-group into sections (mirror of scribe-greedy logic).
    by_label: dict[str, list[LineAlignment]] = {}
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


# --- aligner: whisperx-lite (offline via faster-whisper) ------------------


def align_whisperx_lite(
    lyrics: LyricsDoc,
    transcript: dict,
    *,
    duration_s: float = 0.0,
    audio_path: Optional[str | Path] = None,
    model_size: str = "tiny",
    lookahead: int = 6,
) -> AlignmentResult:
    """Local-only aligner that re-uses ``faster-whisper`` (no API).

    The ``transcript`` argument is ignored when ``audio_path`` is given:
    we re-transcribe locally with faster-whisper and then run the same
    greedy match used by ``scribe-greedy``. When ``audio_path`` is not
    given, we fall through and just use the supplied ``transcript``.

    Trade-offs vs ``scribe-greedy``: free + offline; slower; less
    accurate on singing; needs torch + faster-whisper installed.
    """
    if audio_path is None:
        return align_scribe_greedy(
            lyrics, transcript, duration_s=duration_s, lookahead=lookahead
        )

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:  # pragma: no cover — exercised only when installed
        raise RuntimeError(
            "whisperx-lite requires faster-whisper. "
            "pip install faster-whisper"
        ) from e

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), word_timestamps=True)
    words = []
    for seg in segments:
        for w in seg.words or ():
            words.append(
                {
                    "text": w.word.strip(),
                    "start": float(w.start),
                    "end": float(w.end),
                    "type": "word",
                    "confidence": float(getattr(w, "probability", 1.0) or 1.0),
                }
            )
    transcript = {"words": words}
    return align_scribe_greedy(
        lyrics, transcript, duration_s=duration_s, lookahead=lookahead
    )


# --- aligner: stars (singing-grade — stub) --------------------------------


def align_stars(
    lyrics: LyricsDoc,
    transcript: dict,
    *,
    duration_s: float = 0.0,
    **kwargs,
) -> AlignmentResult:  # pragma: no cover — not implemented
    """Singing-specific alignment (STARS / similar). Not yet implemented.

    See ``misc/docs/alignment_references.md`` for the literature
    motivating this aligner. The slot exists so callers can already
    write ``aligner="stars"`` and get a clear NotImplementedError.
    """
    raise NotImplementedError(
        "stars-style joint singing alignment is not implemented yet. "
        "Use aligner='scribe-greedy' (default) or 'whisperx-lite'."
    )


# Register built-in aligners.
register_aligner(
    "scribe-greedy",
    align_scribe_greedy,
    description="Greedy token-match against a Scribe / faster-whisper transcript.",
)
register_aligner(
    "user",
    align_user_provided,
    description="Caller-provided line-level timings (line_index → start, end).",
)
register_aligner(
    "whisperx-lite",
    align_whisperx_lite,
    description="Offline: faster-whisper transcribe + greedy match.",
    requires=("faster_whisper",),
)
register_aligner(
    "stars",
    align_stars,
    description="Singing-grade joint alignment (not yet implemented).",
)


# --- lacing serialization -------------------------------------------------


def write_alignment_store(
    alignment: AlignmentResult,
    *,
    path: str | Path,
    asset_id: str = "song:audio",
    rate: int = 1000,
) -> None:
    """Write alignment to a ``lacing.SqliteStore`` file (.annot).

    Uses :class:`lacing.tracks.subtitle.SubtitleBuilder` for the
    standard ``(sections, lines, words)`` tier set.
    """
    from lacing import SqliteStore
    from lacing.tracks.subtitle import SubtitleBuilder

    path = Path(path)
    if path.exists():
        path.unlink()
    store = SqliteStore(str(path))
    try:
        b = SubtitleBuilder(
            store,
            asset_id=asset_id,
            rate=rate,
            was_generated_by="muvid:align",
            was_attributed_to="muvid",
        )
        for s in alignment.sections:
            if s.start_s is None or s.end_s is None:
                continue
            b.section(s.label, s.start_s, s.end_s, title=s.title)
        for L in alignment.lines:
            if L.start_s is None or L.end_s is None:
                continue
            b.line(
                L.text,
                L.start_s, L.end_s,
                line_index=L.line_index,
                section=L.section_label,
            )
        for w in alignment.words:
            b.word(
                w.text, w.start_s, w.end_s,
                line_index=w.line_index, confidence=w.confidence,
            )
    finally:
        store.close()
