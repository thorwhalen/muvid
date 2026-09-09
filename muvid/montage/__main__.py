"""muvid montage CLI — ``cw`` dispatch over the SSOT verbs.

Every verb here is the same function a frontend or the MCP transport calls;
this module only adapts them to a terminal::

    python -m muvid.montage render song.wav out.mp4 --photos a.jpg b.jpg c.jpg
    python -m muvid.montage plan song.wav --photos *.jpg --archetype grid
    python -m muvid.montage analyze song.wav

Run ``python -m muvid.montage --help``.
"""

from __future__ import annotations

import json as _json

from muvid.montage import tools as _tools


def _emit(obj) -> None:
    print(_json.dumps(obj, indent=2, default=str))


def _json_arg(value: str) -> str:
    """A path to a JSON file, or inline JSON. Inline first: a long JSON string
    handed to ``Path(...).exists()`` raises ENAMETOOLONG rather than False."""
    from pathlib import Path

    stripped = value.strip()
    if stripped.startswith(("{", "[")):
        return stripped
    p = Path(value)
    try:
        if p.exists():
            return p.read_text(encoding="utf-8")
    except OSError:
        pass
    return value


def vocabulary() -> None:
    """Print the closed vocabularies a treatment may draw on."""
    _emit(_tools.vocabulary())


def schema() -> None:
    """Print the JSON Schema for a treatment spec."""
    _emit(_tools.treatment_schema())


def validate(treatment: str) -> None:
    """Validate a treatment JSON file (or a JSON string), and report repairs."""
    _emit(_tools.validate_treatment(_json_arg(treatment)))


def analyze(
    audio: str,
    *,
    beats: str = "auto",
    beats_per_bar: int = 4,
    sections: str = "",
) -> None:
    """Measure AUDIO: tempo, beat grid, downbeats, bar energy, sections."""
    _emit(
        _tools.analyze_song(
            audio,
            beats=beats,
            beats_per_bar=beats_per_bar,
            sections=_json.loads(_json_arg(sections)) if sections else None,
        )
    )


def plan(
    audio: str,
    *,
    photos: list[str] = (),
    clips: list[str] = (),
    cover: str = "",
    treatment: str = "",
    archetype: str = "",
    strict: bool = False,
    beats: str = "auto",
    beats_per_bar: int = 4,
    sections: str = "",
    out: str = "",
) -> None:
    """Plan the montage of AUDIO over --photos / --clips without rendering."""
    _emit(
        _tools.plan_montage(
            audio,
            photos=list(photos),
            clips=list(clips),
            cover=cover or None,
            treatment=_json_arg(treatment) if treatment else None,
            archetype=archetype or None,
            strict=strict,
            beats=beats,
            beats_per_bar=beats_per_bar,
            sections=_json.loads(_json_arg(sections)) if sections else None,
            out=out or None,
        )
    )


def render(
    audio: str,
    output: str,
    *,
    photos: list[str] = (),
    clips: list[str] = (),
    cover: str = "",
    treatment: str = "",
    archetype: str = "",
    strict: bool = False,
    beats: str = "auto",
    beats_per_bar: int = 4,
    sections: str = "",
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    workdir: str = "",
) -> None:
    """Render a montage of AUDIO over --photos / --clips to OUTPUT."""
    _emit(
        _tools.render_montage(
            audio,
            output,
            photos=list(photos),
            clips=list(clips),
            cover=cover or None,
            treatment=_json_arg(treatment) if treatment else None,
            archetype=archetype or None,
            strict=strict,
            beats=beats,
            beats_per_bar=beats_per_bar,
            sections=_json.loads(_json_arg(sections)) if sections else None,
            width=width,
            height=height,
            fps=fps,
            workdir=workdir or None,
        )
    )


_FUNCS = [vocabulary, schema, validate, analyze, plan, render]


def main() -> int:
    import cw

    return cw.dispatch(_FUNCS)


if __name__ == "__main__":
    raise SystemExit(main())
