"""Low-level ffmpeg/ffprobe primitives shared across :mod:`muvid.visualize`.

Thin, dependency-free wrappers around the ``ffmpeg``/``ffprobe`` binaries that
``muvid.visualize`` assumes on the PATH: running a command with a readable
error, probing duration and streams, checking that an optional filter was
compiled in, and measuring loudness (EBU R128) for two-pass normalization.

Nothing here knows about music or a destination platform — it is the shared
substrate under :mod:`muvid.visualize.canvas`, :mod:`muvid.visualize.visuals`,
and :mod:`muvid.visualize.video`.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PathLike = str | Path

#: Env var bounding a single ffmpeg invocation's wall-clock (seconds). Unset =
#: no timeout (the historical behaviour). A long-running host (the reelee MCP
#: connector renders synchronously over HTTP) sets this so an oversized or
#: adversarial input can't pin a worker indefinitely — a belt-and-braces bound
#: on top of the caller's own input-duration cap.
FFMPEG_TIMEOUT_ENV_VAR = "MUVID_FFMPEG_TIMEOUT_S"


def _ffmpeg_timeout() -> float | None:
    """The per-invocation ffmpeg timeout from ``$MUVID_FFMPEG_TIMEOUT_S`` (or ``None``)."""
    raw = os.environ.get(FFMPEG_TIMEOUT_ENV_VAR)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


#: Loudness targets, in the units EBU R128 / ffmpeg ``loudnorm`` uses.
#: The defaults match what YouTube normalizes playback to, so a track mastered
#: here is neither turned down nor left quiet relative to the rest of a playlist.
DEFAULT_LOUDNESS_I = -14.0  # integrated loudness, LUFS
DEFAULT_LOUDNESS_TP = -1.0  # true peak, dBTP
DEFAULT_LOUDNESS_LRA = 11.0  # loudness range, LU


class FfmpegError(RuntimeError):
    """An ffmpeg/ffprobe invocation failed, or a needed tool/filter is absent."""


@dataclass(frozen=True)
class Loudness:
    """An EBU R128 loudness target, plus the measurement of a specific track.

    ``measured`` is the ``loudnorm`` analysis pass output (``None`` until
    :func:`measure_loudness` has run). Carrying both lets :meth:`filter_spec`
    emit the accurate two-pass filter when a measurement exists and fall back
    to the (less accurate) single-pass form when it does not.
    """

    integrated: float = DEFAULT_LOUDNESS_I
    true_peak: float = DEFAULT_LOUDNESS_TP
    lra: float = DEFAULT_LOUDNESS_LRA
    measured: dict | None = None

    def filter_spec(self) -> str:
        """The ``loudnorm`` filter string for this target."""
        base = f"loudnorm=I={self.integrated}:TP={self.true_peak}:LRA={self.lra}"
        if not self.measured:
            return base
        m = self.measured
        return (
            f"{base}"
            f":measured_I={m['input_i']}"
            f":measured_TP={m['input_tp']}"
            f":measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}"
            f":offset={m['target_offset']}"
            ":linear=true:print_format=summary"
        )


def require_ffmpeg(*tools: str) -> None:
    """Raise a helpful :class:`FfmpegError` if any of ``tools`` is not on PATH.

    Args:
        *tools: Binaries to require (defaults to ``ffmpeg`` and ``ffprobe``).

    Raises:
        FfmpegError: With per-platform install instructions.
    """
    missing = [t for t in (tools or ("ffmpeg", "ffprobe")) if shutil.which(t) is None]
    if missing:
        raise FfmpegError(
            f"{', '.join(missing)} not found on PATH. muvid's media rendering needs "
            "ffmpeg (which ships ffprobe). Install it with:\n"
            "  macOS:          brew install ffmpeg\n"
            "  Debian/Ubuntu:  sudo apt-get install ffmpeg\n"
            "  Windows:        winget install ffmpeg\n"
            "See https://ffmpeg.org/download.html"
        )


def _run_bounded(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run an ffmpeg/ffprobe ``cmd`` (capturing output), bounded by the timeout.

    EVERY ffmpeg/ffprobe spawn that may decode caller-supplied media goes through here,
    so ``$MUVID_FFMPEG_TIMEOUT_S`` bounds them all consistently: a metadata-spoofing or
    decode-bomb input can't pin a worker past the timeout on the synchronous render path
    (the loudnorm analysis pass and ffprobe are full/partial decodes, not just the mux).
    """
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=_ffmpeg_timeout()
        )
    except subprocess.TimeoutExpired as e:
        raise FfmpegError(
            f"{cmd[0]} timed out after {e.timeout:g}s "
            f"(bound by ${FFMPEG_TIMEOUT_ENV_VAR}).\n\ncommand: {shlex.join(cmd)}"
        ) from e


def run_ffmpeg(
    args: list[str], *, overwrite: bool = True
) -> subprocess.CompletedProcess:
    """Run ``ffmpeg`` with ``args``, raising a readable error on failure.

    Args:
        args: Arguments after the global flags (inputs, filters, output).
        overwrite: Pass ``-y`` (overwrite the output without prompting).

    Returns:
        The completed process.

    Raises:
        FfmpegError: ffmpeg exited non-zero; the message carries the tail of
            stderr and the full command, which is what you actually need to
            debug a filtergraph.
    """
    require_ffmpeg("ffmpeg")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if overwrite:
        cmd.append("-y")
    cmd += [str(a) for a in args]
    proc = _run_bounded(cmd)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        raise FfmpegError(
            f"ffmpeg exited {proc.returncode}.\n\n{tail}\n\ncommand: {shlex.join(cmd)}"
        )
    return proc


def probe(media: PathLike) -> dict:
    """Return ``ffprobe``'s ``format`` + ``streams`` JSON for ``media``."""
    require_ffmpeg("ffprobe")
    proc = _run_bounded(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(media),
        ]
    )
    if proc.returncode != 0:
        raise FfmpegError(f"ffprobe could not read {media!s}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def media_duration(media: PathLike) -> float:
    """Duration of ``media`` in seconds.

    Falls back to the longest stream duration when the container has none.

    Raises:
        FfmpegError: The duration could not be determined.
    """
    info = probe(media)
    duration = info.get("format", {}).get("duration")
    if duration is None:
        durations = [
            float(s["duration"]) for s in info.get("streams", []) if s.get("duration")
        ]
        duration = max(durations) if durations else None
    if duration is None:
        raise FfmpegError(f"Could not determine the duration of {media!s}.")
    return float(duration)


@lru_cache(maxsize=1)
def _available_filters() -> frozenset[str]:
    require_ffmpeg("ffmpeg")
    proc = _run_bounded(["ffmpeg", "-hide_banner", "-filters"])
    names = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        # Lines look like: " T.. showcqt   A->V   Convert input audio to a CQT..."
        if len(parts) >= 2 and not line.startswith("Filters:"):
            names.add(parts[1])
    return frozenset(names)


def has_filter(name: str) -> bool:
    """Whether this ffmpeg build has the ``name`` filter compiled in."""
    return name in _available_filters()


def require_filter(name: str, *, needed_for: str) -> None:
    """Raise unless this ffmpeg build has the ``name`` filter.

    Filters like ``drawtext`` (libfreetype) and ``showcqt`` (libfftw/avfilter
    extras) are build-time options, so a working ffmpeg is not enough — the
    specific filter has to be there.

    Raises:
        FfmpegError: Naming the filter, the feature that needs it, and the fix.
    """
    if not has_filter(name):
        raise FfmpegError(
            f"This ffmpeg build has no {name!r} filter, which {needed_for} needs. "
            "Install a full-featured build (Debian/Ubuntu: 'sudo apt-get install "
            "ffmpeg'; macOS: 'brew install ffmpeg'), or choose another visual. "
            "Run 'ffmpeg -filters' to see what your build supports."
        )


def measure_loudness(audio: PathLike, target: Loudness | None = None) -> Loudness:
    """Analyse ``audio`` (loudnorm pass 1) and return ``target`` with the result.

    Two-pass ``loudnorm`` is the only accurate way to hit a loudness target:
    pass 1 measures the program loudness, pass 2 applies a *linear* gain from
    that measurement. Single-pass loudnorm is a dynamic normalizer and will
    both miss the target and squash the dynamics of music.

    Args:
        audio: The audio (or video) file to measure.
        target: The loudness target; a default one is used when omitted.

    Returns:
        A new :class:`Loudness` with ``measured`` populated.
    """
    target = target or Loudness()
    require_ffmpeg("ffmpeg")
    proc = _run_bounded(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(audio),
            "-af",
            f"loudnorm=I={target.integrated}:TP={target.true_peak}"
            f":LRA={target.lra}:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    measured = _last_json_object(proc.stderr or "")
    if measured is None:
        raise FfmpegError(
            f"Could not measure the loudness of {audio!s}.\n"
            f"{(proc.stderr or '').strip()[-500:]}"
        )
    return Loudness(
        integrated=target.integrated,
        true_peak=target.true_peak,
        lra=target.lra,
        measured=measured,
    )


def _last_json_object(text: str) -> dict | None:
    """Extract the last ``{...}`` block from ffmpeg's stderr (loudnorm's report)."""
    start = text.rfind("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
