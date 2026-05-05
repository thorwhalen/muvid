"""``muvid.contracts`` — adapters between muvid's SSOT and sibling shapes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# --- character / environment adapters ------------------------------------


def test_character_to_falaw_includes_voice(tmp_path):
    pytest.importorskip("falaw")
    from falaw.scene import Character, Voice
    from muvid import facade
    from muvid.contracts import character_to_falaw
    from muvid.project import MusicVideoProject

    facade.init_project(tmp_path / "p")
    facade.add_character(
        tmp_path / "p", "alice",
        description="lead singer",
        voice_id="v1",
    )
    p = MusicVideoProject(tmp_path / "p")
    char = character_to_falaw(p, "alice")
    assert isinstance(char, Character)
    assert char.name == "alice"
    assert char.description == "lead singer"
    assert isinstance(char.voice, Voice)
    assert char.voice.voice_id == "v1"


def test_character_to_falaw_no_voice_when_unset(tmp_path):
    pytest.importorskip("falaw")
    from muvid import facade
    from muvid.contracts import character_to_falaw
    from muvid.project import MusicVideoProject

    facade.init_project(tmp_path / "p")
    facade.add_character(tmp_path / "p", "bob", description="background")
    p = MusicVideoProject(tmp_path / "p")
    char = character_to_falaw(p, "bob")
    assert char.voice is None


def test_character_to_falaw_resolves_anchor_image(tmp_path):
    """When refs/ has an image, the falaw Character carries that path as URL."""
    pytest.importorskip("falaw")
    from muvid import facade
    from muvid.contracts import character_to_falaw
    from muvid.project import MusicVideoProject

    facade.init_project(tmp_path / "p")
    facade.add_character(tmp_path / "p", "alice")
    refs_dir = tmp_path / "p" / "characters" / "alice" / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    img = refs_dir / "face.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    p = MusicVideoProject(tmp_path / "p")
    char = character_to_falaw(p, "alice")
    assert char.reference_image_url
    assert char.reference_image_url.endswith("face.png")


def test_environment_to_falaw_round_trips_card_fields(tmp_path):
    pytest.importorskip("falaw")
    from falaw.scene import Environment
    from muvid import facade
    from muvid.contracts import environment_to_falaw
    from muvid.project import MusicVideoProject

    facade.init_project(tmp_path / "p")
    facade.add_environment(
        tmp_path / "p", "park",
        description="wooden bench at dusk",
        time_of_day="dusk",
        lighting="warm",
    )
    p = MusicVideoProject(tmp_path / "p")
    env = environment_to_falaw(p, "park")
    assert isinstance(env, Environment)
    assert env.name == "park"
    assert env.description == "wooden bench at dusk"
    assert env.time_of_day == "dusk"
    assert env.lighting == "warm"
    # Anchor image not yet rendered.
    assert env.reference_image_url == ""


# --- word timings ---------------------------------------------------------


def _seed_alignment(project_root: Path):
    """Write a tiny alignment.annot covering [0, 30] with 4 words."""
    pytest.importorskip("lacing")
    from lacing import SqliteStore
    from lacing.tracks.subtitle import SubtitleBuilder

    align_dir = project_root / "lyrics"
    align_dir.mkdir(parents=True, exist_ok=True)
    path = align_dir / "alignment.annot"
    if path.exists():
        path.unlink()
    store = SqliteStore(str(path))
    try:
        b = SubtitleBuilder(store, asset_id="song:audio")
        b.section("verse", 0.0, 30.0)
        b.line(
            "hello world bye now", 12.5, 17.0,
            section="verse", line_index=0,
            words=[
                ("hello", 12.5, 13.0),
                ("world", 13.2, 14.0),
                ("bye", 15.0, 15.5),
                ("now", 15.6, 17.0),
            ],
        )
    finally:
        store.close()
    return path


def test_word_timings_for_window_returns_absolute_times(tmp_path):
    pytest.importorskip("lacing")
    from muvid import facade
    from muvid.contracts import word_timings_for_window
    from muvid.project import MusicVideoProject

    facade.init_project(tmp_path / "p")
    _seed_alignment(tmp_path / "p")

    p = MusicVideoProject(tmp_path / "p")
    out = word_timings_for_window(p, 12.0, 16.0)
    # "now" at 15.6–17.0 still overlaps [12, 16] (only the start is in).
    assert [w[0] for w in out] == ["hello", "world", "bye", "now"]
    # Times are absolute (song-seconds), not slice-relative.
    assert out[0][1] == pytest.approx(12.5)
    # Tighter window excludes "now".
    tight = word_timings_for_window(p, 12.0, 15.5)
    assert [w[0] for w in tight] == ["hello", "world", "bye"]


def test_word_timings_for_window_empty_without_store(tmp_path):
    from muvid import facade
    from muvid.contracts import word_timings_for_window
    from muvid.project import MusicVideoProject

    facade.init_project(tmp_path / "p")
    p = MusicVideoProject(tmp_path / "p")
    assert word_timings_for_window(p, 0.0, 10.0) == []


def test_shifted_word_timings_clamps_negative_starts():
    from muvid.contracts import shifted_word_timings

    timings = [("hello", 12.5, 13.0), ("world", 13.2, 14.0)]
    shifted = shifted_word_timings(timings, offset_s=13.0)
    assert shifted[0] == ("hello", 0.0, 0.0)  # 12.5 - 13.0 → clamped
    assert shifted[1][1] == pytest.approx(0.2)
    assert shifted[1][2] == pytest.approx(1.0)


def test_animation_renderers_helper_now_routes_via_contracts(tmp_path):
    """Sanity: the shot-window word-timings helper still works after
    being wired through ``muvid.contracts``."""
    pytest.importorskip("lacing")
    pytest.importorskip("an")

    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.renderers import RenderContext
    from muvid.renderers.animation import _word_timings_for_shot
    from muvid.schema import ShotSpec

    facade.init_project(tmp_path / "p")
    _seed_alignment(tmp_path / "p")

    p = MusicVideoProject(tmp_path / "p")
    shot = ShotSpec(
        id="s01", start_s=12.5, end_s=15.0,
        render_strategy="animation", characters=("alice",),
    )
    ctx = RenderContext(
        project=p, shot=shot,
        shot_dir=tmp_path / "p" / "shots" / "s01",
        audio_slice_path=tmp_path / "audio.wav",
        character_image_paths={}, environment_image_path=None,
        lyric_lines=[],
    )
    timings = list(_word_timings_for_shot(ctx))
    # "hello" at song 12.5 → slice 0.0; "world" at 13.2 → 0.7.
    assert timings[0][0] == "hello"
    assert timings[0][1] == pytest.approx(0.0)
    assert timings[1][0] == "world"
    assert timings[1][1] == pytest.approx(0.7)


# --- progress event adapter ----------------------------------------------


def test_progress_event_to_dict_is_json_safe():
    pytest.importorskip("falaw")
    from falaw.events import ProgressEvent
    from muvid.contracts import progress_event_to_dict

    event = ProgressEvent(
        kind="done", application="fal-ai/test",
        call_id="abc123", elapsed_s=1.5, message="finished",
    )
    payload = progress_event_to_dict(event)
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    assert decoded["kind"] == "done"
    assert decoded["application"] == "fal-ai/test"
    assert decoded["call_id"] == "abc123"
    assert decoded["elapsed_s"] == 1.5
    assert decoded["message"] == "finished"
