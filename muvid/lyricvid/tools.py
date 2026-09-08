"""The SSOT verbs for the lyric-video subgenre.

Plain functions, JSON-able arguments in, JSON-able ``dict`` out, and
deliberately agnostic about CLI/MCP/HTTP/agent — following ``ir.tools``, which
muvid's design record already names as the reference for this shape. The CLI
(``python -m muvid.lyricvid``), the MCP tools, the shipped skill and a
production frontend all call *these*, so there is one implementation and one
place a behaviour changes.

Nothing here imports a renderer, an LLM client or numpy at module scope, so
``import muvid.lyricvid.tools`` stays cheap and safe.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "catalog",
    "vocabulary",
    "treatment_schema",
    "analyze_song",
    "propose_treatments",
    "validate_treatment",
    "render_lyric_video",
]


def catalog() -> dict[str, Any]:
    """Every installed subgenre, without importing any renderer.

    >>> c = catalog()
    >>> 'lyric-video' in [s['slug'] for s in c['subgenres']]
    True
    """
    from muvid.subgenres import subgenre_catalog

    _ensure_registered()
    return subgenre_catalog()


def _ensure_registered() -> None:
    """Register muvid's own subgenres, idempotently."""
    from muvid.subgenres import list_subgenres, register_subgenre
    from muvid.lyricvid.manifest import LYRIC_VIDEO

    if LYRIC_VIDEO.slug not in list_subgenres():
        register_subgenre(LYRIC_VIDEO)


def vocabulary() -> dict[str, Any]:
    """The closed vocabularies a treatment may draw on.

    One copy, read by the prompt, the JSON Schema, the UI and the docs.

    >>> v = vocabulary()
    >>> sorted(v)[:2]
    ['archetypes', 'cases']
    """
    from muvid.lyricvid import spec

    return spec.vocabulary()


def treatment_schema() -> dict[str, Any]:
    """JSON Schema for a treatment spec — also the model's output constraint.

    >>> treatment_schema()['type']
    'object'
    """
    from muvid.lyricvid import spec

    return spec.json_schema()


def analyze_song(
    audio: str,
    *,
    lyrics: str | None = None,
    subtitles: str | None = None,
    project: str | None = None,
    aligner: str | None = None,
    max_lines: int = 40,
) -> dict[str, Any]:
    """Measure a song's words and report what a director needs to know.

    Returns the section/line structure, the duration, the words-per-second the
    treatment has to keep up with, and — importantly — whether the word times
    were **measured** or interpolated from line times. A caller that ignores
    that flag will ship a video that looks subtly out of sync.
    """
    from muvid.lyricvid.pipeline import build_timed_text

    tt = build_timed_text(
        audio=audio,
        lyrics=lyrics,
        subtitles=subtitles,
        project=project,
        aligner=aligner,
    )
    words = list(tt.words())
    lines = list(tt.lines())
    span = max(1e-6, tt.duration)
    return {
        "duration_s": round(tt.duration, 3),
        "timing_source": tt.source,
        "timing_measured": tt.measured,
        "n_sections": len(tt.sections),
        "n_lines": len(lines),
        "n_words": len(words),
        "words_per_second": round(len(words) / span, 3),
        "sections": [
            {
                "label": s.label,
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "n_lines": len(s.lines),
            }
            for s in tt.sections
        ],
        "lines": [
            {
                "index": l.index,
                "text": l.text,
                "start": round(l.start, 3),
                "end": round(l.end, 3),
            }
            for l in lines[:max_lines]
        ],
        "lines_truncated": max(0, len(lines) - max_lines),
    }


def propose_treatments(
    audio: str,
    *,
    lyrics: str | None = None,
    subtitles: str | None = None,
    project: str | None = None,
    n: int = 3,
    title: str = "",
    reference_image: str | None = None,
    use_llm: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    """Propose ``n`` treatments, ranked, each with its rationale.

    ``use_llm=False`` (the default) runs the heuristic director: no network, no
    API key, no cost. ``use_llm=True`` asks a model, and the returned payload
    then carries a ``cost`` block — unknown cost is reported as unknown, never
    as zero.
    """
    from muvid.lyricvid.director import propose_treatments as _propose, rank_treatments
    from muvid.lyricvid.pipeline import build_timed_text

    tt = build_timed_text(
        audio=audio, lyrics=lyrics, subtitles=subtitles, project=project
    )
    llm = None
    cost: dict[str, Any] = {
        "spent_usd": 0.0,
        "has_unknown_costs": False,
        "provider": None,
    }
    if use_llm:
        from muvid.lyricvid.director import anthropic_llm

        llm = anthropic_llm(model=model)
        cost = {"spent_usd": None, "has_unknown_costs": True, "provider": "anthropic"}

    specs = _propose(
        tt, n=n, song_title=title, reference_image=reference_image, llm=llm
    )
    ranked = rank_treatments(specs, tt)
    return {
        "song": analyze_song(
            audio, lyrics=lyrics, subtitles=subtitles, project=project, max_lines=0
        ),
        "options": [
            {
                "rank": i,
                "score": round(score, 4),
                "why": why,
                "archetypes": sorted({s.archetype for s in spec.scenes}),
                "mood": spec.direction.mood,
                "rationale": spec.direction.rationale,
                "treatment": spec.to_dict(),
            }
            for i, (spec, score, why) in enumerate(ranked)
        ],
        "cost": cost,
    }


def validate_treatment(treatment: Mapping[str, Any] | str) -> dict[str, Any]:
    """Validate a treatment, and return the repaired version alongside.

    A treatment that is *nearly* right is repaired rather than rejected, because
    a mechanical substitution renders something good now where a retry costs a
    round trip and may fail the same way. Every substitution is reported.

    >>> r = validate_treatment({'scenes': [{'archetype': 'swirl'}]})
    >>> r['valid'], r['repairs']
    (False, ["scenes[0].archetype 'swirl' -> 'one_word_centred'"])
    """
    from muvid.lyricvid import spec as spec_mod

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


def render_lyric_video(
    audio: str,
    output: str,
    *,
    lyrics: str | None = None,
    subtitles: str | None = None,
    project: str | None = None,
    treatment: Mapping[str, Any] | str | None = None,
    renderer: str = "auto",
    title: str = "",
    persona: str | None = None,
    aligner: str | None = None,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Render a lyric video. The one verb that produces a file.

    Everything else in this module exists so that a caller can decide *what* to
    render before paying for it.
    """
    import tempfile

    from muvid.subgenres import RenderRequest
    from muvid.lyricvid.pipeline import render as _render

    if isinstance(treatment, str):
        treatment = json.loads(treatment)

    out = Path(output)
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="muvid-lyricvid-"))
    inputs = {
        k: v
        for k, v in (
            ("audio", audio),
            ("lyrics", lyrics),
            ("subtitles", subtitles),
            ("project", project),
        )
        if v
    }
    params = {
        k: v
        for k, v in (
            ("treatment", treatment),
            ("renderer", renderer),
            ("title", title),
            ("persona", persona),
            ("aligner", aligner),
            ("width", width),
            ("height", height),
            ("fps", fps),
        )
        if v is not None
    }
    result = _render(
        RenderRequest(
            subgenre="lyric-video", inputs=inputs, params=params, workdir=wd, output=out
        )
    )
    return result.to_dict()
