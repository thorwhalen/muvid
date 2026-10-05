"""Why each cut is where it is — the post-mortem of an edit, in plain words.

A person watching an edit asks "why did it cut THERE?", "why this clip?", "why is this
one slightly off the beat?". This module answers from the edit itself and the same
measurements the cutting used — the song's beat grid and sections, each video's picture
signal — so the answer is about the edit as it stands now (a cut a person changed is
explained as changed), and the screen, the assistant and a reader all get one record.

Each cut gets a few sentences in a fixed order — *where and on what* → *how the picture
changes* → *what picture and why that stretch* → *what is worth a look* — plus the
facts behind them (song times, the beat it lands on, the fade's real start) and
``flags`` for the ones worth a look: ``straddles_the_beat``, ``off_the_beat``,
``long_hold``, ``repeated_stretch``. Every clock time in a sentence is also listed in
``times`` with its second, so a screen can turn it into a link that seeks the player.

Plain nouns only (beat, bar, fade, picture, photo, clip); clock times, never floats.
The explanation says what the system did by default when that is the reason — the
post-mortem is only useful if it admits its defaults.

Pure: the measurements come in as arguments (:func:`explain`); the service builds
them (``service.explain_edit``).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

__all__ = ["explain", "clock", "FLAG_WORDS"]

#: A boundary within this of a beat is ON the beat (about a frame at 25 fps).
ON_BEAT_S = 0.04
#: A hold longer than this many bars, or seconds, is flagged.
LONG_HOLD_BARS, LONG_HOLD_S = 4, 8.0
#: A stretch of a clip shown again overlapping this much of an earlier showing.
REPEAT_FRACTION = 0.5
#: A beat "hit" by the picture: a change at least this many times the clip's typical.
HIT_FACTOR = 2.0

#: What each flag says, for a chip.
FLAG_WORDS = {
    "straddles_the_beat": "starts before the beat",
    "off_the_beat": "not on a beat",
    "long_hold": "long hold",
    "repeated_stretch": "shown before",
}

_ORDINAL = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}
_MOVES = {
    "slow_push": "pushes in slowly toward its most detailed part",
    "slow_pull": "pulls back slowly from its most detailed part",
    "pan_left": "drifts slowly to the left",
    "pan_right": "drifts slowly to the right",
    "punch_in": "comes in close on its most detailed part",
}
_STYLE_WORDS = {
    "beat_cut": "hard cuts on the beat",
    "ballad_dissolve": "fades, for a slower song",
    "stop_motion": "quick flip-book holds",
}


def clock(t: float) -> str:
    """``72.41`` -> ``"1:12.4"`` — how a time is said.

    >>> clock(72.41), clock(5.0)
    ('1:12.4', '0:05.0')
    """
    m, sec = divmod(max(0.0, float(t)), 60.0)
    if round(sec, 1) >= 60.0:
        m, sec = m + 1, 0.0
    return f"{int(m)}:{sec:04.1f}"


def _span_words(seconds: float) -> str:
    """A length as a person says it.

    >>> _span_words(0.34), _span_words(0.68), _span_words(21.0)
    ('a third of a second', 'two-thirds of a second', '21 seconds')
    """
    s = float(seconds)
    for limit, words in (
        (0.2, "a fraction of a second"),
        (0.42, "a third of a second"),
        (0.58, "half a second"),
        (0.8, "two-thirds of a second"),
        (1.25, "about a second"),
    ):
        if s < limit:
            return words
    return f"{s:.0f} seconds" if s >= 3 else f"{s:.1f} seconds"


class _Grid:
    """The song's beats and bars, for naming a moment ("beat 3 of bar 34")."""

    def __init__(self, beats: Sequence[float], downbeats: Sequence[float]):
        self.beats = np.asarray(beats, dtype=float)
        self.downs = np.asarray(downbeats, dtype=float)

    def nearest(self, t: float) -> tuple[Optional[float], float]:
        if not self.beats.size:
            return None, float("inf")
        i = int(np.argmin(np.abs(self.beats - t)))
        return float(self.beats[i]), float(t - self.beats[i])

    def name(self, t: float) -> str:
        """The beat at ``t`` (which must be one), by bar."""
        if not self.downs.size:
            return "a beat"
        bar = int(np.searchsorted(self.downs, t + 1e-6))  # 1-based bar number
        if bar == 0:
            return "a beat before the first bar"
        start = self.downs[bar - 1]
        k = int(np.sum((self.beats >= start - 1e-6) & (self.beats < t - 1e-6))) + 1
        return f"the {_ORDINAL.get(k, f'{k}th')} beat of bar {bar}" if k > 1 else f"the first beat of bar {bar}"

    def inner(self, a: float, b: float) -> np.ndarray:
        return self.beats[(self.beats > a + ON_BEAT_S) & (self.beats < b - ON_BEAT_S)]

    def bar_s(self) -> Optional[float]:
        return float(np.median(np.diff(self.downs))) if self.downs.size > 1 else None


def explain(
    entries: Sequence[Any],
    *,
    analysis: Any,
    song_duration: float,
    clips: Mapping[str, Mapping[str, Any]],
    alignments: Mapping[str, Any] = {},
    envelope_of: Callable[[str], Any] = lambda clip_id: None,
    selection: Mapping[str, Any] = {},
    grid_note: Optional[str] = None,
    window: Optional[tuple[float, float]] = None,
) -> dict:
    """The post-mortem of an edit: ``{summary, times, cuts: [...]}``.

    ``entries`` are the edit's validated :class:`~muvid.footage.edl.EdlEntry` cuts;
    ``analysis`` the song's (extended) beat grid and sections; ``clips`` maps a clip
    id to ``{name, kind ('video'|'still'), role}``; ``alignments`` the clip
    placements (for synced cuts); ``envelope_of(clip_id)`` a video's picture signal
    (:class:`muvid.footage.music_cut.Envelope`) or ``None``; ``selection`` how the
    edit was made (``pace``, ``style``, ``strategy``). ``window`` (song seconds)
    keeps only the cuts that overlap it; the summary is always of the whole edit.
    """
    grid = _Grid(analysis.beats, analysis.downbeats)
    bar_s = grid.bar_s()
    sections = list(getattr(analysis, "sections", ()) or ())
    section_starts = {round(s.start, 2): s.label for s in sections[1:]}
    shown: dict[str, list[tuple[float, float, float]]] = {}  # clip -> (from, to, at)
    cuts = []
    for i, e in enumerate(entries):
        cuts.append(
            _explain_cut(
                i,
                e,
                prev=entries[i - 1] if i else None,
                grid=grid,
                bar_s=bar_s,
                section_starts=section_starts,
                clips=clips,
                alignments=alignments,
                envelope_of=envelope_of,
                shown=shown,
            )
        )
    summary, summary_times = _summary(
        cuts, sections, song_duration, selection, grid_note, getattr(analysis, "tempo_bpm", None)
    )
    if window is not None:
        lo, hi = window
        cuts = [c for c in cuts if c["end_s"] > lo and c["start_s"] < hi]
    return {"summary": summary, "times": summary_times, "cuts": cuts}


def _explain_cut(i, e, *, prev, grid, bar_s, section_starts, clips, alignments, envelope_of, shown):
    from muvid.footage.edl import TRANSITION_SPLIT

    start, end = float(e.song_start), float(e.song_end)
    length = end - start
    row = clips.get(e.clip_id or "", {})
    name = row.get("name") or e.clip_id or ""
    kind = "black" if not e.clip_id else ("photo" if row.get("kind") == "still" else "video")
    times: list[dict] = []
    flags: list[str] = []
    said: list[str] = []

    def t(x: float) -> str:
        times.append({"label": clock(x), "s": round(float(x), 3)})
        return clock(x)

    # 1. where, and on what — and 2. how the picture changes
    beat, off = grid.nearest(start)
    fade = e.transition.duration_s if getattr(e, "transition", None) is not None else 0.0
    lead = fade * TRANSITION_SPLIT
    fade_start = start - lead
    section = section_starts.get(round(start, 2))
    where = f" — where the song's {section} starts" if section else ""
    if i == 0:
        said.append(f"The video opens at {t(start)}.")
    elif fade > 0:
        fbeat, foff = grid.nearest(fade_start)
        if fbeat is not None and abs(foff) <= ON_BEAT_S:
            said.append(
                f"A fade of about {_span_words(fade)} starts on {grid.name(fbeat)} at {t(fade_start)}{where}, "
                f"so the new picture is fully in by {t(start + (fade - lead))}."
            )
        elif beat is not None and abs(off) <= ON_BEAT_S:
            flags.append("straddles_the_beat")
            said.append(
                f"This change is centred on {grid.name(beat)} at {t(start)}{where}: it is a fade of about "
                f"{_span_words(fade)}, so the picture starts changing {_span_words(lead)} before the beat "
                f"(at {t(fade_start)}) and finishes {_span_words(fade - lead)} after."
            )
        else:
            flags.append("off_the_beat")
            said.append(f"A fade of about {_span_words(fade)} starts at {t(fade_start)}, not on a beat{where}.")
    elif beat is not None and abs(off) <= ON_BEAT_S:
        said.append(f"A hard cut on {grid.name(beat)} at {t(start)}{where}.")
    else:
        flags.append("off_the_beat")
        near = (
            f" — the nearest beat is {_span_words(abs(off))} {'earlier' if off > 0 else 'later'}"
            if beat is not None and abs(off) < 2.0
            else " — there is no beat near it"
        )
        said.append(f"A hard cut at {t(start)}, not on a beat{near}.")

    # 3. what picture, and why that stretch
    from_s = None
    if kind == "black":
        said.append("Nothing was there to show, so the screen is black.")
    elif kind == "photo":
        move = _MOVES.get((getattr(e, "look_spec", None) or {}).get("name", ""), "is held")
        said.append(f"The photo {name} {move}.")
    elif getattr(e, "source_in", None) is None:
        a = alignments.get(e.clip_id)
        if a is not None:
            from_s = start - a.offset_s + getattr(e, "slip_s", 0.0)
        said.append(f"{name} plays where it was filmed: its own sound has the song in it here.")
    else:
        from_s = float(e.source_in) + getattr(e, "slip_s", 0.0)
        said.append(f"It shows {name} from {clock(from_s)} into the clip{_why_stretch(e, grid, envelope_of, from_s)}.")
    if from_s is not None and kind == "video":
        to_s = from_s + length * getattr(e, "rate", 1.0)
        for a0, a1, at in shown.get(e.clip_id, []):
            overlap = max(0.0, min(to_s, a1) - max(from_s, a0))
            if overlap > REPEAT_FRACTION * max(1e-6, to_s - from_s):
                flags.append("repeated_stretch")
                said.append(f"Most of this stretch was already shown at {t(at)}.")
                break
        shown.setdefault(e.clip_id, []).append((from_s, to_s, start))

    # 4. what is worth a look
    long_limit = max(LONG_HOLD_S, (bar_s or 0.0) * LONG_HOLD_BARS)
    if kind != "black" and length > long_limit:
        flags.append("long_hold")
        said.append(f"It holds for {_span_words(length)}, much longer than most shots here.")

    return {
        "index": i,
        "start_s": round(start, 3),
        "end_s": round(end, 3),
        "clip_id": e.clip_id or None,
        "picture": {"kind": kind, "name": name or None, "from_s": None if from_s is None else round(from_s, 3)},
        "transition": (
            {"kind": "fade", "length_s": round(fade, 3), "starts_s": round(fade_start, 3), "ends_s": round(start + fade - lead, 3)}
            if fade > 0
            else {"kind": "cut"}
        ),
        "on_beat_s": None if beat is None else round(beat, 3),
        "off_beat_ms": None if beat is None else int(round(off * 1000)),
        "flags": flags,
        "text": " ".join(said),
        "times": times,
    }


def _why_stretch(e, grid, envelope_of, from_s: float) -> str:
    """Why this stretch of the video: its picture changes on the beats inside the cut."""
    env = envelope_of(e.clip_id)
    inner = grid.inner(float(e.song_start), float(e.song_end))
    if env is None or getattr(env, "hits", None) is None or not np.size(env.hits):
        return ", chosen to vary the pictures"
    if not inner.size:
        return ", a short stretch between two beats"
    hits = np.nan_to_num(np.asarray(env.hits, dtype=float))
    typical = float(np.mean(hits)) + 1e-9
    k = 0
    for b in inner:
        j = int(round((from_s + (b - e.song_start) * getattr(e, "rate", 1.0)) / env.hop_s))
        lo, hi = max(0, j - 1), min(hits.size, j + 2)
        if hi > lo and float(np.max(hits[lo:hi])) >= HIT_FACTOR * typical:
            k += 1
    m = int(inner.size)
    if k == 0:
        return f", though its picture does not change sharply on any of the {m} beats inside this cut"
    return f", chosen because its picture changes on {k} of the {m} beat{'s' if m > 1 else ''} inside this cut"


def _summary(cuts, sections, song_duration, selection, grid_note, tempo):
    times: list[dict] = []

    def t(x):
        times.append({"label": clock(x), "s": round(float(x), 3)})
        return clock(x)

    shown = [c for c in cuts if c["picture"]["kind"] != "black"]
    n = len(cuts)
    fades = sum(1 for c in cuts if c["transition"]["kind"] == "fade")
    straddle = sum(1 for c in cuts if "straddles_the_beat" in c["flags"])
    off = sum(1 for c in cuts if "off_the_beat" in c["flags"])
    said = [f"{n} shots over the {clock(song_duration)} song: {fades} fades and {n - fades} hard cuts."]
    if straddle:
        said.append(
            f"{straddle} of the fades are centred on their beat, so the picture starts changing a little before it — "
            "choose fades that start on the beat, or hard cuts, to put every change on the beat."
        )
    if off:
        said.append(f"{off} change{'s are' if off > 1 else ' is'} not on a beat.")
    elif not straddle:
        said.append("Every change starts on a beat.")
    style = selection.get("style") or ""
    pace = selection.get("pace") or ""
    chose = []
    if style:
        chose.append({"cuts": "hard cuts", "fades": "fades"}.get(style, style))
    if pace:
        chose.append({"driving": "lively"}.get(pace, pace) + " pace")
    if chose:
        said.append(f"You chose {' and '.join(chose)}.")
    elif tempo:
        said.append(
            f"The song is about {round(tempo)} beats a minute, so Reelee picked "
            f"{'fades, for a slower song' if tempo < 92 else 'hard cuts on the beat'}."
        )
    if sections:
        big = max(sections, key=lambda s: s.end - s.start)
        if (big.end - big.start) > 0.75 * song_duration:
            said.append(
                f"The song was heard as one long stretch from {t(big.start)} to {t(big.end)}, so the pace hardly "
                "changes — when the song's shape is found better, quiet parts will get fewer cuts and loud parts more."
            )
    if grid_note:
        said.append(grid_note[0].upper() + grid_note[1:] + ".")
    if shown:
        longest = max(shown, key=lambda c: c["end_s"] - c["start_s"])
        said.append(
            f"The longest shot holds {_span_words(longest['end_s'] - longest['start_s'])}, from {t(longest['start_s'])}."
        )
    return " ".join(said), times
