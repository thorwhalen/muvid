"""muvid.downloads — the resolver half of render retrieval (muvid#24 B2).

The contract under test is the seam agreed with the connector redesign: resolve() is the
ONLY authority mapping (email, project_id, artifact_id) → file, it refuses everything
that is not the caller's artifact with KeyError (no existence leaks, no traversal), and
tools hand out claims — never paths a remote caller cannot read anyway.
"""

from __future__ import annotations

import pytest

nw = pytest.importorskip("nw")

from muvid.downloads import GENRE, ResolvedArtifact, claim, resolve  # noqa: E402
from muvid.footage.edl import FootageAlignment  # noqa: E402


def _project_with_render(tmp_path, monkeypatch, *, email="u@x.com", render="r1" * 6):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email(email).create_project("p")
    rdir = proj.new_render_dir(render)
    (rdir / "final.mp4").write_bytes(b"rendered-bytes")
    return proj, render


def test_resolve_returns_the_callers_render(tmp_path, monkeypatch):
    _, render = _project_with_render(tmp_path, monkeypatch)
    got = resolve("u@x.com", "p", render)
    assert isinstance(got, ResolvedArtifact)
    assert got.path.read_bytes() == b"rendered-bytes"
    assert got.content_type == "video/mp4"
    assert got.filename == f"p-{render}.mp4"


def test_resolve_refuses_another_callers_render(tmp_path, monkeypatch):
    _, render = _project_with_render(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        resolve("intruder@x.com", "p", render)


def test_resolve_refuses_traversal_shaped_ids(tmp_path, monkeypatch):
    _project_with_render(tmp_path, monkeypatch)
    for bad in ("../../song/song.wav", "a/b", "x" * 65, "", "R!"):
        with pytest.raises(KeyError):
            resolve("u@x.com", "p", bad)
        with pytest.raises(KeyError):
            resolve("u@x.com", bad, "r1r1r1r1r1r1")


def test_resolve_refuses_a_missing_render(tmp_path, monkeypatch):
    _project_with_render(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        resolve("u@x.com", "p", "deadbeef0000")


def test_claim_is_the_connectors_registry_shape():
    assert claim("p", "abc123") == {
        "genre": GENRE,
        "project_id": "p",
        "artifact_id": "abc123",
    }


def test_assemble_meta_carries_the_download_claim(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    from pathlib import Path

    import muvid.footage.assemble as A
    import muvid.mcp.footage_tools as ft
    import muvid.visualize as V
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.identity import use_email

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
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
    monkeypatch.setattr(
        A,
        "assemble_music_video",
        lambda cuts, song, out, canvas: (Path(out).write_bytes(b"v"), Path(out))[1],
    )
    monkeypatch.setattr(V, "verify_video", lambda *a, **k: [])
    monkeypatch.setattr(V, "failures", lambda c: [])
    monkeypatch.setattr(V, "report", lambda c: "ok")

    with use_email("u@x.com"):
        meta = ft.assemble_music_video("p", strategy="best_confidence")
        # The claim in the meta is resolvable back to the artifact — end to end.
        c = meta["download"]
        assert c == claim("p", meta["render_id"])
        got = resolve("u@x.com", c["project_id"], c["artifact_id"])
    assert got.path.read_bytes() == b"v"
