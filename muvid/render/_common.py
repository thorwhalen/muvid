"""Shared helpers for the render strategies."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from muvid.render import RenderContext


def storyboard_prompt(ctx: RenderContext, *, include_lyrics: bool = True) -> str:
    """Compose a single prose prompt for image/video generation.

    Combines: shot description, environment, characters' descriptions,
    framing/camera, the global style, and (optionally) the lyrics
    being sung in this slice.
    """
    shot = ctx.shot
    parts: list[str] = []
    if shot.description:
        parts.append(shot.description)
    if shot.framing and shot.framing != "medium":
        parts.append(f"framing: {shot.framing}")
    if shot.camera:
        parts.append(f"camera: {shot.camera}")
    if shot.environment:
        env_card = ctx.project.read_environment_card(shot.environment)
        env_desc = env_card.get("description") or shot.environment
        parts.append(f"location: {env_desc}")
        if env_card.get("time_of_day"):
            parts.append(f"time: {env_card['time_of_day']}")
        if env_card.get("lighting"):
            parts.append(f"lighting: {env_card['lighting']}")
    for name in shot.characters:
        try:
            card = ctx.project.read_character_card(name)
            desc = card.get("description") or name
            parts.append(f"{name}: {desc}")
        except FileNotFoundError:
            parts.append(name)
    if include_lyrics and ctx.lyric_lines:
        snippet = " / ".join(L["text"] for L in ctx.lyric_lines)
        parts.append(f"lyrics being sung: {snippet}")
    if ctx.global_style:
        parts.append(f"style: {ctx.global_style}")
    return " | ".join(p for p in parts if p)


def upload_local_file_to_temp_url(path: Path) -> str:
    """Get a public URL for a local file via fal's storage.

    fal.ai's image/video models all want URLs. We use ``fal_client.upload_file``
    if available (which is what falaw's ``call_fal`` already uses for input
    files via its session helper).
    """
    try:
        import fal_client  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "fal_client not installed; needed to upload local files. "
            "pip install fal-client"
        ) from e
    return fal_client.upload_file(str(path))


def trim_video_to_duration(src: Path, target_s: float, dst: Path) -> Path:
    """Cut/pad a video to exactly ``target_s`` seconds.

    Falls back to copying ``src`` to ``dst`` if mixing's helpers fail.
    """
    try:
        from mixing.video import Video

        v = Video(str(src))
        if abs(v.duration - target_s) < 0.05:
            if src.resolve() != dst.resolve():
                import shutil
                shutil.copy2(src, dst)
            return dst
        if v.duration > target_s:
            cut = v[0:target_s]
            cut.save(str(dst))
            return dst
        # Pad with the last frame held — simplest approximation: just copy.
        # (A proper hold would require an ffmpeg "tpad" filter.)
        import shutil
        shutil.copy2(src, dst)
        return dst
    except Exception:
        import shutil
        shutil.copy2(src, dst)
        return dst
