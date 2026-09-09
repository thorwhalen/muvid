"""Draw a :class:`~muvid.choreo.scene.ChoreoScene` frame by frame and encode it.

numpy paints each frame — filled circles, rects, triangles, lines, diamonds
and rings, from coordinate grids and coverage masks with a one-pixel
anti-aliased edge — and the raw ``rgb24`` frames are piped straight into
ffmpeg's stdin, which muxes the song and encodes the YouTube-spec H.264/AAC
mp4 :mod:`muvid.visualize.video` produces. Nothing touches the disk between
the scene and the mp4: a 3-minute 720p render is ~15 GB of frames, and that
is why this is a pipe and not a frame directory.

Two things keep it fast enough on a CPU:

* **only the dirty pixels are touched** — every primitive computes its
  bounding box and paints inside it; a frame's cost is the sum of the objects
  alive in it, not the canvas size;
* **the frame buffer is the backdrop copied**, so a frame with nothing alive
  costs one ``memcpy``.

This is the ONE place choreo spawns ffmpeg itself rather than through
:func:`muvid.visualize.ffmpeg.run_ffmpeg`, because that helper has no stdin
seam. It uses the same binary check, the same timeout env var and the same
encode/container arguments, so the mp4 it writes is the one ``verify_video``
expects; when a streaming primitive lands in ``muvid.visualize.ffmpeg`` this
should collapse onto it.
"""

from __future__ import annotations

import math
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from muvid.choreo.scene import Backdrop, ChoreoScene, Obj, unit_hash
from muvid.visualize.ffmpeg import (
    FFMPEG_TIMEOUT_ENV_VAR,
    FfmpegError,
    media_duration,
    require_ffmpeg,
)
from muvid.visualize.video import (
    DEFAULT_AUDIO_BITRATE,
    DEFAULT_CRF,
    DEFAULT_GOP_SECONDS,
    DEFAULT_PRESET,
    _audio_encode_args,
    _container_args,
    _gop_frames,
    _video_encode_args,
)

__all__ = ["RenderedVideo", "Painter", "render_scene", "iter_frames"]

#: How far the vignette darkens the corners (0 = none).
VIGNETTE_STRENGTH = 0.6


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderedVideo:
    """What :func:`render_scene` produced, and how long it took."""

    output: Path
    duration_s: float
    n_frames: int
    render_s: float

    @property
    def frames_per_second(self) -> float:
        return self.n_frames / self.render_s if self.render_s > 0 else float("inf")


# --------------------------------------------------------------------------
# colours and backdrops
# --------------------------------------------------------------------------


def _rgb(hex_colour: str) -> np.ndarray:
    h = hex_colour.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def backdrop_image(bd: Backdrop, width: int, height: int) -> np.ndarray:
    """The ``uint8`` HxWx3 image behind the objects for one backdrop."""
    top, bottom = _rgb(bd.top), _rgb(bd.bottom)
    if bd.kind == "gradient":
        t = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
        img = top[None, None, :] * (1 - t) + bottom[None, None, :] * t
        img = np.broadcast_to(img, (height, width, 3))
    elif bd.kind == "vignette":
        ys = (np.arange(height, dtype=np.float32) - height / 2) / (height / 2)
        xs = (np.arange(width, dtype=np.float32) - width / 2) / (width / 2)
        d2 = (ys[:, None] ** 2 + xs[None, :] ** 2) / 2.0
        img = top[None, None, :] * (1.0 - VIGNETTE_STRENGTH * d2)[:, :, None]
    else:
        img = np.broadcast_to(top[None, None, :], (height, width, 3))
    return np.clip(img + 0.5, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# primitives — each paints only inside its bounding box
# --------------------------------------------------------------------------


def _blend(region: np.ndarray, cov: np.ndarray, colour: np.ndarray) -> None:
    """``region = region*(1-cov) + colour*cov`` in place, on a uint8 view."""
    if not cov.any():
        return
    c = cov[:, :, None]
    mixed = region.astype(np.float32) * (1.0 - c) + colour[None, None, :] * c
    region[...] = np.clip(mixed + 0.5, 0, 255).astype(np.uint8)


def _box(cx: float, cy: float, hw: float, hh: float, W: int, H: int):
    x0, x1 = max(0, int(math.floor(cx - hw - 1))), min(W, int(math.ceil(cx + hw + 2)))
    y0, y1 = max(0, int(math.floor(cy - hh - 1))), min(H, int(math.ceil(cy + hh + 2)))
    return (x0, x1, y0, y1) if x1 > x0 and y1 > y0 else None


def _grid(box, X: np.ndarray, Y: np.ndarray, cx: float, cy: float):
    x0, x1, y0, y1 = box
    return X[x0:x1][None, :] - cx, Y[y0:y1][:, None] - cy


def draw_circle(frame, X, Y, *, cx, cy, r, colour, alpha, inner=0.0):
    box = _box(cx, cy, r, r, frame.shape[1], frame.shape[0])
    if box is None or r <= 0:
        return
    dx, dy = _grid(box, X, Y, cx, cy)
    d = np.sqrt(dx * dx + dy * dy)
    cov = np.clip(r + 0.5 - d, 0.0, 1.0)
    if inner > 0:
        cov = cov - np.clip(inner + 0.5 - d, 0.0, 1.0)
    _blend(frame[box[2]:box[3], box[0]:box[1]], cov * alpha, colour)


def draw_rect(frame, X, Y, *, cx, cy, hw, hh, colour, alpha):
    box = _box(cx, cy, hw, hh, frame.shape[1], frame.shape[0])
    if box is None or hw <= 0 or hh <= 0:
        return
    dx, dy = _grid(box, X, Y, cx, cy)
    cov = np.clip(hw + 0.5 - np.abs(dx), 0.0, 1.0) * np.clip(hh + 0.5 - np.abs(dy), 0.0, 1.0)
    _blend(frame[box[2]:box[3], box[0]:box[1]], cov * alpha, colour)


def draw_diamond(frame, X, Y, *, cx, cy, r, colour, alpha):
    box = _box(cx, cy, r, r, frame.shape[1], frame.shape[0])
    if box is None or r <= 0:
        return
    dx, dy = _grid(box, X, Y, cx, cy)
    # L1 distance to the edge, scaled to ~pixels for a 1-px soft edge
    cov = np.clip((r - (np.abs(dx) + np.abs(dy))) / math.sqrt(2) + 0.5, 0.0, 1.0)
    _blend(frame[box[2]:box[3], box[0]:box[1]], cov * alpha, colour)


def draw_triangle(frame, X, Y, *, cx, cy, hw, hh, colour, alpha):
    """An upward triangle: apex at ``cy - hh``, base at ``cy + hh``, half-width ``hw``."""
    box = _box(cx, cy, hw, hh, frame.shape[1], frame.shape[0])
    if box is None or hw <= 0 or hh <= 0:
        return
    dx, dy = _grid(box, X, Y, cx, cy)
    base = hh - dy                                  # positive inside, above the base
    # the two slanted edges: signed distance to the line through apex and base corner
    n = math.hypot(2 * hh, hw)
    left = (2 * hh * (dx + hw) - hw * (dy + hh)) / n   # >0 to the right of the left edge
    right = (2 * hh * (hw - dx) - hw * (dy + hh)) / n
    cov = np.clip(np.minimum(np.minimum(base, left), right) + 0.5, 0.0, 1.0)
    _blend(frame[box[2]:box[3], box[0]:box[1]], cov * alpha, colour)


def draw_line(frame, X, Y, *, cx, cy, length, thickness, angle_deg, colour, alpha):
    """A capsule: a segment of ``length`` through ``(cx, cy)`` at ``angle_deg``."""
    ux, uy = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    hl, ht = length / 2, thickness / 2
    ext = hl * max(abs(ux), abs(uy)) + ht + 1
    box = _box(cx, cy, hl * abs(ux) + ht, hl * abs(uy) + ht, frame.shape[1], frame.shape[0])
    if box is None or length <= 0 or thickness <= 0 or ext <= 0:
        return
    dx, dy = _grid(box, X, Y, cx, cy)
    along = np.clip(dx * ux + dy * uy, -hl, hl)
    px, py = dx - along * ux, dy - along * uy
    d = np.sqrt(px * px + py * py)
    cov = np.clip(ht + 0.5 - d, 0.0, 1.0)
    _blend(frame[box[2]:box[3], box[0]:box[1]], cov * alpha, colour)


# --------------------------------------------------------------------------
# object state at time t
# --------------------------------------------------------------------------


def _state(o: Obj, t: float, k: int) -> tuple[float, float, float, float]:
    """``(x, y, size, alpha)`` of ``o`` at time ``t`` (frame ``k``), normalised."""
    tau = t - o.t_born
    life = max(1e-6, o.t_die - o.t_born)
    alpha = 1.0
    if o.attack_s > 0:
        alpha *= min(1.0, tau / o.attack_s)
    if o.release_s > 0:
        alpha *= min(1.0, (o.t_die - t) / o.release_s)
    x, y, size = o.x, o.y, o.size
    if o.motion == "scroll":
        x += o.vx * tau
        y += o.vy * tau
    elif o.motion == "drift":
        f = tau * (1.0 - tau / (2.0 * life))  # decelerates to rest at t_die
        x += o.vx * f
        y += o.vy * f
    elif o.motion == "pulse":
        size *= 1.0 + 0.35 * math.exp(-6.0 * tau / life)
    elif o.motion == "flicker":
        x += (unit_hash(o.seed, k, 1) - 0.5) * 0.01
        y += (unit_hash(o.seed, k, 2) - 0.5) * 0.01
        alpha *= 0.6 + 0.4 * unit_hash(o.seed, k, 3)
    return x, y, size, max(0.0, alpha)


class Painter:
    """Paints frames of one scene. Holds the grids and backdrops between frames."""

    def __init__(self, scene: ChoreoScene):
        self.scene = scene
        self.W, self.H = scene.canvas.width, scene.canvas.height
        self.X = np.arange(self.W, dtype=np.float32) + 0.5
        self.Y = np.arange(self.H, dtype=np.float32) + 0.5
        self._backdrops = [(bd.start, bd.end, backdrop_image(bd, self.W, self.H))
                           for bd in scene.backdrops]
        self._fallback = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        self._colours: dict[str, np.ndarray] = {}
        self._objects = sorted(scene.objects, key=lambda o: o.t_born)
        self._next = 0
        self._active: list[Obj] = []

    def _colour(self, hex_colour: str) -> np.ndarray:
        c = self._colours.get(hex_colour)
        if c is None:
            c = self._colours[hex_colour] = _rgb(hex_colour)
        return c

    def _backdrop_at(self, t: float) -> np.ndarray:
        for start, end, img in self._backdrops:
            if start <= t < end:
                return img
        return self._backdrops[-1][2] if self._backdrops else self._fallback

    def alive(self, t: float) -> list[Obj]:
        """Objects alive at ``t``. Frames must be asked for in increasing ``t``."""
        objs = self._objects
        while self._next < len(objs) and objs[self._next].t_born <= t:
            self._active.append(objs[self._next])
            self._next += 1
        self._active = [o for o in self._active if o.t_die > t]
        return sorted(self._active, key=lambda o: o.layer)

    def draw(self, frame: np.ndarray, o: Obj, t: float, k: int) -> None:
        x, y, size, alpha = _state(o, t, k)
        if alpha <= 0.0 or size <= 0.0:
            return
        cx, cy, s = x * self.W, y * self.H, size * self.H
        colour = self._colour(o.colour)
        if o.kind == "circle":
            draw_circle(frame, self.X, self.Y, cx=cx, cy=cy, r=s / 2, colour=colour, alpha=alpha)
        elif o.kind == "ring":
            draw_circle(frame, self.X, self.Y, cx=cx, cy=cy, r=s / 2, colour=colour,
                        alpha=alpha, inner=s * 0.35)
        elif o.kind == "rect":
            draw_rect(frame, self.X, self.Y, cx=cx, cy=cy, hw=s * o.aspect / 2, hh=s / 2,
                      colour=colour, alpha=alpha)
        elif o.kind == "triangle":
            draw_triangle(frame, self.X, self.Y, cx=cx, cy=cy, hw=s * o.aspect / 2, hh=s / 2,
                          colour=colour, alpha=alpha)
        elif o.kind == "diamond":
            draw_diamond(frame, self.X, self.Y, cx=cx, cy=cy, r=s / 2, colour=colour, alpha=alpha)
        elif o.kind == "line":
            draw_line(frame, self.X, self.Y, cx=cx, cy=cy, length=s,
                      thickness=max(1.0, s / max(1e-6, o.aspect)), angle_deg=o.angle,
                      colour=colour, alpha=alpha)

    def frame(self, k: int) -> np.ndarray:
        """Frame ``k`` (at ``k / fps`` seconds) as a fresh ``uint8`` HxWx3 array."""
        t = k / self.scene.canvas.fps
        frame = self._backdrop_at(t).copy()
        for o in self.alive(t):
            self.draw(frame, o, t, k)
        return frame


def n_frames(scene: ChoreoScene) -> int:
    return max(1, int(round(scene.duration * scene.canvas.fps)))


def iter_frames(scene: ChoreoScene) -> Iterator[np.ndarray]:
    """Every frame of ``scene``, in order."""
    painter = Painter(scene)
    for k in range(n_frames(scene)):
        yield painter.frame(k)


# --------------------------------------------------------------------------
# encode
# --------------------------------------------------------------------------


def _timeout_s() -> float | None:
    raw = os.environ.get(FFMPEG_TIMEOUT_ENV_VAR)
    try:
        value = float(raw) if raw else None
    except ValueError:
        return None
    return value if value and value > 0 else None


def encode_command(
    scene: ChoreoScene,
    *,
    audio: Path,
    output: Path,
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
    audio_bitrate: str = DEFAULT_AUDIO_BITRATE,
    gop_seconds: float = DEFAULT_GOP_SECONDS,
) -> list[str]:
    """The ffmpeg command that reads raw frames on stdin and writes the mp4."""
    c = scene.canvas
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{c.width}x{c.height}",
        "-r", str(c.fps), "-i", "pipe:0",
        "-i", str(audio),
        "-map", "0:v", "-map", "1:a",
        *_video_encode_args(crf=crf, preset=preset, fps=c.fps,
                            gop=_gop_frames(c.fps, gop_seconds)),
        *_audio_encode_args(audio_bitrate),
        "-t", f"{scene.duration:.3f}", "-shortest",
        *_container_args(),
        # The manifest promises video/mp4 and the contract promises to write
        # EXACTLY request.output — whatever it is called. Name the muxer rather
        # than letting ffmpeg guess it from an extension the host may not use.
        "-f", "mp4",
        str(output),
    ]


def render_scene(
    scene: ChoreoScene,
    *,
    audio: Path | str,
    output: Path | str,
    workdir: Path | str,
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
) -> RenderedVideo:
    """Paint every frame of ``scene``, pipe them into ffmpeg, mux ``audio``.

    Raises:
        FfmpegError: ffmpeg exited non-zero, died mid-stream, or overran
            ``$MUVID_FFMPEG_TIMEOUT_S``; the message carries the log's tail.
        ValueError: the canvas has an odd dimension (yuv420p cannot encode one).
    """
    c = scene.canvas
    if c.width % 2 or c.height % 2:
        raise ValueError(
            f"canvas {c.width}x{c.height} has an odd dimension; H.264 at yuv420p "
            "needs even ones"
        )
    require_ffmpeg()
    audio, output, workdir = Path(audio), Path(output), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = encode_command(scene, audio=audio, output=output, crf=crf, preset=preset)
    log = workdir / "ffmpeg.log"
    timeout = _timeout_s()
    started = time.monotonic()
    total = n_frames(scene)
    painter = Painter(scene)
    with log.open("wb") as log_f:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=log_f)
        assert proc.stdin is not None
        try:
            for k in range(total):
                if timeout is not None and time.monotonic() - started > timeout:
                    proc.kill()
                    raise FfmpegError(
                        f"choreo render exceeded {timeout:g}s (bound by "
                        f"${FFMPEG_TIMEOUT_ENV_VAR}) at frame {k}/{total}"
                    )
                try:
                    proc.stdin.write(painter.frame(k).tobytes())
                except BrokenPipeError:
                    break  # ffmpeg died; its exit status says why below
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
            remaining = None if timeout is None else max(1.0, timeout - (time.monotonic() - started))
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise FfmpegError(
                    f"ffmpeg timed out after {timeout:g}s (bound by ${FFMPEG_TIMEOUT_ENV_VAR})."
                    f"\n\ncommand: {shlex.join(cmd)}"
                ) from None
        finally:
            if proc.poll() is None:
                proc.kill()
    if proc.returncode != 0:
        tail = "\n".join(log.read_text(errors="replace").strip().splitlines()[-15:])
        raise FfmpegError(
            f"ffmpeg exited {proc.returncode}.\n\n{tail}\n\ncommand: {shlex.join(cmd)}"
        )
    return RenderedVideo(
        output=output, duration_s=media_duration(output), n_frames=total,
        render_s=time.monotonic() - started,
    )
