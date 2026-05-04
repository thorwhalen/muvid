"""Environment cards + canonical establishing image generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mtv.project import MusicVideoProject


def add_environment(
    project: MusicVideoProject,
    name: str,
    *,
    description: str = "",
    time_of_day: str = "",
    lighting: str = "",
) -> dict[str, Any]:
    """Create or update an environment card."""
    project.add_environment(name, description=description)
    card = project.read_environment_card(name)
    card["description"] = description or card.get("description", "")
    if time_of_day:
        card["time_of_day"] = time_of_day
    if lighting:
        card["lighting"] = lighting
    project.write_environment_card(name, card)
    return card


def render_environment(
    project: MusicVideoProject,
    name: str,
    *,
    quality: str = "high",
) -> Path:
    """Generate the canonical establishing image for the environment.

    Saves to ``environments/<name>/establishing.png`` and stores the
    relative path on the card as ``reference_image_path``.
    """
    from falaw import generate_image

    card = project.read_environment_card(name)
    desc = card.get("description") or name
    parts = [desc]
    if card.get("time_of_day"):
        parts.append(f"time of day: {card['time_of_day']}")
    if card.get("lighting"):
        parts.append(f"lighting: {card['lighting']}")
    prompt = " | ".join(parts)
    result = generate_image(prompt, quality=quality, image_size="landscape_16_9")
    if not result.first:
        raise RuntimeError(f"render_environment: no asset returned for {name!r}")
    target = project.environment_dir(name) / "establishing.png"
    result.first.download(to=str(target))
    card["reference_image_path"] = target.relative_to(project.root).as_posix()
    project.write_environment_card(name, card)
    project.log_decision("render_environment", environment=name, prompt=prompt)
    return target


def get_environment_anchor_image(
    project: MusicVideoProject, name: str
) -> Path | None:
    """Return the canonical environment image, or None if not yet rendered."""
    card = project.read_environment_card(name)
    rel = card.get("reference_image_path")
    if not rel:
        return None
    p = (project.root / rel).resolve()
    return p if p.exists() else None
