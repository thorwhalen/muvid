"""muvid.downloads — the resolver half of render retrieval (muvid#24 B2).

The contract under test is the seam agreed with the connector redesign: resolve() is the
ONLY authority mapping (email, project_id, artifact_id) → file, it refuses everything
that is not the caller's artifact with KeyError (no existence leaks, no traversal), and
tools hand out claims — never paths a remote caller cannot read anyway.
"""

from __future__ import annotations

import pytest

nw = pytest.importorskip("nw")

from nw.delivery import Deliverable  # noqa: E402
from muvid.downloads import GENRE, claim, list_deliverables, resolve  # noqa: E402
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
    assert isinstance(got, Deliverable)
    assert got.path.read_bytes() == b"rendered-bytes"
    assert got.content_type == "video/mp4"
    assert got.filename == f"p-{render}.mp4"  # no meta.json ⇒ no ref ⇒ the id


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
        lambda cuts, song, out, canvas, on_note=None: (
            Path(out).write_bytes(b"v"),
            Path(out),
        )[1],
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


# --- the speakable reference (thorwhalen/reelee#296) -------------------------
#
# A render id is a uuid4 slice. Nobody can ask for "a bit less of the wide shot
# in b02fc05417ea", so a render also carries an ordinal rendered as `cut 4`.
# These tests pin the two properties that make it usable: it RESOLVES, and it
# does not move once assigned.


def _render_with_meta(proj, render_id, *, ref_n=None):
    import json

    rdir = proj.new_render_dir(render_id)
    (rdir / "final.mp4").write_bytes(b"v")
    meta = {"render_id": render_id}
    if ref_n is not None:
        meta["ref_n"] = ref_n
    (rdir / "meta.json").write_text(json.dumps(meta))
    return render_id


def test_a_spoken_reference_resolves_to_the_same_file_as_the_id(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    rid = _render_with_meta(proj, "a" * 12)

    by_id = resolve("u@x.com", "p", rid)
    for spoken in ("cut 1", "cut-1", "#1", "1"):
        assert resolve("u@x.com", "p", spoken).path == by_id.path
    assert by_id.ref == "cut 1"
    assert by_id.filename == "p-cut-1.mp4"


def test_refs_are_assigned_oldest_first_and_never_renumber(tmp_path, monkeypatch):
    """The property that makes a reference worth writing down.

    A position in a newest-first list would shift every time the user rendered
    again, so the reference they noted yesterday would point somewhere else
    today. Assignment happens once and is persisted.
    """
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    first = _render_with_meta(proj, "a" * 12)
    refs = proj.ensure_render_refs()
    assert refs[first] == 1

    second = _render_with_meta(proj, "b" * 12)
    refs = proj.ensure_render_refs()
    assert refs[first] == 1, "an existing ref must not move when a render is added"
    assert refs[second] == 2
    assert resolve("u@x.com", "p", "cut 1").artifact_id == first


def test_an_unknown_reference_is_a_keyerror_not_a_wrong_file(tmp_path, monkeypatch):
    """`cut 9` when there are two renders must refuse, not clamp to the last one."""
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    _render_with_meta(proj, "a" * 12)
    with pytest.raises(KeyError):
        resolve("u@x.com", "p", "cut 9")


def test_listing_is_how_a_reference_becomes_discoverable(tmp_path, monkeypatch):
    """Without a listing, a user can only name a render they still remember —
    which is the state that left five finished videos unreachable."""
    from muvid.downloads import list_deliverables

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    ws = FootageWorkspace.for_email("u@x.com")
    proj = ws.create_project("p")
    _render_with_meta(proj, "a" * 12)
    _render_with_meta(proj, "b" * 12)

    got = list_deliverables("u@x.com")
    assert {d.ref for d in got} == {"cut 1", "cut 2"}
    assert all(d.genre == GENRE and d.project_id == "p" for d in got)

    # Scoped to one project, and blind to everyone else's work.
    assert len(list_deliverables("u@x.com", "p")) == 2
    assert list_deliverables("intruder@x.com") == []


# --- the visualizer genre (muvid#8) -----------------------------------------
#
# muvid hosts TWO genres behind one `muvid_` prefix and two separate per-user
# workspaces. The footage one was made retrievable first; the visualizer's was
# deferred behind "a download URL depends on the storage backend minting one".
# That premise was obsolete — the connector's signed route never calls
# dol.content_url, it resolves a path and streams it.


def _visualizer_render(
    tmp_path, monkeypatch, *, email="u@x.com", pid="viz", rid="v" * 12, thumb=False
):
    import json

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.mcp.workspace import VisualizerWorkspace

    ws = VisualizerWorkspace.for_email(email)
    try:
        proj = ws.open_project(pid)
    except FileNotFoundError:
        proj = ws.create_project(pid)
    d = proj.new_render_dir(rid)
    (d / "video.mp4").write_bytes(b"viz-bytes")
    if thumb:
        (d / "thumbnail.jpg").write_bytes(b"jpeg-bytes")
    (d / "meta.json").write_text(json.dumps({"render_id": rid, "visual": "bars"}))
    return proj, rid


def test_a_visualizer_render_resolves_without_any_storage_migration(
    tmp_path, monkeypatch
):
    """muvid#8's stated blocker was never the real one."""
    _visualizer_render(tmp_path, monkeypatch)
    got = resolve("u@x.com", "viz", "v" * 12)
    assert got.path.read_bytes() == b"viz-bytes"
    assert got.content_type == "video/mp4"
    assert got.meta["muvid_genre"] == "visualizer"


def test_one_resolver_spans_both_muvid_genres(tmp_path, monkeypatch):
    """A caller says genre='muvid' for either, and should not have to know which
    drawer their project is in — that split is what produced 'no project X for
    you' against a project that existed (muvid#23)."""
    proj, _ = _project_with_render(tmp_path, monkeypatch)  # footage, project "p"
    _visualizer_render(tmp_path, monkeypatch)  # visualizer, project "viz"

    assert resolve("u@x.com", "p", "r1r1r1r1r1r1").meta["muvid_genre"] == "footage"
    assert resolve("u@x.com", "viz", "v" * 12).meta["muvid_genre"] == "visualizer"

    kinds = {d.meta["muvid_genre"] for d in list_deliverables("u@x.com")}
    assert kinds == {"footage", "visualizer"}


def test_the_poster_is_a_deliverable_of_its_own(tmp_path, monkeypatch):
    """The one small image in the federation, and the only artifact that could
    plausibly be returned inline in a chat."""
    _visualizer_render(tmp_path, monkeypatch, thumb=True)
    poster = resolve("u@x.com", "viz", "v" * 12 + ".thumbnail")
    assert poster.content_type == "image/jpeg"
    assert poster.kind == "image"
    assert poster.path.read_bytes() == b"jpeg-bytes"
    assert poster.duration_s is None
    # And it is listed alongside the video, not instead of it.
    ids = {d.artifact_id for d in list_deliverables("u@x.com", "viz")}
    assert ids == {"v" * 12, "v" * 12 + ".thumbnail"}


def test_a_missing_poster_is_a_keyerror_not_the_video(tmp_path, monkeypatch):
    """Falling back to the video would hand an image slot 4 MB of mp4."""
    _visualizer_render(tmp_path, monkeypatch, thumb=False)
    with pytest.raises(KeyError):
        resolve("u@x.com", "viz", "v" * 12 + ".thumbnail")
    # ...and the listing simply omits it rather than erroring.
    ids = {d.artifact_id for d in list_deliverables("u@x.com", "viz")}
    assert ids == {"v" * 12}


def test_the_visualizer_invents_no_reference_it_cannot_keep(tmp_path, monkeypatch):
    """Visualizer buckets persist no ordinals, so its deliverables carry no ref.

    Deriving one from sort position would be a number that moves under the user
    — worse than no reference at all, because they would write it down.
    """
    _visualizer_render(tmp_path, monkeypatch)
    assert resolve("u@x.com", "viz", "v" * 12).ref is None


def test_visualizer_renders_are_email_scoped_too(tmp_path, monkeypatch):
    _visualizer_render(tmp_path, monkeypatch, email="u@x.com")
    with pytest.raises(KeyError):
        resolve("intruder@x.com", "viz", "v" * 12)
    assert list_deliverables("intruder@x.com") == []


# ---------------------------------------------------------------------------
# list_projects — muvid's half of nw.delivery.ProjectLister (reelee#333)
# ---------------------------------------------------------------------------


def test_a_never_rendered_project_is_findable_with_zero_deliverables(
    tmp_path, monkeypatch
):
    """The reelee#333 acceptance case: a footage project with work in it and
    no cut yet must appear in the listing, counted at 0 ("no cut yet") —
    before this, it was invisible to every surface in the system."""
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.downloads import list_projects
    from muvid.footage.workspace import FootageWorkspace

    FootageWorkspace.for_email("u@x.com").create_project("wip", title="We'll See")
    rows = list_projects("u@x.com")
    assert [r.project_id for r in rows] == ["wip"]
    row = rows[0]
    assert row.title == "We'll See"
    assert row.genre == GENRE
    assert row.deliverable_count == 0
    assert row.meta["muvid_genre"] == "footage"
    assert row.created_at is not None
    assert row.modified_at is not None


def test_rendered_projects_are_counted_and_ordered_newest_first(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.downloads import list_projects
    from muvid.footage.workspace import FootageWorkspace

    ws = FootageWorkspace.for_email("u@x.com")
    older = ws.create_project("older")
    (older.new_render_dir("r1" * 6) / "final.mp4").write_bytes(b"x")
    (older.new_render_dir("r2" * 6) / "final.mp4").write_bytes(b"y")
    newer = ws.create_project("newer")
    # Order is by the manifest's mtime; make it unambiguous.
    past = time.time() - 1000
    os.utime(ws.project_root("older") / "manifest.json", (past, past))

    rows = list_projects("u@x.com")
    assert [r.project_id for r in rows] == ["newer", "older"]
    assert rows[1].deliverable_count == 2
    assert rows[0].deliverable_count == 0


def test_both_drawers_list_disambiguated_by_meta(tmp_path, monkeypatch):
    """One registration spans footage AND visualizer; the same project_id in
    both drawers yields two rows a host tells apart by meta — never by
    (genre, project_id), which collides (muvid#23's split, listed honestly)."""
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.downloads import list_projects
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.workspace import VisualizerWorkspace

    FootageWorkspace.for_email("u@x.com").create_project("same")
    VisualizerWorkspace.for_email("u@x.com").create_project("same")

    rows = list_projects("u@x.com")
    assert [r.project_id for r in rows] == ["same", "same"]
    assert {r.meta["muvid_genre"] for r in rows} == {"footage", "visualizer"}


def test_listing_is_blind_to_other_callers_and_empty_for_the_unknown(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.downloads import list_projects
    from muvid.footage.workspace import FootageWorkspace

    FootageWorkspace.for_email("owner@x.com").create_project("private")
    assert list_projects("someone-else@x.com") == []


def test_listing_never_mints_a_workspace_directory(tmp_path, monkeypatch):
    """Emptiness is the only signal there is — an empty answer must mean the
    directory was never there, so listing must not create it (the seam's
    ProjectLister contract)."""
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.downloads import list_projects
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.workspace import VisualizerWorkspace

    assert list_projects("ghost@x.com") == []
    assert not FootageWorkspace.for_email("ghost@x.com").projects_dir.exists()
    assert not VisualizerWorkspace.for_email("ghost@x.com").projects_dir.exists()


# ---------------------------------------------------------------------------
# organise — muvid's half of nw.delivery.Organiser (ADR asset-surfaces §3.3/§4)
# ---------------------------------------------------------------------------


def _footage_project_two_renders(tmp_path, monkeypatch, *, email="u@x.com"):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email(email).create_project("p")
    for rid in ("r1" * 6, "r2" * 6):
        rdir = proj.new_render_dir(rid)
        (rdir / "final.mp4").write_bytes(b"cut-" + rid.encode())
        (rdir / "meta.json").write_text('{"render_id": "%s"}' % rid)
    return proj


def test_an_accepted_title_resolves_and_the_receipt_is_a_reread(tmp_path, monkeypatch):
    from muvid.downloads import organise

    _footage_project_two_renders(tmp_path, monkeypatch)
    got = organise(
        "u@x.com",
        "p",
        "r1" * 6,
        title="The Slow Open",
        tags=["keeper"],
        note="send this one",
    )
    # The receipt is a re-read: the assigned title is the deliverable's title,
    # tags/note ride meta under the parameter names (the seam's pinning), and
    # the identity did not move.
    assert got.artifact_id == "r1" * 6
    assert got.title == "The Slow Open"
    assert got.meta["tags"] == ["keeper"]
    assert got.meta["note"] == "send this one"
    # The obligation that keeps naming genre-side: the accepted title RESOLVES.
    assert resolve("u@x.com", "p", "The Slow Open").artifact_id == "r1" * 6
    assert resolve("u@x.com", "p", "the slow open").artifact_id == "r1" * 6
    # And no file moved: the raw id still resolves to the same bytes.
    assert resolve("u@x.com", "p", "r1" * 6).path.read_bytes() == b"cut-" + b"r1" * 6


def test_title_collisions_are_refused_naming_the_holder(tmp_path, monkeypatch):
    from muvid.downloads import organise

    _footage_project_two_renders(tmp_path, monkeypatch)
    organise("u@x.com", "p", "r1" * 6, title="The Slow Open")
    with pytest.raises(ValueError, match="already the title"):
        organise("u@x.com", "p", "r2" * 6, title="the slow open")
    with pytest.raises(ValueError, match="collides with render id"):
        organise("u@x.com", "p", "r1" * 6, title="r2" * 6)


def test_ref_shaped_titles_are_refused_by_the_shared_rule(tmp_path, monkeypatch):
    from muvid.downloads import organise

    _footage_project_two_renders(tmp_path, monkeypatch)
    for bad in ("cut 4", "#7", "12"):
        with pytest.raises(ValueError, match="reads as a reference"):
            organise("u@x.com", "p", "r1" * 6, title=bad)


def test_none_leaves_alone_and_empty_clears(tmp_path, monkeypatch):
    from muvid.downloads import organise

    _footage_project_two_renders(tmp_path, monkeypatch)
    organise("u@x.com", "p", "r1" * 6, title="Named", note="keep")
    # None = untouched: only tags change here.
    got = organise("u@x.com", "p", "r1" * 6, tags=["a", "b"])
    assert got.title == "Named" and got.meta["note"] == "keep"
    # "" / [] = clear.
    got = organise("u@x.com", "p", "r1" * 6, title="", tags=[], note="")
    assert got.title == "p"  # back to the derived default
    assert "tags" not in got.meta and "note" not in got.meta
    with pytest.raises(KeyError):
        resolve("u@x.com", "p", "Named")  # the cleared title no longer resolves


def test_organise_authorizes_like_resolve_and_refuses_junk(tmp_path, monkeypatch):
    from muvid.downloads import organise

    _footage_project_two_renders(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        organise("intruder@x.com", "p", "r1" * 6, title="Mine Now")
    with pytest.raises(ValueError, match="nothing to change"):
        organise("u@x.com", "p", "r1" * 6)
    with pytest.raises(ValueError, match="poster"):
        organise("u@x.com", "p", "r1" * 6 + ".thumbnail", title="X")
    # All-or-nothing: a refused title writes nothing, including the valid note.
    with pytest.raises(ValueError):
        organise("u@x.com", "p", "r1" * 6, title="cut 4", note="should not land")
    assert "note" not in resolve("u@x.com", "p", "r1" * 6).meta


def test_pipeline_meta_keys_and_organise_state_never_collide(tmp_path, monkeypatch):
    """The render pipeline writes its own top-level meta.json keys — including
    `note` (instructional strings) — so organise state lives in an `organise`
    sub-dict. A pipeline note must never surface as a user-assigned one, and
    clearing the organise note must never delete the pipeline's key
    (adversarial-review finding)."""
    import json

    from muvid.downloads import organise

    proj = _footage_project_two_renders(tmp_path, monkeypatch)
    meta_path = proj.renders_dir / ("r1" * 6) / "meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "render_id": "r1" * 6,
                "note": "PIPELINE: re-render with --force after editing",
                "ref_n": 1,
            }
        )
    )

    # The pipeline note is not a user note.
    assert "note" not in resolve("u@x.com", "p", "r1" * 6).meta

    organise("u@x.com", "p", "r1" * 6, note="the good one")
    got = resolve("u@x.com", "p", "r1" * 6)
    assert got.meta["note"] == "the good one"

    # Clearing the ORGANISE note leaves the pipeline's key (and ref_n) intact.
    organise("u@x.com", "p", "r1" * 6, note="")
    stored = json.loads(meta_path.read_text())
    assert stored["note"].startswith("PIPELINE")
    assert stored["ref_n"] == 1
    assert "organise" not in stored  # fully cleared sub-dict is removed
