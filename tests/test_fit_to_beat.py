"""Fitting cuts to the beat (``muvid.footage.beat_fit`` and ``service.fit_to_beat``).

Pinned: a synthetic dancer whose accents are a known slip and speed off the beat is
recovered to within one search step, with a high z; the same search on accents that
ignore the beat stays under the threshold (the search's own freedom is in the null);
and the op applies only confident fits, as ONE step of the edit's history, reporting
every cut it looked at with its reason.
"""

from __future__ import annotations

import numpy as np
import pytest

nw = pytest.importorskip("nw")

from muvid.footage import beats as bs  # noqa: E402
from muvid.footage import service  # noqa: E402
from muvid.footage.beat_fit import BeatGrid, fit_cut  # noqa: E402
from muvid.footage.workspace import FootageWorkspace  # noqa: E402

PERIOD = 0.5  # 120 BPM
HOP = 1.0 / 15.0


def _accents(*, offset, song_start, slip, rate, seconds=60.0, noise=0.0, seed=1):
    """A clip's accent signal whose accents fall on the song's beats when the cut at
    ``song_start`` is read with ``(slip, rate)`` — i.e. the dancer is ``slip`` late and
    ``rate`` fast relative to the clip's alignment."""
    tau = np.arange(0.0, seconds, HOP)
    clip_in = song_start - offset + slip
    beats = np.arange(0.0, 100.0, PERIOD)
    peaks = clip_in + rate * (beats - song_start)
    v = np.zeros_like(tau)
    for p in peaks:
        v += np.exp(-0.5 * ((tau - p) / 0.04) ** 2)
    rng = np.random.default_rng(seed)
    return tau, v + noise * rng.random(tau.size)


def test_a_known_slip_and_speed_are_recovered():
    tau, v = _accents(offset=2.0, song_start=10.0, slip=0.1, rate=1.04, noise=0.2)
    fit = fit_cut(
        tau, v, song_start=10.0, song_end=16.0, offset=2.0, grid=BeatGrid(PERIOD, 0.0), null_draws=60
    )
    assert fit.slip_s == pytest.approx(0.1, abs=1 / 60 + 1e-9)
    assert fit.rate == pytest.approx(1.04, abs=0.01 + 1e-9)
    assert fit.score > 0.8 and fit.z > 4
    assert fit.current_score < fit.score  # the unfitted timing is off the beat


def test_accents_that_ignore_the_beat_do_not_pass_the_threshold():
    rng = np.random.default_rng(7)
    tau = np.arange(0.0, 60.0, HOP)
    # Accents at random moments: as bursty as a dancer, locked to nothing.
    v = np.zeros_like(tau)
    for p in rng.uniform(0, 60, 120):
        v += np.exp(-0.5 * ((tau - p) / 0.04) ** 2)
    zs = [
        fit_cut(tau, v, song_start=s, song_end=s + 4.0, offset=0.0, grid=BeatGrid(PERIOD, 0.0), null_draws=60, seed=s).z
        for s in (8, 20, 32, 44)
    ]
    assert max(zs) < service.FIT_MIN_Z


def test_the_fitted_grid_is_the_least_squares_line_through_the_beats():
    beats = [0.02 + 0.5 * k for k in range(40)]
    beats.insert(10, beats[9] + 0.2)  # a spurious extra beat does not bend it
    g = BeatGrid.fitted(beats)
    assert g.period == pytest.approx(0.5, abs=1e-6) and g.phase == pytest.approx(0.02, abs=1e-6)
    assert BeatGrid.fitted([0.1, 0.6]) is None  # too few


# -- the op ---------------------------------------------------------------------------


@pytest.fixture
def fp(tmp_path, monkeypatch):
    """A 30 s song at 120 BPM; clip A placed at offset 2 whose dancer is 0.1 s late and
    4 % fast from song time 10; clip B whose movement ignores the beat."""
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path / "data"))
    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFF-song")
    service.set_song(proj, path=str(song), duration_s=30.0)
    for cid in ("A", "B"):
        clip = tmp_path / f"{cid}.mp4"
        clip.write_bytes(f"clip-{cid}".encode())
        service.add_clip(proj, path=str(clip), clip_id=cid, duration_s=40.0)
    service.set_offset(proj, clip_id="A", offset_s=-2.0)
    service.set_offset(proj, clip_id="B", offset_s=-2.0)
    grid = {"beats": [0.5 * k for k in range(60)]}
    monkeypatch.setattr(service, "beat_grid", lambda fp: grid)
    tau_a, v_a = _accents(offset=-2.0, song_start=10.0, slip=0.1, rate=1.04, seconds=40.0, noise=0.2)
    rng = np.random.default_rng(3)
    tau_b = np.arange(0.0, 40.0, HOP)
    v_b = np.zeros_like(tau_b)
    for p in rng.uniform(0, 40, 80):
        v_b += np.exp(-0.5 * ((tau_b - p) / 0.04) ** 2)

    def signals(fp, *, source, max_points=0):
        tau, v = {"A": (tau_a, v_a), "B": (tau_b, v_b)}[source]
        rec = bs.signal_record(v, t0=float(tau[0]), hop_s=HOP, name=bs.MOTION_STOPS, domain="visual")
        return {"signals": {bs.MOTION_STOPS: rec}}

    monkeypatch.setattr(service, "beat_signals", signals)
    service.save_edit(
        proj,
        edl=[
            {"song_start": 0.0, "song_end": 10.0, "clip_id": "B"},
            {"song_start": 10.0, "song_end": 16.0, "clip_id": "A"},
            {"song_start": 16.0, "song_end": 17.0, "clip_id": "A"},
            {"song_start": 17.0, "song_end": 21.0, "clip_id": "B"},
            {"song_start": 21.0, "song_end": 30.0, "clip_id": None},
        ],
        edit_id="e",
    )
    return proj


def test_the_op_fits_the_confident_cut_and_leaves_the_rest_with_reasons(fp, monkeypatch):
    monkeypatch.setattr("muvid.footage.beat_fit.FIT_NULL_DRAWS", 60)
    out = service.fit_to_beat(fp, edit_id="e")
    rows = {r["index"]: r for r in out["fit"]["cuts"]}
    assert rows[1]["applied"] and rows[1]["reason"] == "fitted"
    assert rows[1]["slip_s"] == pytest.approx(0.1, abs=0.02) and rows[1]["rate"] == pytest.approx(1.04, abs=0.011)
    assert out["edl"][1]["rate"] == rows[1]["rate"]
    assert rows[2]["reason"].startswith("shorter than three beats")
    assert not rows[3]["applied"] and "clearly enough" in rows[3]["reason"]
    assert 4 not in rows  # a gap is not one of the video's cuts
    assert out["fit"]["fitted"] == 1 and out["fit"]["kept"] == 3  # cuts 0, 2, 3
    asked = service.fit_to_beat(fp, edit_id="e", indices=[4], apply=False)
    assert asked["fit"]["cuts"][0]["reason"].startswith("a gap")


def test_by_default_only_the_cuts_inside_the_span_are_looked_at(fp, monkeypatch):
    monkeypatch.setattr("muvid.footage.beat_fit.FIT_NULL_DRAWS", 20)
    service.set_span(fp, edit_id="e", start_s=11.0, end_s=16.5)
    out = service.fit_to_beat(fp, edit_id="e", apply=False)
    assert [r["index"] for r in out["fit"]["cuts"]] == [1, 2]


def test_the_fit_is_one_undo_step_and_a_dry_run_changes_nothing(fp, monkeypatch):
    monkeypatch.setattr("muvid.footage.beat_fit.FIT_NULL_DRAWS", 40)
    before = service.get_edit(fp, edit_id="e")["edl"]
    dry = service.fit_to_beat(fp, edit_id="e", apply=False)
    assert dry["edl"] == before and dry["fit"]["fitted"] == 0
    assert [r["reason"] for r in dry["fit"]["cuts"] if r["index"] == 1] == ["would fit"]
    service.fit_to_beat(fp, edit_id="e", indices=[1])
    assert service.get_edit(fp, edit_id="e")["edl"] != before
    assert service.undo_edit(fp, edit_id="e")["edl"] == before


def test_a_song_without_a_steady_beat_is_refused(fp, monkeypatch):
    monkeypatch.setattr(service, "beat_grid", lambda fp: {"beats": [0.1, 0.9, 1.2]})
    with pytest.raises(service.FootageError, match="no steady beat"):
        service.fit_to_beat(fp, edit_id="e")
