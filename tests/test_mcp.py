"""Tests for muvid.mcp — the ``music-visualizer`` tool surface + aggregation seam.

Gated on the ``[mcp]`` extra (nw + fastmcp). The end-to-end render tests also need
ffmpeg and skip without it. The caller identity is bound via ``use_email`` (the same
override local/stdio use relies on); the SSRF-guarded fetch is patched to local fixtures
because the guard correctly blocks localhost.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("nw")
pytest.importorskip("fastmcp")

import muvid.mcp as mcp  # noqa: E402
import muvid.mcp.tools as tools  # noqa: E402
from muvid.mcp.identity import current_email, use_email  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


# -- aggregation seam --------------------------------------------------------


def test_register_tools_prefixes_and_selects():
    from fastmcp import FastMCP

    srv = FastMCP(name="t")
    names = mcp.register_tools(srv, prefix="muvid_")
    # the visualizer tools are present (the footage music_video tools are registered too)
    assert {
        "muvid_list_visuals",
        "muvid_list_projects",
        "muvid_project_status",
        "muvid_render_visualizer",
    }.issubset(names)
    assert len(names) == len(set(names))  # no collisions across genres
    # include/exclude select a subset
    srv2 = FastMCP(name="t2")
    only = mcp.register_tools(srv2, prefix="m_", include=["list_visuals"])
    assert only == ["m_list_visuals"]
    srv3 = FastMCP(name="t3")
    minus = mcp.register_tools(srv3, exclude={"render_visualizer"})
    assert "render_visualizer" not in minus


def test_no_costed_tools():
    assert mcp.COSTED_TOOLS == []
    assert set(mcp.FREE_TOOLS) == set(mcp.TOOL_NAMES)


def test_list_visuals_lists_the_six_looks():
    out = tools.list_visuals()
    names = [v["name"] for v in out["visuals"]]
    assert names == ["still", "cqt", "bars", "spectrum", "waves", "scope"]
    assert out["default"] == "auto"
    still = next(v for v in out["visuals"] if v["name"] == "still")
    assert still["needs_cover"] is True


# -- identity is fail-closed -------------------------------------------------


def test_current_email_fails_closed_without_context():
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        current_email()  # no token, no use_email override


# -- SSRF guard --------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/x",
        "http://[::1]/x",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ],
)
def test_fetch_rejects_unsafe_targets(bad):
    from muvid.mcp._fetch import FetchError, _validate_target

    with pytest.raises(FetchError):
        _validate_target(bad)


def test_fetch_accepts_a_public_https_target():
    from muvid.mcp._fetch import _validate_target

    _validate_target("https://example.com/song.mp3")  # resolves to a global address


# -- validation (pre-fetch, no network) --------------------------------------


def _bind(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    import nw

    nw.create_genre_project("music-visualizer", "u@x.com", "p", template="cqt")


def test_unknown_visual_is_rejected(tmp_path, monkeypatch):
    from fastmcp.exceptions import ToolError

    _bind(tmp_path, monkeypatch)
    with use_email("u@x.com"), pytest.raises(ToolError):
        tools.render_visualizer("p", audio="https://x/a.mp3", visual="nope")


def test_ken_burns_is_not_exposed(tmp_path, monkeypatch):
    from fastmcp.exceptions import ToolError

    _bind(tmp_path, monkeypatch)
    with use_email("u@x.com"), pytest.raises(ToolError):
        tools.render_visualizer(
            "p", audio="https://x/a.mp3", cover="https://x/c.png", visual="ken_burns"
        )


def test_still_requires_a_cover(tmp_path, monkeypatch):
    from fastmcp.exceptions import ToolError

    _bind(tmp_path, monkeypatch)
    with use_email("u@x.com"), pytest.raises(ToolError):
        tools.render_visualizer("p", audio="https://x/a.mp3", visual="still")


# -- render round-trip (ffmpeg) ----------------------------------------------


@pytest.fixture
def song_and_cover(tmp_path):
    audio, cover = tmp_path / "song.wav", tmp_path / "cover.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2:sample_rate=48000",
            "-ac",
            "2",
            str(audio),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=teal:s=320x320",
            "-frames:v",
            "1",
            str(cover),
        ],
        check=True,
    )
    return audio, cover


@pytest.fixture
def patched_fetch(song_and_cover, monkeypatch):
    audio, cover = song_and_cover

    def fake(url, dest, *, label):
        shutil.copy(audio if label == "audio" else cover, dest)
        return Path(dest)

    monkeypatch.setattr(tools, "_resolve_input", fake)
    return audio, cover


@needs_ffmpeg
def test_reactive_render_without_a_cover_has_no_thumbnail(
    tmp_path, monkeypatch, patched_fetch
):
    _bind(tmp_path, monkeypatch)
    with use_email("u@x.com"):
        out = tools.render_visualizer("p", audio="https://x/a.wav", visual="cqt")
    assert out["visual"] == "cqt"
    assert out["thumbnail"] is None  # no cover -> no thumbnail
    assert Path(out["video"]).exists()
    assert out["ok"] is True


@needs_ffmpeg
def test_still_render_with_cover_produces_a_thumbnail(
    tmp_path, monkeypatch, patched_fetch
):
    _bind(tmp_path, monkeypatch)
    with use_email("u@x.com"):
        out = tools.render_visualizer(
            "p",
            audio="https://x/a.wav",
            cover="https://x/c.png",
            visual="still",
            title="Hello",
        )
    assert out["visual"] == "still"
    assert out["thumbnail"] is not None and Path(out["thumbnail"]).exists()
    # the render is retrievable via project_status
    with use_email("u@x.com"):
        status = tools.project_status("p")
    assert len(status["renders"]) == 1


@needs_ffmpeg
def test_input_duration_cap_rejects_a_long_song(tmp_path, monkeypatch, patched_fetch):
    from fastmcp.exceptions import ToolError

    # Patch the module-level cap directly (auto-restored at teardown) rather than the
    # env, which is only read at import; the fixture song is 2s.
    monkeypatch.setattr(tools, "MAX_DURATION_S", 1)
    _bind(tmp_path, monkeypatch)
    with use_email("u@x.com"), pytest.raises(ToolError, match="render limit"):
        tools.render_visualizer("p", audio="https://x/a.wav", visual="cqt")


# -- ffmpeg timeout ----------------------------------------------------------


@needs_ffmpeg
def test_run_ffmpeg_times_out(monkeypatch):
    from muvid.visualize.ffmpeg import FfmpegError, run_ffmpeg

    monkeypatch.setenv("MUVID_FFMPEG_TIMEOUT_S", "0.001")
    with pytest.raises(FfmpegError, match="timed out"):
        run_ffmpeg(["-f", "lavfi", "-i", "sine=d=5", "-f", "null", "-"])


@needs_ffmpeg
def test_measure_loudness_honors_the_timeout(song_and_cover, monkeypatch):
    # normalize=True routes through measure_loudness — a full decode of caller media. It
    # must be bounded by the same timeout as the mux/encode (find→verify security finding).
    from muvid.visualize.ffmpeg import FfmpegError, measure_loudness

    audio, _ = song_and_cover
    monkeypatch.setenv("MUVID_FFMPEG_TIMEOUT_S", "0.001")
    with pytest.raises(FfmpegError, match="timed out"):
        measure_loudness(audio)


# -- auto-visual resolution --------------------------------------------------


@needs_ffmpeg
def test_auto_resolves_to_cqt_without_cover(tmp_path, monkeypatch, patched_fetch):
    _bind(tmp_path, monkeypatch)
    with use_email("u@x.com"):
        out = tools.render_visualizer("p", audio="https://x/a.wav", visual="auto")
    assert out["visual"] == "cqt"  # the concrete look, not the literal "auto"


@needs_ffmpeg
def test_auto_resolves_to_still_with_cover(tmp_path, monkeypatch, patched_fetch):
    _bind(tmp_path, monkeypatch)
    with use_email("u@x.com"):
        out = tools.render_visualizer(
            "p", audio="https://x/a.wav", cover="https://x/c.png", visual="auto"
        )
    assert out["visual"] == "still"


# -- failure hygiene: no orphaned render dir ---------------------------------


def test_failed_render_leaves_no_orphan_dir(tmp_path, monkeypatch):
    from fastmcp.exceptions import ToolError

    _bind(tmp_path, monkeypatch)

    def boom(url, dest, *, label):
        raise tools._tool_error("fetch boom")  # fails after new_render_dir was created

    monkeypatch.setattr(tools, "_resolve_input", boom)
    from muvid.mcp.workspace import VisualizerWorkspace

    with use_email("u@x.com"):
        with pytest.raises(ToolError):
            tools.render_visualizer("p", audio="https://x/a.wav", visual="cqt")
        proj = VisualizerWorkspace.for_email("u@x.com").open_project("p")
    assert proj.list_renders() == []
    # the render dir was cleaned up entirely — no partial artifact left behind
    assert not proj.renders_dir.exists() or not any(proj.renders_dir.iterdir())


# -- multi-tenant isolation --------------------------------------------------


def test_a_user_cannot_reach_another_users_project(tmp_path, monkeypatch):
    _bind(tmp_path, monkeypatch)  # creates project 'p' under u@x.com
    with use_email("intruder@x.com"):
        with pytest.raises(FileNotFoundError):
            tools.project_status("p")
        with pytest.raises(FileNotFoundError):
            tools.render_visualizer("p", audio="https://x/a.wav", visual="cqt")
        assert tools.list_projects()["projects"] == []


def test_traversal_project_id_is_rejected_via_the_tool(tmp_path, monkeypatch):
    _bind(tmp_path, monkeypatch)
    with use_email("u@x.com"), pytest.raises(ValueError):
        tools.project_status("../intruder@x.com/p")


# -- ordering ----------------------------------------------------------------


def test_list_renders_is_newest_first(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.mcp.workspace import VisualizerWorkspace

    proj = VisualizerWorkspace.for_email("u@x.com").create_project("p")
    base = time.time()
    for rid, dt in [("older", 0), ("newer", 100)]:
        proj.new_render_dir(rid)
        proj.write_render_meta(rid, {"render_id": rid})
        meta = proj.renders_dir / rid / "meta.json"
        os.utime(meta, (base + dt, base + dt))
    assert [r["render_id"] for r in proj.list_renders()] == ["newer", "older"]
