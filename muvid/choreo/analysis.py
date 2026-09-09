"""Turn a song into an EVENT LIST — the musical facts every archetype draws from.

The visualizer (:mod:`muvid.visualize`) reads the spectrum continuously; this
subgenre needs something discrete instead: *when did something happen, in which
register, and how hard*. So the analysis produces

* **events** ``[{t, band, strength}]`` — onsets in three bands (low/mid/high),
  from a half-wave-rectified spectral flux with an adaptive threshold,
  **hysteresis** and a **minimum inter-onset interval derived from the beat
  grid**, so a noisy attack triggers once rather than flickering;
* a **beat grid** (tempo + beat times) — from ``mixing.audio.beat_grid`` when
  librosa is installed (the ``scoring`` extra), else a built-in autocorrelation
  of the onset envelope, and the result says which;
* coarse **sections** from energy — contiguous bar-blocks of similar loudness,
  labelled ``low`` / ``mid`` / ``high`` by tier so a treatment can say "the loud
  parts get the Fischinger grid".

Everything is a function of the samples and the parameters: no randomness, no
network, no model. The output is JSON-able and is written next to the video as
``events.json`` so a render can be inspected — or re-choreographed — without
re-analysing the song.

numpy is imported inside functions only: this module sits on the listing path
of :mod:`muvid.choreo.tools`, and the subprocess import-safety tests keep the
manifest side of the package free of it.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "Analysis",
    "Event",
    "Section",
    "Tempo",
    "BANDS",
    "analyze",
    "beat_grid_numpy",
    "find_sections",
    "onset_envelope",
    "pick_onsets",
]

#: Analysis sample rate. Onsets and tempo do not need full bandwidth, and the
#: decode + STFT of a 15-minute song at this rate stays well under a second.
SAMPLE_RATE = 22050
#: STFT frame and hop, in samples at :data:`SAMPLE_RATE` (46 ms / 11.6 ms).
FRAME = 1024
HOP = 256

#: The three registers, as (low_hz, high_hz]. ``high`` runs to Nyquist.
BANDS: dict[str, tuple[float, float]] = {
    "low": (20.0, 200.0),
    "mid": (200.0, 2000.0),
    "high": (2000.0, SAMPLE_RATE / 2),
}

#: Minimum inter-onset interval per band, in BEATS. A kick does not repeat
#: within half a beat; hats do. These become seconds once the tempo is known,
#: which is what "snapped to the beat grid" means here.
MIN_IOI_BEATS: dict[str, float] = {"low": 0.5, "mid": 0.25, "high": 0.25}
#: An absolute floor under the beat-derived interval, for absurd tempi.
MIN_IOI_S = 0.05

#: Log compression gain, applied to magnitudes normalised by the song's own
#: 99.9th percentile — so the events found do not depend on the master's level.
LOG_GAIN = 200.0

#: Adaptive threshold: local median over this window, plus ``DELTA`` times the
#: band's own robust scale.
THRESHOLD_WINDOW_S = 0.5
DELTA = 0.35

#: How much each band's flux counts toward the beat grid. The beat is where
#: the bass is; hats subdivide it and must not pull the phase to the off-beat.
BAND_WEIGHTS: dict[str, float] = {"low": 1.0, "mid": 0.6, "high": 0.3}
#: Hysteresis: after a trigger the detector re-arms only once the flux drops
#: below this fraction of the excess threshold. 0 disables it.
HYSTERESIS = 0.5

#: Tempo search range and prior (log-normal around ``PRIOR_BPM``, like librosa's).
BPM_MIN, BPM_MAX, PRIOR_BPM = 60.0, 200.0, 120.0
PRIOR_STD_OCTAVES = 1.0

#: Sections: energy is measured per bar of this many beats, a section is at
#: least ``MIN_SECTION_BARS`` long, and a new one starts when the level moves by
#: more than ``SECTION_STEP_DB``.
BEATS_PER_BAR = 4
MIN_SECTION_BARS = 4
SECTION_STEP_DB = 3.0

SECTION_TIERS = ("low", "mid", "high")


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """One onset: when, in which band, how hard (0..1, band-relative)."""

    t: float
    band: str
    strength: float


@dataclass(frozen=True, slots=True, kw_only=True)
class Tempo:
    """The beat grid, and where it came from (``"mixing"`` or ``"numpy"``)."""

    bpm: float
    beats: tuple[float, ...]
    source: str

    @property
    def period(self) -> float:
        """Seconds per beat."""
        return 60.0 / self.bpm if self.bpm > 0 else 0.5


@dataclass(frozen=True, slots=True, kw_only=True)
class Section:
    """A contiguous stretch of similar loudness. ``label`` is its tier."""

    index: int
    label: str
    start: float
    end: float
    energy_db: float


@dataclass(frozen=True, slots=True, kw_only=True)
class Analysis:
    """Everything an archetype needs, and the JSON artifact a render leaves behind.

    >>> a = Analysis(duration=2.0, tempo=Tempo(bpm=120.0, beats=(0.0, 0.5), source='numpy'),
    ...              events=(Event(t=0.0, band='low', strength=1.0),),
    ...              sections=(Section(index=0, label='mid', start=0.0, end=2.0, energy_db=-12.0),))
    >>> Analysis.from_dict(a.to_dict()) == a
    True
    """

    duration: float
    tempo: Tempo
    events: tuple[Event, ...]
    sections: tuple[Section, ...]
    bands: Mapping[str, tuple[float, float]] = field(
        default_factory=lambda: dict(BANDS)
    )
    meta: Mapping[str, Any] = field(default_factory=dict)

    def events_in(self, start: float, end: float, *, band: str | None = None) -> list[Event]:
        """Events with ``start <= t < end`` (optionally one band)."""
        return [
            e for e in self.events
            if start <= e.t < end and (band is None or e.band == band)
        ]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bands"] = {k: list(v) for k, v in self.bands.items()}
        d["meta"] = dict(self.meta)
        return json.loads(json.dumps(d))

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Analysis":
        tempo = d.get("tempo") or {}
        return cls(
            duration=float(d["duration"]),
            tempo=Tempo(
                bpm=float(tempo.get("bpm", 0.0)),
                beats=tuple(float(b) for b in tempo.get("beats", ())),
                source=str(tempo.get("source", "unknown")),
            ),
            events=tuple(
                Event(t=float(e["t"]), band=str(e["band"]), strength=float(e["strength"]))
                for e in d.get("events", ())
            ),
            sections=tuple(
                Section(
                    index=int(s["index"]), label=str(s["label"]),
                    start=float(s["start"]), end=float(s["end"]),
                    energy_db=float(s["energy_db"]),
                )
                for s in d.get("sections", ())
            ),
            bands={k: (float(v[0]), float(v[1])) for k, v in (d.get("bands") or BANDS).items()},
            meta=dict(d.get("meta") or {}),
        )


# --------------------------------------------------------------------------
# signal -> flux
# --------------------------------------------------------------------------


def load_mono(audio: Path | str, *, sample_rate: int = SAMPLE_RATE):
    """Decode ``audio`` to a mono float32 array through muvid's one PCM path.

    Raises:
        ValueError: ffmpeg could not decode the file (``decode_pcm`` returns
            empty bytes rather than raising, and "no usable audio" is a
            refusal here — a silent video is not a render).
    """
    import numpy as np

    from muvid.visualize.ffmpeg import decode_pcm

    raw = decode_pcm(audio, sample_rate=sample_rate, channels=1)
    if not raw:
        raise ValueError(f"could not decode any audio from {audio!s}")
    return np.frombuffer(raw, dtype=np.float32).copy()


def stft_magnitude(y, *, frame: int = FRAME, hop: int = HOP):
    """``(magnitude[n_frames, n_bins], bin_hz_per_bin)`` with frame ``i`` centred at ``i*hop``."""
    import numpy as np

    y = np.asarray(y, dtype=np.float32)
    pad = frame // 2
    y = np.pad(y, (pad, pad))
    if len(y) < frame:
        y = np.pad(y, (0, frame - len(y)))
    n_frames = 1 + (len(y) - frame) // hop
    frames = np.lib.stride_tricks.sliding_window_view(y, frame)[::hop][:n_frames]
    window = np.hanning(frame).astype(np.float32)
    mag = np.abs(np.fft.rfft(frames * window, axis=1)).astype(np.float32)
    return mag, 1.0  # caller derives Hz per bin from the sample rate


def band_flux(mag, *, sample_rate: int = SAMPLE_RATE, frame: int = FRAME,
              bands: Mapping[str, tuple[float, float]] = BANDS) -> dict[str, Any]:
    """Half-wave-rectified log-spectral flux, summed per band, one array per band.

    Log compression first (``log1p(gain * |X|)``) so a loud passage does not
    swamp the onsets inside a quiet one; rectification so only *increases*
    count, which is what an onset is.
    """
    import numpy as np

    n_bins = mag.shape[1]
    freqs = np.arange(n_bins) * (sample_rate / frame)
    ref = float(np.percentile(mag, 99.9)) or 1.0
    logmag = np.log1p(LOG_GAIN * mag / ref)
    diff = np.diff(logmag, axis=0, prepend=logmag[:1])
    rect = np.maximum(diff, 0.0)
    out: dict[str, Any] = {}
    for name, (lo, hi) in bands.items():
        sel = (freqs > lo) & (freqs <= hi)
        flux = rect[:, sel].sum(axis=1) if sel.any() else np.zeros(len(rect), np.float32)
        out[name] = flux.astype(np.float32)
    return out


def onset_envelope(flux: Mapping[str, Any], *, weights: Mapping[str, float] = BAND_WEIGHTS):
    """One envelope for the beat tracker: each band normalised, then weighted.

    Per-band normalisation (by the band's own 95th percentile) stops the
    broadband high band — hundreds of bins against the low band's handful —
    from owning the sum; the weights then let the bass lead.
    """
    import numpy as np

    total = None
    for name, f in flux.items():
        scale = float(np.percentile(f, 95)) or float(f.max()) or 1.0
        part = (f / scale) * float(weights.get(name, 1.0))
        total = part if total is None else total + part
    return total if total is not None else np.zeros(0, np.float32)


# --------------------------------------------------------------------------
# flux -> onsets
# --------------------------------------------------------------------------


def _running_median(x, half: int):
    """Median over ``[i-half, i+half]`` (edge-clamped). Pure numpy, O(n·w log w)."""
    import numpy as np

    n = len(x)
    if n == 0:
        return x
    padded = np.pad(x, (half, half), mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
    return np.median(win, axis=1).astype(np.float32)


def pick_onsets(
    flux,
    *,
    hop_s: float,
    min_ioi_s: float,
    window_s: float = THRESHOLD_WINDOW_S,
    delta: float = DELTA,
    hysteresis: float = HYSTERESIS,
) -> list[tuple[float, float]]:
    """Onset ``(time, strength)`` pairs from one band's flux.

    A local maximum triggers when it exceeds ``median(window) + delta·scale``
    and the flux has dropped back below the hysteresis level since the last
    trigger (so one attack is one event). Inside ``min_ioi_s`` of the previous
    trigger a *stronger* peak replaces it and a weaker one is dropped — a weak
    early trigger must not block the real hit a few frames later. Strength is
    the peak's height over the 95th percentile of all peak heights, clipped
    to 1, so the loudest hits read as 1 and a click reads as small.

    >>> import numpy as np
    >>> f = np.zeros(200, np.float32); f[[20, 24, 100]] = [1.0, 0.9, 0.5]
    >>> [round(t, 3) for t, _ in pick_onsets(f, hop_s=0.01, min_ioi_s=0.1)]
    [0.2, 1.0]
    >>> f[[20, 24]] = [0.3, 1.0]     # now the STRONGER hit is the later one
    >>> [round(t, 3) for t, _ in pick_onsets(f, hop_s=0.01, min_ioi_s=0.1)]
    [0.24, 1.0]
    """
    import numpy as np

    flux = np.asarray(flux, dtype=np.float32)
    n = len(flux)
    if n < 3:
        return []
    half = max(1, int(round(window_s / hop_s / 2)))
    med = _running_median(flux, half)
    scale = float(np.percentile(flux, 95)) or 1.0
    high = med + delta * scale
    low = med + delta * hysteresis * scale
    is_peak = np.zeros(n, dtype=bool)
    is_peak[1:-1] = (flux[1:-1] > flux[:-2]) & (flux[1:-1] >= flux[2:]) & (flux[1:-1] > high[1:-1])
    candidates = np.flatnonzero(is_peak)
    if len(candidates) == 0:
        return []
    min_gap = max(1, int(round(min_ioi_s / hop_s)))
    below = flux < low
    peaks = [(int(i), float(flux[i])) for i in candidates]
    height_ref = float(np.percentile([h for _, h in peaks], 95)) or 1.0
    out: list[list[float]] = []  # [frame, height], mutable so a stronger hit can replace
    armed = True
    i_prev = 0
    for i, h in peaks:
        # re-arm once the flux dipped under the low threshold since the last candidate
        if not armed and below[i_prev:i].any():
            armed = True
        i_prev = i
        if not armed:
            continue  # the same attack, still rising
        if out and i - out[-1][0] < min_gap:
            if h > out[-1][1]:
                out[-1] = [i, h]
                armed = hysteresis <= 0
            continue
        out.append([i, h])
        armed = hysteresis <= 0
    return [(i * hop_s, min(1.0, h / height_ref)) for i, h in out]


# --------------------------------------------------------------------------
# onset envelope -> beat grid
# --------------------------------------------------------------------------


def beat_grid_numpy(
    envelope,
    *,
    hop_s: float,
    duration: float,
    bpm_min: float = BPM_MIN,
    bpm_max: float = BPM_MAX,
    prior_bpm: float = PRIOR_BPM,
) -> Tempo:
    """Tempo and beat times from the autocorrelation of an onset envelope.

    The lag with the strongest (prior-weighted) autocorrelation in
    ``[60/bpm_max, 60/bpm_min]`` is the beat period; the phase is the offset
    whose comb picks up the most envelope. A constant grid — no tracking of
    tempo drift — which is the honest floor for a dependency-free estimate,
    and enough for the inter-onset interval and the section bars it serves.

    >>> import numpy as np
    >>> env = np.zeros(2000, np.float32); env[::50] = 1.0     # a click every 0.5 s
    >>> t = beat_grid_numpy(env, hop_s=0.01, duration=20.0)
    >>> round(t.bpm), t.source, round(t.beats[1] - t.beats[0], 2)
    (120, 'numpy', 0.5)
    """
    import numpy as np

    env = np.asarray(envelope, dtype=np.float64)
    env = env - env.mean()
    n = len(env)
    lag_min = max(1, int(round(60.0 / bpm_max / hop_s)))
    lag_max = int(round(60.0 / bpm_min / hop_s))
    if n < 2 * lag_min + 2 or float(np.abs(env).sum()) == 0.0:
        return _fixed_grid(prior_bpm, duration, source="numpy")
    lag_max = min(lag_max, n // 2)
    size = 1 << int(math.ceil(math.log2(2 * n)))
    spec = np.fft.rfft(env, size)
    ac = np.fft.irfft(spec * np.conj(spec), size)[: lag_max + 1]
    ac = ac / (ac[0] or 1.0)
    lags = np.arange(lag_min, lag_max + 1)
    bpms = 60.0 / (lags * hop_s)
    prior = np.exp(-0.5 * (np.log2(bpms / prior_bpm) / PRIOR_STD_OCTAVES) ** 2)
    score = ac[lag_min:lag_max + 1] * prior
    best = int(lags[int(np.argmax(score))])
    period_s = best * hop_s
    # phase: the comb offset that collects the most (rectified) envelope
    pos = np.maximum(np.asarray(envelope, dtype=np.float64), 0.0)
    sums = [pos[k::best].sum() for k in range(best)]
    phase = int(np.argmax(sums)) * hop_s
    bpm = 60.0 / period_s
    beats = tuple(float(phase + k * period_s) for k in range(int((duration - phase) / period_s) + 1)
                  if phase + k * period_s < duration)
    return Tempo(bpm=float(bpm), beats=beats, source="numpy")


def _fixed_grid(bpm: float, duration: float, *, source: str) -> Tempo:
    period = 60.0 / bpm
    return Tempo(bpm=bpm, beats=tuple(k * period for k in range(int(duration / period) + 1)
                                       if k * period < duration), source=source)


def beat_grid_mixing(audio: Path | str, *, sample_rate: int = SAMPLE_RATE) -> Tempo:
    """The beat grid from ``mixing.audio.beat_grid`` (librosa, ISC).

    Signature found: ``beat_grid(audio, *, sample_rate=22050, hop_length=512,
    start_bpm=120.0, backend='librosa') -> BeatGrid`` with fields
    ``beat_times, downbeat_times, onset_env, onset_hop_s, sample_rate,
    tempo_bpm``. It decodes the path itself (through pydub), so it is handed
    the path rather than our samples.

    Raises:
        ImportError: librosa is not installed — the ONLY thing ``auto`` catches.
            Any other failure inside ``mixing`` propagates; a swallowed one
            would be a plausible-looking wrong grid, the muvid#15 shape.
    """
    from mixing.audio import beat_grid

    grid = beat_grid(str(audio), sample_rate=sample_rate)
    return Tempo(
        bpm=float(grid.tempo_bpm),
        beats=tuple(float(b) for b in grid.beat_times),
        source="mixing",
    )


def resolve_tempo(
    audio: Path | str | None,
    envelope,
    *,
    hop_s: float,
    duration: float,
    source: str = "auto",
) -> Tempo:
    """The beat grid from ``source``: ``mixing`` | ``numpy`` | ``auto``.

    ``auto`` prefers ``mixing`` and falls back to ``numpy`` on ``ImportError``
    only. ``mixing`` never falls back. A grid ``mixing`` returns with no tempo
    (silence) is replaced by the numpy estimate and recorded as such.
    """
    if source not in ("auto", "mixing", "numpy"):
        raise ValueError(f"beat_source must be auto|mixing|numpy, got {source!r}")
    if source in ("auto", "mixing") and audio is not None:
        try:
            tempo = beat_grid_mixing(audio)
        except ImportError:
            if source == "mixing":
                raise
            tempo = None
        if tempo is not None and tempo.bpm > 0 and len(tempo.beats) >= 2:
            return tempo
    return beat_grid_numpy(envelope, hop_s=hop_s, duration=duration)


# --------------------------------------------------------------------------
# energy -> sections
# --------------------------------------------------------------------------


def find_sections(
    y,
    *,
    sample_rate: int,
    tempo: Tempo,
    duration: float,
    beats_per_bar: int = BEATS_PER_BAR,
    min_bars: int = MIN_SECTION_BARS,
    step_db: float = SECTION_STEP_DB,
) -> tuple[Section, ...]:
    """Coarse sections from per-bar RMS energy, labelled by loudness tier.

    Greedy: a section absorbs the next bar unless the level of the *following*
    ``min_bars`` bars sits more than ``step_db`` from the section's mean, and
    the section is already ``min_bars`` long. Sections tile ``[0, duration]``
    exactly and there is always at least one.

    >>> import numpy as np
    >>> sr = 8000; y = np.concatenate([np.full(sr * 8, 0.05), np.full(sr * 8, 0.5)])
    >>> secs = find_sections(y, sample_rate=sr, tempo=Tempo(bpm=120, beats=(), source='numpy'), duration=16.0)
    >>> [(s.label, s.start, s.end) for s in secs]
    [('low', 0.0, 8.0), ('high', 8.0, 16.0)]
    """
    import numpy as np

    y = np.asarray(y, dtype=np.float32)
    bar_s = tempo.period * beats_per_bar
    n_bars = max(1, int(math.ceil(duration / bar_s)))
    levels = []
    for b in range(n_bars):
        seg = y[int(b * bar_s * sample_rate): int(min(duration, (b + 1) * bar_s) * sample_rate)]
        rms = float(np.sqrt(np.mean(seg * seg))) if len(seg) else 0.0
        levels.append(20.0 * math.log10(max(rms, 1e-6)))
    lv = np.asarray(levels)

    bounds = [0]
    cur_start = 0
    for b in range(1, n_bars):
        if b - cur_start < min_bars or n_bars - b < min_bars:
            continue
        ahead = float(lv[b: b + min_bars].mean())
        here = float(lv[cur_start:b].mean())
        if abs(ahead - here) > step_db:
            bounds.append(b)
            cur_start = b
    bounds.append(n_bars)

    raw = []
    for i in range(len(bounds) - 1):
        b0, b1 = bounds[i], bounds[i + 1]
        raw.append((b0 * bar_s, min(duration, b1 * bar_s), float(lv[b0:b1].mean())))
    energies = sorted(e for _, _, e in raw)
    # tiers by tertile of the section energies; one section is 'mid'
    def tier(e: float) -> str:
        if len(energies) < 2 or energies[-1] - energies[0] < step_db:
            return "mid"
        lo, hi = np.percentile(energies, [33.4, 66.7])
        return "low" if e <= lo else "high" if e > hi else "mid"

    return tuple(
        Section(index=i, label=tier(e), start=round(s, 4), end=round(t, 4), energy_db=round(e, 2))
        for i, (s, t, e) in enumerate(raw)
    )


# --------------------------------------------------------------------------
# the one entry point
# --------------------------------------------------------------------------


def analyze(
    audio: Path | str | Any,
    *,
    sample_rate: int = SAMPLE_RATE,
    beat_source: str = "auto",
    bands: Mapping[str, tuple[float, float]] = BANDS,
    min_ioi_beats: Mapping[str, float] = MIN_IOI_BEATS,
) -> Analysis:
    """Analyse a song (a path, or an already-decoded mono array at ``sample_rate``).

    An array input never consults ``mixing`` (it needs a file), so its beat
    grid is always the numpy one; this is the path unit tests use.
    """
    import numpy as np

    path: Path | None
    if isinstance(audio, (str, Path)):
        path = Path(audio)
        y = load_mono(path, sample_rate=sample_rate)
    else:
        path = None
        y = np.asarray(audio, dtype=np.float32)
    duration = len(y) / float(sample_rate)
    hop_s = HOP / float(sample_rate)

    mag, _ = stft_magnitude(y)
    flux = band_flux(mag, sample_rate=sample_rate, bands=bands)
    envelope = onset_envelope(flux)
    tempo = resolve_tempo(path, envelope, hop_s=hop_s, duration=duration, source=beat_source)

    events: list[Event] = []
    for name, f in flux.items():
        ioi = max(MIN_IOI_S, tempo.period * float(min_ioi_beats.get(name, 0.25)))
        for t, s in pick_onsets(f, hop_s=hop_s, min_ioi_s=ioi):
            if t < duration:
                events.append(Event(t=round(float(t), 4), band=name, strength=round(float(s), 4)))
    events.sort(key=lambda e: (e.t, e.band))

    sections = find_sections(y, sample_rate=sample_rate, tempo=tempo, duration=duration)
    return Analysis(
        duration=round(duration, 4),
        tempo=Tempo(bpm=round(tempo.bpm, 3), beats=tuple(round(b, 4) for b in tempo.beats),
                    source=tempo.source),
        events=tuple(events),
        sections=sections,
        bands=dict(bands),
        meta={
            "sample_rate": sample_rate,
            "hop_s": round(hop_s, 6),
            "n_events": {name: sum(1 for e in events if e.band == name) for name in bands},
            "min_ioi_s": {name: round(max(MIN_IOI_S, tempo.period * float(min_ioi_beats.get(name, 0.25))), 4)
                          for name in bands},
        },
    )
