"""Render strategy: image_to_video.

Workflow:

1. If we have an environment anchor image, use it as the i2v seed.
   Else, generate a fresh storyboard still via ``falaw.generate_image``.
2. Call ``falaw.image_to_video(image, prompt, extra={duration: ...})``.
3. Trim/pad to the shot's exact duration.
"""

from __future__ import annotations

from pathlib import Path

from muvid.renderers import RenderContext
from muvid.renderers._common import (
    storyboard_prompt,
    trim_video_to_duration,
    upload_local_file_to_temp_url,
)


def render_image_to_video(ctx: RenderContext, *, quality: str = "balanced") -> Path:
    from falaw import generate_image, image_to_video

    prompt = storyboard_prompt(ctx)
    seed_image_url: str
    if ctx.environment_image_path is not None:
        seed_image_url = upload_local_file_to_temp_url(ctx.environment_image_path)
    else:
        # Make a storyboard still on the fly.
        still_target = ctx.shot_dir / "storyboard.png"
        if not still_target.exists():
            r = generate_image(prompt, quality=quality, image_size="landscape_16_9")
            if not r.first:
                raise RuntimeError(
                    f"image_to_video: storyboard generation failed for {ctx.shot.id}"
                )
            r.first.download(to=str(still_target))
        seed_image_url = upload_local_file_to_temp_url(still_target)

    result = image_to_video(
        seed_image_url,
        prompt,
        quality=quality,
        extra={"duration": max(1, int(round(ctx.shot.duration_s)))},
    )
    if not result.first:
        raise RuntimeError(f"image_to_video: no asset returned for {ctx.shot.id}")
    raw = ctx.shot_dir / "raw.mp4"
    result.first.download(to=str(raw))
    final = ctx.shot_dir / "output.mp4"
    return trim_video_to_duration(raw, ctx.shot.duration_s, final)
