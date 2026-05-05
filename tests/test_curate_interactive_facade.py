"""``muvid.facade.curate_character_interactive`` — JSON-driven curate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("lookbook")


def _seed_refs(project_root: Path, character: str, n: int = 5) -> list[Path]:
    """Write n minimal valid PNGs into the character's refs/ folder."""
    PIL = pytest.importorskip("PIL.Image")
    refs_dir = project_root / "characters" / character / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i in range(n):
        f = refs_dir / f"img_{i:02d}.png"
        # 8x8 distinct-color PNG — enough to satisfy any scorer that opens it.
        img = PIL.new("RGB", (8, 8), (i * 30 % 256, 0, 255 - i * 20 % 256))
        img.save(f, "PNG")
        out.append(f)
    return out


def _image_id_for(path: Path) -> str:
    from lookbook import PathImageRef

    return PathImageRef(path=str(path)).image_id


def test_curate_character_interactive_with_inline_decisions(tmp_path):
    from muvid import facade

    facade.init_project(tmp_path / "p")
    facade.add_character(tmp_path / "p", "alice", description="lead")
    paths = _seed_refs(tmp_path / "p", "alice", n=4)

    keep_ids = [_image_id_for(paths[0]), _image_id_for(paths[2])]
    decisions = [
        {"keep": keep_ids, "reject": [], "stop": True},
    ]

    selected = facade.curate_character_interactive(
        tmp_path / "p", "alice", decisions=decisions, k=4, present=4,
    )
    assert len(selected) == 2
    selected_names = sorted(Path(p).name for p in selected)
    assert "img_00.png" in selected_names
    assert "img_02.png" in selected_names


def test_curate_character_interactive_reads_json_file(tmp_path):
    from muvid import facade

    facade.init_project(tmp_path / "p")
    facade.add_character(tmp_path / "p", "alice")
    paths = _seed_refs(tmp_path / "p", "alice", n=3)

    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            [
                {"keep": [_image_id_for(paths[0])], "stop": True},
            ]
        )
    )

    selected = facade.curate_character_interactive(
        tmp_path / "p", "alice", decisions=decisions_path, k=3, present=3,
    )
    assert len(selected) == 1
    assert Path(selected[0]).name == "img_00.png"


def test_curate_character_interactive_writes_anchor_card_path(tmp_path):
    """First selected image is mirrored as ``card.reference_image_path``."""
    from muvid import facade
    from muvid.project import MusicVideoProject

    facade.init_project(tmp_path / "p")
    facade.add_character(tmp_path / "p", "alice")
    paths = _seed_refs(tmp_path / "p", "alice", n=3)

    facade.curate_character_interactive(
        tmp_path / "p", "alice",
        decisions=[
            {"keep": [_image_id_for(paths[1])], "stop": True},
        ],
        k=3, present=3,
    )

    p = MusicVideoProject(tmp_path / "p")
    card = p.read_character_card("alice")
    # The anchor should point at the first kept image.
    assert "characters/alice/selected" in card.get("reference_image_path", "")
