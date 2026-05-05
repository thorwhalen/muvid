"""Render strategy: animation — handoff to the ``an`` package.

We synthesize a minimal ``an`` scene for this shot's interval: each
lyric line becomes a dialogue beat for the singing character; the shot's
environment becomes the entity backdrop.

Lipsync alignment: muvid already owns the SSOT for word timings (the
``lacing`` alignment store written by ``muvid align``). We build a
:class:`an.audio.WordTimingsLipSync` from those timings and pass it
into ``an.orchestrate`` so ``an`` does NOT re-transcribe the same
audio with whisper. Falls back to ``an``'s default lipsync provider
when no alignment store exists yet (e.g. user skipped ``muvid align``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from muvid.renderers import RenderContext


def render_animation(ctx: RenderContext, *, quality: str = "balanced") -> Path:
    """Synthesize a tiny ``an`` scene for this shot and orchestrate it.

    Falls back to a "still" render if the ``an`` package isn't usable
    (it currently has heavy native deps for the cutout backend).
    """
    try:
        from an.orchestrate import orchestrate
    except Exception:
        # Fall back to the still strategy.
        from muvid.renderers.still import render_still

        return render_still(ctx, quality=quality)

    scene_dir = ctx.shot_dir / "an_scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    md = _build_an_scene_md(ctx)
    (scene_dir / "scene.md").write_text(md)

    lipsync = _make_lipsync_provider(ctx)
    orchestrate_kwargs = {"lipsync": lipsync} if lipsync is not None else {}
    report = orchestrate(str(scene_dir), **orchestrate_kwargs)
    if not getattr(report, "success", False):
        # Fall back rather than throwing a wall of errors.
        from muvid.renderers.still import render_still

        return render_still(ctx, quality=quality)
    out = ctx.shot_dir / "output.mp4"
    src = Path(report.output_path)
    if src != out:
        import shutil

        shutil.copy2(src, out)
    return out


def _make_lipsync_provider(ctx: RenderContext):
    """Build a ``WordTimingsLipSync`` from this project's alignment store.

    Returns ``None`` if either:

    - ``an.audio.WordTimingsLipSync`` is not available (older ``an``), or
    - the project has no ``lyrics/alignment.annot`` yet, or
    - no words fall in this shot's window.

    In those cases the caller skips the override and ``an`` falls back to
    its default offline lipsync.
    """
    try:
        from an.audio import StaticWordTimings, WordTimingsLipSync
    except ImportError:
        return None

    timings = _word_timings_for_shot(ctx)
    if not timings:
        return None

    provider = StaticWordTimings(timings, label="muvid:lacing")
    return WordTimingsLipSync(provider)


def _word_timings_for_shot(
    ctx: RenderContext,
) -> Sequence[tuple[str, float, float]]:
    """Read this shot's word timings, relative to the slice's t=0.

    Thin wrapper over :func:`muvid.contracts.word_timings_for_window`
    + :func:`muvid.contracts.shifted_word_timings`. Kept here as a
    private name so the existing tests in this module's neighbourhood
    don't need to know about ``muvid.contracts``.
    """
    from muvid.contracts import shifted_word_timings, word_timings_for_window

    absolute = word_timings_for_window(
        ctx.project, ctx.shot.start_s, ctx.shot.end_s,
    )
    return shifted_word_timings(absolute, offset_s=ctx.shot.start_s)


def _build_an_scene_md(ctx: RenderContext) -> str:
    duration = max(1.0, ctx.shot.duration_s)
    lyrics = ctx.lyric_lines or [
        {
            "text": ctx.shot.description or "...",
            "start_s": ctx.shot.start_s,
            "end_s": ctx.shot.end_s,
        }
    ]
    speaker = ctx.shot.characters[0] if ctx.shot.characters else "narrator"
    chars_block = ""
    if ctx.shot.characters:
        chars_block = "\n".join(
            f"- {{ kind: character, id: {c}, store: characters, ref: {c}-v1 }}"
            for c in ctx.shot.characters
        )
    env_block = ""
    if ctx.shot.environment:
        env_block = (
            f"- {{ kind: environment, id: {ctx.shot.environment}, "
            f"store: environments, ref: {ctx.shot.environment} }}"
        )
    entities = "\n".join(b for b in (env_block, chars_block) if b)
    dialogue = "\n".join(f"{speaker}: {L['text']}" for L in lyrics)
    return f"""# {ctx.shot.id}

```yaml meta
title: {ctx.shot.id}
duration: {duration}
fps: 24
resolution: {{ width: 640, height: 360 }}
```

## Shot {ctx.shot.id} (cutout)

```yaml shot
duration: {duration}
camera: {{ move: static }}
```

```yaml entities
{entities}
```

```dialogue
{dialogue}
```
"""
