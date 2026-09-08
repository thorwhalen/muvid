"""Tests for the ``beat_grid`` MCP tool (thorwhalen/muvid#18 item 5).

The song's beat grid used to be reachable only as a side effect of ``score_footage`` —
a background, cv2-heavy, per-clip decode pass — although ``mixing.audio.beat_grid`` is
one call on the song alone. These tests pin what the tool promises: the payload shape,
that a second call is a file read (cached under ``scores/`` keyed on ``song_hash``), that
a scored project is served from its scoring run, that a missing librosa is a clean
ToolError naming the install rather than a traceback — and that nothing ELSE the
estimator raises is swallowed (the mixing rule).

No ffmpeg, no librosa, no cv2: the estimator is faked where its result matters, and the
real one is called only to prove the librosa-missing chain end to end.
"""

from __future__ import annotations

import io
import json
import sys
import wave
from types import SimpleNamespace

import pytest

pytest.importorskip("fastmcp")

import muvid.mcp as mcp
import muvid.mcp.footage_tools as ft
from muvid.mcp.identity import use_email

EMAIL = "u@x.com"
SONG_DURATION = 30.0


def _tiny_wav() -> bytes:
    """Half a second of 8 kHz mono silence — a real WAV, so a decode-first estimator
    would still reach its librosa import rather than fail on the file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 4000)
    return buf.getvalue()


def _project_with_song(tmp_path, monkeypatch, *, song_bytes=b"RIFF-stand-in"):
    """A project whose song went in through ``set_song`` (so the manifest carries the
    real ``song_hash`` the cache is keyed on) without ffprobe."""
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(
        "muvid.footage.workspace._probe_duration", lambda p: SONG_DURATION
    )
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email(EMAIL).create_project("p")
    song = tmp_path / "song.wav"
    song.write_bytes(song_bytes)
    proj.set_song(str(song), ext="wav")
    return proj


def _fake_estimator(monkeypatch, *, beats, tempo=120.0, downbeats=()):
    """Stand in for ``mixing.audio.beat_grid`` with the three ``BeatGrid`` attributes
    muvid reads; returns the list of songs it was asked about."""
    calls = []

    def fake(audio, **kwargs):
        calls.append(audio)
        return SimpleNamespace(
            beat_times=list(beats), downbeat_times=list(downbeats), tempo_bpm=tempo
        )

    # The tool imports the name from ``mixing.audio`` at call time (the orchestrator's
    # idiom), so patching the package attribute is what reaches it.
    monkeypatch.setattr("mixing.audio.beat_grid", fake)
    return calls


def _cache_path(proj):
    return proj.root / "scores" / "beat_grid.json"


def test_beat_grid_is_a_declared_free_footage_tool():
    assert "beat_grid" in mcp.FOOTAGE_TOOLS
    assert "beat_grid" in mcp.FREE_TOOLS
    assert "beat_grid" not in mcp.COSTED_TOOLS


def test_beat_grid_payload_then_cache(tmp_path, monkeypatch):
    proj = _project_with_song(tmp_path, monkeypatch)
    calls = _fake_estimator(monkeypatch, beats=[0.5, 1.0, 1.5, 2.0], tempo=120.0)
    # A record for ANOTHER song sitting in the cache slot is ignored, not served.
    cache = _cache_path(proj)
    cache.parent.mkdir()
    cache.write_text(
        json.dumps({"song_hash": "not-this-song", "tempo_bpm": 1.0, "beat_times": [9]})
    )

    with use_email(EMAIL):
        first = ft.beat_grid("p")

    assert first == {
        "project_id": "p",
        "tempo_bpm": 120.0,
        "beats": [0.5, 1.0, 1.5, 2.0],
        "n_beats": 4,
        "song_duration": SONG_DURATION,
        "source": "computed",
    }
    assert "downbeats" not in first  # librosa measures none: absent, never []
    assert calls == [str(proj.song_path())]
    record = json.loads(cache.read_text())
    assert record["song_hash"] == proj.song_hash()
    assert record["beat_times"] == [0.5, 1.0, 1.5, 2.0]
    assert record["tempo_bpm"] == 120.0

    with use_email(EMAIL):
        second = ft.beat_grid("p")
    assert calls == [str(proj.song_path())]  # the estimator did not run again
    assert second == {**first, "source": "cache"}


def test_beat_grid_reports_downbeats_only_when_measured(tmp_path, monkeypatch):
    proj = _project_with_song(tmp_path, monkeypatch)
    _fake_estimator(monkeypatch, beats=[0.5, 1.0, 1.5, 2.0], downbeats=[0.5, 2.0])
    with use_email(EMAIL):
        computed = ft.beat_grid("p")
        cached = ft.beat_grid("p")
    assert computed["downbeats"] == [0.5, 2.0]
    assert cached["downbeats"] == [0.5, 2.0] and cached["source"] == "cache"
    assert json.loads(_cache_path(proj).read_text())["downbeat_times"] == [0.5, 2.0]


def test_beat_grid_is_lifted_from_a_scoring_run(tmp_path, monkeypatch):
    """A scored project already paid for its grid (the orchestrator's first stage
    persists it in scores/manifest.json) — the estimator must not run again."""
    proj = _project_with_song(tmp_path, monkeypatch)
    calls = _fake_estimator(monkeypatch, beats=[9.9])
    manifest = proj.root / "scores" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "song_hash": proj.song_hash(),
                "tempo_bpm": 96.0,
                "beats": {"beat_times": [0.625, 1.25], "downbeat_times": []},
            }
        )
    )

    with use_email(EMAIL):
        lifted = ft.beat_grid("p")
    assert lifted["source"] == "scores"
    assert lifted["beats"] == [0.625, 1.25] and lifted["n_beats"] == 2
    assert lifted["tempo_bpm"] == 96.0
    assert "downbeats" not in lifted
    assert calls == []

    # A scoring run on a PREVIOUS song is not this song's grid: compute, don't lift.
    manifest.write_text(
        json.dumps({"song_hash": "old", "tempo_bpm": 1.0, "beats": {"beat_times": [7]}})
    )
    with use_email(EMAIL):
        computed = ft.beat_grid("p")
    assert computed["source"] == "computed" and computed["beats"] == [9.9]
    assert calls == [str(proj.song_path())]


def test_beat_grid_needs_a_song(tmp_path, monkeypatch):
    from fastmcp.exceptions import ToolError
    from muvid.footage.workspace import FootageWorkspace

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    FootageWorkspace.for_email(EMAIL).create_project("p")
    with use_email(EMAIL), pytest.raises(ToolError, match="no song set"):
        ft.beat_grid("p")


def test_beat_grid_without_librosa_is_a_clean_tool_error(tmp_path, monkeypatch):
    """The connector without the ``scoring`` extra. ``sys.modules['librosa'] = None``
    makes every ``import librosa`` raise, which mixing's ``require_package`` turns into
    the ImportError the tool translates — the REAL estimator runs, so this pins the
    chain end to end rather than a fake's behaviour."""
    from fastmcp.exceptions import ToolError

    proj = _project_with_song(tmp_path, monkeypatch, song_bytes=_tiny_wav())
    monkeypatch.setitem(sys.modules, "librosa", None)

    with use_email(EMAIL), pytest.raises(ToolError, match=r"muvid\[scoring\]") as ei:
        ft.beat_grid("p")
    # Translated, not swallowed: the ImportError rides along as the cause.
    assert isinstance(ei.value.__cause__, ImportError)
    assert not _cache_path(proj).exists()  # a refusal caches nothing


def test_beat_grid_does_not_swallow_an_estimator_failure(tmp_path, monkeypatch):
    """Only ImportError is translated. Anything else mixing raises is a regression that
    must surface as itself (CLAUDE.md: never a broad except around a sibling's call)."""
    _project_with_song(tmp_path, monkeypatch)

    def broken(audio, **kwargs):
        raise RuntimeError("decode failed")

    monkeypatch.setattr("mixing.audio.beat_grid", broken)
    with use_email(EMAIL), pytest.raises(RuntimeError, match="decode failed"):
        ft.beat_grid("p")
