"""Character cards + reference image curation via lookbook.

A character lives at ``characters/<name>/`` inside a project. The card
(``card.json``) holds the human-meaningful description, the reference
image URL/path used as the lipsync anchor, and the optional voice spec.

Reference images go through three states:

- ``refs/`` — raw image dump (anything the user collected, plus
  optional ``generate_references`` outputs).
- ``selected/`` — lookbook's curated subset, ready for LoRA training
  or just for picking the canonical face.
- ``card.reference_image_url`` — a single chosen anchor (the best
  selected image) used by every downstream render.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional, Sequence

from muvid.project import MusicVideoProject


def add_character(
    project: MusicVideoProject,
    name: str,
    *,
    description: str = "",
    voice_id: str = "",
    reference_audio_url: str = "",
    voice_style: str = "",
) -> dict[str, Any]:
    """Create or update a character card. Idempotent."""
    project.add_character(name, description=description)
    card = project.read_character_card(name)
    card["description"] = description or card.get("description", "")
    if voice_id or reference_audio_url or voice_style:
        card["voice"] = {
            "voice_id": voice_id,
            "reference_audio_url": reference_audio_url,
            "style_notes": voice_style,
        }
    project.write_character_card(name, card)
    return card


def add_reference_images(
    project: MusicVideoProject,
    name: str,
    images: Sequence[str | Path],
    *,
    copy: bool = True,
) -> list[Path]:
    """Drop user-provided images into ``characters/<name>/refs/``."""
    refs_dir = project.character_dir(name) / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for src in images:
        src = Path(src).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(src)
        target = refs_dir / src.name
        if target.resolve() != src:
            (shutil.copy2 if copy else shutil.move)(str(src), str(target))
        out.append(target)
    return out


def generate_reference_images(
    project: MusicVideoProject,
    name: str,
    *,
    n: int = 6,
    style_variants: Sequence[str] = (),
    quality: str = "balanced",
) -> list[Path]:
    """Generate raw reference images via ``falaw.generate_image``.

    The character's ``description`` is used as the prompt; each variant
    (e.g. "front portrait", "three-quarter", "wide shot") is appended.
    Output goes to ``characters/<name>/refs/``.
    """
    from falaw import generate_image

    card = project.read_character_card(name)
    desc = card.get("description") or name
    variants = list(style_variants) or [
        "front portrait, eye-level, studio lighting",
        "three-quarter portrait, natural lighting",
        "profile shot, soft daylight",
        "wide environmental shot, full body",
        "close-up of face, neutral expression",
        "over-the-shoulder shot from behind",
    ]
    variants = (variants * ((n // len(variants)) + 1))[:n]
    refs_dir = project.character_dir(name) / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i, variant in enumerate(variants):
        prompt = f"{desc} | {variant}"
        result = generate_image(prompt, quality=quality)
        if not result.first:
            continue
        target = refs_dir / f"gen_{i:03d}.png"
        result.first.download(to=str(target))
        out.append(target)
    project.log_decision(
        "generate_reference_images",
        character=name,
        n_requested=n,
        n_generated=len(out),
        quality=quality,
    )
    return out


def curate_references(
    project: MusicVideoProject,
    name: str,
    *,
    k: int = 8,
    recipe: str = "person_mock",
) -> list[Path]:
    """Run lookbook on ``refs/`` and copy the selected images to ``selected/``.

    Default recipe is ``person_mock`` so this works without the heavy ML
    dependencies; pass ``recipe="person"`` once those are installed.
    """
    from lookbook import curate
    from lookbook.profiles import load as load_profile

    refs_dir = project.character_dir(name) / "refs"
    selected_dir = project.character_dir(name) / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(
        p for p in refs_dir.iterdir() if p.is_file() and not p.name.startswith(".")
    )
    if not images:
        raise RuntimeError(
            f"No reference images in {refs_dir}. Add some first (drop files "
            f"in or call generate_reference_images)."
        )

    profile = load_profile(recipe)
    result = curate(
        images,
        k=k,
        scorer_ids=profile.get("scorers", ()),
        embedder_ids=profile.get("embedders", ()),
        filter_ids=profile.get("filters", ()),
        selector_id=profile.get("selector", "top_k"),
        constraints=profile.get("constraints"),
    )

    # Copy the selected images into selected/.
    for old in selected_dir.iterdir():
        if old.is_file():
            old.unlink()
    selected_paths: list[Path] = []
    for r in result.kept:
        src = Path(getattr(r, "path", None) or r.image_id)
        if not src.exists():
            continue
        target = selected_dir / src.name
        shutil.copy2(src, target)
        selected_paths.append(target)

    # Use the first selected image as the canonical anchor.
    if selected_paths:
        card = project.read_character_card(name)
        card["reference_image_path"] = (
            selected_paths[0].relative_to(project.root).as_posix()
        )
        project.write_character_card(name, card)
    project.log_decision(
        "curate_references",
        character=name,
        recipe=recipe,
        k=k,
        n_in=len(images),
        n_out=len(selected_paths),
    )
    return selected_paths


def get_character_anchor_image(project: MusicVideoProject, name: str) -> Path:
    """Return the canonical anchor image path for a character.

    Resolves in order: ``card.reference_image_path`` (curated),
    first file in ``selected/``, first file in ``refs/``.
    """
    card = project.read_character_card(name)
    rel = card.get("reference_image_path")
    if rel:
        p = (project.root / rel).resolve()
        if p.exists():
            return p
    cdir = project.character_dir(name)
    for sub in ("selected", "refs"):
        d = cdir / sub
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    return f
    raise FileNotFoundError(f"No reference image for character {name!r}")
