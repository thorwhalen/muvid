"""The montage pipeline — the one path from a song and a pool to a finished video.

    audio + photos[] / clips[] (+ cover)
        -> Analysis        beat grid, bars, sections     (muvid.montage.analysis)
        -> TreatmentSpec   the creative decision         (muvid.montage.spec)
        -> Plan            every cut and framing computed (muvid.montage.plan)
        -> mp4             ffmpeg, bounded stages         (muvid.montage.render)

Each arrow is a seam with a working default, so the whole thing runs on a bare
``song.wav`` and three photos with no AI, no API key and no network — and the
plan is written to ``plan.json`` BEFORE any frame is rendered, so a render
that fails still leaves the edit list behind for inspection.

Resource bounds are REFUSED, not clamped: a clamped request silently produces
a different video than the one asked for. The bounds read the same env-var
shape as the lyric video (``MUVID_MONTAGE_MAX_PIXELS`` etc.) with the same
defaults, plus a per-input file-count bound — 64 photos is a montage, 6,400
is a denial of service.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Sequence

from muvid.montage import spec as spec_mod
from muvid.montage.render import Canvas
from muvid.subgenres import RenderRequest, RenderResult

__all__ = [
    "MAX_DURATION_S",
    "MAX_FPS",
    "MAX_MEDIA",
    "MAX_PIXELS",
    "build_treatment",
    "check_render_bounds",
    "render",
]

#: Resource bounds, env-configurable like ``MUVID_LYRICVID_*``. Same defaults:
#: a 4K frame, 60 fps, a 15-minute song. The media bound is this subgenre's own.
MAX_PIXELS = int(os.environ.get("MUVID_MONTAGE_MAX_PIXELS", str(3840 * 2160)))
MAX_FPS = int(os.environ.get("MUVID_MONTAGE_MAX_FPS", "60"))
MAX_DURATION_S = int(os.environ.get("MUVID_MAX_DURATION_S", str(15 * 60)))
MAX_MEDIA = int(os.environ.get("MUVID_MONTAGE_MAX_MEDIA", "64"))
MIN_SIDE = 16

DEFAULT_CANVAS = Canvas(width=1920, height=1080, fps=30)


def check_render_bounds(
    canvas: Canvas, duration_s: float, *, n_photos: int = 0, n_clips: int = 0
) -> None:
    """Refuse a render that would exceed the resource bounds. Refuse, not clamp.

    >>> check_render_bounds(Canvas(width=1920, height=1080, fps=30), 200.0, n_photos=12)
    >>> check_render_bounds(Canvas(width=30000, height=30000, fps=30), 10.0)
    Traceback (most recent call last):
    ...
    ValueError: canvas 30000x30000 is 900000000 px/frame; the bound is 8294400 (MUVID_MONTAGE_MAX_PIXELS)
    >>> check_render_bounds(Canvas(width=640, height=360, fps=24), 10.0, n_photos=65)
    Traceback (most recent call last):
    ...
    ValueError: 65 photos; the bound is 64 per input (MUVID_MONTAGE_MAX_MEDIA)
    """
    px = canvas.width * canvas.height
    if canvas.width < MIN_SIDE or canvas.height < MIN_SIDE:
        raise ValueError(f"canvas {canvas.width}x{canvas.height} is too small")
    if px > MAX_PIXELS:
        raise ValueError(
            f"canvas {canvas.width}x{canvas.height} is {px} px/frame; the bound is "
            f"{MAX_PIXELS} (MUVID_MONTAGE_MAX_PIXELS)"
        )
    if not 1 <= canvas.fps <= MAX_FPS:
        raise ValueError(f"fps {canvas.fps} is outside 1..{MAX_FPS} (MUVID_MONTAGE_MAX_FPS)")
    if duration_s > MAX_DURATION_S:
        raise ValueError(
            f"audio is {duration_s:.0f}s; the render limit is {MAX_DURATION_S}s "
            "(MUVID_MAX_DURATION_S)"
        )
    for name, n in (("photos", n_photos), ("clips", n_clips)):
        if n > MAX_MEDIA:
            raise ValueError(
                f"{n} {name}; the bound is {MAX_MEDIA} per input (MUVID_MONTAGE_MAX_MEDIA)"
            )


def build_treatment(params: dict[str, Any]) -> tuple[spec_mod.TreatmentSpec, list[str], str]:
    """``(treatment, repair_notes, source)`` from a request's params.

    A supplied ``treatment`` is coerced and repaired; otherwise a one-scene
    treatment is built from ``archetype`` (default ``beat_cut``).

    >>> t, notes, source = build_treatment({'archetype': 'grid'})
    >>> t.scenes[0].archetype, notes, source
    ('grid', [], 'archetype')
    >>> build_treatment({'treatment': {'scenes': [{'archetype': 'nope'}]}})[1:]
    (["scenes[0].archetype 'nope' -> 'beat_cut'"], 'supplied')
    """
    raw = params.get("treatment")
    if raw is not None:
        treatment, notes = spec_mod.coerce(raw)
        return treatment, notes, "supplied"
    archetype = str(params.get("archetype") or "beat_cut")
    treatment, notes = spec_mod.coerce({"scenes": [{"archetype": archetype}]})
    return treatment, notes, "archetype"


def _paths(value: Any, *, what: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [str(value)]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    raise TypeError(f"inputs[{what!r}] must be a list of paths, got {type(value).__name__}")


def render(request: RenderRequest) -> RenderResult:
    """Render one montage. Satisfies :class:`muvid.subgenres.Renderer`.

    ``request.inputs`` takes ``audio`` (required), ``photos`` and/or ``clips``
    (at least one non-empty) and an optional ``cover``. ``request.params``
    takes ``treatment`` or ``archetype``, ``strict``, ``beats``,
    ``beats_per_bar``, ``sections``, ``width``, ``height`` and ``fps``.
    """
    from muvid.montage.analysis import analyze, probe_media
    from muvid.montage.plan import plan_montage
    from muvid.montage.render import render_plan
    from muvid.visualize.ffmpeg import media_duration

    inputs = dict(request.inputs)
    params = dict(request.params)

    audio = inputs.get("audio")
    if not audio:
        raise ValueError("montage needs inputs['audio']")
    audio = Path(audio)
    if not audio.exists():
        raise FileNotFoundError(f"audio not found: {audio}")
    photos = _paths(inputs.get("photos"), what="photos")
    clips = _paths(inputs.get("clips"), what="clips")
    cover = inputs.get("cover")
    if not photos and not clips:
        raise ValueError("montage needs inputs['photos'] and/or inputs['clips'] — the pool is empty")

    canvas = Canvas(
        width=int(params.get("width", DEFAULT_CANVAS.width)),
        height=int(params.get("height", DEFAULT_CANVAS.height)),
        fps=int(params.get("fps", DEFAULT_CANVAS.fps)),
    )
    # Counts first (free), then the song's length (one probe), then the pool
    # (one probe + one thumbnail per file). Cheap refusals before expensive ones.
    check_render_bounds(canvas, 0.0, n_photos=len(photos), n_clips=len(clips))
    duration = media_duration(audio)
    check_render_bounds(canvas, duration, n_photos=len(photos), n_clips=len(clips))

    treatment, repair_notes, treatment_source = build_treatment(params)
    if params.get("strict") and repair_notes:
        raise ValueError("treatment needed repairs and strict=True: " + "; ".join(repair_notes))

    analysis = analyze(
        audio,
        beats=str(params.get("beats", "auto")),
        beats_per_bar=int(params.get("beats_per_bar", 4)),
        sections=params.get("sections"),
    )
    media = list(probe_media(photos, kind="photo"))
    media += list(probe_media(clips, kind="clip", start_index=len(media)))
    if cover:
        media += list(probe_media([cover], kind="cover", start_index=len(media)))

    plan = plan_montage(analysis, media, treatment)

    request.workdir.mkdir(parents=True, exist_ok=True)
    request.output.parent.mkdir(parents=True, exist_ok=True)
    # The plan is the inspectable artifact; write it before rendering so a
    # failed render still leaves the edit list behind.
    plan_path = request.workdir / "plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")

    result = render_plan(
        plan,
        canvas=canvas,
        audio=audio,
        output=request.output,
        workdir=request.workdir,
        grade=treatment.direction.grade,
        palette=treatment.direction.palette,
    )
    meta: dict[str, Any] = {
        **dict(result.meta),
        "treatment_source": treatment_source,
        "beat_source": analysis.beat_source,
        "tempo_bpm": round(analysis.tempo_bpm, 3),
        "section_source": analysis.section_source,
        "sections": [
            {"label": s.label, "start": round(s.start, 3), "end": round(s.end, 3)}
            for s in analysis.sections
        ],
        "pool": len(media),
        "reuse": dict(plan.reuse),
    }
    # Omit-when-empty: a repaired treatment renders something other than what
    # was asked for, and the caller has to be able to SEE that.
    if repair_notes:
        meta["treatment_repairs"] = list(repair_notes)
    if plan.notes:
        meta["notes"] = list(plan.notes)
    return RenderResult(
        output=result.output,
        duration_s=result.duration_s if result.duration_s is not None else duration,
        artifacts={**dict(result.artifacts), "plan": plan_path},
        meta=meta,
    )
