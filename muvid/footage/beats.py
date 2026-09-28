"""Beat signals — continuous envelopes of where the beat is, in the song and in each video.

``beat_grid`` answers *when* the song's beats fall (instants). An editor lining a dancer's
hits up with the music wants more than instants: a **continuous** signal it can look at,
threshold anywhere between its minimum and maximum, and bend towards binary. That is what
this module measures, per piece of media and in that media's OWN time (a clip reaches song
time through its offset, exactly as filmstrips do).

Sound, for the song and for every clip with a soundtrack:

- ``audio_onset`` — the onset-strength envelope ``mixing.audio.beat_grid`` estimates beats
  from (librosa's spectral flux on a log-mel spectrogram — the standard envelope, adequate
  for percussive pop; SuperFlux's vibrato suppression matters for voice and strings). The
  estimator's beat instants come along, with a tempo FITTED to them (see ``fitted_tempo``).
- ``novelty`` (the song only) — how much the music changes character around each moment:
  Foote's checkerboard novelty over a self-similarity matrix of timbre and harmony. Its
  peaks are section boundaries (verse, chorus, drop) — the other place an editor cuts.

Picture, for every clip:

- ``motion`` — subject-motion energy: the mean camera-compensated optical-flow magnitude,
  in frame-heights per second.
- ``motion_stops`` — movement accents: how much motion, direction by direction, STOPS
  between samples — the half-wave-rectified decrease of a magnitude-weighted flow
  direction histogram (the histogram of oriented optical flow, HOOF, of Chaudhry et al.
  2009). Measuring motion that stops follows Davis & Agrawala (SIGGRAPH 2018, §4.2),
  using the decrease their prose and released code describe. It is a deceleration
  measure, which is where the conducting literature puts the beat: ensembles synchronise
  with the **maximal deceleration** of the conductor's hand (Luck & Toiviainen 2006), and
  with absolute acceleration along the trajectory (Luck & Sloboda 2009) — the *ictus*,
  not the moment a movement starts (Takehana et al. 2019: movement initiation never
  coincided with beats).
- ``motion_stops_local`` — the same stopping without directions: the decrease of
  camera-compensated SPEED in each cell of an 8 x 6 grid, summed over cells. Per-place
  rather than per-direction, so several dancers braking in different places add up
  instead of cancelling.

**What the evidence on real footage says** (three phone videos of a crowd dancing to one
song, 2 minutes each; beat locking measured as the phase concentration of each signal on
the song's beat, against a null that shifts each 4 s block independently — a whole-signal
circular shift cannot detect locking at all, since it only rotates the phase):

- each clip's own soundtrack locks strongly (z = 7 to 15) — the positive control, and the
  confirmation that the clips' offsets are right;
- ``motion_stops`` and ``motion_stops_local`` lock on one clip (z = 2.8 and 2.5, at the same
  -25 ms lag as that clip's soundtrack) and on neither of the others; whole-frame speed,
  pose-based limb deceleration (a person found in only 38-75 % of frames of a crowd) and
  AIST++-style velocity minima (half a beat off) did no better;
- a 1.25 Hz high-pass (Davis & Agrawala's post-filter) helped no clip consistently.

So these envelopes SHOW where movement lands; on a crowd they are weak evidence of the
beat. ``service.fit_to_beat`` uses them to propose per-cut timing, and applies a fit only
where it beats the same null — so on footage like this it will usually leave cuts alone.

Every signal is **unnormalised** and carries its own grid (``t0``, ``hop_s``) and its
``min``, ``max`` and ``p99`` (a robust top a display can scale by), because thresholding is a
view the caller chooses, not something the data should have decided.

Cached per media content hash and parameters at ``footage/beats/<hash>-<kind>-<key>.json``,
beside ``peaks/`` — derived from media the project holds, expensive to make, cheap to keep.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Callable, Optional

import numpy as np

#: Visual signals' rate: samples per second of clip time. A dance beat at ~130 BPM is
#: ~0.46 s, so 15 puts ~7 samples on each beat.
VISUAL_SAMPLE_FPS = float(os.environ.get("MUVID_BEAT_VISUAL_FPS", "15"))
#: Hard cap on the clip time measured (bounds CPU): the first 15 minutes.
VISUAL_MAX_SECONDS = float(os.environ.get("MUVID_BEAT_VISUAL_MAX_SECONDS", "900"))
#: At most this many frame pairs a second are measured (a 120 fps file is thinned).
_MAX_PAIR_RATE = 32.0
#: Downscale factor before the Farneback flow — the same cost bound the scorer uses.
FLOW_DOWNSCALE = 4
#: Direction bins of the flow direction histogram (45 degrees each).
DIRECTION_BINS = 8
#: Flow below this many pixels (downscaled) is sensor noise, not a direction.
_FLOW_NOISE_PX = 0.05
#: The robust top reported beside min/max.
_TOP_PERCENTILE = 99.0
#: Decimals kept on the wire and on disk.
_DECIMALS = 5

#: The record FORMAT's version, part of the cache key: bump it when the same parameters
#: start producing different signals, so an old cache is not served as the new one.
BEAT_SIGNALS_FORMAT = 4
_DIRNAME = "beats"
_HASH_PREFIX = 16

AUDIO_ONSET = "audio_onset"
NOVELTY = "novelty"
MOTION = "motion"
MOTION_STOPS = "motion_stops"
MOTION_STOPS_LOCAL = "motion_stops_local"

#: What each signal is, in the words a screen can use.
SIGNAL_LABELS = {
    AUDIO_ONSET: "Sound hits",
    NOVELTY: "Section changes",
    MOTION: "Movement",
    MOTION_STOPS: "Moves that stop or turn",
    MOTION_STOPS_LOCAL: "Moves that stop, place by place",
}


# -- the record ---------------------------------------------------------------------


def signal_record(values, *, t0: float, hop_s: float, name: str, domain: str) -> dict:
    """One signal on a regular grid: sample ``i`` is at ``t0 + i * hop_s`` seconds of the
    media's own time. Non-finite samples become ``None`` (not measured, never zero)."""
    arr = np.asarray(values, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    stats = (
        (
            float(finite.min()),
            float(finite.max()),
            float(np.percentile(finite, _TOP_PERCENTILE)),
        )
        if finite.size
        else (None, None, None)
    )
    lo, hi, top = (None if s is None else round(s, _DECIMALS) for s in stats)
    return {
        "name": name,
        "label": SIGNAL_LABELS.get(name, name),
        "domain": domain,
        "t0": round(float(t0), 6),
        "hop_s": round(float(hop_s), 6),
        "n": int(arr.size),
        "min": lo,
        "max": hi,
        "p99": top,
        "values": [
            round(float(v), _DECIMALS) if math.isfinite(v) else None for v in arr
        ],
    }


def decimated(record: dict, max_points: Optional[int]) -> dict:
    """A signal with at most ``max_points`` samples: each kept sample is the MAX of the
    ``k`` it stands for (a beat is a peak; averaging would erase it), on a grid whose
    hop grows by ``k`` and whose ``t0`` moves to the centre of the first block — so a
    pooled peak stays where it was. ``min`` / ``max`` are the full-resolution ones;
    ``p99`` is the POOLED one (max-pooling raises the typical value, and a display
    scaled by the full-resolution p99 would saturate)."""
    n = record["n"]
    if not max_points or n <= max_points:
        return record
    k = math.ceil(n / int(max_points))
    vals = np.array(
        [np.nan if v is None else v for v in record["values"]], dtype=np.float64
    )
    pad = (-n) % k
    if pad:
        vals = np.concatenate([vals, np.full(pad, np.nan)])
    blocks = vals.reshape(-1, k)
    with np.errstate(all="ignore"):
        pooled = np.where(
            np.all(np.isnan(blocks), axis=1), np.nan, np.nanmax(blocks, axis=1)
        )
    finite = pooled[np.isfinite(pooled)]
    hop = record["hop_s"]
    return {
        **record,
        "t0": round(record["t0"] + (k - 1) * hop / 2.0, 6),
        "hop_s": round(hop * k, 6),
        "n": int(pooled.size),
        "p99": (
            round(float(np.percentile(finite, _TOP_PERCENTILE)), _DECIMALS)
            if finite.size
            else None
        ),
        "values": [
            round(float(v), _DECIMALS) if math.isfinite(v) else None for v in pooled
        ],
    }


# -- audio --------------------------------------------------------------------------


def audio_signals(path) -> dict:
    """``{signals: {audio_onset}, beats, tempo_bpm}`` for a media file's soundtrack, from
    ``mixing.audio.beat_grid``. Raises ``ImportError`` without librosa (the caller names
    the install)."""
    from mixing.audio import beat_grid as estimate

    grid = estimate(str(path))
    hop = float(grid.onset_hop_s)
    env = np.asarray(grid.onset_env, dtype=np.float64)
    tempo = float(grid.tempo_bpm)
    return {
        "signals": {
            AUDIO_ONSET: signal_record(
                env, t0=0.0, hop_s=hop, name=AUDIO_ONSET, domain="audio"
            )
        },
        "beats": [round(float(t), 4) for t in grid.beat_times],
        "tempo_bpm": fitted_tempo(grid.beat_times)
        or (round(tempo, 3) if math.isfinite(tempo) and tempo > 0 else None),
    }


#: A fit whose beats stray from their line by more than this share of a period
#: (rms) is not a steady beat train, and no single tempo describes it.
_TEMPO_FIT_MAX_RMS = 0.15


def fitted_tempo(beats, *, min_beats: int = 8) -> "float | None":
    """The tempo (BPM) of a steady beat train, fitted to ALL its beats — or ``None``
    when the beats are too few or not steady enough to have one tempo.

    A beat tracker's own tempo can be biased: librosa's is the median inter-beat
    interval, and on a song whose tracked beats run slightly fast with an occasional
    skip it reported 129.2 BPM where the beats themselves fit 126.9 (a 1.8 % error
    that puts a straight grid a full beat off within a minute).

    So each beat is given its beat NUMBER by walking the intervals: an interval of
    about one period is one step, a skipped beat two, and a spurious extra beat
    (half a period) no step at all — it shares its neighbour's number, so it neither
    adds nor removes a beat. Time is fitted against those numbers by least squares,
    twice, re-numbering with the refined period. A train whose residual exceeds
    ``_TEMPO_FIT_MAX_RMS`` of a period (a tempo change, a rubato, heavy tracking
    errors) has no single tempo: ``None``, and the caller keeps the estimator's.
    """
    b = np.asarray(beats, dtype=np.float64)
    if b.size < min_beats:
        return None
    d = np.diff(b)
    period = float(np.median(d))
    if not period > 0:
        return None
    for _ in range(2):
        k, own = _beat_numbers(b, period)
        if k[own][-1] < min_beats:
            return None
        slope, icept = np.polyfit(k[own], b[own], 1)
        if not slope > 0:
            return None
        period = float(slope)
    k, own = _beat_numbers(b, period)
    # Residual only over the beats that own their number: a spurious extra beat
    # sits half a period off its line by construction.
    resid = b[own] - (k[own] * period + icept)
    if float(np.sqrt(np.mean(resid**2))) > _TEMPO_FIT_MAX_RMS * period:
        return None
    return round(60.0 / period, 3)


def _beat_numbers(b: np.ndarray, period: float) -> tuple:
    """``(numbers, owns)``: each beat's beat number, counted from the last beat that
    OWNED a number (so a spurious extra beat — under 3/4 of a period after it —
    takes that beat's number and does not own it, and the next real beat is still
    one step on), and which beats own theirs."""
    k = np.zeros(b.size)
    owns = np.ones(b.size, dtype=bool)
    last_t, last_k = b[0], 0.0
    for i in range(1, b.size):
        gap = (b[i] - last_t) / period
        if gap < 0.75:
            k[i], owns[i] = last_k, False
            continue
        last_k += max(1.0, float(np.rint(gap)))
        last_t = b[i]
        k[i] = last_k
    return k, owns


#: Novelty: the analysis grid (s) and the checkerboard kernel's full width (s) — a
#: section boundary is a change that holds for several bars, not a fill.
NOVELTY_HOP_S = 0.1
NOVELTY_KERNEL_S = 8.0
#: Timbre coefficients per frame (chroma is always 12).
NOVELTY_MFCC = 20
_NOVELTY_SAMPLE_RATE = 22050


def novelty_signal(path) -> dict:
    """``{signals: {novelty}}`` — Foote's checkerboard novelty of a song: a Gaussian-
    tapered checkerboard kernel slid along the diagonal of the cosine self-similarity
    of per-frame timbre (20 MFCCs) and harmony (12 chroma), each standardised. Peaks
    are where the music changes character — section boundaries."""
    import librosa  # lazy: the 'scoring' extra

    from muvid.visualize.ffmpeg import decode_pcm

    y = np.frombuffer(
        decode_pcm(path, sample_rate=_NOVELTY_SAMPLE_RATE, channels=1), dtype=np.float32
    )
    if y.size == 0:
        raise ValueError("no audio could be decoded")
    hop = int(round(_NOVELTY_SAMPLE_RATE * NOVELTY_HOP_S))
    mfcc = librosa.feature.mfcc(
        y=y, sr=_NOVELTY_SAMPLE_RATE, n_mfcc=NOVELTY_MFCC, hop_length=hop
    )
    chroma = librosa.feature.chroma_stft(y=y, sr=_NOVELTY_SAMPLE_RATE, hop_length=hop)
    return {
        "signals": {
            NOVELTY: signal_record(
                checkerboard_novelty(
                    np.vstack([_standardised(mfcc), _standardised(chroma)])
                ),
                t0=0.0,
                hop_s=hop / _NOVELTY_SAMPLE_RATE,
                name=NOVELTY,
                domain="audio",
            )
        }
    }


def _standardised(x: np.ndarray) -> np.ndarray:
    return (x - x.mean(axis=1, keepdims=True)) / (x.std(axis=1, keepdims=True) + 1e-9)


def checkerboard_novelty(
    features: np.ndarray, *, half: "int | None" = None
) -> np.ndarray:
    """Novelty along a feature sequence ``[dims, frames]``: the correlation of a
    Gaussian-tapered checkerboard kernel (``half`` frames each side) with the cosine
    self-similarity matrix around each frame. Frames without a full kernel are NaN."""
    f = np.asarray(features, dtype=np.float64)
    n = f.shape[1]
    half = int(
        half if half is not None else round(NOVELTY_KERNEL_S / NOVELTY_HOP_S / 2)
    )
    out = np.full(n, np.nan)
    if n < 2 * half + 1 or half < 1:
        return out
    unit = f / (np.linalg.norm(f, axis=0, keepdims=True) + 1e-9)
    r = np.arange(-half, half + 1)
    sign = (
        np.sign(r)[:, None] * np.sign(r)[None, :]
    )  # + on the two diagonal blocks, - off
    taper = np.exp(-0.5 * (r / (half / 2.0)) ** 2)
    kernel = sign * taper[:, None] * taper[None, :]
    # Only the band around the diagonal is ever read, so each window's block is
    # computed on its own: memory stays O(kernel²), never O(frames²) (a whole
    # 10-minute song's matrix would be ~290 MB).
    for i in range(half, n - half):
        w = unit[:, i - half : i + half + 1]
        out[i] = float((kernel * (w.T @ w)).sum())
    return out


def has_audio(path) -> bool:
    """Whether a media file carries an audio stream (an unprobeable file: no)."""
    from muvid.visualize.ffmpeg import probe

    try:
        streams = probe(Path(path)).get("streams", [])
    except Exception:  # noqa: BLE001 — unprobeable: treat as silent, never crash a read
        return False
    return any(s.get("codec_type") == "audio" for s in streams)


# -- visual -------------------------------------------------------------------------


def direction_histogram(
    fx: np.ndarray, fy: np.ndarray, *, bins: int = DIRECTION_BINS
) -> np.ndarray:
    """Flow magnitude summed per direction bin, divided by the pixel count: how much of
    the picture moves which way. Flow under the noise floor votes for no direction."""
    mag = np.hypot(fx, fy).ravel()
    ang = np.arctan2(fy, fx).ravel()
    keep = mag > _FLOW_NOISE_PX
    idx = ((ang[keep] + np.pi) / (2 * np.pi) * bins).astype(int) % bins
    hist = np.bincount(idx, weights=mag[keep], minlength=bins)
    return hist / max(1, mag.size)


def stop_strength(hists: np.ndarray) -> np.ndarray:
    """Per sample, the motion that stopped since the previous one, summed over the
    columns of ``hists`` (``sum(max(0, h[t-1] - h[t]))``) — directions of a flow direction
    histogram (``motion_stops``) or cells of a grid (``motion_stops_local``). ``hists`` is ``[k, n]``
    with NaN rows where nothing was measured; the first sample, and any sample next to
    a NaN row, is NaN."""
    hists = np.asarray(hists, dtype=np.float64)
    out = np.full(hists.shape[0], np.nan)
    if hists.shape[0] > 1:
        out[1:] = np.clip(-np.diff(hists, axis=0), 0.0, None).sum(axis=1)
    return out


#: The grid ``motion_stops_local`` measures speed in: columns x rows of the frame.
REGION_GRID = (8, 6)


def region_speeds(
    fx: np.ndarray, fy: np.ndarray, *, grid: tuple = REGION_GRID
) -> np.ndarray:
    """Mean flow speed in each cell of a ``cols x rows`` grid, row-major. Edge pixels
    that do not fill a whole cell are left out."""
    cols, rows = grid
    mag = np.hypot(fx, fy)
    h, w = mag.shape
    ch, cw = h // rows, w // cols
    if ch == 0 or cw == 0:
        return np.full(cols * rows, float(mag.mean()) if mag.size else 0.0)
    return (
        mag[: ch * rows, : cw * cols]
        .reshape(rows, ch, cols, cw)
        .mean(axis=(1, 3))
        .ravel()
    )


def visual_signals(
    path,
    *,
    sample_fps: float = VISUAL_SAMPLE_FPS,
    max_seconds: float = VISUAL_MAX_SECONDS,
    downscale: int = FLOW_DOWNSCALE,
    bins: int = DIRECTION_BINS,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    """``{signals: {motion, motion_stops, motion_stops_local}}`` for a video, in one decode pass.

    Flow is measured between CONSECUTIVE frames (at most ``_MAX_PAIR_RATE`` pairs a
    second) and each pair's rate is averaged into bins of ``1 / sample_fps`` s. That
    average is the low-pass a decimation needs: measuring on every other frame instead
    aliases whatever moves faster than half the sampling rate into a fake slow rhythm
    (on 30 fps phone footage, a steady 3 Hz pulse that is not in the picture).

    Pairs are timed by the frames' TIMESTAMPS, not by index over the container's frame
    rate — a phone file can declare 120 fps and carry 24. Both signals are per SECOND
    and per frame HEIGHT, so the frame rate does not change their scale and the
    resolution changes it little. Bin ``i`` covers ``[i, i + 1) / sample_fps`` s; a bin
    no pair fell in is ``None``.
    """
    import cv2  # lazy: the 'scoring' extra

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")
    hop = 1.0 / max(0.1, float(sample_fps))
    min_gap = 1.0 / _MAX_PAIR_RATE
    mids, motion, hists, cells = [], [], [], []
    prev_small, prev_t = None, None
    try:
        while cap.grab():
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if t > max_seconds:
                break
            if prev_t is not None and t - prev_t < min_gap:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            small = _small_gray(frame, downscale=downscale)
            if prev_small is not None and t > prev_t:
                fx, fy = _compensated_flow(prev_small, small)
                per_s = 1.0 / (
                    (t - prev_t) * small.shape[0]
                )  # frame-heights per second
                mids.append((t + prev_t) / 2.0)
                motion.append(float(np.mean(np.hypot(fx, fy))) * per_s)
                hists.append(direction_histogram(fx, fy, bins=bins) * per_s)
                cells.append(region_speeds(fx, fy) * per_s)
            prev_small, prev_t = small, t
            if should_cancel is not None and should_cancel():
                break
    finally:
        cap.release()
    n_cells = REGION_GRID[0] * REGION_GRID[1]
    return binned_visual_signals(
        np.asarray(mids),
        np.asarray(motion),
        np.asarray(hists).reshape(-1, bins),
        hop,
        cells=np.asarray(cells).reshape(-1, n_cells),
    )


def _small_gray(frame, *, downscale: int):
    import cv2  # lazy

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    if downscale > 1:
        h, w = gray.shape[:2]
        gray = cv2.resize(gray, (max(1, w // downscale), max(1, h // downscale)))
    return gray


def _compensated_flow(prev_gray, gray):
    """Farneback flow with the camera's own move (the median vector) taken out, so a pan
    does not read as the dancer moving — the compensation ``flow_residual_and_global``
    applies for the scorer."""
    import cv2  # lazy

    flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 2, 15, 3, 5, 1.2, 0)
    fx, fy = flow[..., 0], flow[..., 1]
    return fx - float(np.median(fx)), fy - float(np.median(fy))


def binned_visual_signals(mids, motion, hists, hop: float, *, cells=None) -> dict:
    """Per-pair rates (at pair midpoints ``mids``) averaged into ``hop``-second bins.

    ``motion`` is each bin's mean, reported at the bin's CENTRE (``t0 = hop / 2``).
    ``motion_stops`` is the stop strength between consecutive bin-mean direction
    histograms, so it belongs to the BOUNDARY between two bins and is reported there
    (``t0 = 0``: sample ``i`` at ``i * hop``, sample 0 unmeasured) — half a hop earlier
    than a centre would put it, which matters once it drives a time-warp. Pairs before
    the clip's first frame (a negative container timestamp) are dropped. Pure numpy —
    the part of the visual pass a test can reach."""
    keep = mids >= 0
    mids, motion, hists = mids[keep], motion[keep], hists[keep]
    if mids.size == 0:
        return {
            "signals": {
                MOTION: signal_record(
                    [], t0=hop / 2.0, hop_s=hop, name=MOTION, domain="visual"
                ),
                MOTION_STOPS: signal_record(
                    [], t0=0.0, hop_s=hop, name=MOTION_STOPS, domain="visual"
                ),
            }
        }
    idx = np.floor(mids / hop).astype(int)
    n = int(idx.max()) + 1
    counts = np.bincount(idx, minlength=n).astype(float)

    def bin_means(cols):
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.stack(
                [
                    np.bincount(idx, weights=cols[:, b], minlength=n) / counts
                    for b in range(cols.shape[1])
                ],
                axis=1,
            )

    with np.errstate(invalid="ignore", divide="ignore"):
        mean_motion = np.bincount(idx, weights=motion, minlength=n) / counts
    mean_hists = bin_means(hists)
    extra = {}
    if cells is not None and cells.size:
        extra[MOTION_STOPS_LOCAL] = signal_record(
            stop_strength(bin_means(cells)),
            t0=0.0,
            hop_s=hop,
            name=MOTION_STOPS_LOCAL,
            domain="visual",
        )
    return {
        "signals": {
            **extra,
            MOTION: signal_record(
                mean_motion, t0=hop / 2.0, hop_s=hop, name=MOTION, domain="visual"
            ),
            MOTION_STOPS: signal_record(
                stop_strength(mean_hists),
                t0=0.0,
                hop_s=hop,
                name=MOTION_STOPS,
                domain="visual",
            ),
        }
    }


# -- cached, per piece of media -----------------------------------------------------


def _package_version(name: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return "0"


def cache_key(kind: str) -> str:
    """Every parameter that changes the bytes, so a new setting is a new file: the
    record format, the audio estimator's versions (``mixing`` and ``librosa`` — a
    record must never disagree with a fresh ``beat_grid``), and every constant of the
    visual pass."""
    if kind == "audio":
        return (
            f"a{BEAT_SIGNALS_FORMAT}-mix{_package_version('mixing')}"
            f"-lr{_package_version('librosa')}"
        )
    if kind == "structure":
        return (
            f"s{BEAT_SIGNALS_FORMAT}-lr{_package_version('librosa')}"
            f"-h{NOVELTY_HOP_S:g}-k{NOVELTY_KERNEL_S:g}"
            f"-r{_NOVELTY_SAMPLE_RATE}-m{NOVELTY_MFCC}"
        )
    return (
        f"v{BEAT_SIGNALS_FORMAT}-{VISUAL_SAMPLE_FPS:g}fps-d{FLOW_DOWNSCALE}"
        f"-b{DIRECTION_BINS}-m{VISUAL_MAX_SECONDS:g}s-p{_MAX_PAIR_RATE:g}"
        f"-n{_FLOW_NOISE_PX:g}-g{REGION_GRID[0]}x{REGION_GRID[1]}"
    )


def has_signal(record: dict) -> bool:
    """Whether a measured record carries at least one sample of anything."""
    return any(sig.get("n") for sig in (record.get("signals") or {}).values())


def cached_signals(
    root: Path, media_hash: str, kind: str, compute: Callable[[], dict]
) -> dict:
    """The record for ``(media, kind)``: a file read when it was made before, else
    ``compute()`` written atomically under ``<root>/beats/``.

    One computation per record at a time: a second request for the same record waits
    on a lock and then reads what the first wrote, instead of starting a second
    minutes-long pass (an editor opening every video's channel at once, twice, would
    otherwise run each pass twice). A record with nothing in it is NOT kept — a
    truncated file or an unreadable stream must be measured again next time, not be
    remembered as silence — and ``compute`` should refuse rather than return one."""
    from muvid.footage.media_views import read_json, write_json
    from muvid.footage.workspace import file_lock

    name = f"{media_hash[:_HASH_PREFIX]}-{kind}-{cache_key(kind)}"
    path = Path(root) / _DIRNAME / f"{name}.json"
    cached = read_json(path)
    if cached is not None:
        return cached
    with file_lock(path.with_name(f".{name}.lock")):
        cached = read_json(path)
        if cached is not None:
            return cached
        record = {"media_hash": media_hash, "kind": kind, **compute()}
        if has_signal(record):
            write_json(path, record)
    return record


__all__ = [
    "AUDIO_ONSET",
    "MOTION",
    "MOTION_STOPS",
    "SIGNAL_LABELS",
    "audio_signals",
    "visual_signals",
    "has_audio",
    "fitted_tempo",
    "novelty_signal",
    "checkerboard_novelty",
    "NOVELTY",
    "MOTION_STOPS_LOCAL",
    "direction_histogram",
    "stop_strength",
    "region_speeds",
    "signal_record",
    "decimated",
    "cached_signals",
    "cache_key",
    "has_signal",
    "binned_visual_signals",
]
