"""The lyric-video pipeline — the one path from a song to a finished video.

    audio (+ optional lyrics / subtitles / project)
        -> TimedText          measured word times  (muvid.lyricvid.timed_text)
        -> TreatmentSpec      the creative decision (muvid.lyricvid.director)
        -> Scene              every number computed (muvid.lyricvid.scene)
        -> mp4                a renderer            (render_ass | render_web)

Each arrow is a seam with a working default, so the whole thing runs on a bare
``song.wav`` with no lyrics, no AI, no API key and no network — and every stage
can be replaced without touching its neighbours.

The renderer seam defaults to ``auto``, which picks **ASS** where this ffmpeg can
burn subtitles in (frame-exact, no browser, and the ``.ass`` file it leaves behind
is an editable deliverable in its own right) and **web** otherwise. Asking for a
backend by name never falls back -- it fails loudly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from muvid.lyricvid import spec as spec_mod
from muvid.lyricvid.scene import Canvas, compile_scene
from muvid.lyricvid.timed_text import (
    TimedText,
    from_alignment_store,
    from_lyrics_and_audio,
    from_subtitles,
)
from muvid.subgenres import RenderRequest, RenderResult

__all__ = [
    "RENDERERS",
    "register_renderer",
    "resolve_renderer",
    "select_renderer",
    "build_timed_text",
    "render",
]

#: name -> "module:function", resolved lazily so listing costs no import.
RENDERERS: dict[str, str] = {
    "ass": "muvid.lyricvid.render_ass:render",
    "web": "muvid.lyricvid.render_web:render",
}

DEFAULT_RENDERER = "auto"


def register_renderer(name: str, target: str) -> None:
    """Register a renderer backend as ``"module:function"``.

    The house lazy-registry idiom (cf. ``muvid.footage.strategy``): the target
    is a string so adding a backend costs the import path nothing.

    >>> register_renderer('doctest-demo', 'muvid.lyricvid.render_ass:render')
    >>> 'doctest-demo' in RENDERERS
    True
    >>> del RENDERERS['doctest-demo']
    """
    if ":" not in target:
        raise ValueError(f"renderer target must be 'module:function', got {target!r}")
    RENDERERS[name] = target


def select_renderer(name: str = "auto") -> str:
    """Resolve ``"auto"`` to a backend that can actually run here.

    ``auto`` is a **selector**, not a registered renderer — the same shape as
    ``visual="auto"`` in :mod:`muvid.visualize`. It picks ``ass`` when this
    ffmpeg can burn subtitles in (it needs a libass-enabled build) and ``web``
    otherwise.

    This is deliberately not a fallback inside ``ass``. Asking for ``ass`` on a
    build without libass **fails loudly**, because a renderer that silently
    produces something other than what was asked for is the exact shape muvid
    has been bitten by before. ``auto`` is the caller opting in to "whichever
    works", and the choice it made is recorded in the result's ``meta``.

    >>> select_renderer('web')
    'web'
    >>> select_renderer('auto') in {'ass', 'web'}
    True
    """
    if name != "auto":
        return name
    from muvid.visualize.ffmpeg import has_filter

    return "ass" if has_filter("subtitles") else "web"


def resolve_renderer(name: str | Callable) -> Callable:
    """Import and return a renderer backend. Accepts a callable unchanged."""
    if callable(name):
        return name
    name = select_renderer(name)
    try:
        target = RENDERERS[name]
    except KeyError:
        raise KeyError(
            f"unknown renderer {name!r}; available: {sorted(RENDERERS)}"
        ) from None
    from importlib import import_module

    module_name, _, func = target.partition(":")
    try:
        return getattr(import_module(module_name), func)
    except ImportError as exc:
        raise ImportError(
            f"renderer {name!r} needs an optional dependency: {exc}. "
            f"Try: pip install 'muvid[lyricvid]' (or 'muvid[lyricvid-web]' for web)."
        ) from exc


def build_timed_text(
    *,
    audio: Path | str,
    lyrics: Path | str | None = None,
    subtitles: Path | str | None = None,
    project: Path | str | None = None,
    aligner: str | None = None,
) -> TimedText:
    """Get measured word times from whichever input the caller actually has.

    Order of preference is by how much the input is *trusted*: an existing muvid
    alignment beats a subtitle file, which beats aligning lyrics ourselves,
    which beats transcribing from nothing.
    """
    if project is not None:
        return from_alignment_store(project)
    if subtitles is not None:
        from muvid.visualize.ffmpeg import media_duration

        return from_subtitles(subtitles, duration=media_duration(Path(audio)))
    return from_lyrics_and_audio(audio, lyrics=lyrics, aligner=aligner)


def render(request: RenderRequest) -> RenderResult:
    """Render one lyric video. Satisfies :class:`muvid.subgenres.Renderer`.

    ``request.inputs`` takes ``audio`` (required) and any of ``lyrics``,
    ``subtitles``, ``project``. ``request.params`` takes ``treatment`` (a
    treatment spec as a mapping, or omitted to have one proposed), ``renderer``,
    ``width``, ``height``, ``fps``, ``aligner`` and ``persona``.
    """
    inputs = dict(request.inputs)
    params = dict(request.params)

    audio = inputs.get("audio")
    if not audio:
        raise ValueError("lyric-video needs inputs['audio']")
    audio = Path(audio)
    if not audio.exists():
        raise FileNotFoundError(f"audio not found: {audio}")

    timed = build_timed_text(
        audio=audio,
        lyrics=inputs.get("lyrics"),
        subtitles=inputs.get("subtitles"),
        project=inputs.get("project"),
        aligner=params.get("aligner"),
    )

    treatment_in = params.get("treatment")
    if treatment_in is None:
        from muvid.lyricvid.director import propose_treatments

        persona = params.get("persona")
        treatment = propose_treatments(
            timed,
            n=1,
            song_title=params.get("title", ""),
            personas=[persona] if persona else None,
        )[0]
        treatment_source = "proposed"
    else:
        treatment, _notes = spec_mod.coerce(treatment_in)
        treatment_source = "supplied"

    canvas = Canvas(
        width=int(params.get("width", 1920)),
        height=int(params.get("height", 1080)),
        fps=int(params.get("fps", 30)),
    )
    scene = compile_scene(treatment, timed, canvas=canvas)

    requested = params.get("renderer", DEFAULT_RENDERER)
    backend_name = select_renderer(requested)
    backend = resolve_renderer(backend_name)
    request.workdir.mkdir(parents=True, exist_ok=True)
    request.output.parent.mkdir(parents=True, exist_ok=True)

    result = backend(
        scene, audio=audio, output=request.output, workdir=request.workdir
    )

    # Keep the treatment alongside the video: a lyric video you cannot re-derive
    # or hand-edit is an opaque artifact, which is the thing this design avoids.
    spec_path = request.workdir / "treatment.json"
    spec_path.write_text(treatment.to_json(), encoding="utf-8")

    meta: dict[str, Any] = {
        "renderer": backend_name,
        "renderer_requested": requested,
        "treatment_source": treatment_source,
        "timing_source": timed.source,
        "timing_measured": timed.measured,
        **dict(result.meta),
    }
    return RenderResult(
        output=result.output,
        duration_s=result.duration_s if result.duration_s is not None else timed.duration,
        artifacts={**dict(result.artifacts), "treatment": spec_path},
        meta=meta,
    )
