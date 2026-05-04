"""Render dispatch — turn a single ShotSpec into ``shots/<id>/output.mp4``.

Each render strategy is a small function ``render_<strategy>(project,
shot, *, audio_slice_path, ctx) -> Path``. The dispatcher resolves
shared dependencies (audio slice, lyric lines that fall in the shot,
character anchor image, environment anchor image) once and passes them
in.

Caching: each shot output's name is content-derived. If
``shots/<id>/output.mp4`` exists and the recorded ``shot.json`` hash
matches the current ShotSpec, we skip.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mtv.project import MusicVideoProject
from mtv.schema import ShotSpec


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderContext:
    """Shared resolved inputs for rendering a shot."""

    project: MusicVideoProject
    shot: ShotSpec
    shot_dir: Path
    audio_slice_path: Path
    character_image_paths: dict[str, Path]
    environment_image_path: Path | None
    lyric_lines: list[Any]  # mtv.align.LineAlignment
    global_style: str = ""


def render_shot(
    project: MusicVideoProject,
    shot_id: str,
    *,
    quality: str = "balanced",
    force: bool = False,
) -> Path:
    """Render a single shot. Returns the path to the produced mp4.

    Skipped (returns the existing path) if a previously-rendered output
    matches the current shot definition's hash, unless ``force=True``.
    """
    spec = project.read_spec()
    shot = spec.shot(shot_id)
    shot_dir = project.shot_dir(shot.id)
    shot_dir.mkdir(parents=True, exist_ok=True)
    out_path = shot_dir / "output.mp4"
    hash_path = shot_dir / "output.hash"
    current_hash = _shot_hash(shot, spec.global_style)

    if not force and out_path.exists() and hash_path.exists():
        if hash_path.read_text().strip() == current_hash:
            return out_path

    ctx = _build_context(project, shot, spec.global_style)
    strategy = shot.render_strategy
    if strategy == "lipsync":
        from mtv.render.lipsync import render_lipsync as _render
    elif strategy == "image_to_video":
        from mtv.render.image_to_video import render_image_to_video as _render
    elif strategy == "text_to_video":
        from mtv.render.text_to_video import render_text_to_video as _render
    elif strategy == "still":
        from mtv.render.still import render_still as _render
    elif strategy == "animation":
        from mtv.render.animation import render_animation as _render
    else:
        raise ValueError(f"Unknown render_strategy: {strategy!r}")

    produced = _render(ctx, quality=quality)
    if produced.resolve() != out_path.resolve():
        shutil.copy2(produced, out_path)
    hash_path.write_text(current_hash)
    project.log_decision(
        "render_shot", shot_id=shot.id, strategy=strategy,
        duration_s=shot.duration_s, quality=quality,
    )
    return out_path


def render_all(
    project: MusicVideoProject, *, quality: str = "balanced", force: bool = False
) -> list[Path]:
    spec = project.read_spec()
    return [
        render_shot(project, sh.id, quality=quality, force=force)
        for sh in spec.shots
    ]


# --- internals ------------------------------------------------------------


def _shot_hash(shot: ShotSpec, global_style: str) -> str:
    payload = json.dumps(
        {"shot": _shot_dict(shot), "style": global_style},
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _shot_dict(shot: ShotSpec) -> dict:
    from dataclasses import asdict

    d = asdict(shot)
    d["characters"] = list(d["characters"])
    return d


def _build_context(
    project: MusicVideoProject, shot: ShotSpec, global_style: str
) -> RenderContext:
    from mtv.characters import get_character_anchor_image
    from mtv.environments import get_environment_anchor_image

    shot_dir = project.shot_dir(shot.id)
    audio_slice = _ensure_audio_slice(project, shot)

    char_imgs: dict[str, Path] = {}
    for name in shot.characters:
        try:
            char_imgs[name] = get_character_anchor_image(project, name)
        except FileNotFoundError:
            # render strategies that don't need an image will tolerate missing
            pass

    env_img: Path | None = None
    if shot.environment:
        env_img = get_environment_anchor_image(project, shot.environment)

    lines = _lyric_lines_for_shot(project, shot)

    return RenderContext(
        project=project,
        shot=shot,
        shot_dir=shot_dir,
        audio_slice_path=audio_slice,
        character_image_paths=char_imgs,
        environment_image_path=env_img,
        lyric_lines=lines,
        global_style=global_style,
    )


def _ensure_audio_slice(project: MusicVideoProject, shot: ShotSpec) -> Path:
    """Extract the song's audio over [start_s, end_s] for this shot."""
    from mixing.audio import Audio

    out = project.shot_dir(shot.id) / "audio.wav"
    if out.exists():
        return out
    song = project.song_path()
    audio = Audio(str(song))
    seg = audio[shot.start_s:shot.end_s]
    seg.save(str(out))
    return out


def _lyric_lines_for_shot(project: MusicVideoProject, shot: ShotSpec) -> list:
    """Read the alignment store and return lyric lines that fall in the shot."""
    align_path = project.root / "lyrics" / "alignment.annot"
    if not align_path.exists():
        return []
    try:
        from lacing import SqliteStore, TimeInterval, RationalTime
    except Exception:
        return []
    store = SqliteStore(str(align_path))
    try:
        rate = 1000
        window = TimeInterval(
            RationalTime(int(shot.start_s * rate), rate),
            RationalTime(int(shot.end_s * rate), rate),
        )
        out: list[dict] = []
        for ann in store.intersects(window):
            if ann.tier != "lines":
                continue
            out.append(
                {
                    "text": ann.body.get("text", ""),
                    "start_s": float(ann.reference.interval.start),
                    "end_s": float(ann.reference.interval.end),
                    "line_index": ann.body.get("line_index"),
                    "section": ann.body.get("section"),
                }
            )
        out.sort(key=lambda r: r["start_s"])
        return out
    finally:
        store.close()
