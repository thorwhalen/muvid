"""Schema round-trip and helpers — pure-python, no I/O."""

from __future__ import annotations

from muvid.schema import (
    CharacterRef,
    EnvironmentRef,
    ProjectSpec,
    SCHEMA_VERSION,
    SectionSpec,
    ShotSpec,
    SongInfo,
)


def _example_spec() -> ProjectSpec:
    return ProjectSpec(
        title="Demo",
        song=SongInfo(audio_path="song/x.mp3", duration_s=12.5),
        characters=(CharacterRef(name="maya", description="lead"),),
        environments=(EnvironmentRef(name="park", description="bench"),),
        sections=(
            SectionSpec(id="intro", start_s=0.0, end_s=4.0, label="intro"),
            SectionSpec(id="v1", start_s=4.0, end_s=12.0, label="verse 1"),
        ),
        shots=(
            ShotSpec(
                id="s01", start_s=0.0, end_s=4.0, section_id="intro",
                render_strategy="still", environment="park",
            ),
            ShotSpec(
                id="s02", start_s=4.0, end_s=12.0, section_id="v1",
                render_strategy="lipsync", environment="park",
                characters=("maya",), description="medium close on maya",
            ),
        ),
        global_style="film grain",
    )


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    assert ProjectSpec().schema_version == SCHEMA_VERSION


def test_to_from_dict_roundtrip():
    spec = _example_spec()
    rebuilt = ProjectSpec.from_dict(spec.to_dict())
    assert rebuilt == spec


def test_from_dict_drops_unknown_keys():
    payload = {
        "title": "X",
        "schema_version": 1,
        "shots": [{"id": "s01", "start_s": 0, "end_s": 1, "junk_field": True}],
    }
    spec = ProjectSpec.from_dict(payload)
    assert spec.shots[0].id == "s01"
    assert not hasattr(spec.shots[0], "junk_field")


def test_lookup_helpers():
    spec = _example_spec()
    assert spec.section("intro").label == "intro"
    assert spec.shot("s02").characters == ("maya",)
    assert spec.character("maya").description == "lead"
    assert spec.environment("park").description == "bench"


def test_shot_duration():
    s = ShotSpec(id="x", start_s=2.0, end_s=5.5)
    assert s.duration_s == 3.5


def test_with_returns_new_instance():
    spec = _example_spec()
    new = spec.with_(title="renamed")
    assert spec.title == "Demo"
    assert new.title == "renamed"
