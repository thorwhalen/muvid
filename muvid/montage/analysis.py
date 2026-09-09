"""Measure the song and the pool: beat grid, bars, sections, and media strength.

Everything the planner needs is MEASURED here, once, and handed over as plain
records — the planner (:mod:`muvid.montage.plan`) is pure and never touches a
file. Three measurements:

**Beat grid.** ``mixing.audio.beat_grid`` (librosa, ISC) is the house beat
backend and is used when it is importable; ``mixing`` is a core dependency but
its ``[beats]`` extra is not, so on a bare install this module falls back to
its own small numpy estimator — spectral-flux onset strength, an
autocorrelation tempo with a log-normal prior around 120 BPM, and a comb-filter
phase search — which yields a **fixed-tempo** grid. That is honest for the
material montages are cut to and it is recorded in ``Analysis.beat_source`` so
nobody mistakes it for a tracked grid. Only ``ImportError`` is caught, and only
on the ``auto`` path: a ``mixing`` regression must surface as a traceback, not
as a quietly different grid (the ``mixing`` rule in the design record).

**Bars and sections.** librosa has no downbeat tracker, so downbeats are the
beat phase (0..beats_per_bar-1) with the most onset energy — a closed-form vote.
Sections, when the caller does not supply them, are derived from per-bar RMS
energy: a stretch louder than the median is a chorus, quieter is a verse, the
leading and trailing quiet runs are intro/outro. Coarse, deterministic, and
labelled ``section_source="energy"`` so a caller knows what it got.

**Media strength.** A photo's strength is a closed-form function of its pixel
count and luma contrast (a 64x64 grey thumbnail decoded by ffmpeg — no Pillow).
The reuse policy reserves the strongest for the final chorus.

Module scope is stdlib-only; numpy and ffmpeg are reached inside functions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "Analysis",
    "Media",
    "Section",
    "analyze",
    "beat_grid",
    "derive_sections",
    "downbeats_from_beats",
    "probe_media",
    "sections_from_labels",
]

#: Analysis sample rate for the numpy path and the energy envelope. Beat and
#: loudness measurement do not need bandwidth; a low rate keeps a 15-minute
#: decode at ~40 MB of float32.
ANALYSIS_SR = 11025
#: STFT hop/window for the onset envelope (23 ms / 93 ms at ANALYSIS_SR).
ONSET_HOP = 256
ONSET_WIN = 1024
#: Tempo search range and the prior's centre, in BPM. The log-normal prior is
#: what keeps a 90 BPM song from reading as 180 (librosa uses the same shape).
MIN_BPM = 60.0
MAX_BPM = 200.0
PRIOR_BPM = 120.0
PRIOR_OCTAVE_SD = 1.0
#: When the onset envelope has no usable periodicity (silence, noise, a very
#: short file) the grid falls back to this tempo from t=0, and says so.
FALLBACK_BPM = 120.0
#: Autocorrelation peak below this fraction of the zero-lag value is noise.
MIN_PERIODICITY = 0.05
DEFAULT_BEATS_PER_BAR = 4
#: A section shorter than this many bars is absorbed into a neighbour. Scaled
#: down for short songs so an 8-bar song can still have a chorus.
MIN_SECTION_BARS = 4
#: Leading/trailing quiet runs no longer than this are intro/outro, not verses.
MAX_INTRO_BARS = 8
#: Below this bar-energy range (dB) the song is dynamically flat: one section.
FLAT_RANGE_DB = 1.5
#: Thumbnail side for the contrast measurement.
STRENGTH_THUMB = 64
#: Where a clip's strength frame is sampled (fraction of its duration).
CLIP_SAMPLE_AT = 1 / 3


@dataclass(frozen=True, slots=True, kw_only=True)
class Section:
    """A labelled stretch of the song, in seconds."""

    label: str
    start: float
    end: float
    #: Mean bar energy in dB (relative), when measured.
    energy_db: float | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True, kw_only=True)
class Media:
    """One pool item, measured.

    ``kind`` is ``photo``, ``clip`` or ``cover``; ``duration`` is ``None`` for
    a still; ``strength`` is the closed-form score the reuse policy ranks on.
    """

    index: int
    path: str
    kind: str
    width: int
    height: int
    duration: float | None = None
    strength: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "path": self.path,
            "kind": self.kind,
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "strength": round(self.strength, 4),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Analysis:
    """What the planner knows about the song."""

    duration: float
    tempo_bpm: float
    beats: tuple[float, ...]
    downbeats: tuple[float, ...]
    beats_per_bar: int = DEFAULT_BEATS_PER_BAR
    sections: tuple[Section, ...] = ()
    beat_source: str = ""
    section_source: str = ""
    bar_energy_db: tuple[float, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def beat_s(self) -> float:
        """Seconds per beat at the estimated tempo."""
        return 60.0 / max(1e-6, self.tempo_bpm)

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": round(self.duration, 4),
            "tempo_bpm": round(self.tempo_bpm, 3),
            "beats_per_bar": self.beats_per_bar,
            "n_beats": len(self.beats),
            "beats": [round(t, 4) for t in self.beats],
            "downbeats": [round(t, 4) for t in self.downbeats],
            "sections": [
                {
                    "label": s.label,
                    "start": round(s.start, 4),
                    "end": round(s.end, 4),
                    "energy_db": None if s.energy_db is None else round(s.energy_db, 2),
                }
                for s in self.sections
            ],
            "beat_source": self.beat_source,
            "section_source": self.section_source,
            "bar_energy_db": [round(e, 2) for e in self.bar_energy_db],
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------
# audio decode
# --------------------------------------------------------------------------


def _pcm(audio: Path, sr: int):
    """Mono float32 samples at ``sr``, via muvid's one PCM decode path."""
    import numpy as np

    from muvid.visualize.ffmpeg import decode_pcm

    raw = decode_pcm(audio, sample_rate=sr, channels=1)
    itemsize = np.dtype(np.float32).itemsize
    return np.frombuffer(raw[: len(raw) - len(raw) % itemsize], dtype=np.float32)


# --------------------------------------------------------------------------
# beat grid
# --------------------------------------------------------------------------

BEAT_SOURCES = ("auto", "mixing", "numpy")


def _onset_strength(x, sr: int, *, hop: int = ONSET_HOP, win: int = ONSET_WIN):
    """Spectral-flux onset strength, one value per hop. Chunked, so a long
    song never materialises its whole spectrogram."""
    import numpy as np

    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    window = np.hanning(win).astype(np.float32)
    out = np.empty(len(frames), dtype=np.float32)
    prev = None
    chunk = 2048
    for start in range(0, len(frames), chunk):
        block = frames[start : start + chunk] * window
        mag = np.log1p(np.abs(np.fft.rfft(block, axis=1)))
        if prev is None:
            prev = mag[:1]
        d = np.diff(np.concatenate([prev, mag], axis=0), axis=0)
        out[start : start + len(block)] = np.maximum(d, 0).sum(axis=1)
        prev = mag[-1:]
    scale = float(np.percentile(out, 95)) or 1.0
    return out / scale


def _tempo_period(o, sr: int, hop: int) -> tuple[float, float]:
    """``(period_frames, periodicity)`` from the onset envelope's autocorrelation.

    A log-normal prior centred on :data:`PRIOR_BPM` weights the candidates;
    the peak is refined by parabolic interpolation. ``periodicity`` is the
    chosen peak relative to lag 0, so a caller can tell a real beat from noise.
    """
    import numpy as np

    o = o - o.mean()
    n = len(o)
    size = 1 << (2 * n - 1).bit_length()
    spec = np.fft.rfft(o, size)
    ac = np.fft.irfft(spec * np.conj(spec), size)[:n]
    if ac[0] <= 0:
        return 0.0, 0.0
    ac = ac / ac[0]
    lag_min = int(math.floor(60.0 / MAX_BPM * sr / hop))
    lag_max = int(math.ceil(60.0 / MIN_BPM * sr / hop))
    if lag_max >= n or lag_min < 1:
        return 0.0, 0.0
    lags = np.arange(lag_min, lag_max + 1)
    bpm = 60.0 * sr / (hop * lags)
    prior = np.exp(-0.5 * (np.log2(bpm / PRIOR_BPM) / PRIOR_OCTAVE_SD) ** 2)
    scored = ac[lag_min : lag_max + 1] * prior
    k = int(np.argmax(scored))
    lag = float(lags[k])
    # parabolic refinement on the raw autocorrelation
    if 0 < k < len(lags) - 1:
        y0, y1, y2 = ac[lags[k] - 1], ac[lags[k]], ac[lags[k] + 1]
        denom = y0 - 2 * y1 + y2
        if abs(denom) > 1e-12:
            lag += 0.5 * (y0 - y2) / denom
    return lag, float(ac[lags[k]])


def _beat_phase(o, period: float) -> int:
    """The frame offset in ``[0, period)`` whose comb picks up the most onset energy."""
    import numpy as np

    n = len(o)
    best, best_score = 0, -1.0
    for phi in range(max(1, int(math.ceil(period)))):
        idx = np.round(np.arange(phi, n, period)).astype(int)
        idx = idx[idx < n]
        score = float(o[idx].sum()) if len(idx) else -1.0
        if score > best_score:
            best, best_score = phi, score
    return best


#: An onset peak below this (relative to the envelope's 95th percentile) does
#: not count as evidence when the grid is refined.
_REFINE_MIN_PEAK = 0.2


def _refine_grid(o, period: float, phi: float) -> tuple[float, float]:
    """Refine ``(period, phi)`` by regressing the actual onset peaks on the comb.

    The autocorrelation peak is biased by the peak's shape (parabolic
    interpolation on a click train lands a few tenths of a frame off, which is
    half a second of drift over three minutes). Locating the strongest onset
    near each predicted beat and fitting a line through them is exact for a
    steady tempo and costs nothing. Falls back to the input when fewer than
    four peaks are found.
    """
    import numpy as np

    n = len(o)
    w = max(1, int(round(period / 4)))
    ks, fs = [], []
    k = 0
    while True:
        p = phi + k * period
        if p >= n:
            break
        lo, hi = max(0, int(round(p)) - w), min(n, int(round(p)) + w + 1)
        if hi > lo:
            j = lo + int(np.argmax(o[lo:hi]))
            if o[j] >= _REFINE_MIN_PEAK:
                ks.append(k)
                fs.append(j)
        k += 1
    if len(ks) < 4:
        return period, phi
    slope, intercept = np.polyfit(
        np.asarray(ks, dtype=float), np.asarray(fs, dtype=float), 1
    )
    if not (0.5 * period < slope < 1.5 * period):
        return period, phi
    return float(slope), float(intercept)


def _numpy_beats(x, sr: int, duration: float):
    """``(beats, tempo_bpm, onset_env, hop_s, note)`` — the fallback estimator."""
    import numpy as np

    o = _onset_strength(x, sr)
    hop_s = ONSET_HOP / sr
    period, periodicity = _tempo_period(o, sr, ONSET_HOP)
    if period <= 0 or periodicity < MIN_PERIODICITY:
        beat_s = 60.0 / FALLBACK_BPM
        beats = np.arange(0.0, duration, beat_s)
        return (
            beats,
            FALLBACK_BPM,
            o,
            hop_s,
            f"no periodicity found (peak {periodicity:.3f}); fixed {FALLBACK_BPM:g} BPM grid from 0",
        )
    period, phi = _refine_grid(o, period, float(_beat_phase(o, period)))
    # A frame's time is its window CENTRE (librosa's convention). The flux of
    # frame k peaks once a transient is well inside the Hann window, so timing
    # frames at their start put every beat ~80 ms early; the centre lands the
    # synthetic click track within a hop (measured: +4 ms).
    onset_lead_s = (ONSET_WIN / 2) / sr
    beats = np.arange(phi, len(o), period) * hop_s + onset_lead_s
    beats = beats[(beats >= 0) & (beats < duration)]
    tempo = 60.0 / (period * hop_s)
    return beats, tempo, o, hop_s, ""


def beat_grid(
    audio: Path | str, *, source: str = "auto", duration: float | None = None
):
    """``(beats, tempo_bpm, onset_env, onset_hop_s, source_name, note)``.

    ``source="auto"`` tries ``mixing.audio.beat_grid`` and falls back to the
    numpy estimator only on :class:`ImportError` (librosa absent). Naming a
    source never falls back.
    """
    import numpy as np

    if source not in BEAT_SOURCES:
        raise ValueError(f"beats must be one of {BEAT_SOURCES}, got {source!r}")
    audio = Path(audio)
    if duration is None:
        from muvid.visualize.ffmpeg import media_duration

        duration = media_duration(audio)

    if source in {"auto", "mixing"}:
        try:
            from mixing.audio import beat_grid as _mixing_beat_grid

            bg = _mixing_beat_grid(str(audio))
        except ImportError:
            if source == "mixing":
                raise
            bg = None
        if bg is not None:
            beats = np.asarray(bg.beat_times, dtype=float)
            beats = beats[(beats >= 0) & (beats < duration)]
            tempo = float(bg.tempo_bpm)
            if len(beats) < 2 or tempo <= 0:
                # librosa found nothing to track (silence, a click-free drone):
                # reported, not hidden — and the numpy path gets its turn.
                note = "mixing.audio.beat_grid found no beats"
                if source == "mixing":
                    return (
                        beats,
                        tempo or FALLBACK_BPM,
                        np.asarray(bg.onset_env),
                        float(bg.onset_hop_s),
                        "mixing.audio.beat_grid",
                        note,
                    )
            else:
                return (
                    beats,
                    tempo,
                    np.asarray(bg.onset_env, dtype=float),
                    float(bg.onset_hop_s),
                    "mixing.audio.beat_grid (librosa)",
                    "",
                )

    x = _pcm(audio, ANALYSIS_SR)
    beats, tempo, o, hop_s, note = _numpy_beats(x, ANALYSIS_SR, duration)
    return (
        beats,
        tempo,
        o,
        hop_s,
        "muvid.montage.analysis (numpy onset autocorrelation, fixed tempo)",
        note,
    )


def downbeats_from_beats(
    beats: Sequence[float], onset_at_beats: Sequence[float], beats_per_bar: int
) -> tuple[float, ...]:
    """Downbeats as the beat phase carrying the most onset energy.

    A closed-form vote over ``beats_per_bar`` candidate phases; ties go to
    phase 0 (the first beat), which is also the answer when there is nothing
    to vote with.

    >>> downbeats_from_beats([0, .5, 1, 1.5, 2, 2.5, 3, 3.5], [1, 0, 0, 0, 1, 0, 0, 0], 4)
    (0, 2)
    >>> downbeats_from_beats([0, .5, 1, 1.5, 2, 2.5, 3, 3.5], [0, 0, 1, 0, 0, 0, 1, 0], 4)
    (1, 3)
    """
    beats = list(beats)
    if not beats:
        return ()
    bpb = max(1, int(beats_per_bar))
    energy = list(onset_at_beats) + [0.0] * (len(beats) - len(onset_at_beats))
    scores = [sum(energy[i::bpb]) for i in range(bpb)]
    best = max(range(bpb), key=lambda i: (scores[i], -i))
    return tuple(beats[best::bpb])


# --------------------------------------------------------------------------
# bars, energy, sections
# --------------------------------------------------------------------------


def _bars(downbeats: Sequence[float], duration: float) -> list[tuple[float, float]]:
    """Bar spans ``[start, end)`` covering the song.

    The pickup before the first downbeat and the tail after the last are
    merged into their neighbour when shorter than half a bar — a half-second
    "bar" at the head would otherwise become a half-second section.

    >>> _bars([0.5, 2.5, 4.5], 6.2)
    [(0.0, 2.5), (2.5, 4.5), (4.5, 6.2)]
    >>> _bars([1.5, 3.5], 5.0)
    [(0.0, 1.5), (1.5, 3.5), (3.5, 5.0)]
    """
    edges = [0.0] + [t for t in downbeats if 0.0 < t < duration] + [duration]
    spans = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 1e-6]
    if len(spans) >= 3:
        full = sorted(b - a for a, b in spans[1:-1])[len(spans[1:-1]) // 2]
        if spans[0][1] - spans[0][0] < full / 2:
            spans[1] = (spans[0][0], spans[1][1])
            del spans[0]
        if len(spans) >= 2 and spans[-1][1] - spans[-1][0] < full / 2:
            spans[-2] = (spans[-2][0], spans[-1][1])
            del spans[-1]
    return spans


def _two_means_threshold(values: Sequence[float]) -> float:
    """The split between the quiet and the loud bars — 2-means on one axis,
    initialised at the midrange, iterated to a fixed point. Deterministic.

    >>> round(_two_means_threshold([-20, -21, -19, -10, -11, -20]), 2)
    -15.25
    """
    vals = list(values)
    t = (min(vals) + max(vals)) / 2
    for _ in range(50):
        lo = [v for v in vals if v <= t]
        hi = [v for v in vals if v > t]
        if not lo or not hi:
            return t
        t_new = (sum(lo) / len(lo) + sum(hi) / len(hi)) / 2
        if abs(t_new - t) < 1e-9:
            break
        t = t_new
    return t


def _bar_energy_db(x, sr: int, bars: Sequence[tuple[float, float]]) -> list[float]:
    import numpy as np

    out = []
    for a, b in bars:
        seg = x[int(a * sr) : max(int(a * sr) + 1, int(b * sr))]
        rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2))) if len(seg) else 0.0
        out.append(20 * math.log10(rms + 1e-6))
    return out


def _runs(flags: Sequence[bool]) -> list[list[int]]:
    """Consecutive runs of equal flags, as index lists."""
    runs: list[list[int]] = []
    for i, f in enumerate(flags):
        if runs and flags[runs[-1][0]] == f:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def derive_sections(
    bar_energy_db: Sequence[float],
    bars: Sequence[tuple[float, float]],
    duration: float,
    *,
    min_section_bars: int = MIN_SECTION_BARS,
) -> tuple[Section, ...]:
    """Coarse sections from per-bar energy. Loud = chorus, quiet = verse.

    The threshold splits the bar energies in two (:func:`_two_means_threshold`);
    interior runs shorter than ``min_section_bars`` (scaled down for short
    songs) are absorbed into the longer neighbour; a leading/trailing quiet run
    of at most :data:`MAX_INTRO_BARS` bars is an intro/outro. A dynamically
    flat song is one verse — reported as such rather than invented.

    >>> bars = [(i * 2.0, (i + 1) * 2.0) for i in range(8)]
    >>> e = [-20, -20, -20, -20, -10, -10, -20, -20]
    >>> [(s.label, s.start, s.end) for s in derive_sections(e, bars, 16.0)]
    [('intro', 0.0, 8.0), ('chorus', 8.0, 12.0), ('outro', 12.0, 16.0)]
    >>> [s.label for s in derive_sections([-20] * 8, bars, 16.0)]
    ['verse']
    """
    energy = list(bar_energy_db)
    bars = list(bars)
    n = len(bars)
    if n == 0:
        return (Section(label="verse", start=0.0, end=duration),)
    if n != len(energy):
        raise ValueError(f"{n} bars but {len(energy)} energy values")
    lo, hi = (
        sorted(energy)[max(0, n // 10)],
        sorted(energy)[min(n - 1, n - 1 - n // 10)],
    )
    if hi - lo < FLAT_RANGE_DB or n < 2:
        return (
            Section(label="verse", start=0.0, end=duration, energy_db=sum(energy) / n),
        )
    threshold = _two_means_threshold(energy)
    loud = [v > threshold for v in energy]
    min_run = max(1, min(min_section_bars, n // 3))
    # Absorb short INTERIOR runs into the longer neighbour until stable. The
    # first and last runs are kept whatever their length: a two-bar quiet tail
    # is an outro, not a mislabelled piece of the chorus before it.
    changed = True
    while changed:
        changed = False
        runs = _runs(loud)
        if len(runs) < 3:
            break
        for k, run in enumerate(runs[1:-1], start=1):
            if len(run) < min_run:
                target = max(runs[k - 1], runs[k + 1], key=len)
                for i in run:
                    loud[i] = loud[target[0]]
                changed = True
                break
    runs = _runs(loud)
    sections = []
    for k, run in enumerate(runs):
        is_loud = loud[run[0]]
        label = "chorus" if is_loud else "verse"
        if not is_loud and k == 0 and len(runs) > 1 and len(run) <= MAX_INTRO_BARS:
            label = "intro"
        elif (
            not is_loud
            and k == len(runs) - 1
            and len(runs) > 1
            and len(run) <= MAX_INTRO_BARS
        ):
            label = "outro"
        sections.append(
            Section(
                label=label,
                start=bars[run[0]][0],
                end=bars[run[-1]][1],
                energy_db=sum(energy[i] for i in run) / len(run),
            )
        )
    return tuple(sections)


def sections_from_labels(
    raw: Sequence[Mapping[str, Any]], duration: float
) -> tuple[Section, ...]:
    """Caller-supplied sections, clamped to the song and gap-filled with verses.

    >>> [(s.label, s.start, s.end) for s in sections_from_labels(
    ...     [{'label': 'chorus', 'start': 4, 'end': 8}], 10.0)]
    [('verse', 0.0, 4.0), ('chorus', 4.0, 8.0), ('verse', 8.0, 10.0)]
    """
    spans = []
    for item in raw:
        item = dict(item)
        start = max(0.0, float(item.get("start", 0.0)))
        end = min(duration, float(item.get("end", duration)))
        if end - start <= 1e-6:
            continue
        spans.append((start, end, str(item.get("label", "verse")) or "verse"))
    spans.sort()
    out: list[Section] = []
    cursor = 0.0
    for start, end, label in spans:
        start = max(start, cursor)
        if end - start <= 1e-6:
            continue
        if start - cursor > 1e-6:
            out.append(Section(label="verse", start=cursor, end=start))
        out.append(Section(label=label, start=start, end=end))
        cursor = end
    if duration - cursor > 1e-6:
        out.append(Section(label="verse", start=cursor, end=duration))
    return tuple(out)


# --------------------------------------------------------------------------
# the one call
# --------------------------------------------------------------------------


def analyze(
    audio: Path | str,
    *,
    beats: str = "auto",
    beats_per_bar: int = DEFAULT_BEATS_PER_BAR,
    sections: Sequence[Mapping[str, Any]] | None = None,
) -> Analysis:
    """Measure ``audio``: duration, beat grid, downbeats, bar energy, sections."""
    import numpy as np

    from muvid.visualize.ffmpeg import media_duration

    audio = Path(audio)
    if not audio.exists():
        raise FileNotFoundError(f"audio not found: {audio}")
    duration = media_duration(audio)
    beat_times, tempo, onset_env, hop_s, source_name, note = beat_grid(
        audio, source=beats, duration=duration
    )
    notes = [note] if note else []
    beat_list = [float(t) for t in beat_times]
    if len(beat_list) >= 4:
        # The tempo the grid actually has, not the tracker's rounded estimate:
        # librosa reports 117 or 123 BPM for a 120 BPM click train whose beats
        # it placed correctly (its beats sit on a 23 ms frame grid, so the gaps
        # alternate 0.488/0.511 s and the MEDIAN gap is one of the two). The
        # mean of the gaps near the median is the honest figure, and beat_s
        # (crossfade and punch lengths) reads this.
        gaps = sorted(b - a for a, b in zip(beat_list, beat_list[1:]))
        median_gap = gaps[len(gaps) // 2]
        near = [g for g in gaps if 0.75 * median_gap <= g <= 1.25 * median_gap]
        if near and median_gap > 0:
            tempo = 60.0 / (sum(near) / len(near))
    onset_env = np.asarray(onset_env, dtype=float)
    idx = np.clip(
        np.round(np.asarray(beat_list) / max(hop_s, 1e-9)).astype(int),
        0,
        max(0, len(onset_env) - 1),
    )
    onset_at_beats = onset_env[idx].tolist() if len(onset_env) and beat_list else []
    downbeats = downbeats_from_beats(beat_list, onset_at_beats, beats_per_bar)
    if beat_list and duration > 0:
        covered = (beat_list[-1] - beat_list[0]) / duration
        if covered < 0.5:
            # A tracker that only found beats in one stretch (librosa on a
            # sparse signal) leaves the rest of the song as one bar. Say so.
            notes.append(
                f"beat grid covers {covered:.0%} of the song "
                f"({beat_list[0]:.1f}s-{beat_list[-1]:.1f}s); sections outside it are coarse"
            )

    x = _pcm(audio, ANALYSIS_SR)
    bars = _bars(downbeats, duration)
    energy = _bar_energy_db(x, ANALYSIS_SR, bars)
    if sections:
        secs = sections_from_labels(sections, duration)
        section_source = "supplied"
    else:
        secs = derive_sections(energy, bars, duration)
        section_source = "energy"
    return Analysis(
        duration=duration,
        tempo_bpm=float(tempo),
        beats=tuple(beat_list),
        downbeats=tuple(float(t) for t in downbeats),
        beats_per_bar=int(beats_per_bar),
        sections=secs,
        beat_source=source_name,
        section_source=section_source,
        bar_energy_db=tuple(energy),
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------
# media
# --------------------------------------------------------------------------


def _luma_contrast(path: Path, *, at_s: float | None) -> float:
    """Std-dev of luma on a small grey thumbnail, 0..1. ffmpeg decodes, numpy measures."""
    import numpy as np

    from muvid.visualize.ffmpeg import _run_bounded

    seek = ["-ss", f"{at_s:.3f}"] if at_s else []
    proc = _run_bounded(
        [
            "ffmpeg",
            "-v",
            "error",
            *seek,
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={STRENGTH_THUMB}:{STRENGTH_THUMB}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        text=False,
    )
    raw = proc.stdout or b""
    if not raw:
        return 0.0
    return float(np.frombuffer(raw, dtype=np.uint8).astype(np.float64).std() / 128.0)


def strength_of(width: int, height: int, contrast: float) -> float:
    """The closed-form strength: log10 pixel count plus contrast.

    >>> round(strength_of(1920, 1080, 0.3), 3)
    6.617
    """
    return math.log10(max(1, width * height)) + contrast


def probe_media(
    paths: Sequence[Path | str], *, kind: str, start_index: int = 0
) -> tuple[Media, ...]:
    """Measure each file: dimensions, duration (clips) and strength.

    A file with no video stream is refused with its path in the message — a
    montage that silently skipped a photo would be a different montage.
    """
    from muvid.visualize.ffmpeg import probe

    out = []
    for i, p in enumerate(paths):
        path = Path(p)
        if not path.exists():
            raise FileNotFoundError(f"{kind} not found: {path}")
        info = probe(path)
        vstream = next(
            (s for s in info.get("streams", []) if s.get("codec_type") == "video"), None
        )
        if vstream is None:
            raise ValueError(f"{kind} {path} has no video stream")
        width, height = int(vstream.get("width") or 0), int(vstream.get("height") or 0)
        if width <= 0 or height <= 0:
            raise ValueError(f"{kind} {path} has no usable dimensions")
        duration = None
        if kind == "clip":
            d = info.get("format", {}).get("duration") or vstream.get("duration")
            duration = float(d) if d else None
            if not duration or duration <= 0:
                raise ValueError(f"clip {path} has no duration")
        contrast = _luma_contrast(
            path, at_s=(duration * CLIP_SAMPLE_AT) if duration else None
        )
        out.append(
            Media(
                index=start_index + i,
                path=str(path),
                kind=kind,
                width=width,
                height=height,
                duration=duration,
                strength=strength_of(width, height, contrast),
            )
        )
    return tuple(out)
