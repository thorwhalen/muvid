"""Shared helpers for the render strategies."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from muvid.renderers import RenderContext


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

    - Within 50ms of target → copy unchanged.
    - Longer than target → cut to ``target_s`` via mixing.video.
    - Shorter than target → pad by holding the last video frame and
      appending silence to the audio track. Done in a single ffmpeg call
      using the ``tpad`` (video) and ``apad`` (audio) filters.

    On any failure, falls back to copying ``src`` to ``dst`` so the caller
    always gets *some* file at ``dst`` — but the caller should treat a
    failure as a soft warning (the video may not be the requested length).
    """
    try:
        from mixing.video import Video

        v = Video(str(src))
        delta = target_s - v.duration

        if abs(delta) < 0.05:
            if src.resolve() != dst.resolve():
                import shutil

                shutil.copy2(src, dst)
            return dst

        if delta < 0:
            # src is longer than target — straight cut.
            cut = v[0:target_s]
            cut.save(str(dst))
            return dst

        # src is shorter than target — pad video via tpad (clone last frame)
        # and pad audio via apad (silence). One ffmpeg call.
        return _pad_video_to_duration(src, target_s, dst, pad_seconds=delta)

    except Exception:
        import shutil

        shutil.copy2(src, dst)
        return dst


def _pad_video_to_duration(
    src: Path, target_s: float, dst: Path, *, pad_seconds: float
) -> Path:
    """Pad ``src`` to ``target_s`` by holding the last frame + silencing audio.

    Uses ffmpeg's ``tpad=stop_mode=clone`` (video) and ``apad`` (audio) filters
    in a single call. Re-encodes (filter graph requires it). Falls through to
    a simple copy on any subprocess failure.
    """
    import shutil
    import subprocess

    if pad_seconds <= 0:
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        return dst

    # Whole-millisecond stop_duration is what tpad takes (in seconds, float).
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        f"tpad=stop_mode=clone:stop_duration={pad_seconds:.3f}",
        "-af",
        f"apad=pad_dur={pad_seconds:.3f}",
        # -t caps the output length so any rounding can't push past target.
        "-t",
        f"{target_s:.3f}",
        # Sane defaults: H.264 + AAC, faststart for streaming.
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return dst
    except (subprocess.CalledProcessError, FileNotFoundError):
        # ffmpeg not on PATH or filter rejected — copy as a soft fallback so
        # the caller still gets a file. The duration mismatch will be visible
        # in any QA report (avp.inspect.shot_report).
        shutil.copy2(src, dst)
        return dst
