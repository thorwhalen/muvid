# muvid.choreo.analysis

Turn a song into an EVENT LIST — the musical facts every archetype draws from.

The visualizer ([`muvid.visualize`](muvid.visualize.html.md#module-muvid.visualize)) reads the spectrum continuously; this
subgenre needs something discrete instead: \*when did something happen, in which
register, and how hard\*. So the analysis produces

* **events** `[{t, band, strength}]` — onsets in three bands (low/mid/high),
  from a half-wave-rectified spectral flux with an adaptive threshold,
  **hysteresis** and a \*\*minimum inter-onset interval derived from the beat
  grid\*\*, so a noisy attack triggers once rather than flickering;
* a **beat grid** (tempo + beat times) — from `mixing.audio.beat_grid` when
  librosa is installed (the `scoring` extra), else a built-in autocorrelation
  of the onset envelope, and the result says which;
* coarse **sections** from energy — contiguous bar-blocks of similar loudness,
  labelled `low` / `mid` / `high` by tier so a treatment can say “the loud
  parts get the Fischinger grid”.

Everything is a function of the samples and the parameters: no randomness, no
network, no model. The output is JSON-able and is written next to the video as
`events.json` so a render can be inspected — or re-choreographed — without
re-analysing the song.

numpy is imported inside functions only: this module sits on the listing path
of [`muvid.choreo.tools`](muvid.choreo.tools.html.md#module-muvid.choreo.tools), and the subprocess import-safety tests keep the
manifest side of the package free of it.

### Module Attributes

| [`BANDS`](#muvid.choreo.analysis.BANDS)   | The three registers, as (low_hz, high_hz].   |
|----------------------------------------------------------|----------------------------------------------|

### Functions

| [`analyze`](#muvid.choreo.analysis.analyze)(audio, \*[, sample_rate, ...])             | Analyse a song (a path, or an already-decoded mono array at `sample_rate`).   |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`beat_grid_numpy`](#muvid.choreo.analysis.beat_grid_numpy)(envelope, \*, hop_s, duration)     | Tempo and beat times from the autocorrelation of an onset envelope.           |
| [`find_sections`](#muvid.choreo.analysis.find_sections)(y, \*, sample_rate, tempo, duration) | Coarse sections from per-bar RMS energy, labelled by loudness tier.           |
| [`onset_envelope`](#muvid.choreo.analysis.onset_envelope)(flux, \*[, weights])                | One envelope for the beat tracker: each band normalised, then weighted.       |
| [`pick_onsets`](#muvid.choreo.analysis.pick_onsets)(flux, \*, hop_s, min_ioi_s[, ...])     | Onset `(time, strength)` pairs from one band's flux.                          |

### Classes

| [`Analysis`](#muvid.choreo.analysis.Analysis)(\*, duration, tempo, events, sections)   | Everything an archetype needs, and the JSON artifact a render leaves behind.   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`Event`](#muvid.choreo.analysis.Event)(\*, t, band, strength)                      | One onset: when, in which band, how hard (0..1, band-relative).                |
| [`Section`](#muvid.choreo.analysis.Section)(\*, index, label, start, end, energy_db)  | A contiguous stretch of similar loudness.                                      |
| [`Tempo`](#muvid.choreo.analysis.Tempo)(\*, bpm, beats, source)                     | The beat grid, and where it came from (`"mixing"` or `"numpy"`).               |

### *class* muvid.choreo.analysis.Analysis(\*, duration, tempo, events, sections, bands=<factory>, meta=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything an archetype needs, and the JSON artifact a render leaves behind.

```pycon
>>> a = Analysis(duration=2.0, tempo=Tempo(bpm=120.0, beats=(0.0, 0.5), source='numpy'),
...              events=(Event(t=0.0, band='low', strength=1.0),),
...              sections=(Section(index=0, label='mid', start=0.0, end=2.0, energy_db=-12.0),))
>>> Analysis.from_dict(a.to_dict()) == a
True
```

#### events_in(start, end, , band=None)

Events with `start <= t < end` (optionally one band).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Event`](#muvid.choreo.analysis.Event)]

### muvid.choreo.analysis.BANDS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[float](https://docs.python.org/3/builtins/functions.html#float), [float](https://docs.python.org/3/builtins/functions.html#float)]]* *= {'high': (2000.0, 11025.0), 'low': (20.0, 200.0), 'mid': (200.0, 2000.0)}*

The three registers, as (low_hz, high_hz]. `high` runs to Nyquist.

### *class* muvid.choreo.analysis.Event(, t, band, strength)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One onset: when, in which band, how hard (0..1, band-relative).

### *class* muvid.choreo.analysis.Section(, index, label, start, end, energy_db)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A contiguous stretch of similar loudness. `label` is its tier.

### *class* muvid.choreo.analysis.Tempo(, bpm, beats, source)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The beat grid, and where it came from (`"mixing"` or `"numpy"`).

#### *property* period *: [float](https://docs.python.org/3/builtins/functions.html#float)*

Seconds per beat.

### muvid.choreo.analysis.analyze(audio, , sample_rate=22050, beat_source='auto', bands={'high': (2000.0, 11025.0), 'low': (20.0, 200.0), 'mid': (200.0, 2000.0)}, min_ioi_beats={'high': 0.25, 'low': 0.5, 'mid': 0.25})

Analyse a song (a path, or an already-decoded mono array at `sample_rate`).

An array input never consults `mixing` (it needs a file), so its beat
grid is always the numpy one; this is the path unit tests use.

* **Return type:**
  [`Analysis`](#muvid.choreo.analysis.Analysis)

### muvid.choreo.analysis.beat_grid_numpy(envelope, , hop_s, duration, bpm_min=60.0, bpm_max=200.0, prior_bpm=120.0)

Tempo and beat times from the autocorrelation of an onset envelope.

The lag with the strongest (prior-weighted) autocorrelation in
`[60/bpm_max, 60/bpm_min]` is the beat period; the phase is the offset
whose comb picks up the most envelope. A constant grid — no tracking of
tempo drift — which is the honest floor for a dependency-free estimate,
and enough for the inter-onset interval and the section bars it serves.

* **Return type:**
  [`Tempo`](#muvid.choreo.analysis.Tempo)

```pycon
>>> import numpy as np
>>> env = np.zeros(2000, np.float32); env[::50] = 1.0     # a click every 0.5 s
>>> t = beat_grid_numpy(env, hop_s=0.01, duration=20.0)
>>> round(t.bpm), t.source, round(t.beats[1] - t.beats[0], 2)
(120, 'numpy', 0.5)
```

### muvid.choreo.analysis.find_sections(y, , sample_rate, tempo, duration, beats_per_bar=4, min_bars=4, step_db=3.0)

Coarse sections from per-bar RMS energy, labelled by loudness tier.

Greedy: a section absorbs the next bar unless the level of the *following*
`min_bars` bars sits more than `step_db` from the section’s mean, and
the section is already `min_bars` long. Sections tile `[0, duration]`
exactly and there is always at least one.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Section`](#muvid.choreo.analysis.Section), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

```pycon
>>> import numpy as np
>>> sr = 8000; y = np.concatenate([np.full(sr * 8, 0.05), np.full(sr * 8, 0.5)])
>>> secs = find_sections(y, sample_rate=sr, tempo=Tempo(bpm=120, beats=(), source='numpy'), duration=16.0)
>>> [(s.label, s.start, s.end) for s in secs]
[('low', 0.0, 8.0), ('high', 8.0, 16.0)]
```

### muvid.choreo.analysis.onset_envelope(flux, , weights={'high': 0.3, 'low': 1.0, 'mid': 0.6})

One envelope for the beat tracker: each band normalised, then weighted.

Per-band normalisation (by the band’s own 95th percentile) stops the
broadband high band — hundreds of bins against the low band’s handful —
from owning the sum; the weights then let the bass lead.

### muvid.choreo.analysis.pick_onsets(flux, , hop_s, min_ioi_s, window_s=0.5, delta=0.35, hysteresis=0.5)

Onset `(time, strength)` pairs from one band’s flux.

A local maximum triggers when it exceeds `median(window) + delta·scale`
and the flux has dropped back below the hysteresis level since the last
trigger (so one attack is one event). Inside `min_ioi_s` of the previous
trigger a *stronger* peak replaces it and a weaker one is dropped — a weak
early trigger must not block the real hit a few frames later. Strength is
the peak’s height over the 95th percentile of all peak heights, clipped
to 1, so the loudest hits read as 1 and a click reads as small.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float)]]

```pycon
>>> import numpy as np
>>> f = np.zeros(200, np.float32); f[[20, 24, 100]] = [1.0, 0.9, 0.5]
>>> [round(t, 3) for t, _ in pick_onsets(f, hop_s=0.01, min_ioi_s=0.1)]
[0.2, 1.0]
>>> f[[20, 24]] = [0.3, 1.0]     # now the STRONGER hit is the later one
>>> [round(t, 3) for t, _ in pick_onsets(f, hop_s=0.01, min_ioi_s=0.1)]
[0.24, 1.0]
```
