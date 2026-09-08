"""muvid choreo CLI — ``cw`` dispatch over the SSOT verbs.

Every verb here is the same function an MCP tool or a frontend would call;
this module only adapts them to a terminal. Run ``python -m muvid.choreo --help``.

    python -m muvid.choreo render song.wav out.mp4 --archetype star_guitar
"""

from __future__ import annotations

import json as _json

from muvid.choreo import tools as _tools


def _emit(obj) -> None:
    print(_json.dumps(obj, indent=2, default=str))


def subgenres() -> None:
    """List every installed subgenre, without importing any renderer."""
    _emit(_tools.catalog())


def manifest() -> None:
    """Print this subgenre's manifest."""
    _emit(_tools.manifest())


def vocabulary() -> None:
    """Print the closed vocabularies a treatment may draw on."""
    _emit(_tools.vocabulary())


def schema() -> None:
    """Print the JSON Schema for a treatment spec."""
    _emit(_tools.treatment_schema())


def analyze(audio: str, *, beat_source: str = "auto", max_events: int = 200) -> None:
    """Report AUDIO's events, tempo and sections — what the archetypes will see."""
    _emit(_tools.analyze_song(audio, beat_source=beat_source, max_events=max_events))


def _treatment_arg(value: str) -> str:
    """A path to a JSON file, or inline JSON. Inline first: a long JSON string
    handed to ``Path(...).exists()`` raises ENAMETOOLONG rather than False."""
    from pathlib import Path

    stripped = value.strip()
    if stripped.startswith("{"):
        return stripped
    p = Path(value)
    try:
        if p.exists():
            return p.read_text(encoding="utf-8")
    except OSError:
        pass
    return value


def validate(treatment: str) -> None:
    """Validate a treatment JSON file (or a JSON string), and report repairs."""
    _emit(_tools.validate_treatment(_treatment_arg(treatment)))


def render(
    audio: str,
    output: str,
    *,
    cover: str = "",
    treatment: str = "",
    archetype: str = "",
    seed: int = 0,
    beat_source: str = "auto",
    strict: bool = False,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    workdir: str = "",
) -> None:
    """Render a choreo video from AUDIO to OUTPUT."""
    treat = _json.loads(_treatment_arg(treatment)) if treatment else None
    _emit(
        _tools.render_choreo(
            audio, output,
            cover=cover or None, treatment=treat, archetype=archetype or None,
            seed=seed, beat_source=beat_source, strict=strict,
            width=width, height=height, fps=fps, workdir=workdir or None,
        )
    )


_FUNCS = [subgenres, manifest, vocabulary, schema, analyze, validate, render]


def main() -> int:
    import cw

    return cw.dispatch(_FUNCS)


if __name__ == "__main__":
    raise SystemExit(main())
