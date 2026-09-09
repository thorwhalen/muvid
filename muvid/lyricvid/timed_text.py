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
    "from_alignment_result",
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
        """True when every word time was measured rather than interpolated.

        False for an empty text: "all of nothing was measured" is the kind of
        vacuous truth that reads as reassurance in a report.
        """
        ws = list(self.words())
        return bool(ws) and all(w.measured for w in ws)

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
                                {
                                    "text": w.text,
                                    "start": w.start,
                                    "end": w.end,
                                    "measured": w.measured,
                                }
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
    # utf-8-sig: a byte-order mark otherwise survives into the first cue's
    # index line, which then fails the isdigit() filter and leaks "﻿1"
    # into the rendered text of the first line.
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    suffix = path.suffix.lower()

    lines: list[Line] = []
    # The extension decides when it can; the sniff is only for an unknown one.
    # (An LRC whose lyric contains "-->" used to be parsed as SRT — and came back
    # empty.)
    is_srt = suffix == ".srt" or (suffix != ".lrc" and bool(_SRT_TIME.search(text)))
    if is_srt:
        blocks = re.split(r"\n\s*\n", text.strip())
        for block in blocks:
            m = _SRT_TIME.search(block)
            if not m:
                continue
            start = _hms(*m.group(1, 2, 3, 4))
            end = _hms(*m.group(5, 6, 7, 8))
            body = "\n".join(
                l
                for l in block.splitlines()
                if not _SRT_TIME.search(l) and not l.strip().isdigit()
            ).strip()
            body = re.sub(r"<[^>]+>", "", body)
            if body:
                lines.append(
                    Line(
                        words=_spread_words_over(body.replace("\n", " "), start, end),
                        index=len(lines),
                    )
                )
        source = "srt"
    else:
        stamped: list[tuple[float, str]] = []
        for raw in text.splitlines():
            line = raw.strip()
            # A line may carry SEVERAL leading stamps ("[00:01.00][00:05.00]repeat
            # me" — the same words sung twice). Peel them all; each is a line.
            times: list[float] = []
            while True:
                m = _LRC_LINE.match(line)
                if not m:
                    break
                times.append(_hms("0", m.group(1), m.group(2), m.group(3)))
                line = m.group(4)
                if not _LRC_LINE.match(line):
                    break
            for t in times:
                stamped.append((t, line))
        stamped.sort(key=lambda p: p[0])
        source = "lrc"
        for i, (start, body) in enumerate(stamped):
            end = (
                stamped[i + 1][0] if i + 1 < len(stamped) else (duration or start + 3.0)
            )
            word_stamps = list(_LRC_WORD.finditer(body))
            if word_stamps:
                source = "lrc-enhanced"
                ws: list[Word] = []
                # text BEFORE the first <stamp> belongs to the line's own time
                lead_text = body[: word_stamps[0].start()].strip()
                if lead_text:
                    first_word_t = _hms("0", *word_stamps[0].group(1, 2, 3))
                    ws.extend(_spread_words_over(lead_text, start, first_word_t))
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
                        Line(
                            words=_spread_words_over(body, start, end), index=len(lines)
                        )
                    )

    if not lines:
        return TimedText(sections=(), duration=duration, source=source)
    return TimedText(
        sections=(Section(label="*", lines=tuple(lines)),),
        duration=duration or max(l.end for l in lines),
        source=source,
    )


def from_alignment_store(
    project_root: Path | str, *, duration: float = 0.0
) -> TimedText:
    """Read muvid's own three-tier alignment (sections / lines / words).

    muvid already declares itself the word-timing SSOT — ``muvid.align`` writes
    a ``lacing`` store and ``muvid.contracts`` reads it — so this subgenre reads
    that store rather than growing a second transcription path.
    """
    from muvid.contracts import word_timings_for_window
    from muvid.project import MusicVideoProject

    project = MusicVideoProject(Path(project_root))
    if not duration:
        # song_path() is a METHOD that raises when no song is registered; the
        # first cut read it as a property and handed a bound method to Path().
        try:
            song = project.song_path()
        except RuntimeError:
            song = None
        if song is not None and Path(song).exists():
            from muvid.visualize.ffmpeg import media_duration

            duration = media_duration(song)
    timings = list(word_timings_for_window(project, 0.0, duration or 1e9))
    return from_words(timings, duration=duration, source="muvid-alignment")


#: The same token boundaries ``muvid.align._tokenize`` uses (it lowercases, then
#: matches this), so a ``WordAlignment.token_index`` addresses the same token
#: here. Kept in sync by the doctest below rather than by importing a private.
_LYRIC_TOKEN_RE = re.compile(r"(?:[^\W_]|['’])+")


def _lyric_tokens(text: str) -> list[str]:
    """The line's tokens, in the lyric's OWN case, at ``muvid.align``'s boundaries.

    >>> _lyric_tokens("Don't stop, Me now!")
    ["Don't", 'stop', 'Me', 'now']

    Any script, not just ASCII — an accent is a letter, never a word boundary:

    >>> _lyric_tokens("cabrés même ô")
    ['cabrés', 'même', 'ô']
    """
    lowered = text.lower()
    if len(lowered) != len(text):  # a case-fold that changed length; be safe
        return _LYRIC_TOKEN_RE.findall(lowered)
    return [text[m.start() : m.end()] for m in _LYRIC_TOKEN_RE.finditer(lowered)]


def _line_words_from_alignment(ln) -> tuple[Word, ...]:
    """One line's words: the LYRIC's text, with the aligner's times where it has them.

    ``WordAlignment.text`` is the transcript's word, not the lyric's — an ASR
    that heard "Apple" for a lyric that says "apple" would otherwise change
    the rendered text. The lyric document is what the user committed to, so
    its tokens win and the alignment contributes only the timing.

    Tokens the aligner did not match are interpolated between the nearest
    measured neighbours (or the line's own span) and marked ``measured=False``
    *individually*, so a line that is half-matched reports half-measured
    rather than all-or-nothing.
    """
    tokens = _lyric_tokens(ln.text or "")
    if not tokens:
        return ()
    timed: dict[int, tuple[float, float]] = {}
    for wa in ln.word_alignments or ():
        i = int(wa.token_index)
        if 0 <= i < len(tokens) and wa.start_s is not None and wa.end_s is not None:
            timed[i] = (float(wa.start_s), float(wa.end_s))

    line_start = (
        ln.start_s
        if ln.start_s is not None
        else (min(s for s, _ in timed.values()) if timed else None)
    )
    line_end = (
        ln.end_s
        if ln.end_s is not None
        else (max(e for _, e in timed.values()) if timed else None)
    )
    if line_start is None or line_end is None:
        return ()  # nothing measured and nothing to interpolate from

    out: list[Word] = []
    i = 0
    n = len(tokens)
    while i < n:
        if i in timed:
            s, e = timed[i]
            out.append(Word(text=tokens[i], start=s, end=e, measured=True))
            i += 1
            continue
        # an unmeasured run: spread it between the neighbouring measured bounds
        j = i
        while j < n and j not in timed:
            j += 1
        lo = timed[i - 1][1] if i > 0 and (i - 1) in timed else float(line_start)
        hi = timed[j][0] if j < n else float(line_end)
        if hi <= lo:
            hi = lo + 1e-3 * (j - i)
        out.extend(_spread_words_over(" ".join(tokens[i:j]), lo, hi))
        i = j
    return tuple(out)


def from_alignment_result(
    result, *, duration: float = 0.0, source: str = "muvid-align"
) -> TimedText:
    """Convert a :class:`muvid.align.AlignmentResult` into a timed tree.

    Keeps the sections and lines the aligner found — which is strictly better
    than flattening to words and re-splitting on gaps, because the lyrics
    document already *knows* where the lines are.

    A line whose words the aligner could not match still has a ``[start, end]``
    (interpolated by the aligner from its neighbours); its words are spread
    across that span and marked ``measured=False``, so downstream can see the
    difference between a measured onset and a guessed one.

    >>> from types import SimpleNamespace as NS
    >>> w = NS(text='hi', start_s=0.0, end_s=0.4, token_index=0)
    >>> ln = NS(line_index=0, text='hi there', start_s=0.0, end_s=1.0,
    ...         word_alignments=(w,))
    >>> sec = NS(label='verse', lines=(ln,))
    >>> tt = from_alignment_result(NS(sections=(sec,)), duration=1.0)
    >>> [(x.text, x.measured) for x in tt.words()]
    [('hi', True), ('there', False)]
    >>> tt.sections[0].label
    'verse'
    """
    sections: list[Section] = []
    for sec in result.sections:
        lines: list[Line] = []
        for ln in sec.lines:
            words = _line_words_from_alignment(ln)
            if words:
                lines.append(Line(words=words, index=int(ln.line_index), text=ln.text))
        if lines:
            sections.append(Section(label=sec.label or "*", lines=tuple(lines)))
    dur = duration or max((l.end for s in sections for l in s.lines), default=0.0)
    return TimedText(sections=tuple(sections), duration=dur, source=source)


def _has(module: str) -> bool:
    from importlib.util import find_spec

    return find_spec(module) is not None


def _transcribe_words_offline(audio: Path) -> list[tuple[str, float, float]]:
    """Word timings straight from ``faster-whisper``, for the no-lyrics case."""
    from faster_whisper import WhisperModel

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        str(audio), word_timestamps=True, condition_on_previous_text=False
    )
    out: list[tuple[str, float, float]] = []
    for seg in segments:
        for w in seg.words or ():
            text = w.word.strip()
            if text:
                out.append((text, float(w.start), float(w.end)))
    return out


def from_lyrics_and_audio(
    audio: Path | str,
    *,
    lyrics: Path | str | None = None,
    aligner: str | None = None,
    duration: float = 0.0,
) -> TimedText:
    """Align ``lyrics`` to ``audio`` through muvid's aligner registry.

    Goes through :func:`muvid.align.align_lyrics` rather than calling an ASR
    directly, so a singing-grade aligner registered later is picked up here
    for free. The aligner is chosen honestly by what is installed:

    * ``whisperx-lite`` — offline, free, needs ``faster-whisper``. **Default
      when available.** Runs on the audio itself.
    * ``scribe-greedy`` — needs a Scribe transcript, which costs money and an
      ElevenLabs key; only reached when asked for by name, never as a silent
      fallback that spends.

    With no ``lyrics`` at all there is nothing to align *to*; the transcript's
    own words become the text (``source='transcript'``).

    ``aligner`` may be any registered name; unknown names raise from
    ``muvid.align`` with the registered list.
    """
    from muvid.visualize.ffmpeg import media_duration

    audio = Path(audio)
    if not audio.exists():
        raise FileNotFoundError(f"audio not found: {audio}")
    duration = duration or media_duration(audio)

    if lyrics is None:
        if not _has("faster_whisper"):
            raise RuntimeError(
                "No lyrics were given, so the words must be transcribed — but "
                "faster-whisper is not installed. Either `pip install "
                "faster-whisper` (offline, free), or pass --lyrics / --subtitles."
            )
        return from_words(
            _transcribe_words_offline(audio), duration=duration, source="transcript"
        )

    from muvid import align as align_mod
    from muvid.lyrics import parse_lyrics_md

    doc = parse_lyrics_md(Path(lyrics).read_text(encoding="utf-8"))
    if not doc.lines:
        raise ValueError(f"no lyric lines found in {lyrics}")

    name = aligner
    kwargs: dict = {}
    if name is None:
        if _has("faster_whisper"):
            name = "whisperx-lite"
        else:
            raise RuntimeError(
                "No offline aligner is available: install faster-whisper "
                "(`pip install faster-whisper`), pass --subtitles with timed "
                "lines, or choose an aligner by name (see muvid.align.list_aligners)."
            )
    transcript: dict = {}
    if name == "whisperx-lite":
        kwargs["audio_path"] = str(audio)
    elif name == "scribe-greedy":
        from muvid.lyrics import transcribe  # ElevenLabs Scribe: paid, keyed

        transcript = transcribe(audio)
    result = align_mod.align_lyrics(
        doc, transcript, duration_s=duration, aligner=name, **kwargs
    )
    return from_alignment_result(result, duration=duration, source=f"aligner:{name}")
