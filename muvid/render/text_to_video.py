"""Render strategy: text_to_video. Pure prompt → video clip."""

from __future__ import annotations

from pathlib import Path

from muvid.render import RenderContext
from muvid.render._common import storyboard_prompt, trim_video_to_duration


def render_text_to_video(ctx: RenderContext, *, quality: str = "balanced") -> Path:
    from falaw import text_to_video

    prompt = storyboard_prompt(ctx)
    result = text_to_video(
        prompt,
        quality=quality,
        extra={"duration": max(1, int(round(ctx.shot.duration_s)))},
    )
    if not result.first:
        raise RuntimeError(f"text_to_video: no asset returned for {ctx.shot.id}")
    raw = ctx.shot_dir / "raw.mp4"
    result.first.download(to=str(raw))
    final = ctx.shot_dir / "output.mp4"
    return trim_video_to_duration(raw, ctx.shot.duration_s, final)
