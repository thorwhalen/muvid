"""The timed text tree — song → sections → lines → words, with measured times.

Everything downstream reads this and nothing else, which is what lets the
subgenre accept four quite different kinds of input without the renderers
caring which one arrived:

============================  ==================================================
input the caller has          how it gets here
============================  ==================================================
a muvid project               :func:`from_alignment_store` — muvid already owns
                              the word-timing SSOT (``muvid.align`` writes a
                              three-tier lacing store); we read it, never
                              re-transcribe.
an ``.srt`` / ``.lrc`` file   :func:`from_subtitles` — line-level times, words
                              spread inside a line.
lyrics text + audio           :func:`from_lyrics_and_audio` — runs muvid's
                              registered aligner.
audio only                    the aligner's transcription path, same function.
============================  ==================================================

Line-level input is not word-level input, and pretending otherwise is how lyric
videos end up subtly out of sync. When words are interpolated inside a line
rather than measured, :attr:`Word.measured` is ``False`` — renderers and the
quantiser can then prefer ``line`` quantisation, and a caller can be told the
timing is approximate instead of discovering it in the render.

Import-safe: stdlib only at module scope. The muvid alignment machinery and any
ASR are imported inside the functions that need them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

__all__ = [
    "Word",
    "Line",
    "Section",
    "TimedText",
    "from_alignment_store",
    "from_subtitles",
    "from_lyrics_and_audio",
    "from_words",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class Word:
    """One sung word on the song timeline. Times are seconds, absolute."""

    text: str
    start: float
    end: float
    #: False when the time was interpolated inside a line rather than measured.
    measured: bool = True

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """One sung line."""

    words: tuple[Word, ...]
    index: int = 0
    text: str = ""

    def __post_init__(self) -> None:
        if not self.text:
            object.__setattr__(self, "text", " ".join(w.text for w in self.words))

    @property
    def start(self) -> float:
        return min((w.start for w in self.words), default=0.0)

    @property
    def end(self) -> float:
        return max((w.end for w in self.words), default=0.0)

    @property
    def measured(self) -> bool:
        return all(w.measured for w in self.words)


@dataclass(frozen=True, slots=True, kw_only=True)
class Section:
    """A labelled span — ``verse``, ``chorus``, whatever the lyrics document says."""

    label: str
    lines: tuple[Line, ...]

    @property
    def start(self) -> float:
        return min((l.start for l in self.lines), default=0.0)

    @property
    def end(self) -> float:
        return max((l.end for l in self.lines), default=0.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class TimedText:
    """The whole song's text, timed.

    >>> tt = from_words([('hello', 0.0, 0.5), ('world', 0.5, 1.0)], duration=1.0)
    >>> [w.text for w in tt.words()]
    ['hello', 'world']
    >>> tt.sections[0].label
    '*'
    """

    sections: tuple[Section, ...]
    duration: float = 0.0
    #: Where the timing came from, for provenance and for honest reporting.
    source: str = "unknown"

    def words(self) -> Iterator[Word]:
        for s in self.sections:
            for l in s.lines:
                yield from l.words

    def lines(self) -> Iterator[Line]:
        for s in self.sections:
            yield from s.lines

    @property
    def measured(self) -> bool:
        """True when every word time was measured rather than interpolated."""
        return all(w.measured for w in self.words())

    def section_for(self, t: float) -> Section | None:
        for s in self.sections:
            if s.start <= t <= s.end:
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": self.duration,
            "source": self.source,
            "measured": self.measured,
            "sections": [
                {
                    "label": s.label,
                    "lines": [
                        {
                            "index": l.index,
                            "text": l.text,
                            "words": [
                                {"text": w.text, "start": w.start, "end": w.end,
                                 "measured": w.measured}
                                for w in l.words
                            ],
                        }
                        for l in s.lines
                    ],
                }
                for s in self.sections
            ],
        }


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def from_words(
    words: Iterable[tuple[str, float, float]],
    *,
    duration: float = 0.0,
    line_gap_s: float = 0.9,
    source: str = "words",
) -> TimedText:
    """Build from a flat ``(text, start, end)`` stream, splitting lines on gaps.

    The gap heuristic is deliberately simple and deliberately visible: a real
    lyrics document is always better, and when one exists the other builders
    use it instead of guessing.
    """
    ws = [Word(text=t, start=float(a), end=float(b)) for t, a, b in words if t.strip()]
    if not ws:
        return TimedText(sections=(), duration=duration, source=source)
    lines: list[Line] = []
    current: list[Word] = [ws[0]]
    for prev, w in zip(ws, ws[1:]):
        if w.start - prev.end >= line_gap_s:
            lines.append(Line(words=tuple(current), index=len(lines)))
            current = []
        current.append(w)
    lines.append(Line(words=tuple(current), index=len(lines)))
    return TimedText(
        sections=(Section(label="*", lines=tuple(lines)),),
        duration=duration or ws[-1].end,
        source=source,
    )


def _spread_words_over(text: str, start: float, end: float) -> tuple[Word, ...]:
    """Distribute a line's words across its span, weighted by word length.

    Weighting by length is a small thing that reads much better than an even
    split: "I" and "extraordinary" do not take the same time to sing.
    """
    tokens = [t for t in re.split(r"\s+", text.strip()) if t]
    if not tokens:
        return ()
    weights = [max(1, len(t)) for t in tokens]
    total = sum(weights)
    span = max(1e-6, end - start)
    out: list[Word] = []
    cursor = start
    for tok, w in zip(tokens, weights):
        d = span * (w / total)
        out.append(Word(text=tok, start=cursor, end=cursor + d, measured=False))
        cursor += d
    return tuple(out)


_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
_LRC_LINE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]\s*(.*)")
#: Enhanced LRC puts per-word stamps inline: ``[00:12.00]<00:12.00>Some <00:12.5>words``
_LRC_WORD = re.compile(r"<(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?>\s*([^<]*)")


def _hms(h: str, m: str, s: str, frac: str | None) -> float:
    ms = int((frac or "0").ljust(3, "0")[:3])
    return int(h) * 3600 + int(m) * 60 + int(s) + ms / 1000.0


def from_subtitles(path: Path | str, *, duration: float = 0.0) -> TimedText:
    """Read ``.srt``, ``.lrc`` or enhanced ``.lrc`` into a timed tree.

    Enhanced LRC carries real per-word stamps and is used as such; plain LRC and
    SRT give line times only, so their words are spread and marked
    ``measured=False``.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()

    lines: list[Line] = []
    if suffix == ".srt" or "-->" in text:
        blocks = re.split(r"\n\s*\n", text.strip())
        for block in blocks:
            m = _SRT_TIME.search(block)
            if not m:
                continue
            start = _hms(*m.group(1, 2, 3, 4))
            end = _hms(*m.group(5, 6, 7, 8))
            body = "\n".join(
                l for l in block.splitlines()
                if not _SRT_TIME.search(l) and not l.strip().isdigit()
            ).strip()
            body = re.sub(r"<[^>]+>", "", body)
            if body:
                lines.append(
                    Line(words=_spread_words_over(body.replace("\n", " "), start, end),
                         index=len(lines))
                )
        source = "srt"
    else:
        stamped: list[tuple[float, str]] = []
        for raw in text.splitlines():
            m = _LRC_LINE.match(raw.strip())
            if not m:
                continue
            stamped.append((_hms("0", m.group(1), m.group(2), m.group(3)), m.group(4)))
        source = "lrc"
        for i, (start, body) in enumerate(stamped):
            end = stamped[i + 1][0] if i + 1 < len(stamped) else (duration or start + 3.0)
            word_stamps = list(_LRC_WORD.finditer(body))
            if word_stamps:
                source = "lrc-enhanced"
                ws: list[Word] = []
                for j, wm in enumerate(word_stamps):
                    w_start = _hms("0", wm.group(1), wm.group(2), wm.group(3))
                    w_end = (
                        _hms("0", *word_stamps[j + 1].group(1, 2, 3))
                        if j + 1 < len(word_stamps)
                        else end
                    )
                    for tok in wm.group(4).split():
                        ws.append(Word(text=tok, start=w_start, end=w_end))
                if ws:
                    lines.append(Line(words=tuple(ws), index=len(lines)))
            else:
                body = re.sub(r"<[^>]+>", "", body).strip()
                if body:
                    lines.append(
                        Line(words=_spread_words_over(body, start, end), index=len(lines))
                    )

    if not lines:
        return TimedText(sections=(), duration=duration, source=source)
    return TimedText(
        sections=(Section(label="*", lines=tuple(lines)),),
        duration=duration or max(l.end for l in lines),
        source=source,
    )


def from_alignment_store(project_root: Path | str, *, duration: float = 0.0) -> TimedText:
    """Read muvid's own three-tier alignment (sections / lines / words).

    muvid already declares itself the word-timing SSOT — ``muvid.align`` writes
    a ``lacing`` store and ``muvid.contracts`` reads it — so this subgenre reads
    that store rather than growing a second transcription path.
    """
    from muvid.contracts import word_timings_for_window
    from muvid.project import MusicVideoProject

    project = MusicVideoProject(Path(project_root))
    song = getattr(project, "song_path", None)
    if not duration and song and Path(song).exists():
        from muvid.visualize.ffmpeg import media_duration

        duration = media_duration(song)
    timings = list(word_timings_for_window(project, 0.0, duration or 1e9))
    return from_words(timings, duration=duration, source="muvid-alignment")


def from_lyrics_and_audio(
    audio: Path | str,
    *,
    lyrics: Path | str | None = None,
    aligner: str | None = None,
    duration: float = 0.0,
) -> TimedText:
    """Align ``lyrics`` to ``audio`` using muvid's registered aligner.

    ``aligner=None`` uses muvid's default. This deliberately goes through
    ``muvid.align``'s registry rather than calling an ASR directly, so a better
    singing-grade aligner registered later is picked up here for free.
    """
    from muvid.visualize.ffmpeg import media_duration

    audio = Path(audio)
    duration = duration or media_duration(audio)

    from muvid import align as align_mod

    resolve = getattr(align_mod, "resolve_aligner", None)
    if resolve is None:  # pragma: no cover - depends on muvid.align's shape
        raise RuntimeError(
            "muvid.align exposes no aligner registry on this version; pass a "
            "subtitle file instead, or upgrade muvid."
        )
    fn = resolve(aligner) if aligner else resolve(getattr(align_mod, "DEFAULT_ALIGNER", None))
    words = fn(audio=audio, lyrics=Path(lyrics) if lyrics else None)
    return from_words(words, duration=duration, source=f"aligner:{aligner or 'default'}")
