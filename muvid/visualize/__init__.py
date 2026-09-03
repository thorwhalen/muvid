"""Turn a song (+ optional cover) into a visualizer music video.

The lightweight, deterministic, ffmpeg-only half of ``muvid``: given audio and
usually a cover image, it produces a 16:9, H.264, loudness-normalized mp4 — a
still cover, a Ken Burns pan, or an audio-reactive visualizer (CQT, spectrogram,
waveform, bars, vectorscope) — plus a matching thumbnail. No AI, no network; the
narrative pipeline (:mod:`muvid.facade`, :mod:`muvid.renderers`) is a separate
concern and this subpackage stands on its own.

The one call most people need:

    >>> from muvid.visualize import render_audio_video
    >>> render_audio_video("song.wav", image="cover.png")            # doctest: +SKIP

Everything else is a knob on that: :func:`~muvid.visualize.visuals.list_visuals`
names the built-in looks, :func:`~muvid.visualize.visuals.register_visual` adds
your own, :class:`~muvid.visualize.canvas.CoverLayout` controls how the cover
sits on the canvas, and :func:`~muvid.visualize.canvas.thumbnail_image` derives
a 16:9 thumbnail from that same composition. :func:`~muvid.visualize.verify.
verify_video` checks a render against what a platform will actually accept.

Because the whole song is known before the first frame is drawn, a visual can
also be driven by *precomputed* audio analysis:
:func:`~muvid.visualize.reactive.flash_filter` turns an onset envelope into an
ffmpeg ``sendcmd`` script, which is how the spectrogram pulses on the beat.

Needs ``ffmpeg`` (and ``ffprobe``) on the PATH. Every built-in visual is
ffmpeg-native, except Ken Burns, which renders through ``burns`` (already a
dependency of ``mixing``, so it needs no extra).
"""

from muvid.visualize.ffmpeg import (
    FfmpegError,
    Loudness,
    PathLike,
    decode_pcm,
    has_filter,
    measure_loudness,
    media_duration,
    probe,
    require_ffmpeg,
    run_ffmpeg,
)
from muvid.visualize.canvas import (
    DEFAULT_SIZE,
    THUMBNAIL_SIZE,
    CoverLayout,
    TitleStyle,
    canvas_image,
    thumbnail_image,
)
from muvid.visualize.reactive import (
    DEFAULT_FLASH_LABEL,
    ENVELOPE_SR,
    FLASH_BRIGHTNESS,
    FLASH_COMPONENTS,
    FLASH_DECAY,
    FLASH_FILTERS,
    FLASH_SATURATION,
    flash_filter,
    onset_envelope,
)
from muvid.visualize.visuals import (
    DEFAULT_TINT,
    VisualContext,
    VisualPlan,
    list_visuals,
    register_visual,
    resolve_visual,
)
from muvid.visualize.video import (
    DEFAULT_FPS,
    RenderResult,
    render_audio_video,
)
from muvid.visualize.verify import (
    Check,
    failures,
    report,
    verify_video,
)

__all__ = [
    "Check",
    "failures",
    "report",
    "verify_video",
    "FfmpegError",
    "Loudness",
    "PathLike",
    "decode_pcm",
    "has_filter",
    "measure_loudness",
    "media_duration",
    "probe",
    "require_ffmpeg",
    "run_ffmpeg",
    "DEFAULT_SIZE",
    "THUMBNAIL_SIZE",
    "CoverLayout",
    "TitleStyle",
    "canvas_image",
    "thumbnail_image",
    "DEFAULT_FLASH_LABEL",
    "ENVELOPE_SR",
    "FLASH_BRIGHTNESS",
    "FLASH_COMPONENTS",
    "FLASH_DECAY",
    "FLASH_FILTERS",
    "FLASH_SATURATION",
    "flash_filter",
    "onset_envelope",
    "DEFAULT_TINT",
    "VisualContext",
    "VisualPlan",
    "list_visuals",
    "register_visual",
    "resolve_visual",
    "DEFAULT_FPS",
    "RenderResult",
    "render_audio_video",
]
