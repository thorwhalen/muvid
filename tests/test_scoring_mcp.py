"""Tests for the scoring MCP tools: registration (light) + the async job flow (gated).

The job flow needs the core tier (ffmpeg + cv2 + librosa) and nw.jobs; it exercises
score_footage → footage_score_status (long-poll) → footage_scores → assemble weighted.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess

import numpy as np
import pytest

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAS_CV2 = importlib.util.find_spec("cv2") is not None
HAS_LIBROSA = importlib.util.find_spec("librosa") is not None
HAS_NW = importlib.util.find_spec("nw") is not None
core = pytest.mark.skipif(
    not (HAS_FFMPEG and HAS_CV2 and HAS_LIBROSA and HAS_NW),
    reason="needs ffmpeg + cv2 + librosa + nw (scoring job)",
)
SR = 44100


def test_register_tools_includes_scoring():
    pytest.importorskip("fastmcp")
    import muvid.mcp as mcp
    from fastmcp import FastMCP

    names = mcp.register_tools(FastMCP(name="t"), prefix="muvid_")
    for t in ("muvid_score_footage", "muvid_footage_score_status", "muvid_footage_scores"):
        assert t in names
    assert len(names) == len(set(names))  # no dup names
    assert mcp.COSTED_TOOLS == []


def test_scoring_tools_reject_missing_prereqs(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    pytest.importorskip("nw")
    from fastmcp.exceptions import ToolError

    import muvid.mcp.scoring_tools as st
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.identity import use_email

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    FootageWorkspace.for_email("u@x.com").create_project("p")
    with use_email("u@x.com"):
        with pytest.raises(ToolError, match="no song"):
            st.score_footage("p")
        with pytest.raises(ToolError, match="no scores yet"):
            st.footage_scores("p")


def _song(tmp_path, dur=6.0):
    from scipy.io import wavfile

    t = np.arange(int(dur * SR)) / SR
    x = np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 440 * t)
    x *= 0.5 + 0.5 * (np.sin(2 * np.pi * 2.0 * t) > 0.6)
    x /= np.max(np.abs(x))
    p = tmp_path / "song.wav"
    wavfile.write(str(p), SR, (x * 32767).astype(np.int16))
    return p, x


def _clip(tmp_path, song, name, *, a, b, pattern="testsrc"):
    from scipy.io import wavfile

    seg = song[int(a * SR) : int(b * SR)]
    awav = tmp_path / f"{name}_a.wav"
    wavfile.write(str(awav), SR, (seg / np.max(np.abs(seg)) * 32767).astype(np.int16))
    out = tmp_path / f"{name}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"{pattern}=size=320x240:rate=25:duration={b - a}", "-i", str(awav),
         "-shortest", "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )
    return out


@core
def test_score_job_flow_and_weighted_assemble(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("MUVID_SCORING_ENABLE_LIPSYNC", "0")

    import muvid.footage.assemble as A
    import muvid.mcp.footage_tools as ft
    import muvid.mcp.scoring_tools as st
    import muvid.visualize as V
    from muvid.footage.align import align_footage
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.identity import use_email

    song_p, song = _song(tmp_path)
    ca = _clip(tmp_path, song, "A", a=0.0, b=6.0)
    cb = _clip(tmp_path, song, "B", a=0.0, b=6.0, pattern="testsrc2")
    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    proj.set_song(str(song_p), ext="wav")
    proj.add_clip("A", str(ca), ext="mp4")
    proj.add_clip("B", str(cb), ext="mp4")
    aligns = align_footage(
        str(proj.song_path()), list(proj.clip_paths().items()), song_duration=proj.song_duration()
    )
    proj.save_alignments(aligns)

    with use_email("u@x.com"):
        started = st.score_footage("p")
        assert "job_id" in started
        status = st.footage_score_status("p", job_id=started["job_id"], wait_s=25)
        assert status["status"] == "succeeded", status
        # summary (no clip_id) is bounded
        summary = st.footage_scores("p")
        assert "motion_beat_bas" in summary["metrics"]
        assert "selection_margin" in summary
        # per-clip detail: values are null-masked (NaN never on the wire)
        detail = st.footage_scores("p", clip_id="A")
        vals = detail["metrics"]["sharpness"]["values"]
        assert all(v is None or isinstance(v, float) for v in vals)

        # weighted assemble reads the persisted scores (stub the ffmpeg render).
        monkeypatch.setattr(
            A, "assemble_music_video",
            lambda cuts, s, out, canvas: (__import__("pathlib").Path(out).write_bytes(b"v"),
                                          __import__("pathlib").Path(out))[1],
        )
        monkeypatch.setattr(V, "verify_video", lambda *a, **k: [])
        monkeypatch.setattr(V, "failures", lambda c: [])
        monkeypatch.setattr(V, "report", lambda c: "ok")
        out = ft.assemble_music_video("p", strategy="weighted", preset="energetic")
        assert out["strategy"] == "weighted" and out["ok"] is True


@core
def test_weighted_assemble_without_scores_is_a_clean_error(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from fastmcp.exceptions import ToolError

    import muvid.mcp.footage_tools as ft
    from muvid.footage.align import align_footage
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.identity import use_email

    song_p, song = _song(tmp_path)
    ca = _clip(tmp_path, song, "A", a=0.0, b=6.0)
    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    proj.set_song(str(song_p), ext="wav")
    proj.add_clip("A", str(ca), ext="mp4")
    proj.save_alignments(
        align_footage(str(proj.song_path()), list(proj.clip_paths().items()),
                      song_duration=proj.song_duration())
    )
    with use_email("u@x.com"), pytest.raises(ToolError, match="scoring first|valid edit"):
        ft.assemble_music_video("p", strategy="weighted")
