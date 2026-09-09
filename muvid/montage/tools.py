"""The SSOT verbs for the montage subgenre: analyze, plan, render.

Plain functions, JSON-able arguments in, JSON-able ``dict`` out, and
deliberately agnostic about CLI/MCP/HTTP/agent — the same shape as
:mod:`muvid.lyricvid.tools`. The CLI (``python -m muvid.montage``), the generic
subgenre MCP transport and a future frontend all call *these*, so there is one
implementation and one place a behaviour changes.

``plan`` exists separately from ``render`` because the plan IS the creative
product: a caller can look at the edit list (which photo where, how the
choruses are paced, what got reserved for the finale) before paying for a
render, and ``render`` computes the identical plan — it is deterministic.

Nothing here imports numpy or ffmpeg at module scope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "analyze_song",
    "plan_montage",
    "render_montage",
    "treatment_schema",
    "validate_treatment",
    "vocabulary",
]


def vocabulary() -> dict[str, Any]:
    """The closed vocabularies a treatment may draw on.

    >>> sorted(vocabulary())[:3]
    ['archetype_params', 'archetypes', 'cut_feels']
    """
    from muvid.montage import spec

    return spec.vocabulary()


def treatment_schema() -> dict[str, Any]:
    """JSON Schema for a treatment spec — also a model's output constraint.

    >>> treatment_schema()['type']
    'object'
    """
    from muvid.montage import spec

    return spec.json_schema()


def validate_treatment(treatment: Mapping[str, Any] | str) -> dict[str, Any]:
    """Validate a treatment, and return the repaired version alongside.

    >>> r = validate_treatment({'scenes': [{'archetype': 'swirl'}]})
    >>> r['valid'], r['repairs']
    (False, ["scenes[0].archetype 'swirl' -> 'beat_cut'"])
    """
    from muvid.montage import spec as spec_mod

    if isinstance(treatment, str):
        treatment = json.loads(treatment)
    raw = spec_mod.TreatmentSpec.from_dict(treatment)
    errors = spec_mod.validate(raw)
    repaired, notes = spec_mod.repair(raw)
    return {
        "valid": not errors,
        "errors": errors,
        "repairs": notes,
        "treatment": repaired.to_dict(),
    }


def analyze_song(
    audio: str,
    *,
    beats: str = "auto",
    beats_per_bar: int = 4,
    sections: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Measure a song: tempo, beat grid, downbeats, bar energy and sections.

    The payload says WHERE the beats came from (``beat_source``) and whether
    the sections were supplied or derived from energy — a caller that ignores
    that will trust a fixed-tempo grid on a rubato ballad.
    """
    from muvid.montage.analysis import analyze

    return analyze(audio, beats=beats, beats_per_bar=beats_per_bar, sections=sections).to_dict()


def _pool(
    photos: Sequence[str], clips: Sequence[str], cover: str | None
) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    if photos:
        inputs["photos"] = [str(p) for p in photos]
    if clips:
        inputs["clips"] = [str(c) for c in clips]
    if cover:
        inputs["cover"] = str(cover)
    return inputs


def _params(
    *,
    treatment: Mapping[str, Any] | str | None,
    archetype: str | None,
    strict: bool,
    beats: str,
    beats_per_bar: int,
    sections: Sequence[Mapping[str, Any]] | None,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
) -> dict[str, Any]:
    if isinstance(treatment, str):
        treatment = json.loads(treatment)
    params: dict[str, Any] = {"beats": beats, "beats_per_bar": beats_per_bar}
    if treatment is not None:
        params["treatment"] = dict(treatment)
    elif archetype:
        params["archetype"] = archetype
    if strict:
        params["strict"] = True
    if sections:
        params["sections"] = [dict(s) for s in sections]
    for k, v in (("width", width), ("height", height), ("fps", fps)):
        if v is not None:
            params[k] = v
    return params


def plan_montage(
    audio: str,
    *,
    photos: Sequence[str] = (),
    clips: Sequence[str] = (),
    cover: str | None = None,
    treatment: Mapping[str, Any] | str | None = None,
    archetype: str | None = None,
    strict: bool = False,
    beats: str = "auto",
    beats_per_bar: int = 4,
    sections: Sequence[Mapping[str, Any]] | None = None,
    out: str | None = None,
) -> dict[str, Any]:
    """Plan the montage without rendering it: the edit list, as JSON.

    Identical to what :func:`render_montage` would render. ``out`` writes it
    to a file as well.
    """
    from muvid.montage.analysis import analyze, probe_media
    from muvid.montage.pipeline import MAX_MEDIA, build_treatment
    from muvid.montage.plan import plan_montage as _plan

    photos, clips = [str(p) for p in photos], [str(c) for c in clips]
    if not photos and not clips:
        raise ValueError("a montage needs photos and/or clips")
    for name, seq in (("photos", photos), ("clips", clips)):
        if len(seq) > MAX_MEDIA:
            raise ValueError(f"{len(seq)} {name}; the bound is {MAX_MEDIA} per input")
    params = _params(
        treatment=treatment, archetype=archetype, strict=strict, beats=beats,
        beats_per_bar=beats_per_bar, sections=sections,
    )
    spec, notes, source = build_treatment(params)
    if strict and notes:
        raise ValueError("treatment needed repairs and strict=True: " + "; ".join(notes))
    analysis = analyze(audio, beats=beats, beats_per_bar=beats_per_bar, sections=sections)
    media = list(probe_media(photos, kind="photo"))
    media += list(probe_media(clips, kind="clip", start_index=len(media)))
    if cover:
        media += list(probe_media([cover], kind="cover", start_index=len(media)))
    plan = _plan(analysis, media, spec).to_dict()
    plan["treatment_source"] = source
    if notes:
        plan["treatment_repairs"] = notes
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(plan, indent=2), encoding="utf-8")
        plan["written_to"] = str(out)
    return plan


def render_montage(
    audio: str,
    output: str,
    *,
    photos: Sequence[str] = (),
    clips: Sequence[str] = (),
    cover: str | None = None,
    treatment: Mapping[str, Any] | str | None = None,
    archetype: str | None = None,
    strict: bool = False,
    beats: str = "auto",
    beats_per_bar: int = 4,
    sections: Sequence[Mapping[str, Any]] | None = None,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Render a montage. The one verb that produces a file.

    Goes through :func:`muvid.subgenres.render_subgenre` when the manifest is
    registered (so the schemas are enforced), and straight to the pipeline
    otherwise — the same renderer either way.
    """
    import tempfile

    from muvid.montage.manifest import MONTAGE
    from muvid.subgenres import RenderRequest, get_subgenre, render_subgenre
    from muvid.montage.pipeline import render as _render

    inputs = {"audio": str(audio), **_pool(photos, clips, cover)}
    params = _params(
        treatment=treatment, archetype=archetype, strict=strict, beats=beats,
        beats_per_bar=beats_per_bar, sections=sections, width=width, height=height,
        fps=fps,
    )
    out = Path(output)
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="muvid-montage-"))
    try:
        get_subgenre(MONTAGE.slug)
    except KeyError:
        result = _render(
            RenderRequest(subgenre=MONTAGE.slug, inputs=inputs, params=params,
                          workdir=wd, output=out)
        )
    else:
        result = render_subgenre(
            MONTAGE.slug, inputs=inputs, params=params, workdir=wd, output=out
        )
    payload = result.to_dict()
    payload["workdir"] = str(wd)
    return payload
