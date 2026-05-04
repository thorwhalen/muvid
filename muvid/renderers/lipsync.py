"""Render strategy: lipsync.

For shots where a character is "singing on screen". We need a still
image of the character (the curated anchor) plus the audio slice. Calls
``falaw.animate_face`` (image+audio → talking video).

If multiple characters are present, we pick the first one and warn —
multi-character lipsync isn't in v0.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from muvid.renderers import RenderContext
from muvid.renderers._common import trim_video_to_duration, upload_local_file_to_temp_url


def render_lipsync(ctx: RenderContext, *, quality: str = "balanced") -> Path:
    from falaw import animate_face

    if not ctx.character_image_paths:
        raise RuntimeError(
            f"render_lipsync: shot {ctx.shot.id!r} has no character anchor "
            f"images. Add at least one reference image (muvid character add-images)."
        )
    if len(ctx.character_image_paths) > 1:
        warnings.warn(
            f"render_lipsync: shot {ctx.shot.id!r} has multiple characters "
            f"({list(ctx.character_image_paths)}); v0 only lipsyncs the first."
        )
    char_name, image_path = next(iter(ctx.character_image_paths.items()))

    image_url = upload_local_file_to_temp_url(image_path)
    audio_url = upload_local_file_to_temp_url(ctx.audio_slice_path)

    prompt = ctx.shot.description or f"{char_name} singing"
    if ctx.lyric_lines:
        prompt += " — lyrics: " + " / ".join(L["text"] for L in ctx.lyric_lines)

    result = animate_face(image_url, audio_url, prompt=prompt, quality=quality)
    if not result.first:
        raise RuntimeError(f"render_lipsync: no asset returned for {ctx.shot.id}")
    raw = ctx.shot_dir / "raw.mp4"
    result.first.download(to=str(raw))
    final = ctx.shot_dir / "output.mp4"
    return trim_video_to_duration(raw, ctx.shot.duration_s, final)
