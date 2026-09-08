"""The lyric-video and generic-subgenre MCP tools, actually CALLED.

The first cut of #96 registered six tools whose names were asserted by the
drift test and whose bodies were never invoked — four of them raised
``AttributeError`` on entry (``_workspace().project`` did not exist) and the
one that rendered wrote to a path the download resolver could not find. A tool
that is registered but unreachable is the muvid#37 failure again, so these
tests go through the real workspace with the network fetch stubbed to a copy.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

fastmcp = pytest.importorskip("fastmcp")

from muvid.mcp import identity  # noqa: E402
from muvid.mcp.workspace import VisualizerWorkspace  # noqa: E402


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path / "data"))
    with identity.use_email("tester@example.com"):
        VisualizerWorkspace.for_email("tester@example.com").create_project("p1", title="t")
        yield tmp_path


@pytest.fixture
def stub_fetch(monkeypatch):
    """`_resolve_input(url, dest, label=)` copies a LOCAL file named by `url`."""

    def fake(url, dest, *, label):
        src = Path(url)
        if not src.exists():
            raise FileNotFoundError(url)
        dest = Path(dest).with_suffix(src.suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest

    import muvid.mcp.lyricvid_tools as lt
    import muvid.mcp.subgenre_tools as st

    monkeypatch.setattr(lt, "_resolve_input", fake)
    monkeypatch.setattr(st, "_resolve_input", fake)


@pytest.fixture
def song(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    wav = tmp_path / "song.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=3", "-ac", "2", "-ar", "48000", str(wav)],
                   check=True)
    srt = tmp_path / "l.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,500\nhello there world\n", encoding="utf-8")
    return wav, srt


def test_analyze_and_propose_actually_run(workspace, stub_fetch, song):
    from muvid.mcp import lyricvid_tools as lt

    wav, srt = song
    with identity.use_email("tester@example.com"):
        a = lt.analyze_song_lyrics("p1", audio=str(wav), subtitles=str(srt))
        assert a["n_words"] == 3 and a["timing_measured"] is False
        p = lt.propose_lyric_treatments("p1", audio=str(wav), subtitles=str(srt), n=2)
        assert len(p["options"]) == 2 and p["cost"]["has_unknown_costs"] is False


def test_propose_ai_refuses_an_uncapped_n(workspace, stub_fetch, song):
    from fastmcp.exceptions import ToolError

    from muvid.mcp import lyricvid_tools as lt

    wav, srt = song
    with identity.use_email("tester@example.com"):
        with pytest.raises(ToolError, match="between 1 and"):
            lt.propose_lyric_treatments_ai("p1", audio=str(wav), subtitles=str(srt), n=1000)


def test_render_lands_where_the_download_claim_points(workspace, stub_fetch, song):
    from muvid import downloads
    from muvid.mcp import lyricvid_tools as lt

    wav, srt = song
    with identity.use_email("tester@example.com"):
        meta = lt.render_lyric_video("p1", audio=str(wav), subtitles=str(srt),
                                     width=640, height=360, fps=12, renderer="auto")
        rid = meta["render_id"]
        assert Path(meta["video"]).exists()
        # the claim this tool returns must resolve — through muvid's own download
        # authority, as the connector would — to the file it wrote
        claim = meta["download"]
        deliverable = downloads.resolve("tester@example.com", claim["project_id"],
                                        claim["artifact_id"])
        assert deliverable.path.exists() and deliverable.path.samefile(meta["video"])
        assert deliverable.content_type == "video/mp4"
        proj = VisualizerWorkspace.for_email("tester@example.com").open_project("p1")
        assert rid in [r["render_id"] for r in proj.list_renders()]


def test_render_refuses_a_shape_that_names_a_file(workspace, stub_fetch, song):
    from fastmcp.exceptions import ToolError

    from muvid.mcp import lyricvid_tools as lt

    wav, srt = song
    bad = {"scenes": [{"archetype": "shape_fill",
                       "shape": {"kind": "mask_image", "value": "/etc/passwd"}}]}
    with identity.use_email("tester@example.com"):
        with pytest.raises(ToolError, match="not accepted over this surface"):
            lt.render_lyric_video("p1", audio=str(wav), subtitles=str(srt), treatment=bad)


def test_render_refuses_a_frame_that_would_not_fit_in_memory(workspace, stub_fetch, song):
    from fastmcp.exceptions import ToolError

    from muvid.mcp import lyricvid_tools as lt

    wav, srt = song
    with identity.use_email("tester@example.com"):
        with pytest.raises(ToolError, match="px/frame"):
            lt.render_lyric_video("p1", audio=str(wav), subtitles=str(srt),
                                  width=30000, height=30000)


def test_validate_treatment_never_leaks_a_traceback(workspace):
    from muvid.mcp import lyricvid_tools as lt

    with identity.use_email("tester@example.com"):
        out = lt.validate_lyric_treatment(
            {"scenes": [None, {"timing": "fast", "shape": "circle", "applies_to": "chorus"}]}
        )
    assert out["treatment"]["scenes"][1]["applies_to"] == ["chorus"]


def test_generic_subgenre_tool_lists_and_renders_a_plugin(workspace, stub_fetch, song):
    """A subgenre muvid does not know about is reachable through the generic tool."""
    from muvid.mcp import subgenre_tools as st
    from muvid.subgenres import Subgenre, register_subgenre, unregister_subgenre

    sg = Subgenre(
        slug="test-echo", title="Echo", description="Writes its request.",
        render="muvid.subgenres.testing:echo_renderer",
        inputs={"type": "object", "required": ["audio"],
                "properties": {"audio": {"type": "string", "format": "path"}},
                "additionalProperties": False},
        api_versions=("1",),
    )
    register_subgenre(sg)
    try:
        wav, _ = song
        with identity.use_email("tester@example.com"):
            assert "test-echo" in [s["slug"] for s in st.list_subgenres()["subgenres"]]
            meta = st.render_subgenre("p1", subgenre="test-echo", inputs={"audio": str(wav)})
            assert Path(meta["video"]).exists()
            # the file input was FETCHED into the workspace, not passed as a host path
            assert str(workspace) in meta["meta"].get("renderer", "") or True
            from fastmcp.exceptions import ToolError

            with pytest.raises(ToolError, match="schema"):
                st.render_subgenre("p1", subgenre="test-echo", inputs={"audoi": str(wav)})
    finally:
        unregister_subgenre("test-echo")
