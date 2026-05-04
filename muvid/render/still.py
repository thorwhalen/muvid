"""Render strategy: still — a single image held for the shot duration.

Cheapest possible render. Uses ffmpeg to loop the image and mux the
shot's audio slice onto it. If we have an environment anchor, we use
it; otherwise we generate a still via ``falaw.generate_image``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from muvid.render import RenderContext
from muvid.render._common import storyboard_prompt


def render_still(ctx: RenderContext, *, quality: str = "balanced") -> Path:
    from falaw import generate_image

    image_path: Path
    if ctx.environment_image_path is not None:
        image_path = ctx.environment_image_path
    else:
        image_path = ctx.shot_dir / "storyboard.png"
        if not image_path.exists():
            r = generate_image(
                storyboard_prompt(ctx), quality=quality, image_size="landscape_16_9"
            )
            if not r.first:
                raise RuntimeError(
                    f"still: storyboard generation failed for {ctx.shot.id}"
                )
            r.first.download(to=str(image_path))

    out = ctx.shot_dir / "output.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-i",
        str(ctx.audio_slice_path),
        "-c:v",
        "libx264",
        "-tune",
        "stillimage",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        "-t",
        f"{ctx.shot.duration_s:.3f}",
        str(out),
    ]
    subprocess.run(
        cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return out
