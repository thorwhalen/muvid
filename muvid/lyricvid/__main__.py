"""muvid lyric-video CLI — ``cw`` dispatch over the SSOT verbs.

Every verb here is the same function the MCP tools, the shipped skill and a
production frontend call; this module only adapts them to a terminal. Run
``python -m muvid.lyricvid --help``.
"""

from __future__ import annotations

import json as _json

from muvid.lyricvid import tools as _tools


def _emit(obj) -> None:
    print(_json.dumps(obj, indent=2, default=str))


def subgenres() -> None:
    """List every installed subgenre, without importing any renderer."""
    _emit(_tools.catalog())


def vocabulary() -> None:
    """Print the closed vocabularies a treatment may draw on."""
    _emit(_tools.vocabulary())


def schema() -> None:
    """Print the JSON Schema for a treatment spec."""
    _emit(_tools.treatment_schema())


def analyze(
    audio: str,
    *,
    lyrics: str = "",
    subtitles: str = "",
    project: str = "",
    aligner: str = "",
) -> None:
    """Measure AUDIO's words and report the structure a director needs."""
    _emit(
        _tools.analyze_song(
            audio,
            lyrics=lyrics or None,
            subtitles=subtitles or None,
            project=project or None,
            aligner=aligner or None,
        )
    )


def propose(
    audio: str,
    *,
    lyrics: str = "",
    subtitles: str = "",
    project: str = "",
    n: int = 3,
    title: str = "",
    reference_image: str = "",
    use_llm: bool = False,
    model: str = "",
) -> None:
    """Propose N ranked treatments for AUDIO. No AI and no cost unless --use-llm."""
    _emit(
        _tools.propose_treatments(
            audio,
            lyrics=lyrics or None,
            subtitles=subtitles or None,
            project=project or None,
            n=n,
            title=title,
            reference_image=reference_image or None,
            use_llm=use_llm,
            model=model or None,
        )
    )


def validate(treatment: str) -> None:
    """Validate a treatment JSON file (or a JSON string), and report repairs."""
    from pathlib import Path

    _emit(_tools.validate_treatment(_treatment_arg(treatment)))


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


def render(
    audio: str,
    output: str,
    *,
    lyrics: str = "",
    subtitles: str = "",
    project: str = "",
    treatment: str = "",
    renderer: str = "auto",
    title: str = "",
    persona: str = "",
    aligner: str = "",
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    workdir: str = "",
) -> None:
    """Render a lyric video from AUDIO to OUTPUT."""
    from pathlib import Path

    treat = _json.loads(_treatment_arg(treatment)) if treatment else None
    _emit(
        _tools.render_lyric_video(
            audio,
            output,
            lyrics=lyrics or None,
            subtitles=subtitles or None,
            project=project or None,
            treatment=treat,
            renderer=renderer,
            title=title,
            persona=persona or None,
            aligner=aligner or None,
            width=width,
            height=height,
            fps=fps,
            workdir=workdir or None,
        )
    )


_FUNCS = [subgenres, vocabulary, schema, analyze, propose, validate, render]


def main() -> int:
    import cw

    return cw.dispatch(_FUNCS)


if __name__ == "__main__":
    raise SystemExit(main())
