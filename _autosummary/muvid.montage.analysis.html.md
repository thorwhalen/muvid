# muvid.montage.analysis

Measure the song and the pool: beat grid, bars, sections, and media strength.

Everything the planner needs is MEASURED here, once, and handed over as plain
records — the planner ([`muvid.montage.plan`](muvid.montage.plan.html.md#module-muvid.montage.plan)) is pure and never touches a
file. Three measurements:

**Beat grid.** `mixing.audio.beat_grid` (librosa, ISC) is the house beat
backend and is used when it is importable; `mixing` is a core dependency but
its `[beats]` extra is not, so on a bare install this module falls back to
its own small numpy estimator — spectral-flux onset strength, an
autocorrelation tempo with a log-normal prior around 120 BPM, and a comb-filter
phase search — which yields a **fixed-tempo** grid. That is honest for the
material montages are cut to and it is recorded in `Analysis.beat_source` so
nobody mistakes it for a tracked grid. Only `ImportError` is caught, and only
on the `auto` path: a `mixing` regression must surface as a traceback, not
as a quietly different grid (the `mixing` rule in the design record).

**Bars and sections.** librosa has no downbeat tracker, so downbeats are the
beat phase (0..beats_per_bar-1) with the most onset energy — a closed-form vote.
Sections, when the caller does not supply them, are derived from per-bar RMS
energy: a stretch louder than the median is a chorus, quieter is a verse, the
leading and trailing quiet runs are intro/outro. Coarse, deterministic, and
labelled `section_source="energy"` so a caller knows what it got.

**Media strength.** A photo’s strength is a closed-form function of its pixel
count and luma contrast (a 64x64 grey thumbnail decoded by ffmpeg — no Pillow).
The reuse policy reserves the strongest for the final chorus.

Module scope is stdlib-only; numpy and ffmpeg are reached inside functions.

### Functions

| [`analyze`](#muvid.montage.analysis.analyze)(audio, \*[, beats, beats_per_bar, ...])    | Measure `audio`: duration, beat grid, downbeats, bar energy, sections.    |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`beat_grid`](#muvid.montage.analysis.beat_grid)(audio, \*[, source, duration])           | `(beats, tempo_bpm, onset_env, onset_hop_s, source_name, note)`.          |
| [`derive_sections`](#muvid.montage.analysis.derive_sections)(bar_energy_db, bars, duration, \*) | Coarse sections from per-bar energy.                                      |
| [`downbeats_from_beats`](#muvid.montage.analysis.downbeats_from_beats)(beats, onset_at_beats, ...)   | Downbeats as the beat phase carrying the most onset energy.               |
| [`probe_media`](#muvid.montage.analysis.probe_media)(paths, \*, kind[, start_index])        | Measure each file: dimensions, duration (clips) and strength.             |
| [`sections_from_labels`](#muvid.montage.analysis.sections_from_labels)(raw, duration)                | Caller-supplied sections, clamped to the song and gap-filled with verses. |

### Classes

| [`Analysis`](#muvid.montage.analysis.Analysis)(\*, duration, tempo_bpm, beats, ...)      | What the planner knows about the song.      |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------|
| [`Media`](#muvid.montage.analysis.Media)(\*, index, path, kind, width, height[, ...]) | One pool item, measured.                    |
| [`Section`](#muvid.montage.analysis.Section)(\*, label, start, end[, energy_db])        | A labelled stretch of the song, in seconds. |

### *class* muvid.montage.analysis.Analysis(\*, duration, tempo_bpm, beats, downbeats, beats_per_bar=4, sections=(), beat_source='', section_source='', bar_energy_db=(), notes=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What the planner knows about the song.

#### *property* beat_s *: [float](https://docs.python.org/3/builtins/functions.html#float)*

Seconds per beat at the estimated tempo.

### *class* muvid.montage.analysis.Media(, index, path, kind, width, height, duration=None, strength=0.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One pool item, measured.

`kind` is `photo`, `clip` or `cover`; `duration` is `None` for
a still; `strength` is the closed-form score the reuse policy ranks on.

### *class* muvid.montage.analysis.Section(, label, start, end, energy_db=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A labelled stretch of the song, in seconds.

#### energy_db *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)*

Mean bar energy in dB (relative), when measured.

### muvid.montage.analysis.analyze(audio, , beats='auto', beats_per_bar=4, sections=None)

Measure `audio`: duration, beat grid, downbeats, bar energy, sections.

* **Return type:**
  [`Analysis`](#muvid.montage.analysis.Analysis)

### muvid.montage.analysis.beat_grid(audio, , source='auto', duration=None)

`(beats, tempo_bpm, onset_env, onset_hop_s, source_name, note)`.

`source="auto"` tries `mixing.audio.beat_grid` and falls back to the
numpy estimator only on [`ImportError`](https://docs.python.org/3/builtins/exceptions.html#ImportError) (librosa absent). Naming a
source never falls back.

### muvid.montage.analysis.derive_sections(bar_energy_db, bars, duration, , min_section_bars=4)

Coarse sections from per-bar energy. Loud = chorus, quiet = verse.

The threshold splits the bar energies in two (`_two_means_threshold()`);
interior runs shorter than `min_section_bars` (scaled down for short
songs) are absorbed into the longer neighbour; a leading/trailing quiet run
of at most `MAX_INTRO_BARS` bars is an intro/outro. A dynamically
flat song is one verse — reported as such rather than invented.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Section`](#muvid.montage.analysis.Section), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

```pycon
>>> bars = [(i * 2.0, (i + 1) * 2.0) for i in range(8)]
>>> e = [-20, -20, -20, -20, -10, -10, -20, -20]
>>> [(s.label, s.start, s.end) for s in derive_sections(e, bars, 16.0)]
[('intro', 0.0, 8.0), ('chorus', 8.0, 12.0), ('outro', 12.0, 16.0)]
>>> [s.label for s in derive_sections([-20] * 8, bars, 16.0)]
['verse']
```

### muvid.montage.analysis.downbeats_from_beats(beats, onset_at_beats, beats_per_bar)

Downbeats as the beat phase carrying the most onset energy.

A closed-form vote over `beats_per_bar` candidate phases; ties go to
phase 0 (the first beat), which is also the answer when there is nothing
to vote with.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`float`](https://docs.python.org/3/builtins/functions.html#float), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

```pycon
>>> downbeats_from_beats([0, .5, 1, 1.5, 2, 2.5, 3, 3.5], [1, 0, 0, 0, 1, 0, 0, 0], 4)
(0, 2)
>>> downbeats_from_beats([0, .5, 1, 1.5, 2, 2.5, 3, 3.5], [0, 0, 1, 0, 0, 0, 1, 0], 4)
(1, 3)
```

### muvid.montage.analysis.probe_media(paths, , kind, start_index=0)

Measure each file: dimensions, duration (clips) and strength.

A file with no video stream is refused with its path in the message — a
montage that silently skipped a photo would be a different montage.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Media`](#muvid.montage.analysis.Media), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

### muvid.montage.analysis.sections_from_labels(raw, duration)

Caller-supplied sections, clamped to the song and gap-filled with verses.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Section`](#muvid.montage.analysis.Section), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

```pycon
>>> [(s.label, s.start, s.end) for s in sections_from_labels(
...     [{'label': 'chorus', 'start': 4, 'end': 8}], 10.0)]
[('verse', 0.0, 4.0), ('chorus', 4.0, 8.0), ('verse', 8.0, 10.0)]
```
