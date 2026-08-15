"""muvid.footage.lacing_bridge — project ↔ lacing standoff records (muvid#31).

The contract under test: editor_document() emits the three record kinds
reelee-web#203 needs (clip-alignment/v1, clip-score-track/v1, music-video-edl/v1),
everything referenced to the song by content hash on one shared song-time axis; and
edl_from_annotations() reads a DECISION tier back into a plain EDL that survives
fill_gaps + validate_edl + derive_cuts unchanged — the round trip the multichannel
editor depends on.
"""

from __future__ import annotations

import pytest

nw = pytest.importorskip("nw")
lacing = pytest.importorskip("lacing")

from muvid.footage.edl import (  # noqa: E402
    EdlEntry,
    FootageAlignment,
    derive_cuts,
    fill_gaps,
    validate_edl,
)
from muvid.footage.lacing_bridge import (  # noqa: E402
    CLIP_ALIGNMENT_SCHEMA,
    CLIP_SCORE_TRACK_SCHEMA,
    DECISION_TIER,
    MUSIC_VIDEO_EDL_SCHEMA,
    alignment_annotations,
    edl_annotations,
    edl_from_annotations,
    editor_document,
    score_track_annotations,
)

SONG_HASH = "a" * 64  # a stand-in content hash


def _fake_project(tmp_path, monkeypatch, *, with_scores=False):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    (proj.root / "song").mkdir()
    (proj.root / "song" / "song.wav").write_bytes(b"x")
    (proj.root / "clips").mkdir()
    for cid in ("A", "B"):
        (proj.root / "clips" / f"{cid}.mp4").write_bytes(b"x")
    m = proj.manifest()
    m.update(
        song="song.wav",
        song_duration=20.0,
        clips=[
            {"clip_id": "A", "file": "A.mp4", "name": "A"},
            {"clip_id": "B", "file": "B.mp4", "name": "B"},
        ],
    )
    proj._write_manifest(m)
    proj.save_alignments(
        [
            FootageAlignment("A", 0.0, 0.8, 12.0, (0.0, 12.0)),
            FootageAlignment("B", 8.0, 0.6, 20.0, (8.0, 20.0)),
        ]
    )
    monkeypatch.setattr(type(proj), "song_hash", lambda self: SONG_HASH)
    if with_scores:
        import numpy as np

        from muvid.footage.scoring.grid import ScoreTrack, save_scores

        n = 40
        rng = np.random.default_rng(0)
        tracks = {
            cid: [
                ScoreTrack(
                    clip_id=cid,
                    metric="quality",
                    t0=0.0,
                    hop_s=0.5,
                    raw_values=rng.uniform(0, 1, n).astype("float32"),
                    mask=np.ones(n, dtype=bool),
                )
            ]
            for cid in ("A", "B")
        }
        save_scores(
            proj.root,
            tracks,
            t0=0.0,
            hop_s=0.5,
            n=n,
            metrics=["quality"],
            song_hash=SONG_HASH,
        )
    return proj


# -- alignment_annotations ----------------------------------------------------


def test_alignment_annotations_one_per_clip_referenced_to_the_song():
    aligns = [
        FootageAlignment("A", 0.0, 0.8, 12.0, (0.0, 12.0)),
        FootageAlignment("B", 8.0, 0.6, 20.0, (8.0, 20.0)),
    ]
    anns = alignment_annotations(aligns, song_asset_id=SONG_HASH, attributed_to="u:x")
    assert len(anns) == 2
    for a, ann in zip(aligns, anns):
        assert ann.body_schema_uri == CLIP_ALIGNMENT_SCHEMA
        assert ann.tier == f"clip:{a.clip_id}"
        assert ann.reference.asset_id == SONG_HASH
        assert ann.reference.interval.start.to_seconds() == pytest.approx(a.coverage[0])
        assert ann.reference.interval.end.to_seconds() == pytest.approx(a.coverage[1])
        assert ann.body["offset_s"] == a.offset_s
        assert ann.confidence == pytest.approx(a.confidence)


def test_alignment_annotation_survives_a_non_overlapping_clip():
    # A clip that never touches the song must still produce a record — it stays
    # addressable — anchored at a zero-length interval rather than crashing on an
    # inverted [hi, lo) coverage pair.
    a = FootageAlignment("Z", 999.0, 0.0, 5.0, (0.0, 0.0), overlaps=False)
    (ann,) = alignment_annotations([a], song_asset_id=SONG_HASH, attributed_to="u:x")
    assert ann.body["overlaps"] is False
    assert ann.reference.interval.start == ann.reference.interval.end


# -- score_track_annotations ---------------------------------------------------


def test_score_track_annotations_one_per_clip_metric_with_masked_values_as_null():
    import numpy as np

    from muvid.footage.scoring.grid import ScoreTensor

    S = np.array([[[0.1], [np.nan], [0.9]]], dtype="float32")  # 1 clip, 3 frames, 1 metric
    M = np.array([[[True], [False], [True]]])
    tensor = ScoreTensor(
        clip_ids=["A"],
        metrics=["quality"],
        t0=0.0,
        hop_s=0.5,
        n=3,
        S=S,
        M=M,
        raw=S,
        norms={"quality": None},
    )
    (ann,) = score_track_annotations(tensor, song_asset_id=SONG_HASH, attributed_to="u:x")
    assert ann.body_schema_uri == CLIP_SCORE_TRACK_SCHEMA
    assert ann.tier == "clip:A"
    assert ann.body["values"] == [pytest.approx(0.1), None, pytest.approx(0.9)]
    assert ann.body["hop_s"] == 0.5


# -- edl_annotations / edl_from_annotations round trip -------------------------


def test_edl_round_trips_through_annotations_including_gaps():
    aligns = [FootageAlignment("A", 5.0, 0.9, 10.0, (5.0, 15.0))]
    entries = validate_edl(fill_gaps([EdlEntry(5, 15, "A")], 20.0), aligns, 20.0)

    anns = edl_annotations(entries, song_asset_id=SONG_HASH, attributed_to="u:x")
    assert [a.tier for a in anns] == [DECISION_TIER] * len(anns)
    assert all(a.body_schema_uri == MUSIC_VIDEO_EDL_SCHEMA for a in anns)
    assert [a.body["clip_id"] for a in anns] == [None, "A", None]  # head/foot/tail gaps

    back = edl_from_annotations(anns)
    assert back == [
        {"song_start": e.song_start, "song_end": e.song_end, "clip_id": e.clip_id or None}
        for e in entries
    ]
    # …and the round trip feeds straight back through the real render path.
    re_entries = validate_edl(back, aligns, 20.0)
    cuts = derive_cuts(re_entries, aligns, {"A": "/tmp/a.mp4"})
    assert [c.clip_path for c in cuts] == ["", "/tmp/a.mp4", ""]


def test_edl_from_annotations_ignores_other_tiers_and_schemas():
    aligns = [FootageAlignment("A", 0.0, 0.9, 20.0, (0.0, 20.0))]
    decision = edl_annotations(
        validate_edl([EdlEntry(0, 20, "A")], aligns, 20.0),
        song_asset_id=SONG_HASH,
        attributed_to="u:x",
    )
    other = alignment_annotations(aligns, song_asset_id=SONG_HASH, attributed_to="u:x")
    assert edl_from_annotations(decision + other) == edl_from_annotations(decision)


def test_edl_from_annotations_skips_a_reference_with_no_interval():
    # Adversarial-review finding 1: AnnotationRef legally has interval=None. A
    # music-video-edl/v1 body attached to one (malformed/untrusted editor input) must
    # be SKIPPED, not crash the whole export with an AttributeError.
    import uuid

    from lacing.model import Annotation, AnnotationRef, Provenance
    from lacing.time import RationalTime

    aligns = [FootageAlignment("A", 0.0, 0.9, 20.0, (0.0, 20.0))]
    good = edl_annotations(
        validate_edl([EdlEntry(0, 20, "A")], aligns, 20.0),
        song_asset_id=SONG_HASH,
        attributed_to="u:x",
    )
    bad = Annotation(
        id=uuid.uuid4(),
        tier=DECISION_TIER,
        reference=AnnotationRef(target_id=uuid.uuid4()),  # interval=None
        body={"clip_id": None},
        body_schema_uri=MUSIC_VIDEO_EDL_SCHEMA,
        provenance=Provenance(
            was_generated_by="user:x",
            was_attributed_to="user:x",
            generated_at_time=RationalTime(0, 1),
        ),
    )
    assert edl_from_annotations(good + [bad]) == edl_from_annotations(good)


def test_edl_from_annotations_refuses_another_songs_decision_lane():
    # muvid#35: a stale clipboard from ANOTHER project — same clip_id, similar song
    # duration — passes schema/tier and can even pass validate_edl, silently splicing in
    # the wrong spans. Given the project's own song, that must be an error that SAYS so,
    # not a skip that leaves an empty lane to be misdiagnosed downstream.
    aligns = [FootageAlignment("A", 0.0, 0.9, 20.0, (0.0, 20.0))]
    stale = edl_annotations(
        validate_edl([EdlEntry(0, 20, "A")], aligns, 20.0),
        song_asset_id="b" * 64,  # a different song
        attributed_to="u:x",
    )
    with pytest.raises(ValueError, match="different song"):
        edl_from_annotations(stale, expected_song_asset_id=SONG_HASH)


def test_edl_from_annotations_song_check_is_permissive_without_evidence():
    # Two ways there is nothing to contradict, neither of which is an error: no
    # expectation passed (the default — the check is opt-in), and a reference kind that
    # carries no asset_id at all (only MediaRef has one; an editor may legally hang a
    # DECISION body off an AnnotationRef sub-interval). The check reports a WRONG song;
    # it does not demand proof of the right one.
    import uuid

    from lacing.model import Annotation, AnnotationRef, Provenance
    from lacing.time import RationalTime

    aligns = [FootageAlignment("A", 0.0, 0.9, 20.0, (0.0, 20.0))]
    matching = edl_annotations(
        validate_edl([EdlEntry(0, 20, "A")], aligns, 20.0),
        song_asset_id=SONG_HASH,
        attributed_to="u:x",
    )
    assert edl_from_annotations(matching, expected_song_asset_id=None) == (
        edl_from_annotations(matching, expected_song_asset_id=SONG_HASH)
    )

    no_asset_id = Annotation(
        id=uuid.uuid4(),
        tier=DECISION_TIER,
        reference=AnnotationRef(
            target_id=matching[0].id, interval=matching[0].reference.interval
        ),
        body={"clip_id": "A"},
        body_schema_uri=MUSIC_VIDEO_EDL_SCHEMA,
        provenance=Provenance(
            was_generated_by="user:x",
            was_attributed_to="user:x",
            generated_at_time=RationalTime(0, 1),
        ),
    )
    assert edl_from_annotations([no_asset_id], expected_song_asset_id=SONG_HASH) == [
        {"song_start": 0.0, "song_end": 20.0, "clip_id": "A"}
    ]


def test_editor_document_degrades_when_the_default_proposal_cant_build(
    tmp_path, monkeypatch
):
    # Adversarial-review finding 2: a self-inconsistent persisted alignment (coverage
    # wider than the clip actually is — exactly the shape proj.load_alignments()
    # deserializes without re-checking) must not take down the WHOLE document. The
    # alignment/score annotations are independently fine; only the DECISION proposal
    # fails, and that must be reported, not raised.
    proj = _fake_project(tmp_path, monkeypatch)
    proj.save_alignments(
        [FootageAlignment("A", 0.0, 0.9, 5.0, (0.0, 20.0))]  # coverage > duration_s
    )
    doc = editor_document(proj)
    assert doc["decision_error"] is not None
    assert not [a for a in doc["annotations"] if a["tier"] == DECISION_TIER]
    # everything else in the document still built
    assert any(a["body_schema_uri"] == CLIP_ALIGNMENT_SCHEMA for a in doc["annotations"])


def test_editor_document_tool_degrades_when_the_default_proposal_cant_build(
    tmp_path, monkeypatch
):
    pytest.importorskip("fastmcp")
    import muvid.mcp.footage_tools as ft
    from muvid.mcp.identity import use_email

    proj = _fake_project(tmp_path, monkeypatch)
    proj.save_alignments([FootageAlignment("A", 0.0, 0.9, 5.0, (0.0, 20.0))])
    with use_email("u@x.com"):
        doc = ft.footage_editor_document("p")
    assert doc["decision_error"] is not None


def test_edl_from_annotations_sorts_by_start():
    aligns = [FootageAlignment("A", 0.0, 0.9, 20.0, (0.0, 20.0))]
    entries = validate_edl(
        fill_gaps([EdlEntry(10, 20, "A"), EdlEntry(0, 10, "A")], 20.0), aligns, 20.0
    )
    anns = edl_annotations(entries, song_asset_id=SONG_HASH, attributed_to="u:x")
    import random

    shuffled = anns[:]
    random.Random(1).shuffle(shuffled)
    back = edl_from_annotations(shuffled)
    assert [e["song_start"] for e in back] == sorted(e["song_start"] for e in back)


# -- editor_document (project-level) -------------------------------------------


def test_editor_document_without_scores(tmp_path, monkeypatch):
    proj = _fake_project(tmp_path, monkeypatch)
    doc = editor_document(proj)
    assert doc["project_id"] == "p"
    assert doc["song_asset_id"] == SONG_HASH
    assert doc["song_duration"] == 20.0
    tier_names = {t["name"] for t in doc["tiers"]}
    assert tier_names == {DECISION_TIER, "clip:A", "clip:B"}

    schemas = {a["body_schema_uri"] for a in doc["annotations"]}
    assert CLIP_ALIGNMENT_SCHEMA in schemas
    assert MUSIC_VIDEO_EDL_SCHEMA in schemas
    assert CLIP_SCORE_TRACK_SCHEMA not in schemas  # no scores persisted yet

    # The DECISION lane in the document is itself a valid, renderable EDL.
    decision = [a for a in doc["annotations"] if a["tier"] == DECISION_TIER]
    edl = edl_from_annotations(
        [__import__("lacing").model.Annotation.model_validate(a) for a in decision]
    )
    entries = validate_edl(
        edl,
        [
            FootageAlignment("A", 0.0, 0.8, 12.0, (0.0, 12.0)),
            FootageAlignment("B", 8.0, 0.6, 20.0, (8.0, 20.0)),
        ],
        20.0,
    )
    assert entries[0].song_start == 0.0 and entries[-1].song_end == 20.0


def test_editor_document_includes_scores_when_persisted(tmp_path, monkeypatch):
    proj = _fake_project(tmp_path, monkeypatch, with_scores=True)
    doc = editor_document(proj)
    schemas = {a["body_schema_uri"] for a in doc["annotations"]}
    assert CLIP_SCORE_TRACK_SCHEMA in schemas
    score_anns = [a for a in doc["annotations"] if a["body_schema_uri"] == CLIP_SCORE_TRACK_SCHEMA]
    assert {a["body"]["clip_id"] for a in score_anns} == {"A", "B"}


def test_editor_document_json_round_trips(tmp_path, monkeypatch):
    import json

    proj = _fake_project(tmp_path, monkeypatch, with_scores=True)
    doc = editor_document(proj)
    assert json.loads(json.dumps(doc)) == doc


# -- MCP tool surface ------------------------------------------------------


def test_editor_document_tool(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    import muvid.mcp.footage_tools as ft
    from muvid.mcp.identity import use_email

    proj = _fake_project(tmp_path, monkeypatch)
    monkeypatch.setattr(proj.__class__, "song_hash", lambda self: SONG_HASH)
    with use_email("u@x.com"):
        doc = ft.footage_editor_document("p")
    assert doc["project_id"] == "p"

    decision = [a for a in doc["annotations"] if a["tier"] == DECISION_TIER]
    with use_email("u@x.com"):
        out = ft.footage_edl_from_annotations("p", annotations=decision)
    assert out["edl"][0]["song_start"] == 0.0
    assert out["edl"][-1]["song_end"] == 20.0


def test_editor_document_tool_requires_alignment(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    from fastmcp.exceptions import ToolError

    import muvid.mcp.footage_tools as ft
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.identity import use_email

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    FootageWorkspace.for_email("u@x.com").create_project("p")
    with use_email("u@x.com"), pytest.raises(ToolError, match="align_footage"):
        ft.footage_editor_document("p")


def test_edl_from_annotations_tool_scopes_to_the_caller(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    from fastmcp.exceptions import ToolError

    import muvid.mcp.footage_tools as ft
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.identity import use_email

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    FootageWorkspace.for_email("owner@x.com").create_project("p")
    with use_email("intruder@x.com"):
        with pytest.raises(ToolError, match="no such project"):
            ft.footage_edl_from_annotations("p", annotations=[])


def test_edl_from_annotations_tool_refuses_another_projects_lane(tmp_path, monkeypatch):
    # muvid#35 at the callsite: the tool must supply the project's OWN song id, and map
    # the bridge's ValueError to a ToolError whose text still names the cause.
    pytest.importorskip("fastmcp")
    from fastmcp.exceptions import ToolError

    import muvid.mcp.footage_tools as ft
    from muvid.mcp.identity import use_email

    _fake_project(tmp_path, monkeypatch)  # its song_hash() is SONG_HASH
    aligns = [FootageAlignment("A", 0.0, 0.9, 20.0, (0.0, 20.0))]
    stale = [
        a.model_dump(mode="json")
        for a in edl_annotations(
            validate_edl([EdlEntry(0, 20, "A")], aligns, 20.0),
            song_asset_id="b" * 64,
            attributed_to="u:x",
        )
    ]
    with use_email("u@x.com"), pytest.raises(ToolError, match="different song"):
        ft.footage_edl_from_annotations("p", annotations=stale)
