"""Caption tracks: one subtitle file per language, timed per sung line (muvid#145).

A lyric video for learners wants toggleable caption tracks on YouTube — a
translation, a transliteration (Hepburn romaji for kana) — timed from the same
:class:`~muvid.lyricvid.timed_text.TimedText` that drives the picture, so the
words on screen and the caption under them change together.

Each language is a **transform** of a line's text:

- a mapping ``{line text: caption}`` — a hand-written translation. A sung line
  the mapping does not cover is an ERROR naming every such line: a caption track
  with silent holes is a plausible artifact nobody re-checks;
- a callable ``line text -> caption``;
- a name from :data:`CAPTION_TRANSFORMS` (``"hepburn"``, ``"original"``) — the
  form that survives JSON, so a render request or the CLI can ask for it.

A transform returning ``""`` omits that line from its track, on purpose.
Every check happens before any track is built — a remote render request
reaches this through the ``captions`` param, and the subgenre schema validator
does not read ``anyOf`` — so a malformed request fails as a ``ValueError``
before the render, never after it.

Timing, per line: the caption appears ``lead_s`` before the line's first sung
glyph (or word) — a reader needs the head start — stays at least ``min_s``, and
leaves when the next line's caption arrives (the last one ``tail_s`` after its
line ends). ``offset_s`` shifts everything, e.g. by a title card prepended to
the video. Lines are captioned in the order they are SUNG, whatever order the
timing lists them in; lines sung together get overlapping captions rather
than one of them a few milliseconds long. No caption outlasts the song. The
files hand straight to ``yb.youtube.CaptionTrack(path, language)``.

``pysubs2`` (the ``lyricvid`` extra, which the default renderer already needs)
is imported only when a track is built.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Union

from muvid.lyricvid.timed_text import Line, TimedText

if TYPE_CHECKING:  # pragma: no cover
    import pysubs2

__all__ = [
    "CAPTION_TRANSFORMS",
    "caption_tracks",
    "write_caption_tracks",
]

#: A caption appears this long before its line is first sung.
LEAD_S = 0.15
#: ...and stays at least this long (unless the next caption arrives first).
MIN_S = 1.2
#: The last caption leaves this long after its line ends.
TAIL_S = 1.5
#: A track's language becomes part of a file name: BCP-47 characters only.
_LANG = re.compile(r"[A-Za-z0-9]{1,8}(-[A-Za-z0-9]{1,8})*")
#: ...and so does the stem.
_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
#: Formats ``pysubs2`` writes that a caption track can be.
FORMATS = ("srt", "vtt", "ass", "ssa")

LineTransform = Union[Callable[[str], str], Mapping[str, str], str]


def _hepburn(text: str) -> str:
    from muvid.lyricvid.glyph_align import romanize_hepburn

    return romanize_hepburn(text)


#: Named transforms — what a JSON caller (a render request, the CLI) can ask for.
CAPTION_TRANSFORMS: dict[str, Callable[[str], str]] = {
    "hepburn": _hepburn,
    "original": lambda text: text,
}


def _check_lang(lang: Any) -> str:
    if not isinstance(lang, str) or not _LANG.fullmatch(lang):
        raise ValueError(
            f"caption language {lang!r} is not a BCP-47 tag (e.g. 'en', 'ja-Latn')"
        )
    return lang


def _resolve(
    lang: str, transform: LineTransform
) -> Callable[[str], str] | Mapping[str, str]:
    _check_lang(lang)
    if isinstance(transform, str):
        try:
            return CAPTION_TRANSFORMS[transform]
        except KeyError:
            raise ValueError(
                f"unknown caption transform {transform!r} for {lang!r}; named "
                f"transforms are {sorted(CAPTION_TRANSFORMS)} — or pass a "
                "{line text: caption} mapping"
            ) from None
    if isinstance(transform, Mapping):
        bad = [k for k, v in transform.items() if not isinstance(v, str)]
        if bad:
            raise ValueError(f"the {lang!r} captions must be text; not for {bad[:5]}")
        return transform
    if callable(transform):
        return transform
    raise ValueError(
        f"caption transform for {lang!r} must be a mapping, a callable or a name"
    )


def _caption_text(lang: str, line: str, text: Any) -> str:
    """A caption as it will be written, or a refusal saying why it cannot be."""
    if not isinstance(text, str):
        raise ValueError(f"the {lang!r} caption for {line!r} is not text: {text!r}")
    if "{" in text or "}" in text:
        # styling codes to every format pysubs2 writes: silently stripped
        raise ValueError(
            f"the {lang!r} caption for {line!r} contains braces, which "
            "subtitle formats read as styling and drop; use () or []"
        )
    if "-->" in text:
        # a timing line: SRT readers start a new cue there, blank line or not
        raise ValueError(
            f"the {lang!r} caption for {line!r} contains '-->', which "
            "subtitle readers take for a cue's timing line"
        )
    # no blank line inside a caption: in an .srt a blank line ends the cue
    return "\n".join(part for part in text.strip().splitlines() if part.strip())


def _onset(line: Line) -> float:
    """When the line is first sung: its first timed glyph, else its first word."""
    times = [
        t
        for w in line.words
        for t in ([g.start for g in w.glyphs] or [w.start])
        if t == t
    ]  # NaN: a glyph nobody timed
    if not times:
        raise ValueError(f"line {line.text!r} has no time to caption it by")
    return min(times)


def _windows(
    lines: list[Line], *, lead_s: float, min_s: float, tail_s: float, until: float
) -> list[tuple[float, float]]:
    """``lines`` in SUNG order -> one ``(start, end)`` each."""
    starts = [_onset(ln) - lead_s for ln in lines]
    out = []
    for i, ln in enumerate(lines):
        end = max(ln.end, starts[i] + min_s)
        nxt = starts[i + 1] if i + 1 < len(lines) else ln.end + tail_s
        if nxt > starts[i]:  # lines sung TOGETHER overlap rather than flash
            end = min(end, nxt)
        out.append((starts[i], min(end, until)))
    return out


def caption_tracks(
    timed_text: TimedText,
    transforms: Mapping[str, LineTransform],
    *,
    lead_s: float = LEAD_S,
    min_s: float = MIN_S,
    tail_s: float = TAIL_S,
    offset_s: float = 0.0,
) -> dict[str, "pysubs2.SSAFile"]:
    """One subtitle track per language, a caption per sung line.

    ``transforms`` maps a BCP-47 language tag to a :data:`LineTransform`.
    Every transform is checked (unknown names, uncovered lines) before any
    track is built, so a bad request fails whole rather than half-written.

    >>> from muvid.lyricvid.timed_text import from_word_records
    >>> tt = from_word_records([
    ...     {"text": "バス", "start": 1.0, "end": 1.5, "line_end": True},
    ...     {"text": "スタート", "start": 2.0, "end": 3.0, "line_end": True}])
    >>> tracks = caption_tracks(tt, {"en": {"バス": "Bus", "スタート": "Start"},
    ...                              "ja-Latn": "hepburn"})
    >>> [(e.start, e.end, e.text) for e in tracks["ja-Latn"]]
    [(850, 1850, 'basu'), (1850, 3050, 'sutāto')]
    """
    import pysubs2

    lines = sorted((ln for ln in timed_text.lines() if ln.words), key=_onset)
    fns = {lang: _resolve(lang, t) for lang, t in transforms.items()}
    texts: dict[str, list[str]] = {}
    for lang, fn in fns.items():
        got, missing = [], []
        for ln in lines:
            if isinstance(fn, Mapping):
                if ln.text not in fn:
                    missing.append(ln.text)
                    continue
                text = fn[ln.text]
            else:
                text = fn(ln.text)
            got.append(_caption_text(lang, ln.text, text))
        if missing:
            raise ValueError(
                f"the {lang!r} captions do not cover {len(missing)} sung line(s): "
                f"{sorted(set(missing))} — add them, or map a line to '' to omit it"
            )
        texts[lang] = got

    until = timed_text.duration if timed_text.duration > 0 else float("inf")
    windows = _windows(lines, lead_s=lead_s, min_s=min_s, tail_s=tail_s, until=until)
    tracks = {}
    for lang, got in texts.items():
        track = pysubs2.SSAFile()
        for (t0, t1), text in zip(windows, got):
            t0, t1 = max(0.0, t0 + offset_s), t1 + offset_s
            if text and t1 > t0:
                event = pysubs2.SSAEvent(start=round(t0 * 1000), end=round(t1 * 1000))
                event.plaintext = text  # newlines -> line breaks, not cue breaks
                track.append(event)
        tracks[lang] = track
    return tracks


def write_caption_tracks(
    tracks: Mapping[str, "pysubs2.SSAFile"],
    out_dir: Path | str,
    *,
    stem: str = "captions",
    fmt: str = "srt",
) -> dict[str, Path]:
    """Save each track as ``{out_dir}/{stem}.{lang}.{fmt}``; return ``{lang: path}``."""
    if not _STEM.fullmatch(stem):
        raise ValueError(
            f"caption file stem {stem!r}: letters, digits, '_' and '-' only"
        )
    if fmt not in FORMATS:
        raise ValueError(f"caption format {fmt!r}: one of {FORMATS}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for lang, track in tracks.items():
        _check_lang(lang)  # the language becomes a file name
        path = out / f"{stem}.{lang}.{fmt}"
        track.save(str(path), format_=fmt)
        paths[lang] = path
    return paths
