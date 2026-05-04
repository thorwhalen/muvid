"""Render strategy: animation — handoff to the ``an`` package.

We synthesize a minimal ``an`` scene for this shot's interval: each
lyric line becomes a dialogue beat for the singing character; the shot's
environment becomes the entity backdrop. ``an`` has its own
WhisperLipSync that re-aligns to the synthesized TTS, but for music
videos we want lipsync against the *original song* — so we instead
write the audio slice as the scene's pre-baked audio and let an's
viseme machinery sync against it.

This is a deliberately minimal v0 — `an` is mature enough to grow this
out into a proper scripted scene later.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

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
    report = orchestrate(str(scene_dir))
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
