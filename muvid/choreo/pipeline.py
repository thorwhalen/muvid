"""The choreo pipeline — the one path from a song to a finished video.

    audio (+ optional cover)
        -> Analysis        events, beat grid, sections   (muvid.choreo.analysis)
        -> TreatmentSpec   the creative decision          (muvid.choreo.spec)
        -> ChoreoScene     every object computed          (muvid.choreo.scene)
        -> mp4             numpy frames -> ffmpeg         (muvid.choreo.render)

Each arrow is a seam with a working default, so the whole thing runs on a bare
``song.wav`` with no cover, no AI, no network. :func:`render` satisfies
:class:`muvid.subgenres.Renderer`; it is what the manifest names.

Resource bounds are **refusals, not clamps** — the same shape and defaults as
:func:`muvid.lyricvid.pipeline.check_render_bounds` under ``MUVID_CHOREO_*``
names — because a clamped request silently produces a different video than
the one asked for. The bounds are checked before the analysis runs, so an
oversized request fails in milliseconds.

The cover, when given, is a **palette source only**: decoded once through
ffmpeg to a 24x24 thumbnail and reduced to six colours by numpy. No Pillow.
"""

from __future__ import annotations

import colorsys
import os
from pathlib import Path
from typing import Any, Mapping

from muvid.choreo import spec as spec_mod
from muvid.choreo.analysis import analyze
from muvid.choreo.scene import Canvas, compile_scene
from muvid.subgenres import RenderRequest, RenderResult

__all__ = [
    "MAX_DURATION_S",
    "MAX_FPS",
    "MAX_FRAMES",
    "MAX_INPUT_FILES",
    "MAX_PIXELS",
    "check_input_counts",
    "check_render_bounds",
    "palette_from_cover",
    "render",
]

#: Resource bounds, env-configurable. Same defaults as the lyric video's.
MAX_PIXELS = int(os.environ.get("MUVID_CHOREO_MAX_PIXELS", str(3840 * 2160)))
MAX_FPS = int(os.environ.get("MUVID_CHOREO_MAX_FPS", "60"))
MAX_DURATION_S = int(os.environ.get("MUVID_MAX_DURATION_S", str(15 * 60)))
MAX_FRAMES = int(os.environ.get("MUVID_CHOREO_MAX_FRAMES", str(15 * 60 * 30)))
#: How many files any one input may name. The inputs today are single paths;
#: the bound exists so a list-valued input added later is bounded from day one.
MAX_INPUT_FILES = int(os.environ.get("MUVID_CHOREO_MAX_INPUT_FILES", "64"))

#: Thumbnail size the cover is reduced to before colours are picked.
_PALETTE_THUMB = 24
DEFAULT_ARCHETYPE = "fischinger"


def check_render_bounds(canvas: Canvas, duration_s: float) -> None:
    """Refuse a render that would exceed the resource bounds. Refuse, not clamp.

    >>> check_render_bounds(Canvas(width=1920, height=1080, fps=30), 200.0)
    >>> check_render_bounds(Canvas(width=30000, height=30000, fps=30), 10.0)
    Traceback (most recent call last):
    ...
    ValueError: canvas 30000x30000 is 900000000 px/frame; the bound is 8294400 (MUVID_CHOREO_MAX_PIXELS)
    """
    px = canvas.width * canvas.height
    if canvas.width < 16 or canvas.height < 16:
        raise ValueError(f"canvas {canvas.width}x{canvas.height} is too small")
    if px > MAX_PIXELS:
        raise ValueError(
            f"canvas {canvas.width}x{canvas.height} is {px} px/frame; the bound is "
            f"{MAX_PIXELS} (MUVID_CHOREO_MAX_PIXELS)"
        )
    if not 1 <= canvas.fps <= MAX_FPS:
        raise ValueError(
            f"fps {canvas.fps} is outside 1..{MAX_FPS} (MUVID_CHOREO_MAX_FPS)"
        )
    if duration_s > MAX_DURATION_S:
        raise ValueError(
            f"audio is {duration_s:.0f}s; the render limit is {MAX_DURATION_S}s "
            "(MUVID_MAX_DURATION_S)"
        )
    frames = int(round(duration_s * canvas.fps))
    if frames > MAX_FRAMES:
        raise ValueError(
            f"{frames} frames ({duration_s:.0f}s at {canvas.fps} fps) exceeds "
            f"{MAX_FRAMES} (MUVID_CHOREO_MAX_FRAMES)"
        )


def check_input_counts(inputs: Mapping[str, Any]) -> None:
    """Refuse any input naming more than :data:`MAX_INPUT_FILES` files.

    >>> check_input_counts({'audio': 'a.wav'})
    >>> check_input_counts({'frames': ['x'] * 65})
    Traceback (most recent call last):
    ...
    ValueError: inputs['frames'] names 65 files; the bound is 64 (MUVID_CHOREO_MAX_INPUT_FILES)
    """
    for key, value in inputs.items():
        if isinstance(value, (list, tuple)) and len(value) > MAX_INPUT_FILES:
            raise ValueError(
                f"inputs[{key!r}] names {len(value)} files; the bound is "
                f"{MAX_INPUT_FILES} (MUVID_CHOREO_MAX_INPUT_FILES)"
            )


# --------------------------------------------------------------------------
# palette from a cover
# --------------------------------------------------------------------------


def _hex(rgb) -> str:
    return "#" + "".join(
        f"{int(round(min(1.0, max(0.0, float(c))) * 255)):02x}" for c in rgb
    )


def palette_from_cover(cover: Path | str, *, workdir: Path | str) -> spec_mod.Palette:
    """Six colours from an image: dark bg pair, light fg, three spread accents.

    The image is decoded by ffmpeg to a :data:`_PALETTE_THUMB` square (one
    process, any format ffmpeg reads), then: ``bg`` is the mean of the darkest
    quarter (darkened further so objects always read), ``bg2`` the next quarter,
    ``fg`` the lightest eighth pushed toward white, ``low`` the most saturated
    pixel, and ``mid``/``high`` that hue a third of a turn on — so the three
    bands are always distinguishable whatever the cover.
    """
    import numpy as np

    from muvid.visualize.ffmpeg import run_ffmpeg

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    raw = workdir / "cover_thumb.rgb"
    n = _PALETTE_THUMB
    run_ffmpeg(
        [
            "-i",
            str(cover),
            "-frames:v",
            "1",
            "-vf",
            f"scale={n}:{n}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            str(raw),
        ]
    )
    px = (
        np.frombuffer(raw.read_bytes(), dtype=np.uint8)
        .reshape(-1, 3)
        .astype(np.float32)
        / 255.0
    )
    lum = px @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    order = np.argsort(lum)
    q = max(1, len(px) // 4)
    bg = px[order[:q]].mean(axis=0) * 0.6
    bg2 = px[order[q : 2 * q]].mean(axis=0) * 0.75
    fg = px[order[-max(1, len(px) // 8) :]].mean(axis=0)
    fg = fg + (1.0 - fg) * 0.6
    sat = (px.max(axis=1) - px.min(axis=1)) / np.maximum(px.max(axis=1), 1e-6)
    accent = px[int(np.argmax(sat * (0.3 + lum)))]
    h, s, v = colorsys.rgb_to_hsv(*(float(c) for c in accent))
    s, v = max(s, 0.55), max(v, 0.85)
    low, mid, high = (colorsys.hsv_to_rgb((h + k / 3) % 1.0, s, v) for k in range(3))
    return spec_mod.Palette(
        bg=_hex(bg),
        bg2=_hex(bg2),
        fg=_hex(fg),
        low=_hex(low),
        mid=_hex(mid),
        high=_hex(high),
    )


# --------------------------------------------------------------------------
# the renderer
# --------------------------------------------------------------------------


def _resolve_treatment(
    params: Mapping[str, Any],
) -> tuple[spec_mod.TreatmentSpec, list[str], str, bool]:
    """``(treatment, repair_notes, source, palette_was_given)``."""
    raw = params.get("treatment")
    if raw is None:
        archetype = params.get("archetype", DEFAULT_ARCHETYPE)
        if archetype not in spec_mod.ARCHETYPES:
            raise ValueError(
                f"unknown archetype {archetype!r}; known: {sorted(spec_mod.ARCHETYPES)}"
            )
        return spec_mod.default_treatment(archetype), [], "archetype", False
    treatment, notes = spec_mod.coerce(raw)
    palette_given = (
        isinstance(raw, Mapping)
        and isinstance(raw.get("direction"), Mapping)
        and bool(raw["direction"].get("palette"))
    )
    if params.get("strict") and notes:
        raise ValueError(
            "treatment needed repairs and strict=True: " + "; ".join(notes)
        )
    return treatment, notes, "supplied", palette_given


def render(request: RenderRequest) -> RenderResult:
    """Render one choreo video. Satisfies :class:`muvid.subgenres.Renderer`.

    ``request.inputs`` takes ``audio`` (required) and ``cover``. ``request.params``
    takes ``treatment`` or ``archetype``, ``seed``, ``beat_source``, ``strict``,
    ``width``, ``height``, ``fps``.
    """
    from dataclasses import replace

    from muvid.visualize.ffmpeg import media_duration

    inputs = dict(request.inputs)
    params = dict(request.params)
    check_input_counts(inputs)

    audio = inputs.get("audio")
    if not audio:
        raise ValueError("choreo needs inputs['audio']")
    audio = Path(audio)
    if not audio.exists():
        raise FileNotFoundError(f"audio not found: {audio}")
    cover = inputs.get("cover")
    if cover and not Path(cover).exists():
        raise FileNotFoundError(f"cover not found: {cover}")

    canvas = Canvas(
        width=int(params.get("width", 1920)),
        height=int(params.get("height", 1080)),
        fps=int(params.get("fps", 30)),
    )
    duration = media_duration(audio)
    check_render_bounds(canvas, duration)

    seed = int(params.get("seed", 0))
    beat_source = str(params.get("beat_source", "auto"))
    treatment, repair_notes, treatment_source, palette_given = _resolve_treatment(
        params
    )

    request.workdir.mkdir(parents=True, exist_ok=True)
    request.output.parent.mkdir(parents=True, exist_ok=True)

    palette_source = "treatment" if palette_given else "default"
    if cover and not palette_given:
        palette = palette_from_cover(cover, workdir=request.workdir)
        treatment = replace(
            treatment, direction=replace(treatment.direction, palette=palette)
        )
        palette_source = "cover"

    analysis = analyze(audio, beat_source=beat_source)
    scene = compile_scene(treatment, analysis, canvas=canvas, seed=seed)

    # The inspectable side artifacts: what was heard, what was decided, what
    # was drawn. A video you cannot re-derive is an opaque artifact.
    events_path = request.workdir / "events.json"
    events_path.write_text(analysis.to_json(), encoding="utf-8")
    treatment_path = request.workdir / "treatment.json"
    treatment_path.write_text(treatment.to_json(), encoding="utf-8")
    scene_path = request.workdir / "scene.json"
    scene_path.write_text(scene.to_json(), encoding="utf-8")

    from muvid.choreo.render import render_scene

    rendered = render_scene(
        scene, audio=audio, output=request.output, workdir=request.workdir
    )

    meta: dict[str, Any] = {
        "archetypes": scene.meta["archetypes"],
        "seed": seed,
        "tempo_bpm": analysis.tempo.bpm,
        "beat_source": analysis.tempo.source,
        "beat_source_requested": beat_source,
        "n_events": len(analysis.events),
        "n_sections": len(analysis.sections),
        "n_objects": len(scene.objects),
        "treatment_source": treatment_source,
        "palette_source": palette_source,
        "n_frames": rendered.n_frames,
        "render_s": round(rendered.render_s, 2),
    }
    if repair_notes:
        meta["treatment_repairs"] = list(repair_notes)
    if scene.meta.get("uncovered_sections"):
        meta["uncovered_sections"] = scene.meta["uncovered_sections"]
    return RenderResult(
        output=rendered.output,
        duration_s=rendered.duration_s,
        artifacts={
            "events": events_path,
            "scene": scene_path,
            "treatment": treatment_path,
        },
        meta=meta,
    )
