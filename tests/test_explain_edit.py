"""The post-mortem of an edit, and the cutting fixes it would otherwise apologise for.

- fades START on the beat (a fade centred on its beat reads as "slightly off");
- the beat grid is carried through quiet passages (no 21 s hold at the end);
- ``explain_edit`` says, per cut, where it lands, how it changes, what picture and
  why, with flags for what is worth a look, and every clock time as a link target.
"""

from __future__ import annotations

import numpy as np
import pytest

from muvid.footage import music_cut, service
from muvid.footage.edl import TRANSITION_SPLIT, EdlEntry, Transition
from muvid.footage.explain import explain
from muvid.footage.music_cut import FreeSource, extend_grid, fill_spans
from muvid.montage.analysis import Analysis, Section


def _analysis(duration=40.0, bpm=80.0, *, beats_until=None):
    beat = 60.0 / bpm
    beats = tuple(float(t) for t in np.arange(0.5, beats_until or duration, beat))
    return Analysis(
        duration=duration, tempo_bpm=bpm, beats=beats, downbeats=beats[::4],
        # sections change on a beat, as the real ones (cut on bars) do
        sections=(Section(label="verse", start=0.0, end=beats[len(beats) // 2], energy_db=-20.0),
                  Section(label="chorus", start=beats[len(beats) // 2], end=duration, energy_db=-10.0)),
        beat_source="test", section_source="test",
    )


def _sources():
    return [FreeSource("v1", "/x/v1.mp4", duration_s=30.0), FreeSource("v2", "/x/v2.mp4", duration_s=30.0),
            FreeSource("p1", "/x/p1.jpg", kind="still")]


def _fill(**kw):
    return fill_spans("/x/s.wav", _sources(), envelope_of=lambda s: None, anchor_of=lambda s: (0.5, 0.5),
                      size_of=lambda p: (1080, 1920), canvas=(1080, 1920), **kw)


def test_fades_start_on_the_beat():
    an = _analysis()
    cut = _fill(analysis=an, archetype="ballad_dissolve")
    beats = np.asarray(an.beats)
    fades = [e for e in cut.entries if e.transition is not None]
    assert fades
    for e in fades:
        start = e.song_start - e.transition.duration_s * TRANSITION_SPLIT
        assert np.min(np.abs(beats - start)) < 1e-3, f"fade at {e.song_start} starts off the beat"


def test_the_beat_is_carried_through_a_quiet_ending():
    an = _analysis(duration=40.0, beats_until=30.0)  # the tracker went silent at 30 s
    extended, note = extend_grid(an, 40.0)
    assert extended.beats[-1] > 39.0 and "carried on" in note
    cut = _fill(analysis=an, archetype="beat_cut")
    assert max(e.song_end - e.song_start for e in cut.entries) < 8.0, "no long hold to the end"
    assert cut.report["grid_note"]


def _clips():
    return {"v1": {"name": "The abbey", "kind": "video"}, "p1": {"name": "The dog", "kind": "still"}}


def test_a_fade_centred_on_its_beat_is_said_and_flagged():
    an = _analysis(bpm=60.0)  # beats at 0.5, 1.5, 2.5 ...
    entries = [
        EdlEntry(0.0, 4.5, "v1", source_in=1.0),
        EdlEntry(4.5, 8.5, "v1", source_in=10.0, transition=Transition(0.6, "fade")),  # centred on 4.5
    ]
    out = explain(entries, analysis=an, song_duration=40.0, clips=_clips())
    cut = out["cuts"][1]
    assert "straddles_the_beat" in cut["flags"]
    assert "before the beat" in cut["text"]
    assert cut["transition"]["starts_s"] == pytest.approx(4.2)
    assert {"label": "0:04.2", "s": 4.2} in cut["times"]
    assert "centred on their beat" in out["summary"]


def test_off_the_beat_long_holds_and_photos_are_named():
    an = _analysis(bpm=60.0)
    entries = [
        EdlEntry(0.0, 1.5, "v1", source_in=0.0),
        EdlEntry(1.5, 2.2, "p1", source_in=0.0, look="null", look_spec={"name": "slow_push"}),
        EdlEntry(2.2, 20.0, "v1", source_in=5.0),  # 2.2 is not a beat; 17.8 s hold
    ]
    out = explain(entries, analysis=an, song_duration=40.0, clips=_clips())
    photo, late = out["cuts"][1], out["cuts"][2]
    assert "The dog pushes in slowly" in photo["text"]
    assert "off_the_beat" in late["flags"] and "long_hold" in late["flags"]
    assert out["cuts"][0]["text"].startswith("The video opens at 0:00.0.")


def test_a_window_keeps_the_cuts_it_overlaps_and_the_whole_summary():
    an = _analysis(bpm=60.0)
    entries = [EdlEntry(float(a), float(a + 2), "v1", source_in=float(a)) for a in range(0, 10, 2)]
    out = explain(entries, analysis=an, song_duration=10.0, clips=_clips(), window=(3.0, 5.0))
    assert [c["index"] for c in out["cuts"]] == [1, 2]
    assert out["summary"].startswith("5 shots")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    from muvid.footage.workspace import FootageWorkspace

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(music_cut, "_default_analysis", lambda path: _analysis(bpm=60.0))
    p = FootageWorkspace.for_email("u@x.com").create_project("bath")
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFF-song")
    service.set_song(p, path=str(song), duration_s=40.0)
    for cid in ("v1", "v2"):
        clip = tmp_path / f"{cid}.mp4"
        clip.write_bytes(cid.encode())
        service.add_clip(p, path=str(clip), clip_id=cid, duration_s=30.0)
        service.set_has_song(p, clip_id=cid, has_song="no")
    return p


def test_explain_edit_and_the_style_choice_through_the_service(proj):
    cuts = service.propose_edit(proj, style="cuts")
    assert not any(e.get("transition") for e in cuts["edl"])
    fades = service.propose_edit(proj, style="fades")
    assert any(e.get("transition") for e in fades["edl"])
    out = service.explain_edit(proj, edit_id=fades["edit_id"])
    assert out["cuts"] and all(c["text"] for c in out["cuts"])
    assert "You chose fades" in out["summary"]
    assert not any("straddles_the_beat" in c["flags"] for c in out["cuts"])
    with pytest.raises(service.FootageError):
        service.propose_edit(proj, style="swirl", save=False)
    assert "explain_edit" in {op.name for op in service.FOOTAGE_OP_SPECS}
