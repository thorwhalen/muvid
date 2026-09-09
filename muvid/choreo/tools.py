"""The SSOT verbs for the choreo subgenre.

Plain functions, JSON-able arguments in, JSON-able ``dict`` out, and
deliberately agnostic about CLI/MCP/HTTP/agent — the same shape as
:mod:`muvid.lyricvid.tools`. The CLI (``python -m muvid.choreo``) and any
MCP or HTTP surface call *these*, so there is one implementation and one
place a behaviour changes.

Nothing here imports numpy or a renderer at module scope, so
``import muvid.choreo.tools`` stays cheap and safe.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "catalog",
    "manifest",
    "vocabulary",
    "treatment_schema",
    "analyze_song",
    "validate_treatment",
    "render_choreo",
]


def catalog() -> dict[str, Any]:
    """Every installed subgenre, without importing any renderer."""
    from muvid.subgenres import subgenre_catalog

    return subgenre_catalog()


def manifest() -> dict[str, Any]:
    """This subgenre's manifest, as a catalogue would show it.

    >>> manifest()['slug']
    'choreo'
    """
    from muvid.choreo.manifest import CHOREO

    return CHOREO.to_dict()


def vocabulary() -> dict[str, Any]:
    """The closed vocabularies a treatment may draw on.

    >>> sorted(vocabulary())
    ['archetype_params', 'archetypes', 'backgrounds', 'densities']
    """
    from muvid.choreo import spec

    return spec.vocabulary()


def treatment_schema() -> dict[str, Any]:
    """JSON Schema for a treatment spec.

    >>> treatment_schema()['type']
    'object'
    """
    from muvid.choreo import spec

    return spec.json_schema()


def analyze_song(
    audio: str,
    *,
    beat_source: str = "auto",
    max_events: int = 200,
) -> dict[str, Any]:
    """Hear a song the way the archetypes will: events, tempo, sections.

    The full event list is what ``events.json`` carries after a render; this
    verb truncates it to ``max_events`` so a terminal or an agent gets the
    shape without the flood.
    """
    from muvid.choreo.analysis import analyze

    a = analyze(audio, beat_source=beat_source)
    d = a.to_dict()
    events = d.pop("events")
    d["events"] = events[:max_events]
    d["events_truncated"] = max(0, len(events) - max_events)
    d["n_beats"] = len(d["tempo"].pop("beats"))
    return d


def validate_treatment(treatment: Mapping[str, Any] | str) -> dict[str, Any]:
    """Validate a treatment and return the repaired version alongside.

    >>> r = validate_treatment({'scenes': [{'archetype': 'swirl'}]})
    >>> r['valid'], r['repairs']
    (False, ["scenes[0].archetype 'swirl' -> 'fischinger'"])
    """
    from muvid.choreo import spec as spec_mod

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


def render_choreo(
    audio: str,
    output: str,
    *,
    cover: str | None = None,
    treatment: Mapping[str, Any] | str | None = None,
    archetype: str | None = None,
    seed: int = 0,
    beat_source: str = "auto",
    strict: bool = False,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Render a choreo video. The one verb that produces a file."""
    import tempfile

    from muvid.choreo.pipeline import render as _render
    from muvid.subgenres import RenderRequest

    if isinstance(treatment, str):
        treatment = json.loads(treatment)
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="muvid-choreo-"))
    inputs = {k: v for k, v in (("audio", audio), ("cover", cover)) if v}
    params = {
        k: v
        for k, v in (
            ("treatment", treatment),
            ("archetype", archetype),
            ("seed", seed),
            ("beat_source", beat_source),
            ("strict", strict),
            ("width", width),
            ("height", height),
            ("fps", fps),
        )
        if v is not None
    }
    result = _render(
        RenderRequest(
            subgenre="choreo",
            inputs=inputs,
            params=params,
            workdir=wd,
            output=Path(output),
        )
    )
    return result.to_dict()
