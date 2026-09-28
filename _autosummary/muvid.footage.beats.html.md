# muvid.footage.beats

Beat signals — continuous envelopes of where the beat is, in the song and in each video.

`beat_grid` answers *when* the song’s beats fall (instants). An editor lining a dancer’s
hits up with the music wants more than instants: a **continuous** signal it can look at,
threshold anywhere between its minimum and maximum, and bend towards binary. That is what
this module measures, per piece of media and in that media’s OWN time (a clip reaches song
time through its offset, exactly as filmstrips do).

Sound, for the song and for every clip with a soundtrack:

- `audio_onset` — the onset-strength envelope `mixing.audio.beat_grid` estimates beats
  from (librosa’s spectral flux on a log-mel spectrogram — the standard envelope, adequate
  for percussive pop; SuperFlux’s vibrato suppression matters for voice and strings). The
  estimator’s beat instants come along, with a tempo FITTED to them (see `fitted_tempo`).
- `novelty` (the song only) — how much the music changes character around each moment:
  Foote’s checkerboard novelty over a self-similarity matrix of timbre and harmony. Its
  peaks are section boundaries (verse, chorus, drop) — the other place an editor cuts.

Picture, for every clip:

- `motion` — subject-motion energy: the mean camera-compensated optical-flow magnitude,
  in frame-heights per second.
- `visual_impact` — the visual beat: how much motion, direction by direction, STOPS
  between samples — the half-wave-rectified decrease of a magnitude-weighted directogram.
  This is the “impact envelope” of Davis & Agrawala, *Visual Rhythm and Beat* (SIGGRAPH
  2018, §4.2; their printed Eq. 13 has the sign of an increase, their prose and released
  code the decrease used here). It is a deceleration measure, which is where the
  conducting literature puts the beat: ensembles synchronise with the \*\*maximal
  deceleration\*\* of the conductor’s hand (Luck & Toiviainen 2006), and with absolute
  acceleration along the trajectory (Luck & Sloboda 2009) — the *ictus*, not the moment a
  movement starts (Takehana et al. 2019: movement initiation never coincided with beats).
- `region_impact` — the same deceleration without the directogram: the decrease of
  camera-compensated SPEED in each cell of an 8 x 6 grid, summed over cells. Per-region
  rather than per-direction, so several dancers braking in different places add up
  instead of cancelling.

**What the evidence on real footage says** (three phone videos of a crowd dancing to one
song, 2 minutes each; beat locking measured as the phase concentration of each signal on
the song’s beat, against a null that shifts each 4 s block independently — a whole-signal
circular shift cannot detect locking at all, since it only rotates the phase):

- each clip’s own soundtrack locks strongly (z = 7 to 15) — the positive control, and the
  confirmation that the clips’ offsets are right;
- `visual_impact` and `region_impact` lock on one clip (z = 2.8 and 2.5, at the same
  -25 ms lag as that clip’s soundtrack) and on neither of the others; whole-frame speed,
  pose-based limb deceleration (a person found in only 38-75 % of frames of a crowd) and
  AIST++-style velocity minima (half a beat off) did no better;
- a 1.25 Hz high-pass (Davis & Agrawala’s post-filter) helped no clip consistently.

So these envelopes SHOW where movement lands; on a crowd they are weak evidence of the
beat, and nothing here decides a warp by itself.

Every signal is **unnormalised** and carries its own grid (`t0`, `hop_s`) and its
`min`, `max` and `p99` (a robust top a display can scale by), because thresholding is a
view the caller chooses, not something the data should have decided.

Cached per media content hash and parameters at `footage/beats/<hash>-<kind>-<key>.json`,
beside `peaks/` — derived from media the project holds, expensive to make, cheap to keep.

### Module Attributes

| [`SIGNAL_LABELS`](#muvid.footage.beats.SIGNAL_LABELS)   | What each signal is, in the words a screen can use.   |
|------------------------------------------------------------------|-------------------------------------------------------|

### Functions

| [`audio_signals`](#muvid.footage.beats.audio_signals)(path)                                | `{signals: {audio_onset}, beats, tempo_bpm}` for a media file's soundtrack, from `mixing.audio.beat_grid`.                                                                                                                                                                   |
|-----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`visual_signals`](#muvid.footage.beats.visual_signals)(path, \*[, sample_fps, ...])        | `{signals: {motion, visual_impact}}` for a video, in one decode pass.                                                                                                                                                                                                        |
| [`has_audio`](#muvid.footage.beats.has_audio)(path)                                    | Whether a media file carries an audio stream (an unprobeable file: no).                                                                                                                                                                                                      |
| [`fitted_tempo`](#muvid.footage.beats.fitted_tempo)(beats, \*[, min_beats])               | The tempo (BPM) of a steady beat train, fitted to ALL its beats — or `None` when the beats are too few or not steady enough to have one tempo.                                                                                                                               |
| [`novelty_signal`](#muvid.footage.beats.novelty_signal)(path)                               | `{signals: {novelty}}` — Foote's checkerboard novelty of a song: a Gaussian- tapered checkerboard kernel slid along the diagonal of the cosine self-similarity of per-frame timbre (20 MFCCs) and harmony (12 chroma), each standardised.                                    |
| [`checkerboard_novelty`](#muvid.footage.beats.checkerboard_novelty)(features, \*[, half])         | Novelty along a feature sequence `[dims, frames]`: the correlation of a Gaussian-tapered checkerboard kernel (`half` frames each side) with the cosine self-similarity matrix around each frame.                                                                             |
| [`directogram`](#muvid.footage.beats.directogram)(fx, fy, \*[, bins])                    | Flow magnitude summed per direction bin, divided by the pixel count: how much of the picture moves which way.                                                                                                                                                                |
| [`deceleration_flux`](#muvid.footage.beats.deceleration_flux)(hists)                           | Per sample, the motion that stopped since the previous one, summed over the columns of `hists` (`sum(max(0, h[t-1] - h[t]))`) — directions of a directogram (`visual_impact`) or cells of a grid (`region_impact`).                                                          |
| [`region_speeds`](#muvid.footage.beats.region_speeds)(fx, fy, \*[, grid])                  | Mean flow speed in each cell of a `cols x rows` grid, row-major.                                                                                                                                                                                                             |
| [`signal_record`](#muvid.footage.beats.signal_record)(values, \*, t0, hop_s, name, domain) | One signal on a regular grid: sample `i` is at `t0 + i * hop_s` seconds of the media's own time.                                                                                                                                                                             |
| [`decimated`](#muvid.footage.beats.decimated)(record, max_points)                      | A signal with at most `max_points` samples: each kept sample is the MAX of the `k` it stands for (a beat is a peak; averaging would erase it), on a grid whose hop grows by `k` and whose `t0` moves to the centre of the first block — so a pooled peak stays where it was. |
| [`cached_signals`](#muvid.footage.beats.cached_signals)(root, media_hash, kind, compute)    | The record for `(media, kind)`: a file read when it was made before, else `compute()` written atomically under `<root>/beats/`.                                                                                                                                              |
| [`cache_key`](#muvid.footage.beats.cache_key)(kind)                                    | Every parameter that changes the bytes, so a new setting is a new file: the record format, the audio estimator's versions (`mixing` and `librosa` — a record must never disagree with a fresh `beat_grid`), and every constant of the visual pass.                           |
| [`has_signal`](#muvid.footage.beats.has_signal)(record)                                 | Whether a measured record carries at least one sample of anything.                                                                                                                                                                                                           |
| [`binned_visual_signals`](#muvid.footage.beats.binned_visual_signals)(mids, motion, hists, ...)    | Per-pair rates (at pair midpoints `mids`) averaged into `hop`-second bins.                                                                                                                                                                                                   |

### muvid.footage.beats.SIGNAL_LABELS *= {'audio_onset': 'Sound hits', 'motion': 'Movement', 'novelty': 'Section changes', 'region_impact': 'Moves that land, by region', 'visual_impact': 'Moves that land'}*

What each signal is, in the words a screen can use.

### muvid.footage.beats.audio_signals(path)

`{signals: {audio_onset}, beats, tempo_bpm}` for a media file’s soundtrack, from
`mixing.audio.beat_grid`. Raises `ImportError` without librosa (the caller names
the install).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.beats.binned_visual_signals(mids, motion, hists, hop, , cells=None)

Per-pair rates (at pair midpoints `mids`) averaged into `hop`-second bins.

`motion` is each bin’s mean, reported at the bin’s CENTRE (`t0 = hop / 2`).
`visual_impact` is the deceleration flux between consecutive bin-mean
directograms, so it belongs to the BOUNDARY between two bins and is reported there
(`t0 = 0`: sample `i` at `i * hop`, sample 0 unmeasured) — half a hop earlier
than a centre would put it, which matters once it drives a time-warp. Pairs before
the clip’s first frame (a negative container timestamp) are dropped. Pure numpy —
the part of the visual pass a test can reach.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.beats.cache_key(kind)

Every parameter that changes the bytes, so a new setting is a new file: the
record format, the audio estimator’s versions (`mixing` and `librosa` — a
record must never disagree with a fresh `beat_grid`), and every constant of the
visual pass.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.footage.beats.cached_signals(root, media_hash, kind, compute)

The record for `(media, kind)`: a file read when it was made before, else
`compute()` written atomically under `<root>/beats/`.

One computation per record at a time: a second request for the same record waits
on a lock and then reads what the first wrote, instead of starting a second
minutes-long pass (an editor opening every video’s channel at once, twice, would
otherwise run each pass twice). A record with nothing in it is NOT kept — a
truncated file or an unreadable stream must be measured again next time, not be
remembered as silence — and `compute` should refuse rather than return one.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.beats.checkerboard_novelty(features, , half=None)

Novelty along a feature sequence `[dims, frames]`: the correlation of a
Gaussian-tapered checkerboard kernel (`half` frames each side) with the cosine
self-similarity matrix around each frame. Frames without a full kernel are NaN.

* **Return type:**
  `ndarray`

### muvid.footage.beats.deceleration_flux(hists)

Per sample, the motion that stopped since the previous one, summed over the
columns of `hists` (`sum(max(0, h[t-1] - h[t]))`) — directions of a directogram
(`visual_impact`) or cells of a grid (`region_impact`). `hists` is `[k, n]`
with NaN rows where nothing was measured; the first sample, and any sample next to
a NaN row, is NaN.

* **Return type:**
  `ndarray`

### muvid.footage.beats.decimated(record, max_points)

A signal with at most `max_points` samples: each kept sample is the MAX of the
`k` it stands for (a beat is a peak; averaging would erase it), on a grid whose
hop grows by `k` and whose `t0` moves to the centre of the first block — so a
pooled peak stays where it was. `min` / `max` are the full-resolution ones;
`p99` is the POOLED one (max-pooling raises the typical value, and a display
scaled by the full-resolution p99 would saturate).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.beats.directogram(fx, fy, , bins=8)

Flow magnitude summed per direction bin, divided by the pixel count: how much of
the picture moves which way. Flow under the noise floor votes for no direction.

* **Return type:**
  `ndarray`

### muvid.footage.beats.fitted_tempo(beats, , min_beats=8)

The tempo (BPM) of a steady beat train, fitted to ALL its beats — or `None`
when the beats are too few or not steady enough to have one tempo.

A beat tracker’s own tempo can be biased: librosa’s is the median inter-beat
interval, and on a song whose tracked beats run slightly fast with an occasional
skip it reported 129.2 BPM where the beats themselves fit 126.9 (a 1.8 % error
that puts a straight grid a full beat off within a minute).

So each beat is given its beat NUMBER by walking the intervals: an interval of
about one period is one step, a skipped beat two, and a spurious extra beat
(half a period) no step at all — it shares its neighbour’s number, so it neither
adds nor removes a beat. Time is fitted against those numbers by least squares,
twice, re-numbering with the refined period. A train whose residual exceeds
`_TEMPO_FIT_MAX_RMS` of a period (a tempo change, a rubato, heavy tracking
errors) has no single tempo: `None`, and the caller keeps the estimator’s.

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.footage.beats.has_audio(path)

Whether a media file carries an audio stream (an unprobeable file: no).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### muvid.footage.beats.has_signal(record)

Whether a measured record carries at least one sample of anything.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### muvid.footage.beats.novelty_signal(path)

`{signals: {novelty}}` — Foote’s checkerboard novelty of a song: a Gaussian-
tapered checkerboard kernel slid along the diagonal of the cosine self-similarity
of per-frame timbre (20 MFCCs) and harmony (12 chroma), each standardised. Peaks
are where the music changes character — section boundaries.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.beats.region_speeds(fx, fy, , grid=(8, 6))

Mean flow speed in each cell of a `cols x rows` grid, row-major. Edge pixels
that do not fill a whole cell are left out.

* **Return type:**
  `ndarray`

### muvid.footage.beats.signal_record(values, , t0, hop_s, name, domain)

One signal on a regular grid: sample `i` is at `t0 + i * hop_s` seconds of the
media’s own time. Non-finite samples become `None` (not measured, never zero).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.beats.visual_signals(path, , sample_fps=15.0, max_seconds=900.0, downscale=4, bins=8, should_cancel=None)

`{signals: {motion, visual_impact}}` for a video, in one decode pass.

Flow is measured between CONSECUTIVE frames (at most `_MAX_PAIR_RATE` pairs a
second) and each pair’s rate is averaged into bins of `1 / sample_fps` s. That
average is the low-pass a decimation needs: measuring on every other frame instead
aliases whatever moves faster than half the sampling rate into a fake slow rhythm
(on 30 fps phone footage, a steady 3 Hz pulse that is not in the picture).

Pairs are timed by the frames’ TIMESTAMPS, not by index over the container’s frame
rate — a phone file can declare 120 fps and carry 24. Both signals are per SECOND
and per frame HEIGHT, so the frame rate does not change their scale and the
resolution changes it little. Bin `i` covers `[i, i + 1) / sample_fps` s; a bin
no pair fell in is `None`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
