# muvid.footage.beats

Beat signals — continuous envelopes of where the beat is, in the song and in each video.

`beat_grid` answers *when* the song’s beats fall (instants). An editor lining a dancer’s
hits up with the music wants more than instants: a **continuous** signal it can look at,
threshold anywhere between its minimum and maximum, and bend towards binary. That is what
this module measures, per piece of media and in that media’s OWN time (a clip reaches song
time through its offset, exactly as filmstrips do):

- `audio_onset` — the onset-strength envelope `mixing.audio.beat_grid` estimates beats
  from (the same estimator `beat_grid` uses, so the two never disagree about the song),
  for the song and for every clip that has a soundtrack. The estimator’s beat instants and
  tempo come along.
- `motion` — subject-motion energy: the mean camera-compensated optical-flow magnitude
  (the scoring layer’s `flow_residual_and_global` kernel), in frame-heights per second so
  its scale depends little on resolution or sampling rate (not at all is not claimed: the
  flow’s window and noise floor are in downscaled pixels).
- `visual_impact` — the visual BEAT envelope: how much motion, direction by direction,
  STOPS from one sample to the next — the half-wave-rectified decrease of a
  magnitude-weighted **directogram** (a histogram of flow directions), after Davis &
  Agrawala, *Visual Rhythm and Beat* (SIGGRAPH 2018), whose visual beats are sudden
  decelerations. A hit stopping dead and a change of direction (motion leaving one
  direction bin) both register; motion energy alone misses the turn, which is most of
  what a dance beat looks like. Measured on phone footage of dancers, this deceleration
  flux locked to the song’s beat about twice as strongly as the increase or the total
  change did.

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
| [`directogram`](#muvid.footage.beats.directogram)(fx, fy, \*[, bins])                    | Flow magnitude summed per direction bin, divided by the pixel count: how much of the picture moves which way.                                                                                                                                                                |
| [`impact_from_directograms`](#muvid.footage.beats.impact_from_directograms)(hists)                    | The visual-beat envelope: per sample, the motion that stopped since the previous one, summed over directions (`sum(max(0, h[t-1] - h[t]))`).                                                                                                                                 |
| [`signal_record`](#muvid.footage.beats.signal_record)(values, \*, t0, hop_s, name, domain) | One signal on a regular grid: sample `i` is at `t0 + i * hop_s` seconds of the media's own time.                                                                                                                                                                             |
| [`decimated`](#muvid.footage.beats.decimated)(record, max_points)                      | A signal with at most `max_points` samples: each kept sample is the MAX of the `k` it stands for (a beat is a peak; averaging would erase it), on a grid whose hop grows by `k` and whose `t0` moves to the centre of the first block — so a pooled peak stays where it was. |
| [`cached_signals`](#muvid.footage.beats.cached_signals)(root, media_hash, kind, compute)    | The record for `(media, kind)`: a file read when it was made before, else `compute()` written atomically under `<root>/beats/`.                                                                                                                                              |
| [`cache_key`](#muvid.footage.beats.cache_key)(kind)                                    | Every parameter that changes the bytes, so a new setting is a new file: the record format, the audio estimator's versions (`mixing` and `librosa` — a record must never disagree with a fresh `beat_grid`), and every constant of the visual pass.                           |
| [`has_signal`](#muvid.footage.beats.has_signal)(record)                                 | Whether a measured record carries at least one sample of anything.                                                                                                                                                                                                           |
| [`binned_visual_signals`](#muvid.footage.beats.binned_visual_signals)(mids, motion, hists, hop)    | Per-pair rates (at pair midpoints `mids`) averaged into `hop`-second bins.                                                                                                                                                                                                   |

### muvid.footage.beats.SIGNAL_LABELS *= {'audio_onset': 'Sound hits', 'motion': 'Movement', 'visual_impact': 'Moves that land'}*

What each signal is, in the words a screen can use.

### muvid.footage.beats.audio_signals(path)

`{signals: {audio_onset}, beats, tempo_bpm}` for a media file’s soundtrack, from
`mixing.audio.beat_grid`. Raises `ImportError` without librosa (the caller names
the install).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.beats.binned_visual_signals(mids, motion, hists, hop)

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

### muvid.footage.beats.has_audio(path)

Whether a media file carries an audio stream (an unprobeable file: no).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### muvid.footage.beats.has_signal(record)

Whether a measured record carries at least one sample of anything.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### muvid.footage.beats.impact_from_directograms(hists)

The visual-beat envelope: per sample, the motion that stopped since the previous
one, summed over directions (`sum(max(0, h[t-1] - h[t]))`). `hists` is
`[k, bins]` with NaN rows where nothing was measured; the first sample, and any
sample next to a NaN row, is NaN.

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
