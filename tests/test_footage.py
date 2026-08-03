"""Tests for the footage-aligned music_video genre (thorwhalen/reelee#229).

Pure tests (strategy/EDL/genre/import-safety) always run; the align + assemble tests need
ffmpeg AND mixing's set-aligner (mixing>=0.0.28) and skip otherwise.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

nw = pytest.importorskip("nw")

from muvid.footage.edl import (  # noqa: E402
    AssemblyCut,
    EdlEntry,
    FootageAlignment,
    derive_cuts,
    validate_edl,
)
from muvid.footage import strategy as S  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
try:
    import mixing.audio as _ma

    HAS_ALIGNER = hasattr(_ma, "align_clips_to_reference")
except Exception:
    HAS_ALIGNER = False
needs_pipeline = pytest.mark.skipif(
    not (HAS_FFMPEG and HAS_ALIGNER),
    reason="needs ffmpeg + mixing>=0.0.28 (align_clips_to_reference)",
)
SR = 44100

# 3 overlapping alignments of a 30s song, for the pure strategy/EDL tests.
_ALIGNS = [
    FootageAlignment("A", 0.0, 0.90, 20.0, (0.0, 20.0)),
    FootageAlignment("B", 8.0, 0.70, 22.0, (8.0, 30.0)),
    FootageAlignment("C", 12.0, 0.95, 6.0, (12.0, 18.0)),
]


# -- selection strategies + registry ----------------------------------------


@pytest.mark.parametrize("name", ["best_confidence", "longest_take", "fewest_cuts"])
def test_builtin_strategies_yield_a_valid_edl(name):
    edl = S.select_edl(name, _ALIGNS, 30.0)
    entries = validate_edl(edl, _ALIGNS, 30.0)  # must not raise
    assert entries[0].song_start == pytest.approx(0.0)
    assert entries[-1].song_end == pytest.approx(30.0)
    # in-order, contiguous, non-overlapping (validate_edl enforces; re-assert shape)
    for a, b in zip(entries, entries[1:]):
        assert a.song_end == pytest.approx(b.song_start)


def test_fewest_cuts_makes_the_fewest_switches():
    assert len(S.select_edl("fewest_cuts", _ALIGNS, 30.0)) <= len(
        S.select_edl("best_confidence", _ALIGNS, 30.0)
    )


def test_strategy_registry_register_resolve_list():
    assert set(S.list_strategies()) >= {
        "best_confidence",
        "longest_take",
        "fewest_cuts",
    }

    def mine(aligns, song_duration):
        return [EdlEntry(a.coverage[0], a.coverage[1], a.clip_id) for a in aligns[:1]]

    S.register_selection_strategy("_test_only", mine)
    assert S.resolve_strategy("_test_only") is mine
    assert S.resolve_strategy(mine) is mine  # a bare callable resolves to itself
    with pytest.raises(KeyError):
        S.resolve_strategy("nope")


# -- validate_edl SSOT -------------------------------------------------------


def test_validate_rejects_gap_overlap_and_out_of_coverage():
    with pytest.raises(ValueError, match="gap"):
        validate_edl([EdlEntry(0, 5, "A"), EdlEntry(10, 15, "B")], _ALIGNS, 30.0)
    with pytest.raises(ValueError, match="overlap"):
        validate_edl([EdlEntry(0, 12, "A"), EdlEntry(8, 20, "B")], _ALIGNS, 30.0)
    with pytest.raises(ValueError, match="does not contain"):
        validate_edl([EdlEntry(0, 30, "C")], _ALIGNS, 30.0)  # C only covers 12-18
    with pytest.raises(ValueError, match="unknown clip"):
        validate_edl([EdlEntry(0, 20, "Z")], _ALIGNS, 30.0)
    with pytest.raises(ValueError, match="empty"):
        validate_edl([], _ALIGNS, 30.0)


def test_derive_cuts_centralizes_clip_in():
    cuts = derive_cuts([EdlEntry(12, 18, "B")], _ALIGNS, {"B": "/tmp/b.mp4"})
    assert isinstance(cuts[0], AssemblyCut)
    assert cuts[0].clip_in == pytest.approx(4.0)  # song_start(12) - offset(8)
    assert cuts[0].duration == pytest.approx(6.0)


# -- genre + import safety ---------------------------------------------------


def test_music_video_genre_registered_and_ready():
    import muvid.genre  # registers both genres

    g = nw.get_genre("music_video")
    assert g.status == "available"
    assert g.is_ready() is True
    assert g.list_templates() == ["landscape", "portrait", "square"]
    assert nw.has_genre_project_factory("music_video")


def test_import_genre_is_light():
    # In a CLEAN interpreter (popping numpy/scipy from a live session corrupts them),
    # importing the genre must not pull moviepy/fastmcp/cv2/numpy.
    import sys

    code = (
        "import sys, muvid.genre_music_video as g; import nw; "
        "assert 'music_video' in nw.list_genres(); "
        "heavy=[m for m in ('moviepy','fastmcp','cv2','numpy') if m in sys.modules]; "
        "print(heavy); assert not heavy, heavy"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"import not light / failed:\n{r.stdout}\n{r.stderr}"


# -- workspace ---------------------------------------------------------------


def test_workspace_is_stateful_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email("a@x.com").create_project("p", canvas="portrait")
    assert proj.canvas() == (1080, 1920)
    proj.save_alignments([FootageAlignment("A", 1.0, 0.9, 5.0, (1.0, 6.0))])
    assert proj.load_alignments()[0].clip_id == "A"
    # another user can't open it
    with pytest.raises(FileNotFoundError):
        FootageWorkspace.for_email("b@x.com").open_project("p")


# -- end-to-end align + assemble (ffmpeg + aligner) --------------------------


def _song(tmp_path) -> tuple[Path, np.ndarray]:
    from scipy.io import wavfile

    t = np.arange(int(20 * SR)) / SR
    x = np.zeros_like(t)
    for f0, f1 in [(180, 520), (440, 130), (700, 900), (110, 250)]:
        x += np.sin(2 * np.pi * (f0 + (f1 - f0) * (t / t[-1])) * t)
    x *= 0.6 + 0.4 * np.sin(2 * np.pi * 1.7 * t)
    x /= np.max(np.abs(x))
    p = tmp_path / "song.wav"
    wavfile.write(str(p), SR, (x * 32767).astype(np.int16))
    return p, x


def _clip(tmp_path, song, name, *, a, b, size="1280x720"):
    """A 'phone recording' of song[a:b]: the noisy segment muxed into a testsrc video."""
    from scipy.io import wavfile

    rng = np.random.default_rng(abs(hash(name)) % (2**32))
    seg = song[int(a * SR) : int(b * SR)]
    seg = seg + rng.normal(0, 0.1, len(seg))
    seg = seg / np.max(np.abs(seg))
    awav = tmp_path / f"{name}_a.wav"
    wavfile.write(str(awav), SR, (seg * 32767).astype(np.int16))
    out = tmp_path / f"{name}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate=30:duration={b - a}",
            "-i",
            str(awav),
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    return out


@needs_pipeline
def test_align_recovers_offsets_with_high_confidence(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.align import align_footage
    from muvid.footage.workspace import FootageWorkspace

    song_p, song = _song(tmp_path)
    ca = _clip(tmp_path, song, "A", a=1.0, b=11.0)
    cb = _clip(tmp_path, song, "B", a=8.0, b=19.0)
    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    proj.set_song(str(song_p), ext="wav")
    proj.add_clip("A", str(ca), ext="mp4")
    proj.add_clip("B", str(cb), ext="mp4")
    aligns = align_footage(
        str(proj.song_path()),
        list(proj.clip_paths().items()),
        song_duration=proj.song_duration(),
    )
    by = {a.clip_id: a for a in aligns}
    assert by["A"].offset_s == pytest.approx(1.0, abs=0.1)
    assert by["B"].offset_s == pytest.approx(8.0, abs=0.1)
    assert by["A"].confidence > 0.5 and by["B"].confidence > 0.5


@needs_pipeline
def test_full_assemble_from_a_strategy(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.align import align_footage
    from muvid.footage.assemble import assemble_music_video
    from muvid.footage.edl import derive_cuts, validate_edl
    from muvid.footage.strategy import select_edl
    from muvid.footage.workspace import FootageWorkspace

    song_p, song = _song(tmp_path)
    ca = _clip(tmp_path, song, "A", a=1.0, b=11.0)
    cb = _clip(tmp_path, song, "B", a=8.0, b=19.0, size="1080x1920")  # portrait
    proj = FootageWorkspace.for_email("u@x.com").create_project("p", canvas="landscape")
    proj.set_song(str(song_p), ext="wav")
    proj.add_clip("A", str(ca), ext="mp4")
    proj.add_clip("B", str(cb), ext="mp4")
    aligns = align_footage(
        str(proj.song_path()),
        list(proj.clip_paths().items()),
        song_duration=proj.song_duration(),
    )
    edl = validate_edl(
        select_edl("best_confidence", aligns, proj.song_duration()),
        aligns,
        proj.song_duration(),
    )
    cuts = derive_cuts(edl, aligns, proj.clip_paths())
    out = assemble_music_video(
        cuts,
        str(proj.song_path()),
        str(proj.new_render_dir("r") / "final.mp4"),
        canvas=(1280, 720),
    )
    assert out.exists()
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=width,height",
            "-of",
            "json",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    import json

    d = json.loads(r.stdout)
    assert float(d["format"]["duration"]) == pytest.approx(
        edl[-1].song_end - edl[0].song_start, abs=0.6
    )
    vs = [s for s in d["streams"] if "width" in s][0]
    assert (vs["width"], vs["height"]) == (
        1280,
        720,
    )  # fixed canvas, not the portrait clip


# -- tool surface ------------------------------------------------------------


def test_register_tools_includes_footage(tmp_path):
    pytest.importorskip("fastmcp")
    import muvid.mcp as mcp
    from fastmcp import FastMCP

    names = mcp.register_tools(FastMCP(name="t"), prefix="muvid_")
    assert "muvid_assemble_music_video" in names
    assert "muvid_align_footage" in names
    assert len(names) == len(set(names))
    assert mcp.COSTED_TOOLS == []


def test_music_video_factory_round_trip(tmp_path, monkeypatch):
    import muvid.genre_music_video  # noqa: F401 — register the genre + factory

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    info = nw.create_genre_project(
        "music_video", "user@example.com", "proj-A", template="portrait"
    )
    assert info["project_id"] == "proj-A"
    assert info["canvas"] == "portrait"
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email("user@example.com").open_project("proj-A")
    assert proj.canvas() == (1080, 1920)


def test_validate_edl_range_and_positive_span():
    with pytest.raises(ValueError, match="non-positive"):
        validate_edl([EdlEntry(5, 5, "A")], _ALIGNS, 30.0)
    with pytest.raises(ValueError, match="outside"):
        validate_edl([EdlEntry(0, 40, "B")], _ALIGNS, 30.0)  # past song end (30)


def test_validate_edl_caps_entry_count(monkeypatch):
    import muvid.footage.edl as E

    monkeypatch.setattr(E, "MAX_EDL_ENTRIES", 3)
    many = [
        EdlEntry(i, i + 1, "A") for i in range(5)
    ]  # A covers 0-20, so 5 contiguous ok
    with pytest.raises(ValueError, match="cut limit"):
        E.validate_edl(many, _ALIGNS, 30.0)


def test_set_song_clears_stale_alignments(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    proj.save_alignments([FootageAlignment("A", 1.0, 0.9, 5.0, (1.0, 6.0))])
    assert proj.load_alignments()  # present
    song = tmp_path / "s.wav"
    song.write_bytes(b"RIFF")  # a stand-in file
    monkeypatch.setattr("muvid.footage.workspace._probe_duration", lambda p: 30.0)
    proj.set_song(str(song), ext="wav")
    assert proj.load_alignments() == []  # cleared — a new song invalidates offsets


def _fake_state(tmp_path, monkeypatch):
    """Build a project's on-disk state (song + one clip + alignment) WITHOUT ffmpeg."""
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    (proj.root / "song").mkdir()
    (proj.root / "song" / "song.wav").write_bytes(b"x")
    (proj.root / "clips").mkdir()
    (proj.root / "clips" / "A.mp4").write_bytes(b"x")
    m = proj.manifest()
    m.update(
        song="song.wav",
        song_duration=30.0,
        clips=[{"clip_id": "A", "file": "A.mp4", "name": "A"}],
    )
    proj._write_manifest(m)
    proj.save_alignments([FootageAlignment("A", 0.0, 0.9, 30.0, (0.0, 30.0))])
    return proj


def test_assemble_tool_auto_and_validation(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    from fastmcp.exceptions import ToolError

    import muvid.footage.assemble as A
    import muvid.mcp.footage_tools as ft
    import muvid.visualize as V
    from muvid.mcp.identity import use_email

    _fake_state(tmp_path, monkeypatch)
    monkeypatch.setattr(
        A,
        "assemble_music_video",
        lambda cuts, song, out, canvas: (Path(out).write_bytes(b"v"), Path(out))[1],
    )
    monkeypatch.setattr(V, "verify_video", lambda *a, **k: [])
    monkeypatch.setattr(V, "failures", lambda c: [])
    monkeypatch.setattr(V, "report", lambda c: "ok")

    with use_email("u@x.com"):
        out = ft.assemble_music_video("p", strategy="best_confidence")
    assert out["strategy"] == "best_confidence" and out["ok"] is True
    assert Path(out["video"]).exists()
    # an invalid explicit EDL surfaces as a clean ToolError (not a raw ValueError)
    with use_email("u@x.com"), pytest.raises(ToolError, match="valid edit"):
        ft.assemble_music_video(
            "p", edl=[{"song_start": 0, "song_end": 40, "clip_id": "A"}]
        )


def test_footage_tools_reject_another_users_project(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    from fastmcp.exceptions import ToolError

    import muvid.mcp.footage_tools as ft
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.identity import use_email

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    FootageWorkspace.for_email("owner@x.com").create_project("p")
    with use_email("intruder@x.com"):
        with pytest.raises(ToolError, match="no such project"):
            ft.footage_status("p")
        with pytest.raises(ToolError, match="no such project"):
            ft.align_footage("p")


def test_set_song_duration_cap(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    from fastmcp.exceptions import ToolError

    import muvid.mcp.footage_tools as ft
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp import _fetch
    from muvid.mcp.identity import use_email

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    FootageWorkspace.for_email("u@x.com").create_project("p")
    monkeypatch.setattr(ft, "_SONG_MAX_DURATION_S", 1)
    monkeypatch.setattr(
        _fetch,
        "fetch_to_file_streaming",
        lambda url, dest, *, max_bytes: (Path(dest).write_bytes(b"x"), Path(dest))[1],
    )
    monkeypatch.setattr(ft, "_duration", lambda p: 999.0)
    with use_email("u@x.com"), pytest.raises(ToolError, match="limit is exceeded"):
        ft.set_song("p", url="https://example.com/song.mp3")


def test_add_footage_stores_the_clip_without_samefile_error(tmp_path, monkeypatch):
    # Regression: add_footage fetches into a tempdir then add_clip copies into clips/ —
    # previously both used the same path → shutil.SameFileError on every real upload.
    pytest.importorskip("fastmcp")
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    import muvid.mcp.footage_tools as ft
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp import _fetch
    from muvid.mcp.identity import use_email

    monkeypatch.setattr(
        _fetch, "fetch_to_file_streaming",
        lambda url, dest, *, max_bytes: (Path(dest).write_bytes(b"video-bytes"), Path(dest))[1],
    )
    monkeypatch.setattr(ft, "_duration", lambda p: 10.0)
    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    with use_email("u@x.com"):
        out = ft.add_footage("p", url="https://example.com/clip.mp4", name="phone A")
    cid = out["clip_id"]
    stored = proj.clip_paths()
    assert cid in stored and Path(stored[cid]).read_bytes() == b"video-bytes"
    assert Path(stored[cid]).suffix == ".mp4"


def test_add_footage_enforces_the_clip_cap(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    import muvid.mcp.footage_tools as ft
    from fastmcp.exceptions import ToolError
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.identity import use_email

    monkeypatch.setattr(ft, "_MAX_CLIPS", 1)
    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    (tmp_path / "x").write_bytes(b"\x00")  # a source file for the pre-fill copy
    proj.add_clip("existing", str(tmp_path / "x"), ext="mp4")  # pre-fill to the cap
    with use_email("u@x.com"), pytest.raises(ToolError, match="limit"):
        ft.add_footage("p", url="https://example.com/clip.mp4")
