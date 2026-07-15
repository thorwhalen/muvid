"""Render an audio file into a video: pick a visual, mux, normalize, encode.

This is the assembler. A :mod:`~muvid.visualize.visuals` strategy says what the
frames look like; everything platform-facing lives here — the 16:9 canvas, the
H.264/AAC encode YouTube prefers, EBU R128 loudness normalization, and the
guarantee that the video ends exactly when the song does.

    >>> from muvid.visualize import render_audio_video
    >>> result = render_audio_video("song.wav", image="cover.png")  # doctest: +SKIP
    >>> result.path, result.duration                                # doctest: +SKIP
    (PosixPath('song.mp4'), 154.92)
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from muvid.visualize.canvas import (
    DEFAULT_SIZE,
    CoverLayout,
    TitleStyle,
    cover_chain,
    overlay_chain,
    title_chain,
)
from muvid.visualize.ffmpeg import (
    Loudness,
    PathLike,
    media_duration,
    measure_loudness,
    require_ffmpeg,
    run_ffmpeg,
)
from muvid.visualize.visuals import Visual, VisualContext, VisualPlan, resolve_visual

DEFAULT_FPS = 24

#: Constant-rate-factor: visually lossless enough for a source YouTube re-encodes.
DEFAULT_CRF = 18
DEFAULT_PRESET = "medium"

#: YouTube's recommended audio for stereo uploads.
DEFAULT_AUDIO_BITRATE = "384k"

#: Pinned deliberately: ``loudnorm`` upsamples to 192 kHz internally for true-peak
#: detection, and leaks that rate into the output unless the rate is fixed here.
AUDIO_SAMPLE_RATE = 48000

#: Seconds between keyframes. YouTube nominally asks for a GOP of half the frame
#: rate (a keyframe every 0.5 s); for the static and near-static pictures this
#: module produces, that multiplies the intra-frames — and the upload size — for
#: no gain, since YouTube re-encodes anyway. Two seconds is the usual compromise.
#: Pass ``gop_seconds=0.5`` for strict compliance.
DEFAULT_GOP_SECONDS = 2.0

#: Length of the single segment the still fast-path encodes before looping it.
_STILL_SEGMENT_SECONDS = 2.0


@dataclass
class RenderResult:
    """A rendered video and what is worth knowing about it.

    Usable anywhere a path is (it implements ``os.PathLike``).

    Attributes:
        path: The rendered mp4.
        duration: Its duration in seconds.
        size: Frame size.
        fps: Frame rate.
        visual: The strategy that produced it.
        loudness: The applied loudness target and measurement, if normalized.
        canvas: The composed canvas image, when the strategy built one — reuse
            it as the thumbnail rather than re-deriving it.
    """

    path: Path
    duration: float
    size: tuple[int, int]
    fps: int
    visual: str
    loudness: Loudness | None = None
    canvas: Path | None = None
    extras: dict = field(default_factory=dict)

    def __fspath__(self) -> str:
        return str(self.path)


def render_audio_video(
    audio: PathLike,
    image: PathLike | None = None,
    *,
    visual: str | Visual = "auto",
    saveas: PathLike | None = None,
    size: tuple[int, int] = DEFAULT_SIZE,
    fps: int = DEFAULT_FPS,
    title: str | None = None,
    layout: CoverLayout | None = None,
    title_style: TitleStyle | None = None,
    normalize: bool = False,
    loudness: Loudness | None = None,
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
    audio_bitrate: str = DEFAULT_AUDIO_BITRATE,
    gop_seconds: float = DEFAULT_GOP_SECONDS,
    options: dict | None = None,
    workdir: PathLike | None = None,
) -> RenderResult:
    """Render ``audio`` into a video, using ``visual`` for the picture.

    The video is exactly as long as the audio, 16:9, H.264/yuv420p + AAC — what
    YouTube asks for. With ``normalize=True`` the audio is brought to a fixed
    EBU R128 loudness with a two-pass ``loudnorm``, which is what makes a batch
    of songs play back at a consistent level.

    Args:
        audio: The song (``.wav`` is preferred when you have it — YouTube
            re-encodes regardless, so give it the cleanest input).
        image: Cover art. Used for the picture, and composed onto a 16:9 canvas.
        visual: A registered strategy name (``"still"``, ``"ken_burns"``,
            ``"cqt"``, ``"bars"``, ``"spectrum"``, ``"waves"``, ``"scope"``),
            ``"auto"``, or any callable (see :mod:`muvid.visualize.visuals`).
        saveas: Output path (default: ``<audio-stem>.mp4``).
        size: Canvas size; the default is 1080p.
        fps: Frame rate.
        title: Burn this title into the frame.
        layout: How the cover sits on the canvas.
        title_style: How the title is drawn.
        normalize: Loudness-normalize the audio (two-pass EBU R128).
        loudness: The loudness target; a YouTube-appropriate default is used
            when omitted.
        crf / preset / audio_bitrate / gop_seconds: Encoder knobs.
        options: Strategy-specific options, passed to the visual.
        workdir: Where intermediates go (a temporary directory by default).

    Returns:
        A :class:`RenderResult`.

    Raises:
        ValueError: ``size`` has an odd dimension — H.264 at yuv420p (the only
            pixel format every player decodes) cannot encode one.
    """
    # Arguments first, environment second: a caller who passed an odd size has a
    # bug worth reporting whether or not ffmpeg happens to be installed.
    _check_even(size)
    require_ffmpeg()
    audio = Path(audio)
    out = Path(saveas) if saveas else audio.with_suffix(".mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    duration = media_duration(audio)

    with _work_dir(workdir) as work:
        ctx = VisualContext(
            audio=audio,
            image=Path(image) if image else None,
            duration=duration,
            size=size,
            fps=fps,
            layout=layout or CoverLayout(),
            title=title,
            title_style=title_style,
            workdir=work,
            options=dict(options or {}),
        )
        plan = resolve_visual(visual, ctx)

        loud = None
        if normalize:
            loud = measure_loudness(audio, loudness or Loudness())

        encode = dict(
            crf=crf, preset=preset, audio_bitrate=audio_bitrate, gop_seconds=gop_seconds
        )
        if plan.still is not None:
            _render_still(plan.still, ctx, out, loud=loud, **encode)
        else:
            _render_filtergraph(plan, ctx, out, loud=loud, **encode)

        canvas = plan.still if plan.still and plan.still.exists() else None
        if canvas and workdir is None:
            canvas = _keep(canvas, out.with_suffix(".canvas.png"))

    return RenderResult(
        path=out,
        duration=media_duration(out),
        size=size,
        fps=fps,
        visual=visual
        if isinstance(visual, str)
        else getattr(visual, "__name__", "custom"),
        loudness=loud,
        canvas=canvas,
    )


def _render_still(
    canvas: Path,
    ctx: VisualContext,
    out: Path,
    *,
    loud: Loudness | None,
    crf: int,
    preset: str,
    audio_bitrate: str,
    gop_seconds: float,
) -> None:
    """Encode one short segment of the static canvas, then loop it, copying.

    A still video is thousands of identical frames. Encoding them all is a
    waste: instead encode a couple of seconds once, then concatenate that
    segment with ``-c:v copy``, which touches no pixels. On a small machine
    this is an order of magnitude faster than the naive encode, and the output
    is byte-for-byte the same picture.

    The loop count is finite and the output is bounded by ``-t`` on purpose:
    ``-shortest`` alone does *not* stop an infinitely looping copied stream,
    and ffmpeg will happily fill the disk.
    """
    segment_seconds = min(_STILL_SEGMENT_SECONDS, ctx.duration)
    segment = ctx.workdir / "segment.mp4"
    # The segment must start on a keyframe and be a whole number of GOPs, or the
    # copies would not concatenate cleanly.
    gop = _gop_frames(ctx.fps, gop_seconds)
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-framerate",
            str(ctx.fps),
            "-i",
            str(canvas),
            "-t",
            f"{segment_seconds:.3f}",
            "-an",
            *_video_encode_args(crf=crf, preset=preset, fps=ctx.fps, gop=gop),
            "-tune",
            "stillimage",
            str(segment),
        ]
    )
    loops = math.ceil(ctx.duration / segment_seconds)
    run_ffmpeg(
        [
            "-stream_loop",
            str(loops),
            "-i",
            str(segment),
            "-i",
            str(ctx.audio),
            *(["-af", loud.filter_spec()] if loud else []),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            *_audio_encode_args(audio_bitrate),
            "-t",
            f"{ctx.duration:.3f}",
            *_container_args(),
            str(out),
        ]
    )


def _render_filtergraph(
    plan: VisualPlan,
    ctx: VisualContext,
    out: Path,
    *,
    loud: Loudness | None,
    crf: int,
    preset: str,
    audio_bitrate: str,
    gop_seconds: float,
) -> None:
    """The general path: build one filter_complex, encode video and audio."""
    inputs: list[str] = ["-i", str(ctx.audio)]
    for group in plan.inputs:
        inputs += [str(a) for a in group]

    chains: list[str] = []
    audio_label = "0:a"
    if plan.uses_audio:
        chains.append("[0:a]asplit=2[atrack][aviz]")
        audio_label = "[atrack]"
    chains += list(plan.filters)

    video = plan.video
    if ctx.image is not None and not plan.has_cover:
        index = 1 + len(plan.inputs)
        inputs += ["-loop", "1", "-framerate", str(ctx.fps), "-i", str(ctx.image)]
        chains.append(cover_chain(ctx.size, ctx.layout, src=f"{index}:v", out="_fgc"))
        chains.append(
            overlay_chain(background=video, cover="_fgc", out="_vc", shortest=True)
        )
        video = "_vc"

    if ctx.title and not plan.has_title:
        chains.append(
            title_chain(ctx.title, ctx.size, ctx.title_style, src=video, out="_vt")
        )
        video = "_vt"

    chains.append(f"[{video}]fps={ctx.fps},format=yuv420p[vout]")

    if loud:
        chains.append(f"{_as_label(audio_label)}{loud.filter_spec()}[aout]")
        audio_label = "[aout]"

    run_ffmpeg(
        [
            *inputs,
            "-filter_complex",
            ";".join(chains),
            "-map",
            "[vout]",
            "-map",
            audio_label,
            *_video_encode_args(
                crf=crf,
                preset=preset,
                fps=ctx.fps,
                gop=_gop_frames(ctx.fps, gop_seconds),
            ),
            *_audio_encode_args(audio_bitrate),
            # -t bounds the render; -shortest ends it with the audio. Note we do
            # NOT pass `-fflags +shortest`: the widely copied incantation cuts
            # tens of milliseconds off the end of the song.
            "-t",
            f"{ctx.duration:.3f}",
            "-shortest",
            *_container_args(),
            str(out),
        ]
    )


def _as_label(stream: str) -> str:
    """``0:a`` -> ``[0:a]``; an already-bracketed label passes through."""
    return stream if stream.startswith("[") else f"[{stream}]"


def _gop_frames(fps: int, gop_seconds: float) -> int:
    """Keyframe interval in frames (at least one)."""
    return max(1, int(round(fps * gop_seconds)))


def _container_args() -> list[str]:
    """Mux the mp4 the way YouTube asks for it.

    ``+faststart`` puts the moov atom first. The other two are the fix for a
    requirement that is easy to miss: YouTube's spec says *"No Edit Lists (or the
    video might not get processed correctly)"*, and ffmpeg writes an ``elst`` box
    by default (to signal AAC encoder priming delay). ``+faststart`` alone does
    not remove it — ``-use_editlist 0`` does, and ``+negative_cts_offsets``
    carries the B-frame timing that the edit list would otherwise have expressed.
    """
    return [
        "-use_editlist",
        "0",
        "-movflags",
        "+faststart+negative_cts_offsets",
    ]


def _video_encode_args(*, crf: int, preset: str, fps: int, gop: int) -> list[str]:
    """H.264 as YouTube asks for it: High profile, yuv420p, closed GOP, 2 B-frames."""
    return [
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-g",
        str(gop),
        # No -keyint_min: x264 clamps it to keyint/2+1 and silently ignores it.
        "-sc_threshold",
        "0",  # closed GOP: no scene-cut keyframes
        "-bf",
        "2",
    ]


def _audio_encode_args(bitrate: str) -> list[str]:
    """AAC-LC, 48 kHz, stereo. The sample rate is pinned on purpose — see above."""
    return [
        "-c:a",
        "aac",
        "-b:a",
        bitrate,
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-ac",
        "2",
    ]


def _check_even(size: tuple[int, int]) -> None:
    """H.264 at yuv420p cannot encode odd dimensions — fail before ffmpeg does.

    Cover art is routinely an odd square (1401x1401, 999x999); a canvas sized
    from it inherits the problem, and libx264's own error ("width not divisible
    by 2") is a long way from the cause.
    """
    width, height = size
    if width % 2 or height % 2:
        even = (width - width % 2, height - height % 2)
        raise ValueError(
            f"size={size} has an odd dimension; H.264 at yuv420p needs even ones "
            f"(the pixel format every player can decode). Use size={even}."
        )


class _work_dir:
    """Context manager yielding ``workdir``, or a temporary directory."""

    def __init__(self, workdir: PathLike | None):
        self._given = Path(workdir) if workdir else None
        self._tmp: tempfile.TemporaryDirectory | None = None

    def __enter__(self) -> Path:
        if self._given:
            self._given.mkdir(parents=True, exist_ok=True)
            return self._given
        self._tmp = tempfile.TemporaryDirectory(prefix="muvid-visualize-")
        return Path(self._tmp.name)

    def __exit__(self, *exc) -> None:
        if self._tmp:
            self._tmp.cleanup()


def _keep(src: Path, dest: Path) -> Path:
    """Copy a temporary artefact out before its temp dir is cleaned up."""
    dest.write_bytes(src.read_bytes())
    return dest
