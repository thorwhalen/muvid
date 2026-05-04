"""MusicVideoProject — folder layout, persistence, decisions log.

Avoids ffprobe / network: ``set_song`` is exercised separately as an
integration probe and skipped if ffprobe is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mtv.project import MusicVideoProject
from mtv.schema import SectionSpec, ShotSpec


def test_init_creates_expected_folders(tmp_path):
    root = tmp_path / "p"
    proj = MusicVideoProject.init(root, title="X")
    for sub in (
        "song", "lyrics", "characters", "environments",
        "script", "shots", "output", ".mtv",
    ):
        assert (root / sub).is_dir()
    spec = proj.read_spec()
    assert spec.title == "X"
    assert spec.song is None


def test_init_refuses_nonempty_folder(tmp_path):
    (tmp_path / "marker").write_text("hi")
    with pytest.raises(FileExistsError):
        MusicVideoProject.init(tmp_path)


def test_add_character_idempotent_and_persists(tmp_path):
    proj = MusicVideoProject.init(tmp_path / "p")
    proj.add_character("maya", description="lead")
    proj.add_character("maya", description="lead")  # second call is fine
    spec = proj.read_spec()
    names = [c.name for c in spec.characters]
    assert names.count("maya") == 1
    card = proj.read_character_card("maya")
    assert card["name"] == "maya"


def test_upsert_shot_orders_by_start_s(tmp_path):
    proj = MusicVideoProject.init(tmp_path / "p")
    proj.upsert_shot(ShotSpec(id="b", start_s=5.0, end_s=10.0))
    proj.upsert_shot(ShotSpec(id="a", start_s=0.0, end_s=5.0))
    spec = proj.read_spec()
    assert [s.id for s in spec.shots] == ["a", "b"]


def test_upsert_shot_replaces_same_id(tmp_path):
    proj = MusicVideoProject.init(tmp_path / "p")
    proj.upsert_shot(ShotSpec(id="x", start_s=0.0, end_s=1.0, description="first"))
    proj.upsert_shot(ShotSpec(id="x", start_s=0.0, end_s=1.0, description="second"))
    spec = proj.read_spec()
    assert len(spec.shots) == 1
    assert spec.shots[0].description == "second"


def test_log_decision_appends_jsonl(tmp_path):
    proj = MusicVideoProject.init(tmp_path / "p")
    proj.log_decision("foo", x=1)
    proj.log_decision("bar", y="two")
    log = (tmp_path / "p" / ".mtv" / "decisions.jsonl").read_text().splitlines()
    assert len(log) == 2
    assert json.loads(log[0]) == {"kind": "foo", "x": 1}
    assert json.loads(log[1]) == {"kind": "bar", "y": "two"}


def test_set_song_probes_duration(tmp_path):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available on PATH")
    song = tmp_path / "src.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=cl=mono:r=22050",
         "-t", "3", str(song)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proj = MusicVideoProject.init(tmp_path / "p", song_path=song, copy_song=True)
    spec = proj.read_spec()
    assert spec.song is not None
    assert spec.song.duration_s == pytest.approx(3.0, abs=0.05)
    assert (tmp_path / "p" / spec.song.audio_path).exists()
