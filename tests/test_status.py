"""Structured status + format_status."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_status_on_fresh_project_shows_init_only(tmp_path):
    from muvid import facade

    facade.init_project(tmp_path / "p")
    s = facade.status(tmp_path / "p")
    assert s["stages"]["init"] is True
    assert s["stages"]["transcribe"] is False
    assert s["stages"]["align"] is False
    assert s["stages"]["script"] is False
    assert s["stages"]["compose"] is False
    assert s["stages"]["render"]["total"] == 0
    assert s["alignment"] is None


def test_status_after_adding_a_shot(tmp_path):
    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.schema import ShotSpec

    facade.init_project(tmp_path / "p")
    p = MusicVideoProject(tmp_path / "p")
    p.upsert_shot(
        ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still")
    )
    s = facade.status(tmp_path / "p")
    assert s["stages"]["script"] is True
    assert s["stages"]["render"]["total"] == 1
    assert s["stages"]["render"]["done"] == 0
    assert s["stages"]["render"]["pending"] == 1
    assert s["stages"]["render"]["shots"][0]["id"] == "s01"
    assert s["stages"]["render"]["shots"][0]["rendered"] is False


def test_format_status_is_string(tmp_path):
    from muvid import facade

    facade.init_project(tmp_path / "p", title="My Video")
    text = facade.format_status(facade.status(tmp_path / "p"))
    assert "My Video" in text
    assert "init" in text  # the stage label


def test_status_json_is_serializable(tmp_path):
    from muvid import facade

    facade.init_project(tmp_path / "p")
    s = facade.status(tmp_path / "p")
    # Round-trip through JSON to confirm no non-serializable objects.
    encoded = json.dumps(s, default=str)
    assert isinstance(encoded, str)
    assert "stages" in json.loads(encoded)


def test_status_alignment_summary_after_align(tmp_path):
    """When the alignment store exists, status reports counts + confidence."""
    pytest.importorskip("lacing")
    from muvid import facade
    from muvid.align import align_lyrics, write_alignment_store
    from muvid.lyrics import parse_lyrics_md

    facade.init_project(tmp_path / "p")
    md = "[v]\nhello hello\nworld world\n"
    transcript = {
        "words": [
            {"text": "hello", "start": 0.0, "end": 0.5, "type": "word"},
            {"text": "hello", "start": 0.5, "end": 1.0, "type": "word"},
            {"text": "world", "start": 2.0, "end": 2.5, "type": "word"},
            {"text": "world", "start": 2.5, "end": 3.0, "type": "word"},
        ]
    }
    al = align_lyrics(parse_lyrics_md(md), transcript, duration_s=4.0)
    align_path = tmp_path / "p" / "lyrics" / "alignment.annot"
    write_alignment_store(al, path=align_path)

    s = facade.status(tmp_path / "p")
    assert s["alignment"] is not None
    assert s["alignment"]["n_lines"] == 2
    assert s["alignment"]["n_words"] == 4
    assert sum(s["alignment"]["confidence_histogram"].values()) == 4
