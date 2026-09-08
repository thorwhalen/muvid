"""The ``lyric-video`` genre's MCP tool surface.

Six tools over :mod:`muvid.lyricvid.tools` — the same functions the CLI, the
shipped skill and a production frontend call, adapted to a remote connector by
resolving caller-supplied URLs into the caller's own workspace and returning a
download claim rather than a server-side path.

**Five are free; one is not.** :func:`propose_lyric_treatments` runs muvid's
heuristic director — no network, no model, no cost. :func:`propose_lyric_treatments_ai`
asks an LLM, and is muvid's **first costed tool**. It is a separate tool rather
than a flag on the free one precisely so ``FREE_TOOLS`` and ``COSTED_TOOLS``
stay honest: a tool whose cost depends on an argument cannot be metered by a
host that only knows its name.

Cost is reported as *unknown*, never as zero. muvid's budget gate is
conjunctive — a price it cannot determine must force approval, and encoding an
unpriced call as ``0.0`` is the exact bug muvid#47 was filed about.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from muvid.lyricvid import tools as _lv
from muvid.mcp.tools import _download_claim, _resolve_input, _tool_error, _workspace

#: Ceiling on proposals per call. For the AI tool each proposal is one model
#: call; for the free one it is CPU. Both are bounds a host cannot see through
#: the tool name, so they live here rather than in the caller's good intentions.
MAX_AI_OPTIONS = int(os.environ.get("MUVID_LYRICVID_MAX_AI_OPTIONS", "8"))
MAX_FREE_OPTIONS = int(os.environ.get("MUVID_LYRICVID_MAX_FREE_OPTIONS", "24"))

__all__ = [
    "list_archetypes",
    "analyze_song_lyrics",
    "propose_lyric_treatments",
    "propose_lyric_treatments_ai",
    "validate_lyric_treatment",
    "render_lyric_video",
]


def list_archetypes() -> dict:
    """List the lyric-video layout archetypes and the treatment vocabulary. Free.

    Returns the closed sets a treatment may draw on — archetypes, motions,
    persistence, quantisation, cut styles — each with a one-line description,
    plus the JSON Schema a treatment must satisfy. Everything an agent needs to
    author a treatment without guessing.
    """
    vocab = _lv.vocabulary()
    return {
        "archetypes": vocab["archetypes"],
        "motions": vocab["motions"],
        "persistence": vocab["persistence"],
        "quantize": vocab["quantize"],
        "cut_styles": vocab["cut_styles"],
        "schema": _lv.treatment_schema(),
    }


def _fetch_inputs(project_id: str, audio: str, lyrics: str | None,
                  subtitles: str | None) -> tuple[Any, dict]:
    proj = _workspace().open_project(project_id)
    workdir = Path(proj.root) / "lyricvid"
    workdir.mkdir(parents=True, exist_ok=True)
    inputs: dict[str, str] = {
        "audio": str(_resolve_input(audio, workdir / "song", label="audio"))
    }
    if lyrics:
        inputs["lyrics"] = str(_resolve_input(lyrics, workdir / "lyrics", label="lyrics"))
    if subtitles:
        inputs["subtitles"] = str(
            _resolve_input(subtitles, workdir / "subs", label="subtitles")
        )
    return proj, inputs


def analyze_song_lyrics(
    project_id: str, *, audio: str, lyrics: str | None = None,
    subtitles: str | None = None,
) -> dict:
    """Measure a song's words: sections, lines, words-per-second, timing quality. Free.

    The ``timing_measured`` flag matters: ``false`` means word times were
    interpolated inside lines rather than measured, and a treatment should then
    quantise to ``line`` rather than ``word``.
    """
    _proj, inputs = _fetch_inputs(project_id, audio, lyrics, subtitles)
    return _lv.analyze_song(**inputs)


def propose_lyric_treatments(
    project_id: str, *, audio: str, lyrics: str | None = None,
    subtitles: str | None = None, n: int = 3, title: str = "",
) -> dict:
    """Propose N ranked lyric-video treatments, with rationales. Free.

    Uses muvid's heuristic director: it reads the lyrics' structure, repetition
    and density and picks an archetype, palette and motion. No model, no
    network, no cost. For an LLM-authored treatment use
    ``propose_lyric_treatments_ai``.
    """
    if not 1 <= n <= MAX_FREE_OPTIONS:
        raise _tool_error(f"n must be between 1 and {MAX_FREE_OPTIONS}")
    _proj, inputs = _fetch_inputs(project_id, audio, lyrics, subtitles)
    return _lv.propose_treatments(**inputs, n=n, title=title, use_llm=False)


def propose_lyric_treatments_ai(
    project_id: str, *, audio: str, lyrics: str | None = None,
    subtitles: str | None = None, n: int = 3, title: str = "",
    reference_image: str | None = None, model: str | None = None,
) -> dict:
    """Propose N lyric-video treatments using an LLM creative director. COSTED.

    Spends money on a model call. The returned ``cost`` block reports
    ``has_unknown_costs: true`` — muvid cannot price a model call ahead of
    time, and an unknown price must force approval rather than read as free.

    ``reference_image`` may be an image of a layout you want echoed (a
    photographed page, a poster). The model describes it as an *archetype plus
    a shape*, never as coordinates.
    """
    # `n` is the number of MODEL CALLS. The budget gate meters by tool name and
    # cannot see an argument, so an uncapped n turns one approved call into a
    # thousand. Refuse rather than clamp: a caller who asked for 50 should learn
    # the bound, not silently get 8.
    if not 1 <= n <= MAX_AI_OPTIONS:
        raise _tool_error(f"n must be between 1 and {MAX_AI_OPTIONS} (each is a model call)")
    _proj, inputs = _fetch_inputs(project_id, audio, lyrics, subtitles)
    ref = None
    if reference_image:
        workdir = Path(_workspace().open_project(project_id).root) / "lyricvid"
        ref = str(_resolve_input(reference_image, workdir / "ref", label="reference_image"))
    out = _lv.propose_treatments(
        **inputs, n=n, title=title, reference_image=ref, use_llm=True, model=model
    )
    out["cost"] = {
        "spent_usd": None,
        "has_unknown_costs": True,
        "provider": "anthropic",
        "note": "A model call cannot be priced ahead of time; unknown is not zero.",
    }
    return out


def validate_lyric_treatment(treatment: dict) -> dict:
    """Validate a treatment and return the repaired version. Free.

    A nearly-right treatment is repaired onto the valid space rather than
    rejected, and every substitution is listed under ``repairs``.
    """
    if not isinstance(treatment, dict):
        raise _tool_error("treatment must be a JSON object")
    try:
        return _lv.validate_treatment(treatment)
    except (TypeError, ValueError) as exc:  # from_dict is total now; belt and braces
        raise _tool_error(f"treatment could not be read: {exc}") from exc


def render_lyric_video(
    project_id: str, *, audio: str, lyrics: str | None = None,
    subtitles: str | None = None, treatment: dict | None = None,
    archetype: str | None = None,
    renderer: str = "auto", title: str = "", width: int = 1920,
    height: int = 1080, fps: int = 30,
) -> dict:
    """Render a lyric video. Free — no model is called on this path.

    ``treatment`` omitted, the heuristic director chooses one; ``archetype``
    alone (the value an nw Template carries) builds a one-scene treatment from
    it. ``renderer='auto'`` picks ``ass`` where this ffmpeg can burn subtitles
    in and ``web`` otherwise, and records which; ``ass`` additionally returns
    an editable ``.ass`` subtitle file among the artifacts.
    """
    import uuid

    if renderer not in {"auto", "ass", "web"}:
        raise _tool_error(f"renderer must be 'auto', 'ass' or 'web', got {renderer!r}")
    if treatment is not None:
        if not isinstance(treatment, dict):
            raise _tool_error("treatment must be a JSON object")
        _refuse_untrusted_shapes(treatment)
    elif archetype is not None:
        from muvid.lyricvid.spec import ARCHETYPES

        if archetype not in ARCHETYPES:
            raise _tool_error(
                f"unknown archetype {archetype!r}; one of {sorted(ARCHETYPES)}"
            )
        treatment = {"scenes": [{"archetype": archetype}]}

    proj, inputs = _fetch_inputs(project_id, audio, lyrics, subtitles)
    render_id = uuid.uuid4().hex[:12]
    # The workspace's own layout: renders/<id>/video.mp4 + meta.json beside it.
    # That is what downloads.resolve() and list_renders() read; writing
    # renders/<id>.mp4 rendered fine and was then unreachable by the claim
    # this tool returned.
    render_dir = proj.new_render_dir(render_id)
    out = render_dir / "video.mp4"

    try:
        result = _lv.render_lyric_video(
            inputs["audio"],
            str(out),
            lyrics=inputs.get("lyrics"),
            subtitles=inputs.get("subtitles"),
            treatment=treatment,
            renderer=renderer,
            title=title,
            width=width,
            height=height,
            fps=fps,
            workdir=str(render_dir / "work"),
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        # A caller sees WHAT was wrong, never a traceback, never a host path.
        raise _tool_error(f"render refused: {exc}") from exc

    meta = {
        "render_id": render_id,
        "video": result["output"],
        "duration": result.get("duration_s"),
        "renderer": result.get("meta", {}).get("renderer", renderer),
        "artifacts": result.get("artifacts", {}),
        "meta": result.get("meta", {}),
        "download": _download_claim(project_id, render_id),
        "note": (
            f"Call `reelee_get_download_url(genre='muvid', project_id='{project_id}', "
            f"artifact_id='{render_id}')` for a link to watch and download this."
        ),
    }
    proj.write_render_meta(render_id, meta)
    return meta


def _refuse_untrusted_shapes(treatment: dict) -> None:
    """Refuse a treatment whose shape names a FILE.

    ``shape.kind == 'mask_image'`` makes ``shape.value`` a path the renderer
    opens. Over MCP that path would be a remote caller's string reaching
    ``Image.open`` on the host — a filesystem-existence oracle at best and a
    cross-tenant read at worst. Only shapes that carry no reference to anything
    outside the spec are accepted here; a mask image would have to arrive
    through ``_resolve_input`` like every other file, and that plumbing is not
    built, so it is refused rather than half-trusted.
    """
    from muvid.lyricvid.spec import INLINE_SHAPE_KINDS

    for i, sc in enumerate(treatment.get("scenes") or []):
        shape = (sc or {}).get("shape") if isinstance(sc, dict) else None
        if isinstance(shape, dict) and shape.get("kind") not in (None, *INLINE_SHAPE_KINDS):
            raise _tool_error(
                f"scenes[{i}].shape.kind {shape.get('kind')!r} is not accepted over "
                f"this surface; use one of {sorted(INLINE_SHAPE_KINDS)}"
            )
