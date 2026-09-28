# muvid.footage.beat_fit

Fitting a cut’s timing to the beat — the pure numerics behind `service.fit_to_beat`.

A cut shows its clip through an affine map of song time (`edl.clip_time_at`):
`clip_in + rate * (t - song_start)` with `clip_in = song_start - offset + slip`. So
a movement accent recorded at clip time `tau` lands at song time
`song_start + (tau - clip_in) / rate`. Fitting a cut means choosing `(slip, rate)`
so the clip’s accents (`beats.MOTION_STOPS`) land on the song’s beat grid.

**The score** of a candidate is the accents’ weighted mean alignment with the grid,
`sum(w * cos(2 pi (t - phase) / period)) / sum(w)`: +1 when every accent is on a beat,
-1 when every one is half a beat off, 0 when they ignore the beat. Weights are the
accent strengths above their median, so the steady floor of a moving crowd does not
vote.

**The confidence** of the best fit is a z-score against a null that keeps what the
signal is made of and destroys only its relation to the beat: the samples are cut into
blocks of about two beats and each block is rotated by its own random amount, and the
SAME search is run on every such null signal. The search’s own freedom (it will always
find some slip that looks good) is therefore in the null too. A whole-signal rotation
would not do: it only turns every phase by the same angle, which the slip search undoes.

**The window** is wider than a short cut: a 1.5 s cut holds three beats, too few to judge
by. The fit is judged over at least `window_s` of the clip around the cut, read
through the same map — the same dancer, a few seconds either side.

Pure numpy; nothing here reads a file.

### Module Attributes

| [`FIT_WINDOW_S`](#muvid.footage.beat_fit.FIT_WINDOW_S)   | Seconds of clip the fit is judged over, at least (centred on the cut).   |
|-----------------------------------------------------------------|--------------------------------------------------------------------------|
| [`FIT_MIN_BEATS`](#muvid.footage.beat_fit.FIT_MIN_BEATS)  | A cut shorter than this many beats is not fitted.                        |

### Functions

| [`fit_cut`](#muvid.footage.beat_fit.fit_cut)(tau, values, \*, song_start, ...[, ...])   | The best timing for a cut over song time `[song_start, song_end]` of a clip at `offset`, from the clip's accent signal `values` sampled at clip times `tau`.   |
|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|

### Classes

| [`BeatGrid`](#muvid.footage.beat_fit.BeatGrid)(period, phase)                         | A steady beat: beat `k` falls at `phase + k * period` seconds of the song.                                                 |
|--------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| [`Fit`](#muvid.footage.beat_fit.Fit)(slip_s, rate, score, current_score, z, ...) | The best `(slip, rate)` for one cut, its score, the current setting's score, and the z of the best score against the null. |

### *class* muvid.footage.beat_fit.BeatGrid(period, phase)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A steady beat: beat `k` falls at `phase + k * period` seconds of the song.

#### *classmethod* fitted(beats, , min_beats=8)

The least-squares grid through tracked beat instants (each beat numbered by
`beats._beat_numbers`, so a skipped or spurious beat does not bend it), or
`None` when the beats are too few or not steady (`beats.fitted_tempo`).

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`BeatGrid`](#muvid.footage.beat_fit.BeatGrid)]

### muvid.footage.beat_fit.FIT_MIN_BEATS *= 3.0*

A cut shorter than this many beats is not fitted.

### muvid.footage.beat_fit.FIT_WINDOW_S *= 8.0*

Seconds of clip the fit is judged over, at least (centred on the cut).

### *class* muvid.footage.beat_fit.Fit(slip_s, rate, score, current_score, z, n_accents)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The best `(slip, rate)` for one cut, its score, the current setting’s score,
and the z of the best score against the null.

### muvid.footage.beat_fit.fit_cut(tau, values, , song_start, song_end, offset, current_slip=0.0, current_rate=1.0, grid, feasible=None, window_s=8.0, slips=None, rates=None, null_draws=200, seed=0)

The best timing for a cut over song time `[song_start, song_end]` of a clip at
`offset`, from the clip’s accent signal `values` sampled at clip times `tau`.

`feasible(slip, rate) -> bool` rules out settings the clip cannot hold. `None`
when no accent lands in the window under any setting.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Fit`](#muvid.footage.beat_fit.Fit)]
