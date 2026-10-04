"""Footage that does not contain the song: free cuts, photos, and cutting to the music.

Pins the three layers of the "montage" half of a music video:

- the edit model (:mod:`muvid.footage.edl`): a FREE cut (``source_in`` set) reads its
  clip from its own in-point, needs no alignment and is never refused as unvouched,
  while still being checked for containment;
- the planner bridge (:mod:`muvid.footage.music_cut`): free cuts cover exactly the
  spans asked for, on the beat grid, inside each video, choosing the stretch whose
  picture changes land on the beats;
- the service (:mod:`muvid.footage.service`): photos are accepted, each clip gets a
  role (synced / cut to the music) from listening or from ``set_has_song``, and
  ``propose_edit`` fills every span the synced clips leave — Noel's "Bath" case, where
  nothing could be synced, is a whole song cut to the music instead of mostly black.

The edit-level and service tests need no ffmpeg (the song analysis is injected);
one end-to-end test decodes and renders, marked ``needs_ffmpeg``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from muvid.footage import music_cut, service
from muvid.footage.edl import (
    STILL_DURATION_S,
    EdlEntry,
    UnreliableAlignmentError,
    clip_in_of,
    derive_cuts,
    unplaced,
    validate_edl,
    with_start,
)
from muvid.footage.music_cut import Envelope, FreeSource, fill_spans
from muvid.montage.analysis import Analysis, Section

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg + ffprobe")


def _analysis(duration: float = 40.0, bpm: float = 120.0) -> Analysis:
    beat = 60.0 / bpm
    beats = tuple(float(t) for t in np.arange(0.0, duration, beat))
    return Analysis(
        duration=duration,
        tempo_bpm=bpm,
        beats=beats,
        downbeats=beats[::4],
        sections=(
            Section(label="verse", start=0.0, end=duration / 2, energy_db=-20.0),
            Section(label="chorus", start=duration / 2, end=duration, energy_db=-10.0),
        ),
        beat_source="test",
        section_source="test",
    )


# -- the edit model -------------------------------------------------------------------


def test_free_cut_needs_no_alignment_and_is_not_refused():
    a = unplaced("c", 10.0)  # never placed: unvouched
    e = EdlEntry(song_start=30.0, song_end=34.0, clip_id="c", source_in=2.0)
    [v] = validate_edl([e], [a], 34.0 + 0.0, allow_unreliable=False)[-1:]
    assert v.is_free and clip_in_of(v, a) == 2.0
    [cut] = derive_cuts([v], [a], {"c": "/x/c.mp4"})[-1:]
    assert cut.clip_in == 2.0


def test_anchored_cut_to_the_same_unplaced_clip_is_still_refused():
    a = unplaced("c", 10.0)
    with pytest.raises((UnreliableAlignmentError, ValueError)):
        validate_edl([EdlEntry(0.0, 4.0, "c")], [a], 4.0)


def test_free_cut_past_the_end_of_its_clip_is_refused():
    a = unplaced("c", 10.0)
    with pytest.raises(ValueError, match="does not contain"):
        validate_edl([EdlEntry(0.0, 4.0, "c", source_in=7.0)], [a], 4.0)


def test_free_cut_moves_its_in_point_not_a_slip_when_its_start_moves():
    e = EdlEntry(10.0, 14.0, "c", source_in=2.0)
    moved = with_start(e, 11.0)
    assert moved.source_in == pytest.approx(3.0) and moved.slip_s == 0.0


def test_a_gap_cannot_carry_an_in_point():
    with pytest.raises(ValueError, match="gap"):
        validate_edl(
            [{"song_start": 0.0, "song_end": 4.0, "clip_id": None, "source_in": 1.0}],
            [],
            4.0,
        )


def test_free_cut_round_trips_through_json():
    e = EdlEntry(0.0, 4.0, "c", source_in=2.5)
    wire = service.edl_json(e)
    assert wire["source_in"] == 2.5
    [back] = validate_edl([wire], [unplaced("c", 10.0)], 4.0)
    assert back == e


# -- the planner bridge ----------------------------------------------------------------


def test_choose_source_in_lands_picture_changes_on_the_beats():
    hits = np.zeros(200)
    hits[[63, 73, 83]] = 1.0  # changes at 6.3, 7.3, 8.3 s
    env = Envelope(hop_s=0.1, activity=np.full(200, 0.1), hits=hits)
    s, fit = music_cut.choose_source_in(
        env, clip_duration=20.0, length=3.0, beat_offsets=[0.5, 1.5, 2.5]
    )
    assert s == pytest.approx(5.8) and fit > 1


def test_choose_source_in_avoids_what_was_already_shown():
    env = Envelope(hop_s=0.1, activity=np.full(100, 0.1), hits=np.zeros(100))
    s, _ = music_cut.choose_source_in(
        env, clip_duration=10.0, length=2.0, used=[(0.0, 6.0)]
    )
    assert s >= 6.0 - 1e-9


def _sources():
    return [
        FreeSource("long", "/x/long.mp4", duration_s=30.0),
        FreeSource("short", "/x/short.mp4", duration_s=3.0),
        FreeSource("mid", "/x/mid.mp4", duration_s=8.0),
        FreeSource("pic", "/x/pic.jpg", kind="still"),
    ]


def _fill(spans=None, **kw):
    return fill_spans(
        "/x/song.wav",
        _sources(),
        spans=spans,
        analysis=_analysis(),
        envelope_of=lambda s: None,
        anchor_of=lambda s: (0.5, 0.5),
        size_of=lambda p: (1080, 1920),
        canvas=(1080, 1920),
        **kw,
    )


def test_fill_spans_covers_the_song_with_free_cuts_inside_each_video():
    cut = _fill()
    entries = cut.entries
    assert entries[0].song_start == 0.0 and entries[-1].song_end == pytest.approx(40.0)
    for a, b in zip(entries, entries[1:]):
        assert a.song_end == pytest.approx(b.song_start)
        assert a.clip_id != b.clip_id, "a take-over must not repeat its neighbour"
    by_id = {s.clip_id: s for s in _sources()}
    for e in entries:
        assert e.is_free
        src = by_id[e.clip_id]
        if not src.is_still:
            assert e.source_in + (e.song_end - e.song_start) <= src.duration_s + 1e-6
        else:
            assert e.look and e.look_spec["name"] in music_cut.STILL_MOVES + ("punch_in",)
    placements = [unplaced(s.clip_id, s.duration_s or STILL_DURATION_S) for s in _sources()]
    validate_edl(entries, placements, 40.0, canvas=(1080, 1920))  # the gate agrees
    assert cut.report["style"] == "beat_cut"  # 120 bpm


def test_fill_spans_fills_only_the_spans_it_is_given():
    entries = _fill(spans=[(4.0, 12.0), (20.0, 26.0)]).entries
    covered = sum(e.song_end - e.song_start for e in entries)
    assert covered == pytest.approx(14.0)
    assert all(4.0 - 1e-6 <= e.song_start and e.song_end <= 26.0 + 1e-6 for e in entries)
    assert not any(12.0 < e.song_start < 20.0 for e in entries)


def test_cover_crop_follows_the_shown_shape_not_the_stored_one():
    landscape_on_portrait = music_cut.cover_crop((1920, 1080), (1080, 1920))
    assert landscape_on_portrait.h == 1.0 and landscape_on_portrait.w < 0.35
    assert music_cut.cover_crop((360, 640), (1080, 1920)) is None


# -- the service ----------------------------------------------------------------------


@pytest.fixture
def bath(tmp_path, monkeypatch):
    """Noel's case in miniature: a song, two videos nobody could place, one photo."""
    from muvid.footage.workspace import FootageWorkspace

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(music_cut, "_default_analysis", lambda path: _analysis())
    proj = FootageWorkspace.for_email("u@x.com").create_project("bath")
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFF-song")
    service.set_song(proj, path=str(song), duration_s=40.0)
    for cid, dur in (("abbey", 12.0), ("dog", 6.0)):
        clip = tmp_path / f"{cid}.mp4"
        clip.write_bytes(cid.encode())
        service.add_clip(proj, path=str(clip), clip_id=cid, duration_s=dur)
    # what listening found on the real shoot: no song in either (support 0.03-0.33)
    proj.save_alignments([_heard(cid, dur, support=0.2) for cid, dur in (("abbey", 12.0), ("dog", 6.0))])
    return proj


def _heard(cid, dur, *, support, reliable=False, offset=3.0, margin=None):
    from muvid.footage.edl import FootageAlignment

    return FootageAlignment(
        clip_id=cid, offset_s=offset, confidence=0.1, duration_s=dur,
        coverage=(offset, offset + dur), overlaps=True, support=support,
        reliable=reliable,
        margin=margin if margin is not None else (-0.2 if not reliable else 0.3),
    )


def test_a_project_nothing_could_be_synced_in_is_cut_to_the_music(bath):
    assert service.footage_roles(bath) == {"abbey": "to_the_music", "dog": "to_the_music"}
    out = service.propose_edit(bath)
    edl = out["edl"]
    assert all(e["clip_id"] for e in edl), "no black: the whole song is cut to the music"
    assert all("source_in" in e for e in edl)
    assert out["music"]["n_cuts"] == len(edl)
    assert out["assemble_refusal"] is None
    assert "cut to the music" in service.get_edit(bath, edit_id=out["edit_id"])["how_made"]


def test_a_synced_clip_keeps_its_place_and_the_rest_is_cut_to_the_music(bath):
    service.set_offset(bath, clip_id="abbey", offset_s=10.0)  # declared: synced
    assert service.footage_roles(bath)["abbey"] == "synced"
    edl = service.propose_edit(bath)["edl"]
    synced = [e for e in edl if e["clip_id"] == "abbey" and "source_in" not in e]
    assert synced and synced[0]["song_start"] == pytest.approx(10.0)
    free = [e for e in edl if "source_in" in e]
    assert free and all(e["clip_id"] == "dog" for e in free)
    assert all(e["clip_id"] for e in edl)


def test_set_has_song_overrides_the_listening(bath):
    service.set_offset(bath, clip_id="abbey", offset_s=10.0)
    reply = service.set_has_song(bath, clip_id="abbey", has_song="no")
    assert reply["role"] == "to_the_music"
    assert not any(
        e["clip_id"] == "abbey" and "source_in" not in e
        for e in service.propose_edit(bath, save=False)["edl"]
    )
    assert service.set_has_song(bath, clip_id="abbey", has_song="auto")["role"] == "synced"
    with pytest.raises(service.FootageError):
        service.set_has_song(bath, clip_id="abbey", has_song="maybe")


def test_set_has_song_is_in_the_catalogue():
    assert "set_has_song" in {op.name for op in service.FOOTAGE_OP_SPECS}


def test_pace_changes_how_often_the_music_cuts_come(bath):
    slow = service.propose_edit(bath, pace="slow", save=False)["music"]["n_cuts"]
    driving = service.propose_edit(bath, pace="driving", save=False)["music"]["n_cuts"]
    assert driving > slow


# -- end to end: real media -----------------------------------------------------------


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@needs_ffmpeg
def test_photos_and_unsynced_videos_render_over_the_song(tmp_path, monkeypatch):
    from muvid.footage.workspace import FootageWorkspace

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path / "data"))
    proj = FootageWorkspace.for_email("u@x.com").create_project("e2e")
    song = tmp_path / "song.wav"
    # a click track at 120 bpm, so there is a beat to cut on
    _ffmpeg("-f", "lavfi", "-i",
            "aevalsrc='0.8*sin(2*PI*880*t)*lt(mod(t,0.5),0.03)':s=22050:d=8", str(song))
    clip = tmp_path / "walk.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=6",
            "-f", "lavfi", "-i", "anoisesrc=d=6:a=0.3", "-shortest", "-pix_fmt",
            "yuv420p", str(clip))
    photo = tmp_path / "abbey.png"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=400x300:duration=1", "-frames:v", "1",
            str(photo))
    service.set_song(proj, path=str(song))
    service.add_clip(proj, path=str(clip), filename="walk.mp4")
    added = service.add_clip(proj, path=str(photo), filename="abbey.png")
    assert added["kind"] == "still" and added["duration"] is None
    assert [c.get("kind") for c in proj.list_clips()] == [None, "still"]
    service.align(proj)  # noise has no song in it: not placed confidently
    assert set(service.footage_roles(proj).values()) == {"to_the_music"}
    out = service.propose_edit(proj)
    assert all(e["clip_id"] for e in out["edl"])
    assert {e["clip_id"] for e in out["edl"]} == set(service.footage_roles(proj))
    meta = service.render(proj, edit_id=out["edit_id"])
    video = proj.root / "renders" / meta["render_id"] / "final.mp4"
    assert video.is_file()
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
         str(video)], capture_output=True, check=True, text=True).stdout)
    assert float(probe["format"]["duration"]) == pytest.approx(8.0, abs=0.3)


def test_a_video_nobody_listened_to_is_not_guessed_about(bath):
    """A concert cut to the music is a desynced concert (muvid#59 by another road)."""
    bath.save_alignments([_heard("abbey", 12.0, support=0.2)])  # dog: never listened to
    assert service.footage_roles(bath)["dog"] == "not_listened"
    with pytest.raises(service.FootageError, match="not been listened to"):
        service.propose_edit(bath, save=False)
    service.set_has_song(bath, clip_id="dog", has_song="no")  # or say what it is
    assert all(e["clip_id"] for e in service.propose_edit(bath, save=False)["edl"])


def test_a_song_heard_but_not_placed_stays_synced_and_is_set_aside(bath):
    """The muvid#59 case: the song is there, its place ambiguous. Not montaged; its
    span is set aside (and filled from the footage that IS cut to the music)."""
    bath.save_alignments(
        [_heard("abbey", 12.0, support=0.45, margin=0.1), _heard("dog", 6.0, support=0.5, margin=0.0)]
    )
    assert service.footage_roles(bath) == {"abbey": "synced", "dog": "to_the_music"}
    out = service.propose_edit(bath, save=False)
    assert not any(e["clip_id"] == "abbey" for e in out["edl"])
    assert [x["clip_id"] for x in out["coverage"]["excluded"]] == ["abbey"]
    assert all(e["clip_id"] == "dog" for e in out["edl"])


def test_swapping_a_cut_onto_a_clip_cut_to_the_music_starts_it_at_its_start(bath):
    out = service.propose_edit(bath)
    edl = service.get_edit(bath, edit_id=out["edit_id"])["edl"]
    i, e = next((i, e) for i, e in enumerate(edl) if e["clip_id"] == "abbey" and not e.get("transition"))
    reply = service.set_cut(bath, edit_id=out["edit_id"], index=i, clip_id="dog")
    swapped = reply["edl"][i]
    assert swapped["clip_id"] == "dog" and 0.0 <= swapped["source_in"] <= 1.0


@pytest.mark.parametrize("seed", range(150))
def test_planned_cuts_always_pass_the_edit_gate(seed):
    """Random pools, tempos and spans: what the planner emits is a valid edit (the
    adversarial review's fuzz, kept)."""
    import random

    from muvid.footage.edl import fill_gaps

    r = random.Random(seed)
    d, bpm = r.uniform(2, 120), r.uniform(55, 150)
    srcs = [
        FreeSource(f"v{i}", f"/x/v{i}.mp4", duration_s=r.choice([r.uniform(0.6, 4), r.uniform(3, 40)]))
        for i in range(r.randint(1, 6))
    ]
    if r.random() < 0.4:
        srcs += [FreeSource(f"p{i}", f"/x/p{i}.jpg", kind="still") for i in range(r.randint(1, 3))]
    spans = [(0, d * 0.3), (d * 0.5, d * 0.8)] if r.random() < 0.3 else None
    cut = fill_spans(
        "/x/s.wav", srcs, spans=spans, analysis=_analysis(d, bpm),
        envelope_of=lambda s: None, anchor_of=lambda s: (0.5, 0.5),
        size_of=lambda p: (1080, 1920), canvas=(1080, 1920),
    )
    if not cut.entries:  # only a sub-second video: nothing can be shown, and it says so
        assert all((s.duration_s or 99) < 1.0 for s in srcs)
        return
    places = [unplaced(s.clip_id, s.duration_s or STILL_DURATION_S) for s in srcs]
    validate_edl(fill_gaps(cut.entries, d), places, d, canvas=(1080, 1920))


def test_a_dissolve_into_a_photo_passes_the_gate():
    srcs = [FreeSource("p1", "/x/p1.jpg", kind="still"), FreeSource("p2", "/x/p2.jpg", kind="still")]
    cut = fill_spans(
        "/x/s.wav", srcs, analysis=_analysis(30.0, 70.0), envelope_of=lambda s: None,
        anchor_of=lambda s: (0.5, 0.5), size_of=lambda p: (1080, 1920), canvas=(1080, 1920),
    )
    assert cut.report["style"] == "ballad_dissolve"
    assert any(e.transition for e in cut.entries), "the slow song keeps its dissolves"
    places = [unplaced(s.clip_id, STILL_DURATION_S) for s in srcs]
    validate_edl(cut.entries, places, 30.0, canvas=(1080, 1920))


def test_a_heic_photo_is_refused_plainly_without_its_decoder(bath, tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_heif(name, *a, **k):
        if name == "pillow_heif":
            raise ImportError("no pillow_heif")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_heif)
    heic = tmp_path / "IMG_0001.HEIC"
    heic.write_bytes(b"not really")
    with pytest.raises(service.FootageError, match="HEIC"):
        service.add_clip(bath, path=str(heic), filename=heic.name)
