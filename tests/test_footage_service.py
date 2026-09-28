"""Tests for the footage operations (:mod:`muvid.footage.service`) and what hangs off them.

The service is the single source of truth for the ``music_video`` operations: these tests
pin the new ones (declared offsets, named edits and their cut-level changes, rendering a
saved edit), the catalogue's consistency with nw's registry and with the MCP tool lists,
the host-placed ``muvid.Project`` (placement, catalog registration, cover) and the
importer.

The edit tests need no ffmpeg: a song and clips are stored with their durations given,
and offsets are DECLARED, which is exactly the path that needs no measurement. Tests that
decode or encode media are marked ``needs_ffmpeg``. One opt-in real-data smoke test runs
only when ``~/.local/share/muvid/fixtures/que_calor_excerpt/`` exists.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

nw = pytest.importorskip("nw")

from muvid.footage import service  # noqa: E402
from muvid.footage.errors import FootageError  # noqa: E402
from muvid.footage.workspace import FootageWorkspace  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg + ffprobe")
SONG_S = 30.0


# -- fixtures -----------------------------------------------------------------------


def _bytes_file(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


@pytest.fixture
def fp(tmp_path, monkeypatch):
    """A workspace footage project: a 30 s song, clips A (0-20 s) and B (10-30 s),
    both placed by DECLARED offsets — no ffmpeg anywhere."""
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path / "data"))
    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    song = _bytes_file(tmp_path / "song.wav", b"RIFF-song")
    service.set_song(proj, path=str(song), duration_s=SONG_S)
    for cid, payload in (("A", b"clip-a"), ("B", b"clip-b")):
        clip = _bytes_file(tmp_path / f"{cid}.mp4", payload)
        service.add_clip(proj, path=str(clip), clip_id=cid, duration_s=20.0)
    service.set_offset(proj, clip_id="A", offset_s=0.0)
    service.set_offset(proj, clip_id="B", offset_s=10.0)
    return proj


def _edl_ab():
    return [
        {"song_start": 0.0, "song_end": 12.0, "clip_id": "A"},
        {"song_start": 12.0, "song_end": 30.0, "clip_id": "B"},
    ]


# -- declared offsets ---------------------------------------------------------------


def test_set_offset_is_declared_trusted_and_covers_by_duration(fp):
    by = {a.clip_id: a for a in fp.load_alignments()}
    assert by["B"].source == "declared" and by["B"].reliable is True
    assert by["B"].coverage == (10.0, 30.0) and by["B"].support is None
    record = json.loads((fp.root / "alignments.json").read_text())
    assert {r["source"] for r in record} == {"declared"}


def test_alignment_source_defaults_to_measured_for_old_records():
    from muvid.footage.edl import FootageAlignment

    old = {
        "clip_id": "A",
        "offset_s": 1.0,
        "confidence": 0.9,
        "duration_s": 5.0,
        "coverage": [1.0, 6.0],
    }
    assert FootageAlignment.from_dict(old).source == "measured"


def test_set_offset_refuses_unknown_clips(fp):
    with pytest.raises(FootageError, match="unknown clip_id 'Z'.*A, B"):
        service.set_offset(fp, clip_id="Z", offset_s=0.0)


def test_align_keeps_declared_offsets_and_measures_the_rest(fp, tmp_path, monkeypatch):
    import muvid.footage.align as A
    from muvid.footage.edl import FootageAlignment

    measured_ids = []

    def fake_align(song, clips, *, song_duration):
        measured_ids.extend(cid for cid, _ in clips)
        return [
            FootageAlignment(cid, 3.0, 0.9, 20.0, (3.0, 23.0), support=0.9, margin=0.5)
            for cid, _ in clips
        ]

    monkeypatch.setattr(A, "align_footage", fake_align)
    service.add_clip(
        fp,
        path=str(_bytes_file(tmp_path / "C.mp4", b"clip-c")),
        clip_id="C",
        duration_s=20.0,
    )
    out = service.align(fp)
    assert measured_ids == ["C"] and out["kept_declared"] == ["A", "B"]
    sources = {a["clip_id"]: a["source"] for a in out["alignments"]}
    assert sources == {"A": "declared", "B": "declared", "C": "measured"}
    # a declared offset is measured again only once it is forgotten, by name
    with pytest.raises(FootageError, match="nothing to forget"):
        service.clear_offset(fp, clip_id="C")
    cleared = service.clear_offset(fp, clip_id="A")
    assert cleared["cleared"] == "A" and cleared["offset_s"] == 0.0
    out = service.align(fp)
    assert measured_ids == ["C", "A", "C"] and out["kept_declared"] == ["B"]


def test_align_op_cannot_overwrite_declared_offsets():
    import muvid.genre  # noqa: F401

    align = nw.genre_op("music_video", "align")
    assert align.params_schema.get("properties", {}) == {}
    clear = nw.genre_op("music_video", "clear_offset")
    assert clear.effect == "destroy"
    assert clear.title == "Forget where I placed this video"


def test_removing_a_clip_keeps_the_other_clips_declared_offsets(fp):
    out = service.remove_clip(fp, clip_id="A")
    assert out["alignment_invalidated"] is True
    assert [a.clip_id for a in fp.load_alignments()] == ["B"]


# -- named edits --------------------------------------------------------------------


def test_save_get_list_and_delete_an_edit(fp):
    saved = service.save_edit(fp, edl=_edl_ab(), name="Mine", edit_id="e1")
    assert saved["edit_id"] == "e1" and saved["problem"] is None
    assert saved["n_cuts"] == 2 and saved["how_made"] == "by hand"
    assert (fp.root / "edits" / "e1.json").exists()
    assert [e["edit_id"] for e in service.edits(fp)["edits"]] == ["e1"]
    got = service.get_edit(fp, edit_id="e1")
    assert got["edl"][1] == {"song_start": 12.0, "song_end": 30.0, "clip_id": "B"}
    assert got["coverage"]["uncovered"] == []
    with pytest.raises(FootageError, match="already exists"):
        service.save_edit(fp, edl=_edl_ab(), edit_id="e1")
    service.delete_edit(fp, edit_id="e1")
    assert service.edits(fp)["edits"] == []
    with pytest.raises(FootageError, match="unknown edit 'e1'"):
        service.get_edit(fp, edit_id="e1")


def test_an_edit_that_does_not_validate_is_refused_and_not_saved(fp):
    bad = [{"song_start": 0.0, "song_end": 25.0, "clip_id": "A"}]  # A ends at 20 s
    with pytest.raises(FootageError, match="not a valid edit"):
        service.save_edit(fp, edl=bad, edit_id="bad")
    assert not fp.has_edit("bad")


def test_holes_become_explicit_gaps(fp):
    saved = service.save_edit(
        fp, edl=[{"song_start": 2.0, "song_end": 12.0, "clip_id": "A"}], edit_id="g"
    )
    assert [e["clip_id"] for e in saved["edl"]] == [None, "A", None]
    assert saved["edl"][-1]["song_end"] == SONG_S


def test_set_cut_moves_the_neighbour_boundary_and_changes_the_clip(fp):
    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    out = service.set_cut(fp, edit_id="e", index=1, song_start=15.0)
    assert [(e["song_start"], e["song_end"]) for e in out["edl"]] == [
        (0.0, 15.0),
        (15.0, 30.0),
    ]
    out = service.set_cut(fp, edit_id="e", index=0, clip_id="")
    assert out["edl"][0]["clip_id"] is None and out["changed"] == 0
    out = service.set_cut(fp, edit_id="e", index=0, clip_id="A", look="eq=gamma=1.1")
    assert out["edl"][0]["look"] == "eq=gamma=1.1"
    out = service.set_cut(fp, edit_id="e", index=0, look="")
    assert "look" not in out["edl"][0]


def test_set_cut_refusals_leave_the_edit_as_it_was(fp):
    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    before = service.get_edit(fp, edit_id="e")["edl"]
    with pytest.raises(FootageError, match="merge_cut"):
        service.set_cut(fp, edit_id="e", index=1, song_start=0.0)
    with pytest.raises(FootageError, match="not a valid edit"):
        service.set_cut(fp, edit_id="e", index=1, clip_id="A")  # A does not reach 30 s
    with pytest.raises(FootageError, match="out of range"):
        service.set_cut(fp, edit_id="e", index=7, clip_id="A")
    assert service.get_edit(fp, edit_id="e")["edl"] == before


def test_split_and_merge_cuts(fp):
    edl = _edl_ab()
    edl[0]["crop"] = {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}
    edl[0]["crop_end"] = {"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.5}
    service.save_edit(fp, edl=edl, edit_id="e")
    out = service.split_cut(fp, edit_id="e", at_s=6.0)
    first, second = out["edl"][0], out["edl"][1]
    assert (first["song_end"], second["song_start"]) == (6.0, 6.0)
    # the pan is divided where it was at 6 s — half way across 0..12
    assert first["crop_end"]["x"] == pytest.approx(0.25)
    assert second["crop"]["x"] == pytest.approx(0.25)
    assert second["crop_end"]["x"] == pytest.approx(0.5)
    with pytest.raises(FootageError, match="boundary"):
        service.split_cut(fp, edit_id="e", at_s=6.0)
    out = service.merge_cut(fp, edit_id="e", index=1, into="previous")
    assert len(out["edl"]) == 2 and out["edl"][0]["song_end"] == 12.0
    with pytest.raises(FootageError, match="no previous"):
        service.merge_cut(fp, edit_id="e", index=0, into="previous")
    # merging B's span into A is refused: A does not cover 12..30
    with pytest.raises(FootageError, match="not a valid edit"):
        service.merge_cut(fp, edit_id="e", index=1, into="previous")


def test_replace_edit_validates(fp):
    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    out = service.replace_edit(
        fp, edit_id="e", edl=[{"song_start": 0.0, "song_end": 20.0, "clip_id": "A"}]
    )
    assert [e["clip_id"] for e in out["edl"]] == ["A", None]
    with pytest.raises(FootageError):
        service.replace_edit(
            fp, edit_id="e", edl=[{"song_start": 0, "song_end": 29, "clip_id": "A"}]
        )


def test_propose_edit_saves_a_named_edit(fp):
    out = service.propose_edit(fp)
    assert out["strategy"] == "best_confidence" and out["edit_id"]
    rec = service.get_edit(fp, edit_id=out["edit_id"])
    assert rec["name"] == "Edit 1" and rec["how_made"].startswith("cut automatically")
    assert rec["edl"] == out["edl"]
    unsaved = service.propose_edit(fp, save=False)
    assert "edit_id" not in unsaved and len(service.edits(fp)["edits"]) == 1


def test_status_names_the_next_step(fp):
    st = service.status(fp)
    assert st["next_step"]["op"] == "propose_edit"
    assert {a["clip_id"]: a["source"] for a in st["alignments"]} == {
        "A": "declared",
        "B": "declared",
    }
    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    assert service.status(fp)["next_step"]["op"] == "render"


def test_render_refuses_an_unknown_edit(fp):
    with pytest.raises(FootageError, match="unknown edit"):
        service.render(fp, edit_id="nope")


def test_render_renders_the_saved_edit_and_records_it(fp, monkeypatch):
    import muvid.footage.assemble as A
    import muvid.visualize as V

    seen = {}

    def fake_assemble(cuts, song, out, canvas, on_note=None):
        seen["cuts"] = [(c.clip_id, c.song_start, c.song_end) for c in cuts]
        Path(out).write_bytes(b"video")
        return Path(out)

    monkeypatch.setattr(A, "assemble_music_video", fake_assemble)
    monkeypatch.setattr(V, "verify_video", lambda *a, **k: [])
    monkeypatch.setattr(V, "failures", lambda c: [])
    monkeypatch.setattr(V, "report", lambda c: "ok")
    service.save_edit(fp, edl=_edl_ab(), edit_id="e", name="Take one")
    meta = service.render(fp, edit_id="e")
    assert meta["edit_id"] == "e" and meta["label"] == "Take one" and meta["ok"]
    assert seen["cuts"] == [("A", 0.0, 12.0), ("B", 12.0, 30.0)]
    rows = service.renders(fp)["renders"]
    assert rows[0]["edit_id"] == "e" and rows[0]["render_id"] == meta["render_id"]


# -- edit spans: an edit may cover PART of the song -----------------------------------


def test_an_edit_can_cover_part_of_the_song(fp):
    saved = service.save_edit(
        fp,
        edl=[{"song_start": 4.0, "song_end": 12.0, "clip_id": "A"}],
        edit_id="part",
        span=[2.0, 14.0],
    )
    assert saved["span"] == [2.0, 14.0]
    # the edit is stored whole; the span is only the window that renders
    assert [(e["song_start"], e["song_end"], e["clip_id"]) for e in saved["edl"]] == [
        (0.0, 4.0, None),
        (4.0, 12.0, "A"),
        (12.0, SONG_S, None),
    ]
    assert saved["coverage"]["uncovered"] == [
        {"song_start": 2.0, "song_end": 4.0},
        {"song_start": 12.0, "song_end": 14.0},
    ]
    assert json.loads((fp.root / "edits" / "part.json").read_text())["span"] == [2, 14]
    whole = service.save_edit(fp, edl=_edl_ab(), edit_id="whole")
    assert whole["span"] == [0.0, SONG_S]
    assert "span" not in json.loads((fp.root / "edits" / "whole.json").read_text())


def test_cuts_outside_the_span_are_kept(fp):
    saved = service.save_edit(fp, edl=_edl_ab(), edit_id="x", span=[1.0, 20.0])
    assert [e["clip_id"] for e in saved["edl"]] == ["A", "B"]
    with pytest.raises(FootageError, match="not a stretch of the song"):
        service.save_edit(fp, edl=_edl_ab(), edit_id="y", span=[5.0, 99.0])


def test_set_span_is_a_window_that_loses_nothing(fp):
    """The studio's "Start at the player" at 3:00 once wiped every earlier cut, and
    "Whole song" could not bring them back. A span now only limits what renders."""
    edl = _edl_ab()
    edl[1]["crop"] = {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}
    edl[1]["crop_end"] = {"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.5}
    original = service.save_edit(fp, edl=edl, edit_id="e")["edl"]
    out = service.set_span(fp, edit_id="e", start_s=13.0, end_s=21.0)
    assert out["span"] == [13.0, 21.0] and out["edl"] == original
    assert out["coverage"]["uncovered"] == []
    out = service.set_span(fp, edit_id="e", start_s=0.0, end_s=SONG_S)
    assert out["span"] == [0.0, SONG_S] and out["edl"] == original
    assert "span" not in json.loads((fp.root / "edits" / "e.json").read_text())


def test_propose_edit_within_a_span(fp):
    out = service.propose_edit(fp, span=[5.0, 25.0])
    assert out["span"] == [5.0, 25.0]
    assert out["edl"][0]["song_start"] == 0.0 and out["edl"][-1]["song_end"] == SONG_S
    assert out["coverage"]["span"] == [5.0, 25.0]
    assert service.get_edit(fp, edit_id=out["edit_id"])["span"] == [5.0, 25.0]


def test_render_of_a_trimmed_edit_renders_only_its_span(fp, monkeypatch):
    import muvid.footage.assemble as A
    import muvid.visualize as V

    seen = {}

    def fake_assemble(cuts, song, out, canvas, on_note=None, **kw):
        seen.update(kw, span=(cuts[0].song_start, cuts[-1].song_end))
        Path(out).write_bytes(b"video")
        return Path(out)

    def fake_verify(out, **kw):
        seen["verify"] = kw
        return []

    monkeypatch.setattr(A, "assemble_music_video", fake_assemble)
    monkeypatch.setattr(V, "verify_video", fake_verify)
    monkeypatch.setattr(V, "failures", lambda c: [])
    monkeypatch.setattr(V, "report", lambda c: "ok")
    edl = _edl_ab()
    edl[1]["crop"] = {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}
    edl[1]["crop_end"] = {"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.5}
    service.save_edit(fp, edl=edl, edit_id="e")
    service.set_span(fp, edit_id="e", start_s=3.0, end_s=21.0)
    meta = service.render(fp, edit_id="e")
    assert seen["span"] == (3.0, 21.0) and meta["rendered_span"] == [3.0, 21.0]
    # the straddling cut is trimmed in what renders only (its pan re-derived at 21 s)
    assert meta["edl"][-1]["crop_end"]["x"] == pytest.approx(0.25)
    assert service.get_edit(fp, edit_id="e")["edl"][-1]["song_end"] == SONG_S
    assert seen["fade_out_s"] == service.TAIL_FADE_S
    assert seen["verify"]["expected_duration"] == pytest.approx(18.0)
    assert meta["coverage"]["span"] == [3.0, 21.0]


# -- the catalogue: service ↔ nw registry ↔ MCP tools ----------------------------------


def test_catalogue_rows_match_the_registered_nw_ops():
    import muvid.genre_music_video as g

    specs = service.FOOTAGE_OP_SPECS
    ops = nw.genre_ops("music_video")
    assert [o.name for o in ops] == [s.name for s in specs]
    assert ops == g.FOOTAGE_OPS
    for spec, op in zip(specs, ops):
        assert (op.title, op.effect, op.runs) == (spec.title, spec.effect, spec.runs)
        assert op.description == inspect.getdoc(getattr(service, spec.name))
        schema = op.params_schema
        assert schema["additionalProperties"] is False
        assert not set(spec.hide) & set(schema.get("properties", {}))
    catalogue = nw.genre_ops_catalogue("music_video")
    assert json.loads(json.dumps(catalogue)) == catalogue
    assert {
        r["runs"] for r in catalogue if r["name"] in {"align", "score", "render"}
    } == {"job"}
    assert all("free" not in r["title"].lower() for r in catalogue)


def test_mcp_footage_tools_are_derived_from_the_catalogue():
    pytest.importorskip("fastmcp")
    import muvid.mcp as mcp
    import muvid.mcp.footage_tools as ft
    import muvid.mcp.scoring_tools as st
    from muvid.mcp._footage_ops import (
        FOOTAGE_TRANSPORT_TOOLS,
        SCORING_OPS,
        SCORING_TRANSPORT_TOOLS,
        op_tool_name,
    )

    op_names = [s.name for s in service.FOOTAGE_OP_SPECS]
    expected_footage = [op_tool_name(n) for n in op_names if n not in SCORING_OPS]
    expected_scoring = [op_tool_name(n) for n in op_names if n in SCORING_OPS]
    assert mcp.FOOTAGE_TOOLS == expected_footage + FOOTAGE_TRANSPORT_TOOLS
    assert mcp.SCORING_TOOLS == expected_scoring + SCORING_TRANSPORT_TOOLS
    for name in mcp.FOOTAGE_TOOLS:
        assert callable(getattr(ft, name)), name
    for name in mcp.SCORING_TOOLS:
        assert callable(getattr(st, name)), name
    # a generated tool carries its operation's signature, minus the project
    params = inspect.signature(ft.footage_set_cut).parameters
    assert list(params)[:3] == ["project_id", "edit_id", "index"]


def test_a_generated_mcp_tool_runs_the_operation(tmp_path, monkeypatch, fp):
    pytest.importorskip("fastmcp")
    import muvid.mcp.footage_tools as ft
    from fastmcp.exceptions import ToolError
    from muvid.mcp.identity import use_email

    with use_email("u@x.com"):
        out = ft.footage_save_edit("p", edl=_edl_ab(), edit_id="e")
        assert out["project_id"] == "p" and out["edit_id"] == "e"
        assert ft.footage_edits("p")["edits"][0]["edit_id"] == "e"
        with pytest.raises(ToolError, match="unknown edit"):
            ft.footage_get_edit("p", edit_id="zz")


def test_the_mcp_server_builds_with_every_tool():
    fastmcp = pytest.importorskip("fastmcp")
    pytest.importorskip("py2mcp")
    import asyncio

    import muvid.mcp as mcp

    server = fastmcp.FastMCP("t")
    registered = mcp.register_tools(server, prefix="muvid_")
    assert set(registered) == {f"muvid_{n}" for n in mcp.TOOL_NAMES}
    schema = asyncio.run(server.get_tool("muvid_footage_set_cut")).parameters
    assert {"project_id", "edit_id", "index"} <= set(schema["properties"])


# -- the host-placed project ----------------------------------------------------------


def test_placement_is_offered_for_both_genres_and_honoured(tmp_path):
    import muvid
    import muvid.genre  # noqa: F401

    assert nw.can_place_genre_project("music_video")
    assert nw.can_place_genre_project("lyric-video")
    info = nw.create_genre_project(
        "music_video", "u@x.com", "mv", template="portrait", projects_dir=tmp_path
    )
    assert info["canvas"] == "portrait"
    project = muvid.Project(tmp_path / "mv")
    assert isinstance(project, nw.Project) and project.genre == "music_video"
    assert project.footage.canvas() == (1080, 1920)
    assert project.footage.root == project.root / "footage"
    nw.create_genre_project("lyric-video", "u@x.com", "lv", projects_dir=tmp_path)
    assert muvid.Project(tmp_path / "lv").genre == "lyric-video"


def test_unplaced_create_still_uses_the_caller_workspace(tmp_path, monkeypatch):
    import muvid.genre  # noqa: F401

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    nw.create_genre_project("music_video", "u@x.com", "p1")
    assert FootageWorkspace.for_email("u@x.com").open_project("p1")


def test_muvid_project_resolves_the_way_reelee_looks_it_up():
    import importlib

    cls = getattr(importlib.import_module("muvid"), "Project")
    assert isinstance(cls, type) and issubclass(cls, nw.Project)


def test_hosted_media_lands_in_the_host_catalog(tmp_path):
    import muvid
    import muvid.genre  # noqa: F401
    from muvid.catalog import hash_file

    nw.create_genre_project("music_video", "u", "mv", projects_dir=tmp_path)
    project = muvid.Project(tmp_path / "mv")
    fp = project.footage
    song = _bytes_file(tmp_path / "s.wav", b"RIFF-hosted-song")
    out = service.set_song(fp, path=str(song), filename="My Song.wav", duration_s=10.0)
    aid = out["song"]["artifact_id"]
    assert aid == hash_file(song) == fp.song_hash()
    assert out["song"]["name"] == "My Song.wav"
    catalog = project.media_catalog
    assert catalog.has(aid)
    row = json.loads((catalog.rows_dir / f"{aid}.json").read_text())
    assert row["url"] == f"/api/artifacts/{aid}/bytes" and row["kind"] == "audio"
    # the blob is a hardlink of the project's own copy, not a second copy
    assert os.path.samefile(catalog.blobs_dir / aid, fp.song_path())


def test_genre_op_runs_on_the_hosted_project(tmp_path):
    import muvid
    import muvid.genre  # noqa: F401

    nw.create_genre_project("music_video", "u", "mv", projects_dir=tmp_path)
    project = muvid.Project(tmp_path / "mv")
    status = nw.genre_op("music_video", "status")
    assert status.run(project)["next_step"]["op"] == "set_song"
    with pytest.raises(ValueError):  # schema: set_cut needs an integer index
        nw.genre_op("music_video", "set_cut").validate_params(
            {"edit_id": "e", "index": "seven"}
        )
    lyric = nw.genre_op("lyric-video", "status")
    assert lyric.run(project)["editable"] is False


# -- the EDL converter ----------------------------------------------------------------


def test_framing_edl_converts_to_crop_fractions():
    from muvid.importing._edl import edl_from_document, is_framing_edl

    doc = {
        "edl": [
            {
                "song_start": 0.162,
                "song_end": 7.7,
                "clip_id": "c03",
                "clip_in": 8.6,
                "framing": {"w": 1024, "h": 576, "x0": 0, "y0": 0, "x1": 0, "y1": 0},
            },
            {
                "song_start": 7.7,
                "song_end": 9.0,
                "clip_id": "c02",
                "framing": {
                    "w": 660,
                    "h": 370,
                    "x0": 136.3,
                    "y0": 48.6,
                    "x1": 51.7,
                    "y1": 48.6,
                },
            },
            {
                "song_start": 9.0,
                "song_end": 10.0,
                "clip_id": "c01",
                "framing": {
                    "w": 478,
                    "h": 269,
                    "x0": 0,
                    "y0": 100,
                    "x1": 0,
                    "y1": 100.2,
                },
            },
        ]
    }
    assert is_framing_edl(doc) and not is_framing_edl([{"song_start": 0}])
    sizes = {"c01": (478, 850), "c02": (848, 478), "c03": (1024, 576)}
    edl = edl_from_document(doc, source_sizes=sizes)
    assert edl[0] == {"song_start": 0.162, "song_end": 7.7, "clip_id": "c03"}
    assert edl[1]["crop"]["x"] == pytest.approx(136.3 / 848)
    assert edl[1]["crop_end"]["x"] == pytest.approx(51.7 / 848)
    assert edl[2]["crop"]["y"] == pytest.approx(100 / 850) and "crop_end" not in edl[2]
    with pytest.raises(ValueError, match="no frame size"):
        edl_from_document(doc, source_sizes={})
    muvid_shaped = [{"song_start": 0, "song_end": 1, "clip_id": "a", "look": "hflip"}]
    assert edl_from_document(muvid_shaped, source_sizes={})[0]["look"] == "hflip"


# -- media: importer, cover, catalog (ffmpeg) -----------------------------------------


def _tone(path: Path, *, seconds: float) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            str(path),
        ],
        check=True,
    )
    return path


def _video(path: Path, *, seconds: float, size: str = "320x240") -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate=25:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=330:duration={seconds}",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


@needs_ffmpeg
def test_importer_is_idempotent_and_fills_every_part(tmp_path):
    import muvid
    from muvid.importing import import_production

    src = tmp_path / "src"
    src.mkdir()
    _tone(src / "song.wav", seconds=6.0)
    _video(src / "a.mp4", seconds=6.0)
    _video(src / "b.mp4", seconds=4.0, size="240x320")
    _video(src / "final.mp4", seconds=6.0)
    (src / "edl.json").write_text(
        json.dumps(
            {
                "edl": [
                    {
                        "song_start": 0.0,
                        "song_end": 3.0,
                        "clip_id": "a",
                        "framing": {
                            "w": 160,
                            "h": 120,
                            "x0": 0,
                            "y0": 0,
                            "x1": 160,
                            "y1": 0,
                        },
                    },
                    {
                        "song_start": 3.0,
                        "song_end": 5.0,
                        "clip_id": "b",
                        "framing": {
                            "w": 240,
                            "h": 320,
                            "x0": 0,
                            "y0": 0,
                            "x1": 0,
                            "y1": 0,
                        },
                    },
                ]
            }
        )
    )
    manifest = {
        "kind": "footage",
        "id": "demo",
        "title": "Demo",
        "canvas": "landscape",
        "song": {"path": "song.wav"},
        "clips": [
            {"id": "a", "path": "a.mp4", "name": "Cam A", "offset_s": 0.0},
            {"id": "b", "path": "b.mp4", "offset_s": 1.5},
        ],
        "edits": [
            {
                "id": "v1",
                "name": "V1",
                "edl_path": "edl.json",
                "how_made": "planned by hand",
                "span": [0.0, 5.0],
            }
        ],
        "renders": [
            {"id": "final", "path": "final.mp4", "edit_id": "v1", "label": "Final"}
        ],
    }
    projects = tmp_path / "projects"
    dry = import_production(manifest, projects, base_dir=src, dry_run=True)
    assert dry["edits"] == {"v1": "2 entries"} and not projects.exists()
    first = import_production(manifest, projects, base_dir=src)
    assert first["created"] and first["song"] == "set"
    assert first["clips"] == {"a": "added", "b": "added"}
    assert first["offsets"] == {"a": "declared", "b": "declared"}
    assert first["edits"] == {"v1": "saved"} and first["cover_artifact_id"]
    again = import_production(manifest, projects, base_dir=src)
    assert again["song"] == "unchanged" and set(again["clips"].values()) == {
        "unchanged"
    }
    assert set(again["offsets"].values()) == {"unchanged"}
    assert again["edits"] == {"v1": "unchanged"} and not again["created"]

    project = muvid.Project(projects / "demo")
    fp = project.footage
    edit = service.get_edit(fp, edit_id="v1")
    assert edit["problem"] is None and edit["edl"][0]["crop_end"]["x"] == 0.5
    assert edit["span"] == [0.0, 5.0] and edit["coverage"]["span"] == [0.0, 5.0]
    render = service.renders(fp)["renders"][0]
    assert render["edit_id"] == "v1" and render["label"] == "Final"
    assert render["rendered_span"] == [0.0, 5.0]
    # the real encoder: a trimmed edit renders only its span, song cut and faded
    meta = service.render(fp, edit_id="v1")
    from muvid.visualize.ffmpeg import media_duration

    assert meta["ok"], meta["checks"]
    assert media_duration(fp.root / meta["video"]) == pytest.approx(5.0, abs=0.15)
    catalog = project.media_catalog
    status = service.status(fp)
    ids = [
        status["song"]["artifact_id"],
        render["artifact_id"],
        status["cover_artifact_id"],
        *(c["artifact_id"] for c in status["clips"]),
    ]
    assert all(catalog.has(i) for i in ids)
    assert project.cover_artifact_id() == status["cover_artifact_id"]
    assert len(fp.list_renders()) == 2  # the import's one (not doubled) + ours


@needs_ffmpeg
def test_importer_lyric_video(tmp_path):
    import muvid
    from muvid.importing import import_production

    src = tmp_path / "src"
    src.mkdir()
    _tone(src / "song.wav", seconds=3.0)
    _video(src / "published.mp4", seconds=3.0)
    (src / "lyrics.md").write_text("il pleut\n")
    (src / "treatment.json").write_text('{"archetype": "calligram"}')
    manifest = {
        "kind": "lyric-video",
        "id": "lv",
        "title": "Rain",
        "template": "calligram",
        "song": {"path": "song.wav"},
        "sources": [
            {"role": "lyrics", "path": "lyrics.md"},
            {"role": "treatment", "path": "treatment.json"},
        ],
        "renders": [{"id": "v3", "path": "published.mp4", "label": "Readable"}],
    }
    import_production(manifest, tmp_path / "projects", base_dir=src)
    import_production(manifest, tmp_path / "projects", base_dir=src)
    project = muvid.Project(tmp_path / "projects" / "lv")
    assert project.genre == "lyric-video"
    assert (project.resolved_genre() or {}).get("template") == "calligram"
    status = nw.genre_op("lyric-video", "status").run(project)
    assert [r["render_id"] for r in status["renders"]] == ["v3"]
    lyrics = next(s for s in status["sources"] if s["role"] == "lyrics")
    assert lyrics["text"] == "il pleut\n"
    assert project.media_catalog.has(status["renders"][0]["artifact_id"])
    assert project.cover_artifact_id()


def test_importer_refuses_missing_files(tmp_path):
    from muvid.importing import ImportRefused, import_production

    manifest = {"kind": "footage", "id": "x", "song": {"path": "nope.wav"}}
    with pytest.raises(ImportRefused, match="missing files"):
        import_production(manifest, tmp_path, base_dir=tmp_path)


def test_importer_cli_dry_run(tmp_path, capsys):
    from muvid.importing.__main__ import main

    (tmp_path / "m.json").write_text(json.dumps({"kind": "lyric-video", "id": "z"}))
    assert main([str(tmp_path / "m.json"), str(tmp_path / "p"), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


# -- the opt-in real-data smoke test ---------------------------------------------------

_EXCERPT = Path.home() / ".local" / "share" / "muvid" / "fixtures" / "que_calor_excerpt"


@pytest.mark.skipif(
    not (_EXCERPT / "expected.json").exists(),
    reason=f"opt-in: needs the private excerpt at {_EXCERPT}",
)
def test_que_calor_excerpt_end_to_end(tmp_path):
    """The v1 acceptance path, on 30 s of a real song and its three phone clips."""
    import muvid
    import muvid.genre  # noqa: F401

    expected = json.loads((_EXCERPT / "expected.json").read_text())
    expected_offsets = expected["offsets_s"]
    nw.create_genre_project("music_video", "u", "qc", projects_dir=tmp_path)
    fp = muvid.Project(tmp_path / "qc").footage
    service.set_song(fp, path=str(_EXCERPT / "song.m4a"), filename="song.m4a")
    for cid in ("c01", "c02", "c03"):
        service.add_clip(fp, path=str(_EXCERPT / f"{cid}.mp4"), clip_id=cid)
    aligned = {a["clip_id"]: a for a in service.align(fp)["alignments"]}
    for cid, want in expected_offsets.items():
        assert aligned[cid]["offset_s"] == pytest.approx(want, abs=0.1), cid
    edit = service.propose_edit(fp, name="smoke")
    footage_cuts = [i for i, e in enumerate(edit["edl"]) if e["clip_id"]]
    i = footage_cuts[0]
    cut = edit["edl"][i]
    others = [
        cid
        for cid, a in aligned.items()
        if cid != cut["clip_id"]
        and a["coverage"][0] <= cut["song_start"]
        and cut["song_end"] <= a["coverage"][1]
    ]
    if others:
        changed = service.set_cut(
            fp, edit_id=edit["edit_id"], index=i, clip_id=others[0]
        )
        assert changed["edl"][i]["clip_id"] == others[0]
    meta = service.render(fp, edit_id=edit["edit_id"])
    from muvid.visualize.ffmpeg import media_duration

    assert meta["ok"] and meta["artifact_id"]
    assert media_duration(fp.root / meta["video"]) == pytest.approx(
        fp.song_duration(), abs=0.5
    )


# -- review fixes: blobs, ids, host params, tool names, spans, lazy footage -------------


def _blobs_intact(catalog) -> list:
    """The blobs whose bytes no longer hash to their name (should be empty)."""
    from muvid.catalog import hash_file

    return [p.name for p in catalog.blobs_dir.iterdir() if hash_file(p) != p.name]


@needs_ffmpeg
def test_catalog_blobs_keep_their_bytes_through_reimport_and_cover_refresh(tmp_path):
    """Every write of a project media file is a NEW inode, so a hardlinked blob never
    changes under its id — across import, re-import with changed bytes, and a cover
    refresh (the review reproduced 3 of 7 blobs corrupted before this)."""
    import muvid
    from muvid.importing import import_production

    src = tmp_path / "src"
    src.mkdir()
    _tone(src / "song.wav", seconds=4.0)
    _video(src / "a.mp4", seconds=4.0)
    _video(src / "final.mp4", seconds=4.0)
    (src / "poem.txt").write_text("one")
    (src / "t.json").write_text('{"v": 1}')
    footage = {
        "kind": "footage",
        "id": "mv",
        "title": "MV",
        "song": {"path": "song.wav"},
        "clips": [{"id": "a", "path": "a.mp4", "offset_s": 0.0}],
        "renders": [{"id": "final", "path": "final.mp4"}],
    }
    lyric = {
        "kind": "lyric-video",
        "id": "lv",
        "title": "LV",
        "song": {"path": "song.wav"},
        "sources": [
            {"role": "poem", "path": "poem.txt"},
            {"role": "timings", "path": "t.json"},
        ],
        "renders": [{"id": "final", "path": "final.mp4"}],
    }
    projects = tmp_path / "projects"
    for m in (footage, lyric):
        import_production(m, projects, base_dir=src)
    # change the bytes behind every id and import again
    _video(src / "final.mp4", seconds=3.0, size="160x120")
    _video(src / "a.mp4", seconds=3.0, size="160x120")
    (src / "t.json").write_text('{"v": 2}')
    for m in (footage, lyric):
        import_production(m, projects, base_dir=src)
    mv, lv = muvid.Project(projects / "mv"), muvid.Project(projects / "lv")
    service.refresh_cover(mv.footage)
    lv.lyric.refresh_cover()
    for project in (mv, lv):
        catalog = project.media_catalog
        assert len(list(catalog.blobs_dir.iterdir())) >= 4
        assert _blobs_intact(catalog) == []


@pytest.mark.parametrize("bad", ["*", "a/b", "..", "a.x", " ", "x" * 65])
def test_clip_ids_are_names_not_patterns(fp, tmp_path, bad):
    with pytest.raises(FootageError, match="invalid clip_id"):
        service.add_clip(
            fp,
            path=str(_bytes_file(tmp_path / "z.mp4", b"z")),
            clip_id=bad,
            duration_s=5.0,
        )
    assert {c["clip_id"] for c in fp.list_clips()} == {"A", "B"}
    assert all(Path(p).exists() for p in fp.clip_paths().values())


def test_clip_ids_are_unique_after_normalising(fp, tmp_path):
    with pytest.raises(FootageError, match="already in the project"):
        service.add_clip(
            fp,
            path=str(_bytes_file(tmp_path / "z.mp4", b"z")),
            clip_id=" A ",
            duration_s=5.0,
        )
    assert (fp.root / "clips" / "A.mp4").read_bytes() == b"clip-a"


def test_removing_a_clip_touches_only_its_own_file(fp):
    stray = fp.root / "clips" / "A.x.mp4"
    stray.write_bytes(b"not A's")
    service.remove_clip(fp, clip_id="A")
    assert stray.exists() and not (fp.root / "clips" / "A.mp4").exists()


def test_upload_ops_take_the_file_from_the_host_only(tmp_path):
    import muvid
    import muvid.genre  # noqa: F401
    from pydantic import ValidationError

    for name in ("set_song", "add_clip"):
        op = nw.genre_op("music_video", name)
        assert op.host_params == ("path", "filename")
        assert not {"path", "filename", "duration_s", "ext"} & set(
            op.params_schema["properties"]
        )
        assert op.to_dict()["host_params"] == ["path", "filename"]
    muvid.create_project_at(tmp_path, "mv")
    project = muvid.Project(tmp_path / "mv")
    set_song = nw.genre_op("music_video", "set_song")
    with pytest.raises(ValidationError):  # a client may not name a server file
        set_song.run(project, {"path": "/etc/hosts"})
    song = _bytes_file(tmp_path / "up.tmp", b"RIFF-up")
    import muvid.footage.service as S

    original = S._probe_duration
    S._probe_duration = lambda p: 12.0
    try:
        out = set_song.run(
            project, {}, host={"path": str(song), "filename": "Tune.wav"}
        )
    finally:
        S._probe_duration = original
    assert out["song"]["name"] == "Tune.wav" and out["song_duration"] == 12.0


def test_create_project_at_records_the_genre_the_way_reelee_reads_it(tmp_path):
    import muvid

    reelee_project = pytest.importorskip("reelee.project")
    muvid.create_project_at(tmp_path, "mv", title="MV", template="portrait")
    muvid.create_project_at(tmp_path, "lv", genre="lyric-video")
    mv = reelee_project.open_project(tmp_path / "mv")
    lv = reelee_project.open_project(tmp_path / "lv")
    assert type(mv) is muvid.Project and type(lv) is muvid.Project
    assert mv.resolved_genre()["genre"] == "music_video"
    assert mv.resolved_genre()["params"]["canvas"] == "portrait"
    assert lv.resolved_genre()["genre"] == "lyric-video"
    assert mv.footage.canvas() == (1080, 1920)


def test_reading_a_hosted_project_creates_nothing(tmp_path):
    import muvid

    muvid.create_project_at(tmp_path, "lv", genre="lyric-video")
    project = muvid.Project(tmp_path / "lv")
    before = sorted(p.name for p in project.root.iterdir())
    service.status(project.footage)
    project.lyric.status()
    assert sorted(p.name for p in project.root.iterdir()) == before


def test_hosted_replies_carry_no_absolute_paths(fp, monkeypatch):
    import muvid.footage.assemble as A
    import muvid.visualize as V

    monkeypatch.setattr(
        A,
        "assemble_music_video",
        lambda cuts, song, out, canvas, on_note=None, **kw: (
            Path(out).write_bytes(b"v"),
            Path(out),
        )[1],
    )
    monkeypatch.setattr(V, "verify_video", lambda *a, **k: [])
    monkeypatch.setattr(V, "failures", lambda c: [])
    monkeypatch.setattr(V, "report", lambda c: "ok")
    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    meta = service.render(fp, edit_id="e")
    assert meta["video"] == f"renders/{meta['render_id']}/final.mp4"
    text = json.dumps([service.status(fp), service.renders(fp)])
    assert str(fp.root) not in text


def test_a_trimmed_render_round_trips_its_span(fp, monkeypatch):
    import muvid.footage.assemble as A
    import muvid.visualize as V

    monkeypatch.setattr(
        A,
        "assemble_music_video",
        lambda cuts, song, out, canvas, on_note=None, **kw: (
            Path(out).write_bytes(b"v"),
            Path(out),
        )[1],
    )
    monkeypatch.setattr(V, "verify_video", lambda *a, **k: [])
    monkeypatch.setattr(V, "failures", lambda c: [])
    monkeypatch.setattr(V, "report", lambda c: "ok")
    edl = [dict(e) for e in _edl_ab()]
    edl[1]["song_end"] = 20.0
    service.save_edit(fp, edl=edl, edit_id="e", span=[0.0, 20.0])
    meta = service.render(fp, edit_id="e")
    assert meta["span"] == [0.0, 20.0]
    again = service.assemble(fp, edl=meta["edl"], span=meta["span"])
    assert again["edl"] == meta["edl"] and again["rendered_span"] == [0.0, 20.0]


def test_mcp_speaks_tool_names(fp):
    """The service names OPERATIONS; a connector caller can only call TOOLS, so the
    MCP transport translates — in docstrings, refusals and ``next_step``."""
    pytest.importorskip("fastmcp")
    import inspect

    import muvid.mcp.footage_tools as ft
    from fastmcp.exceptions import ToolError
    from muvid.mcp.identity import use_email

    assert "footage_merge_cut" in ft.footage_set_cut.__doc__
    assert "merge_cut" in service.set_cut.__doc__  # the studio keeps op names
    with use_email("u@x.com"):
        assert ft.footage_status("p")["next_step"]["op"] == "propose_edit"
        ft.footage_save_edit("p", edl=_edl_ab(), edit_id="e")
        assert ft.footage_status("p")["next_step"]["op"] == "footage_render"
        with pytest.raises(ToolError, match="footage_merge_cut"):
            ft.footage_set_cut("p", edit_id="e", index=1, song_start=0.0)
    assert "keep_declared" in inspect.signature(ft.align_footage).parameters
    assert "span" in inspect.signature(ft.assemble_music_video).parameters
    assert service.status(fp)["next_step"]["op"] == "render"


@needs_ffmpeg  # compiling a named look probes the ffmpeg binary (the `looks` package)
def test_named_looks(fp):
    from muvid.footage.named_looks import MAX_ZOOM

    menu = service.looks()["looks"]
    names = {row["name"] for row in menu}
    assert {"punch_in", "slow_push", "pan_left", "pan_right", "cartoon"} <= names
    for row in menu:
        for p in row["params_schema"]["properties"].values():
            if "zoom" in p["title"].lower() or p["title"].startswith("How far in"):
                assert p["maximum"] <= MAX_ZOOM
    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    out = service.set_cut(fp, edit_id="e", index=0, look={"name": "slow_push"})
    assert (
        out["edl"][0]["look"].startswith("zoompan")
        and out["edl"][0]["look_time_varying"]
    )
    spec = {"name": "slow_push", "zoom": 1.08, "anchor_x": 0.5, "anchor_y": 0.5}
    assert out["edl"][0]["look_spec"] == spec
    assert service.get_edit(fp, edit_id="e")["edl"][0]["look_spec"] == spec
    out = service.set_cut(fp, edit_id="e", index=1, look={"name": "black_and_white"})
    assert "look_time_varying" not in out["edl"][1]
    with pytest.raises(FootageError, match="between"):
        service.set_cut(
            fp, edit_id="e", index=0, look={"name": "punch_in", "zoom": 1.5}
        )
    with pytest.raises(FootageError, match="unknown look"):
        service.set_cut(fp, edit_id="e", index=0, look={"name": "sepia"})
    out = service.set_cut(fp, edit_id="e", index=0, look="eq=gamma=1.1")
    assert out["edl"][0]["look"] == "eq=gamma=1.1" and "look_spec" not in out["edl"][0]


def test_folder_import_keeps_what_it_added_when_one_member_is_refused(
    tmp_path, monkeypatch, fp
):
    pytest.importorskip("fastmcp")
    import muvid.mcp._fetch as F
    import muvid.mcp.footage_tools as ft
    from muvid.mcp.identity import use_email

    members = []
    for name in ("one.mp4", "two.mp4"):
        members.append(_bytes_file(tmp_path / name, b"x" + name.encode()))
    monkeypatch.setattr(F, "resolve_share_link", lambda url: (url, "archive"))
    monkeypatch.setattr(
        F,
        "fetch_to_file_streaming",
        lambda url, dest, *, max_bytes, expect_kind="": Path(dest).write_bytes(b"z"),
    )
    monkeypatch.setattr(F, "extract_media_members", lambda *a, **k: (members, []))
    monkeypatch.setattr(ft, "_duration", lambda p: 5.0)
    real = service.add_clip
    calls = []

    def flaky(fp_, **kw):
        calls.append(kw["name"])
        if kw["name"] == "two":
            raise FootageError("refused for the test")
        return real(fp_, **kw)

    monkeypatch.setattr(service, "add_clip", flaky)
    with use_email("u@x.com"):
        out = ft.add_footage_folder("p", url="https://example.com/folder")
    assert [a["name"] for a in out["added"]] == ["one"]
    assert out["skipped"] == [{"name": "two.mp4", "reason": "refused for the test"}]


def test_clip_in_drift_is_reported():
    from muvid.importing._edl import clip_in_drift

    doc = {
        "edl": [
            {"song_start": 1.0, "song_end": 2.0, "clip_id": "a", "clip_in": 1.0},
            {"song_start": 2.0, "song_end": 3.0, "clip_id": "a", "clip_in": 2.02},
            {"song_start": 3.0, "song_end": 4.0, "clip_id": "a", "clip_in": 3.1},
        ]
    }
    rows = clip_in_drift(doc, offsets={"a": 0.0}, fps={"a": 30.0})
    assert [r["index"] for r in rows] == [2] and rows[0]["drift_s"] == pytest.approx(
        0.1
    )


def test_the_host_contract_types_and_ceilings():
    import muvid.genre  # noqa: F401
    from muvid.footage.errors import FootageCancelled

    assert issubclass(FootageError, nw.GenreOpRefused)
    assert issubclass(FootageError, ValueError)
    assert issubclass(FootageCancelled, nw.GenreOpCancelled)
    rows = {r["name"]: r for r in nw.genre_ops_catalogue("music_video")}
    assert rows["set_song"]["max_upload_bytes"] == service.SONG_MAX_BYTES
    assert rows["add_clip"]["max_upload_bytes"] == service.CLIP_MAX_BYTES
    assert rows["render"]["host_params"] == ["should_cancel"]
    assert rows["score"]["host_params"] == ["should_cancel"]
    assert "should_cancel" not in rows["render"]["params_schema"]["properties"]


@needs_ffmpeg
def test_a_cancelled_render_stops_between_cuts_and_leaves_nothing(tmp_path):
    import muvid
    from muvid.footage.errors import FootageCancelled

    muvid.create_project_at(tmp_path, "mv")
    project = muvid.Project(tmp_path / "mv")
    fp = project.footage
    service.set_song(fp, path=str(_tone(tmp_path / "s.wav", seconds=4.0)))
    service.add_clip(fp, path=str(_video(tmp_path / "a.mp4", seconds=4.0)), clip_id="a")
    service.set_offset(fp, clip_id="a", offset_s=0.0)
    service.save_edit(
        fp,
        edl=[
            {"song_start": 0.0, "song_end": 2.0, "clip_id": "a"},
            {"song_start": 2.0, "song_end": 4.0, "clip_id": "a"},
        ],
        edit_id="e",
    )
    polls = []

    def after_first_cut():
        polls.append(1)
        return len(polls) > 1

    render = nw.genre_op("music_video", "render")
    with pytest.raises(nw.GenreOpCancelled):
        render.run(project, {"edit_id": "e"}, host={"should_cancel": after_first_cut})
    assert len(polls) == 2
    assert not list((fp.root / "renders").glob("*/final.mp4"))
    with pytest.raises(FootageCancelled):
        service.render(fp, edit_id="e", should_cancel=lambda: True)


# -- the Edit tab: filmstrips, peaks, bars, undo/redo ---------------------------------


@needs_ffmpeg
def test_filmstrips_peaks_are_made_once_and_registered(tmp_path):
    import time

    import muvid

    muvid.create_project_at(tmp_path, "mv")
    project = muvid.Project(tmp_path / "mv")
    fp = project.footage
    service.set_song(fp, path=str(_tone(tmp_path / "s.wav", seconds=6.0)))
    # a portrait and a landscape camera: frame_w is per clip
    service.add_clip(
        fp,
        path=str(_video(tmp_path / "p.mp4", seconds=61.0, size="180x320")),
        clip_id="p",
    )
    service.add_clip(fp, path=str(_video(tmp_path / "l.mp4", seconds=4.0)), clip_id="l")
    t0 = time.time()
    out = service.filmstrips(fp)
    assert time.time() - t0 < 1.0  # made at ingest; this is a cache read
    assert out["fps"] == 2.0 and set(out["clips"]) == {"p", "l"}
    p = out["clips"]["p"]
    assert (p["frame_h"], p["n_frames"]) == (90, 122)
    assert p["frame_w"] < out["clips"]["l"]["frame_w"]  # 50 vs 120: aspects differ
    assert [(s["first_frame"], s["n_frames"], s["rows"]) for s in p["sheets"]] == [
        (0, 100, 10),
        (100, 22, 3),
    ]
    assert all(project.media_catalog.has(s["artifact_id"]) for s in p["sheets"])
    assert service.filmstrip(fp, clip_id="p") == {"clip_id": "p", "fps": 2.0, **p}
    with pytest.raises(FootageError, match="unknown clip_id"):
        service.filmstrip(fp, clip_id="zz")

    wave = service.peaks(fp, n=50)
    assert wave["n"] == 50 and len(wave["peaks"]) == 50
    assert max(wave["peaks"]) == 1.0 and min(wave["peaks"]) >= 0.0
    assert wave["duration_s"] == pytest.approx(6.0, abs=0.05)
    assert service.peaks(fp, n=50) == wave  # cached
    with pytest.raises(FootageError, match="between"):
        service.peaks(fp, n=0)


def test_beat_grid_numbers_bars(fp, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    beats = [0.5 * i for i in range(1, 11)]
    env = np.zeros(200)
    for t in beats[1::4]:  # the 2nd beat of every 4 carries the onsets
        env[int(round(t / 0.05))] = 1.0
    monkeypatch.setattr(
        "mixing.audio.beat_grid",
        lambda audio, **kw: SimpleNamespace(
            beat_times=beats,
            downbeat_times=[],
            tempo_bpm=120.0,
            onset_env=env,
            onset_hop_s=0.05,
        ),
    )
    monkeypatch.setattr(service, "_read_beat_grid_cache", lambda *a: None)
    grid = service.beat_grid(fp)
    assert grid["downbeats_source"] == "derived" and grid["beats_per_bar"] == 4
    assert grid["downbeats"] == [1.0, 3.0, 5.0]
    assert grid["bar_of_beat"] == [0, 1, 1, 1, 1, 2, 2, 2, 2, 3]


def test_undo_and_redo_edits(fp):
    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    first = service.get_edit(fp, edit_id="e")
    assert (first["can_undo"], first["can_redo"]) == (False, False)
    with pytest.raises(FootageError, match="nothing to undo"):
        service.undo_edit(fp, edit_id="e")
    split = service.split_cut(fp, edit_id="e", at_s=6.0)
    assert split["can_undo"] and not split["can_redo"]
    spanned = service.set_span(fp, edit_id="e", start_s=2.0, end_s=20.0)
    back = service.undo_edit(fp, edit_id="e")
    assert back["edl"] == split["edl"] and back["span"] == [0.0, SONG_S]
    assert back["can_undo"] and back["can_redo"]
    back = service.undo_edit(fp, edit_id="e")
    assert back["edl"] == first["edl"] and not back["can_undo"]
    again = service.redo_edit(fp, edit_id="e")
    assert again["edl"] == split["edl"]
    # a new change after an undo forks: redo is gone
    service.merge_cut(fp, edit_id="e", index=1, into="previous")
    assert not service.get_edit(fp, edit_id="e")["can_redo"]
    assert spanned["span"] == [2.0, 20.0]


def test_rename_edit_changes_only_the_name_and_undoes(fp):
    service.save_edit(fp, edl=_edl_ab(), edit_id="e", name="V3 — punch-ins (101 cuts)")
    before = service.get_edit(fp, edit_id="e")
    renamed = service.rename_edit(fp, edit_id="e", name="  V3 — punch-ins  ")
    assert renamed["name"] == "V3 — punch-ins"
    assert renamed["edl"] == before["edl"] and renamed["edit_id"] == "e"
    assert service.undo_edit(fp, edit_id="e")["name"] == "V3 — punch-ins (101 cuts)"
    with pytest.raises(FootageError, match="cannot be empty"):
        service.rename_edit(fp, edit_id="e", name="   ")
    with pytest.raises(FootageError, match="at most"):
        service.rename_edit(fp, edit_id="e", name="x" * 500)


def test_history_is_bounded(fp, monkeypatch):
    monkeypatch.setattr(service, "EDIT_HISTORY_LIMIT", 3)
    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    for start in (1.0, 2.0, 3.0, 4.0, 5.0):
        service.set_span(fp, edit_id="e", start_s=start, end_s=20.0)
    for _ in range(3):
        service.undo_edit(fp, edit_id="e")
    assert service.get_edit(fp, edit_id="e")["span"] == [2.0, 20.0]
    with pytest.raises(FootageError, match="nothing to undo"):
        service.undo_edit(fp, edit_id="e")


@needs_ffmpeg
def test_each_filmstrip_sheet_holds_its_own_frames(tmp_path):
    """Every sheet is a different stretch of the clip — a bug once wrote the clip's
    last tile over all of them."""
    import hashlib

    from muvid.footage.media_views import _make_filmstrip

    clip = _video(tmp_path / "c.mp4", seconds=120.0)
    index = _make_filmstrip(
        clip, tmp_path / "strip", fps=2, height=90, cols=10, rows=10
    )
    digests = [
        hashlib.sha256((tmp_path / "strip" / s["file"]).read_bytes()).hexdigest()
        for s in index["sheets"]
    ]
    assert len(digests) == 3 and len(set(digests)) == 3


# -- slip: a cut shows a slightly different moment of its video ------------------------


def test_slip_moves_where_a_cut_reads_its_video_and_is_kept(fp):
    from muvid.footage.edl import derive_cuts

    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    out = service.set_cut(fp, edit_id="e", index=0, slip_s=0.25)
    assert out["edl"][0]["slip_s"] == 0.25
    assert "slip_s" not in out["edl"][1]  # absent when 0: every older edit stays byte-identical
    assert service.get_edit(fp, edit_id="e")["edl"][0]["slip_s"] == 0.25
    # The renderer's in-point: song_start - offset + slip (A sits at 0).
    entries = service._edit_entries(fp, "e")[1]
    cuts = derive_cuts(entries, fp.load_alignments(), fp.clip_paths())
    assert cuts[0].clip_in == pytest.approx(0.25)
    assert cuts[1].clip_in == pytest.approx(2.0)  # B sits at +10, cut starts at 12
    out = service.set_cut(fp, edit_id="e", index=0, slip_s=0)
    assert "slip_s" not in out["edl"][0]


def test_slip_is_refused_outside_its_bound_or_its_footage(fp):
    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    before = service.get_edit(fp, edit_id="e")["edl"]
    with pytest.raises(FootageError, match="set_offset"):
        service.set_cut(fp, edit_id="e", index=0, slip_s=0.8)  # over a beat: an alignment, not a slip
    with pytest.raises(FootageError, match="does not contain"):
        service.set_cut(fp, edit_id="e", index=0, slip_s=-0.2)  # before A's first frame
    with pytest.raises(FootageError, match="does not contain"):
        service.set_cut(fp, edit_id="e", index=1, slip_s=0.2)  # past B's last frame
    assert service.get_edit(fp, edit_id="e")["edl"] == before


def test_a_new_video_starts_unslipped(fp):
    service.save_edit(fp, edl=_edl_ab(), edit_id="e")
    service.set_cut(fp, edit_id="e", index=1, slip_s=-0.2)
    out = service.set_cut(fp, edit_id="e", index=1, song_start=12.0, clip_id="B")
    assert out["edl"][1]["slip_s"] == -0.2  # same video: kept
    service.set_cut(fp, edit_id="e", index=0, slip_s=0.1)
    out = service.set_cut(fp, edit_id="e", index=0, clip_id="")
    assert "slip_s" not in out["edl"][0]  # a gap has nothing to slip


def test_slip_survives_the_editor_round_trip():
    from muvid.footage.edl import EdlEntry
    from muvid.footage.lacing_bridge import _edl_body

    assert _edl_body(EdlEntry(0.0, 4.0, "A", slip_s=-0.12))["slip_s"] == -0.12
    assert "slip_s" not in _edl_body(EdlEntry(0.0, 4.0, "A"))


def test_a_slipped_cut_is_not_stretched_over_its_neighbour():
    from muvid.footage.edl import EdlEntry, _absorbable

    by_id = {"A": type("A", (), {"reliable": True})()}
    assert _absorbable(EdlEntry(0.0, 4.0, "A"), by_id)
    assert not _absorbable(EdlEntry(0.0, 4.0, "A", slip_s=0.1), by_id)
