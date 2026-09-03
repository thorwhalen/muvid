"""Precomputed audio-reactivity: pulse a video filter in time with the music.

Because ``muvid`` renders from an audio *file*, not a live stream, the whole
loudness envelope is knowable up front. This module turns that envelope into an
ffmpeg ``sendcmd`` script that rewrites a named ``lutyuv``'s lookup table frame
by frame — a beat-reactive "flash" baked deterministically into the render. No
realtime, and no dependency beyond ``numpy`` and the ffmpeg
:mod:`muvid.visualize` already requires.

It is a general seam, not spectrum-specific: any visual can attach a flash to a
named filter in its chain (see :func:`flash_filter`). The vectorscope reacts to
the music through its own amplitude; the spectrogram uses this.

The whole chain degrades to *nothing* rather than to an error: a track that will
not decode, or an ffmpeg build without :data:`FLASH_FILTERS`, yields an empty
fragment, so a visual can append it unconditionally.
"""

from __future__ import annotations

from pathlib import Path

from muvid.visualize.canvas import (
    brightness_saturation_lut,
    escape_filter_value,
    lut_filter,
)
from muvid.visualize.ffmpeg import PathLike, decode_pcm, has_filter

#: Sample rate the envelope is measured at. Low is fine — we only need a
#: per-frame loudness curve, not audio quality.
ENVELOPE_SR = 22050

#: The loudness percentile mapped to a full-strength pulse. Below 100 so a few
#: peaks saturate and the bulk of the track spans the range, rather than one
#: outlier transient flattening everything else.
_PULSE_PERCENTILE = 92

#: Per-frame persistence of a pulse, 0 (no trail) to <1 (longer afterglow), so a
#: beat flashes and fades rather than blinking for a single frame.
FLASH_DECAY = 0.5

#: Peak brightness boost at a full-strength pulse, in ``eq``'s units (an additive
#: offset as a fraction of full scale, -1..1). At rest the filter is a no-op; this
#: is how far a beat pushes it.
FLASH_BRIGHTNESS = 0.25

#: Peak saturation boost at a full-strength pulse, *added to* 1.0.
FLASH_SATURATION = 0.8

#: Default ``sendcmd`` label for the pulsing lookup table. Distinct per flash, so
#: one filtergraph can carry several without their commands crossing.
DEFAULT_FLASH_LABEL = "flash"

#: The ffmpeg filters a flash chain is built from. Both are core filters, but a
#: stripped build can omit either — and the flash is a garnish, so a build that
#: cannot do it should render the visual *without* the flash rather than fail.
#:
#: This tuple is the *probe*, so it has to name the filters the chain really
#: contains. It said ``eq`` while the point of muvid#69 was to stop needing ``eq``
#: (GPL-only): left stale, an LGPL build — the very build this change exists to
#: serve — would have failed the probe and silently rendered with no flash at all.
FLASH_FILTERS = ("sendcmd", "lutyuv")

#: The ``sendcmd`` commands one flash frame sends: one per ``lutyuv`` component.
#: ``eq`` took two (``brightness``, ``saturation``); a LUT is addressed per plane,
#: so chroma costs two commands carrying the same expression.
FLASH_COMPONENTS = ("y", "u", "v")


def onset_envelope(
    audio: PathLike,
    *,
    fps: int,
    duration: float | None = None,
    sr: int = ENVELOPE_SR,
    decay: float = FLASH_DECAY,
) -> list[float]:
    """Per-video-frame onset strength in ``[0, 1]``, with phosphor-style decay.

    Decodes ``audio`` to mono, measures frame-wise loudness, takes the
    half-wave-rectified *rise* in loudness (an onset/transient measure, so
    sustained loud passages don't stay lit — only attacks do), scales it
    robustly to ``[0, 1]``, then lets each pulse fade by ``decay`` per frame so a
    beat flashes and trails off rather than blinking for a single frame.

    Args:
        audio: The track to analyse.
        fps: Video frame rate — one envelope value per frame.
        duration: Clamp the envelope to this many seconds (defaults to the whole
            track).
        sr: Analysis sample rate.
        decay: Per-frame persistence of a pulse, 0 (no trail) to <1 (longer
            afterglow).

    Returns:
        One value per frame. Empty if the audio could not be decoded.
    """
    # Deferred on purpose: numpy is needed only to measure an envelope, so
    # importing it here keeps it off muvid.visualize's import path for the many
    # renders (a still cover, a Ken Burns pan) that never ask for a flash.
    # Do not hoist this to module scope.
    import numpy as np

    raw = decode_pcm(audio, sample_rate=sr, channels=1)
    # A truncated decode (a corrupt or half-written file) can leave a partial
    # sample at the tail, which np.frombuffer rejects outright — drop it.
    itemsize = np.dtype(np.float32).itemsize
    x = np.frombuffer(raw[: len(raw) - len(raw) % itemsize], dtype=np.float32)
    hop = max(1, sr // fps)
    n = len(x) // hop
    if duration is not None:
        n = min(n, int(round(duration * fps)))
    if n <= 0:
        return []

    frames = x[: n * hop].reshape(n, hop)
    rms_db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1) + 1e-9) + 1e-9)
    flux = np.diff(rms_db, prepend=rms_db[0])
    flux[flux < 0] = 0
    scale = np.percentile(flux, _PULSE_PERCENTILE) or 1.0
    pulse = np.clip(flux / scale, 0, 1)

    # The decay is a recurrence (each frame depends on the previous *output*),
    # so it does not vectorize; at one step per video frame it is cheap anyway.
    out = np.empty(n)
    acc = 0.0
    for i, v in enumerate(pulse):
        acc = max(float(v), acc * decay)
        out[i] = acc
    return out.tolist()


def _write_flash_script(
    envelope: list[float],
    path: PathLike,
    *,
    fps: int,
    target: str,
    brightness: float,
    saturation: float,
) -> Path:
    """Write a ``sendcmd`` script pulsing ``target``'s lookup table.

    Each frame ``i`` gets one command per component of
    :data:`FLASH_COMPONENTS`, re-stating the ``lutyuv`` expression for a
    brightness of ``brightness * envelope[i]`` and a saturation of ``1 +
    saturation * envelope[i]`` — so at rest (envelope 0) the filter is a no-op,
    and on a beat it brightens and intensifies. ``lutyuv`` rebuilds its 256-entry
    table when a component expression is set, which is what makes it drivable at
    all; unlike ``eq`` it needs no ``eval=frame``, because the table IS the state.

    The expressions contain ``,``, which ``sendcmd``'s own parser reads as "next
    command in this interval", so each is single-quoted. That is ``sendcmd``'s
    quoting, not the filtergraph's — this file is never parsed as a graph, so
    :func:`~muvid.visualize.canvas.escape_filter_value` is the wrong tool here
    and is used only on the script's *path*, which does go into a graph.

    Args:
        envelope: Per-frame pulse from :func:`onset_envelope`.
        path: Where to write the script.
        fps: Frame rate (to turn frame index into a timestamp).
        target: The ``sendcmd`` target — a filter labelled ``name@label``.
        brightness: Peak brightness boost (an additive offset, -1..1).
        saturation: Peak saturation boost added to 1.0.

    Returns:
        The written path.
    """
    lines = []
    for i, v in enumerate(envelope):
        t = i / fps
        exprs = brightness_saturation_lut(
            brightness=brightness * v, saturation=1 + saturation * v
        )
        for component in FLASH_COMPONENTS:
            lines.append(f"{t:.3f} [enter] {target} {component} '{exprs[component]}';")
    path = Path(path)
    path.write_text("\n".join(lines))
    return path


def flash_filter(
    audio: PathLike,
    *,
    fps: int,
    duration: float | None,
    workdir: Path,
    label: str = DEFAULT_FLASH_LABEL,
    brightness: float = FLASH_BRIGHTNESS,
    saturation: float = FLASH_SATURATION,
    decay: float = FLASH_DECAY,
) -> str:
    """A filter fragment that makes the stream it follows pulse with the beat.

    Computes the envelope, writes the ``sendcmd`` script into ``workdir``, and
    returns the chain ``,sendcmd=f=…,lutyuv@<label>=…`` to append after the visual
    filter (e.g. ``showspectrum``).

    Returns ``""`` — a fragment that changes nothing — when the audio yields no
    envelope or this ffmpeg build lacks :data:`FLASH_FILTERS`, so a caller can
    append it unconditionally and still render.

    The ``lutyuv`` starts as an identity table (brightness 0, saturation 1); the
    script drives it. It is a LUT rather than an ``eq`` because ``eq`` exists only
    in a GPL-configured ffmpeg (muvid#69) — see
    :func:`~muvid.visualize.canvas.brightness_saturation_lut`.

    Args:
        audio: The track whose beats drive the flash.
        fps: The render's frame rate (one command per component per frame).
        duration: Clamp the flash to this many seconds (``None`` = whole track).
        workdir: Directory to write the ``sendcmd`` script into.
        label: ``sendcmd`` label for this flash's ``lutyuv``.
        brightness: Peak brightness boost on a beat.
        saturation: Peak saturation boost on a beat.
        decay: Per-frame afterglow of a pulse.
    """
    if not all(has_filter(name) for name in FLASH_FILTERS):
        return ""
    envelope = onset_envelope(audio, fps=fps, duration=duration, decay=decay)
    if not envelope:
        return ""
    at_rest = lut_filter(brightness_saturation_lut(), label=label)
    # The sendcmd target is DERIVED from the filter the fragment declares, never
    # spelled a second time. A command addressed to a filter that is not in the
    # graph is completely silent — ffmpeg exits 0, logs nothing even at `warning`,
    # and simply never flashes — so the two spellings drifting apart would cost
    # the effect with nothing anywhere to say so.
    target = at_rest.split("=", 1)[0]
    script = _write_flash_script(
        envelope,
        Path(workdir) / f"{label}.cmd",
        fps=fps,
        target=target,
        brightness=brightness,
        saturation=saturation,
    )
    return f",sendcmd=f={escape_filter_value(str(script))},{at_rest}"
