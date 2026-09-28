"""Tests for the beat signals (:mod:`muvid.footage.beats` and ``service.beat_signals``,
thorwhalen/muvid#124).

What is pinned: a signal is continuous and unnormalised with its stats beside it, and
an unmeasured sample is ``None`` (never zero). Pooling keeps peaks. The visual beat is
DECELERATION (motion leaving a direction), so a back-and-forth move peaks at its
turnarounds and not while it glides. Per-pair rates are averaged into bins rather than
sampled, so a fast wobble cannot alias into a slow rhythm. The op measures each piece of
media once, keys the cache on its content, and refuses cleanly.

Nothing here needs librosa: the audio estimator is faked where its result matters. One
test draws a real video with OpenCV and runs the whole visual pass on it.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from muvid.footage import beats as bs

pytest.importorskip("nw")

from muvid.footage import service  # noqa: E402
from muvid.footage.errors import FootageError  # noqa: E402
from muvid.footage.workspace import FootageWorkspace  # noqa: E402

# -- the record ---------------------------------------------------------------------


def test_signal_record_is_continuous_with_stats_and_none_for_unmeasured():
    rec = bs.signal_record(
        [np.nan, 0.5, 2.0, 1.0], t0=0.25, hop_s=0.5, name=bs.MOTION, domain="visual"
    )
    assert rec["values"] == [None, 0.5, 2.0, 1.0]
    assert (rec["min"], rec["max"], rec["n"]) == (0.5, 2.0, 4)
    assert 1.0 < rec["p99"] <= 2.0
    assert rec["label"] == bs.SIGNAL_LABELS[bs.MOTION]
    empty = bs.signal_record([], t0=0, hop_s=1, name="x", domain="audio")
    assert empty["values"] == [] and empty["min"] is None and empty["max"] is None


def test_decimated_pools_by_max_so_a_beat_survives():
    values = [0.0] * 100
    values[37] = 9.0  # one sharp hit
    rec = bs.signal_record(values, t0=0, hop_s=0.01, name="a", domain="audio")
    small = bs.decimated(rec, 10)
    assert small["n"] == 10 and small["hop_s"] == pytest.approx(0.1)
    assert small["values"][3] == 9.0 and max(small["values"]) == 9.0
    # the pooled sample sits at the CENTRE of its block, so the hit stays near 0.37 s
    t_hit = small["t0"] + 3 * small["hop_s"]
    assert small["t0"] == pytest.approx(0.045) and abs(t_hit - 0.37) <= 0.05
    assert (small["min"], small["max"]) == (rec["min"], rec["max"])  # full-res range
    assert small["p99"] > rec["p99"]  # pooled p99: max-pooling raises typical values
    assert bs.decimated(rec, 0) is rec and bs.decimated(rec, 500) is rec


# -- the visual beat -----------------------------------------------------------------


def test_directogram_puts_motion_in_its_direction_bin():
    fx = np.full((10, 10), 2.0)  # everything moves right
    fy = np.zeros((10, 10))
    h = bs.directogram(fx, fy, bins=8)
    assert h.argmax() == 4  # angle 0 sits in the bin right of -pi..pi's middle
    assert h.sum() == pytest.approx(2.0)
    still = bs.directogram(np.full((4, 4), 0.01), np.zeros((4, 4)))
    assert still.sum() == 0.0  # under the noise floor: no direction


def test_impact_is_deceleration_not_acceleration():
    right, left, none = np.eye(4)[0], np.eye(4)[2], np.zeros(4)
    hists = np.stack([none, right, right, left, none, none])
    impact = bs.deceleration_flux(hists)
    assert np.isnan(impact[0])
    # starting to move (none -> right) is not a hit; gliding is not; turning (right ->
    # left) and stopping (left -> none) are.
    assert list(impact[1:]) == [0.0, 0.0, 1.0, 1.0, 0.0]


def test_binning_averages_pairs_so_a_fast_wobble_does_not_alias():
    # 30 pairs a second alternating 0, 2, 0, 2 ... binned at 15 per second: every bin
    # holds one of each, so the binned motion is flat — sampling every other pair would
    # have read all 0s or all 2s.
    mids = (np.arange(90) + 0.5) / 30.0
    motion = np.tile([0.0, 2.0], 45)
    hists = np.zeros((90, 8))
    out = bs.binned_visual_signals(mids, motion, hists, 1 / 15)
    m = out["signals"][bs.MOTION]
    assert m["t0"] == pytest.approx(1 / 30, abs=1e-6) and m["n"] == 45
    assert set(m["values"]) == {1.0}
    # the impact lives on bin BOUNDARIES: sample i at i * hop
    assert out["signals"][bs.VISUAL_IMPACT]["t0"] == 0.0


def test_binning_drops_pairs_before_the_first_frame():
    out = bs.binned_visual_signals(
        np.array([-0.2, 0.1, 0.6]), np.array([9.0, 1.0, 2.0]), np.zeros((3, 8)), 0.5
    )
    assert out["signals"][bs.MOTION]["values"] == [1.0, 2.0]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_visual_pass_on_a_real_video_peaks_at_the_turnarounds(tmp_path):
    cv2 = pytest.importorskip("cv2")
    fps, seconds, period = 30.0, 4.0, 1.0  # a square swings right then left every second
    path = tmp_path / "swing.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (320, 240))
    assert writer.isOpened()
    for i in range(int(fps * seconds)):
        t = i / fps
        phase = (t % period) / period
        x = int(40 + 200 * (1 - abs(2 * phase - 1)))  # triangle wave: turns at 0.5, 1.0
        frame = np.zeros((240, 320, 3), np.uint8)
        frame[90:150, x : x + 60] = (255, 255, 255)
        cv2.rectangle(frame, (0, 0), (319, 239), (80, 80, 80), 6)  # static texture
        writer.write(frame)
    writer.release()

    out = bs.visual_signals(path, sample_fps=10)
    impact = out["signals"][bs.VISUAL_IMPACT]
    motion = out["signals"][bs.MOTION]
    assert motion["n"] == impact["n"] >= 35 and motion["max"] > 0
    t = impact["t0"] + impact["hop_s"] * np.arange(impact["n"])
    v = np.array([np.nan if x is None else x for x in impact["values"]])
    near_turn = np.abs(t / 0.5 - np.round(t / 0.5)) * 0.5 < 0.12  # within 0.12 s
    assert np.nanmean(v[near_turn]) > 3 * np.nanmean(v[~near_turn])


# -- the op -------------------------------------------------------------------------


@pytest.fixture
def calls():
    """What the faked estimators were asked to measure."""
    return SimpleNamespace(audio=[], visual=[])


@pytest.fixture
def fp(tmp_path, monkeypatch, calls):
    """A 30 s song and one video, stored without ffmpeg (so the video has no probeable
    soundtrack); both estimators are faked and count their calls."""
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path / "data"))
    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFF-song")
    service.set_song(proj, path=str(song), duration_s=30.0)
    clip = tmp_path / "A.mp4"
    clip.write_bytes(b"clip-a")
    service.add_clip(proj, path=str(clip), clip_id="A", duration_s=20.0)

    def fake_audio(audio, **kw):
        calls.audio.append(str(audio))
        return SimpleNamespace(
            beat_times=[0.5, 1.0, 1.5],
            downbeat_times=[],
            tempo_bpm=120.0,
            onset_env=np.array([0.0, 3.0, 1.0, 5.0]),
            onset_hop_s=0.5,
        )

    def fake_visual(path, **kw):
        calls.visual.append(str(path))
        return bs.binned_visual_signals(
            np.array([0.1, 0.6, 1.1]), np.array([1.0, 2.0, 3.0]), np.ones((3, 8)), 0.5
        )

    def fake_novelty(path):
        calls.audio.append(f"novelty:{path}")
        return {"signals": {bs.NOVELTY: bs.signal_record([0.0, 1.0, 0.0], t0=0, hop_s=0.1, name=bs.NOVELTY, domain="audio")}}

    monkeypatch.setattr("mixing.audio.beat_grid", fake_audio)
    monkeypatch.setattr(bs, "visual_signals", fake_visual)
    monkeypatch.setattr(bs, "novelty_signal", fake_novelty)
    return proj


def test_song_beat_signals_are_measured_once_and_cached(fp, calls):
    out = service.beat_signals(fp)
    assert out["source"] == "song" and out["kind"] == "audio"
    assert (out["tempo_bpm"], out["beats"]) == (120.0, [0.5, 1.0, 1.5])
    onset = out["signals"][bs.AUDIO_ONSET]
    assert onset["values"] == [0.0, 3.0, 1.0, 5.0] and onset["hop_s"] == 0.5
    assert out["duration_s"] == 30.0
    assert set(out["signals"]) == {bs.AUDIO_ONSET, bs.NOVELTY}  # the song gets its structure too
    assert service.beat_signals(fp) == out
    assert len(calls.audio) == 2  # onset + novelty once each; the second call was a file read
    cached = list((fp.root / "beats").glob("*-audio-*.json"))
    assert len(cached) == 1 and cached[0].name.startswith(fp.song_hash()[:16])


def test_a_video_gets_visual_signals_and_no_sound_when_it_has_none(fp, calls):
    out = service.beat_signals(fp, source="A")
    assert out["kind"] == "video" and set(out["signals"]) == {bs.MOTION, bs.VISUAL_IMPACT}
    assert out["beats"] == [] and out["tempo_bpm"] is None
    assert out["signals"][bs.MOTION]["values"] == [1.0, 2.0, 3.0]
    assert calls.audio == []  # an unprobeable file has no soundtrack to measure
    service.beat_signals(fp, source="A")
    assert len(calls.visual) == 1


def test_a_video_with_a_soundtrack_gets_its_own_audio_onset(fp, calls, monkeypatch):
    monkeypatch.setattr(bs, "has_audio", lambda path: True)
    out = service.beat_signals(fp, source="A")
    assert set(out["signals"]) == {bs.AUDIO_ONSET, bs.MOTION, bs.VISUAL_IMPACT}
    assert out["beats"] == [0.5, 1.0, 1.5]
    assert calls.audio == [str(fp.clip_paths()["A"])]


def test_max_points_pools_every_signal(fp):
    out = service.beat_signals(fp, max_points=2)
    assert out["signals"][bs.AUDIO_ONSET]["values"] == [3.0, 5.0]
    assert out["signals"][bs.NOVELTY]["values"] == [1.0, 0.0]


def test_refusals(fp, monkeypatch):
    with pytest.raises(FootageError, match="unknown clip_id"):
        service.beat_signals(fp, source="zz")
    with pytest.raises(FootageError, match="max_points"):
        service.beat_signals(fp, max_points=1)

    def missing(audio, **kw):
        raise ImportError("No module named 'librosa'")

    monkeypatch.setattr("mixing.audio.beat_grid", missing)
    with pytest.raises(FootageError, match=r"muvid\[scoring\]"):
        service.beat_signals(fp)


def test_a_video_nothing_could_be_read_from_is_refused_and_not_remembered(fp, calls, monkeypatch):
    def empty(path, **kw):
        calls.visual.append(str(path))
        return bs.binned_visual_signals(np.array([]), np.array([]), np.zeros((0, 8)), 0.5)

    monkeypatch.setattr(bs, "visual_signals", empty)
    for _ in range(2):
        with pytest.raises(FootageError, match="nothing in it could be read"):
            service.beat_signals(fp, source="A")
    assert len(calls.visual) == 2  # measured again, not remembered as silence
    assert not list((fp.root / "beats").glob("*-video-*.json"))


def test_an_unreadable_video_is_a_refusal_naming_it(fp, monkeypatch):
    def corrupt(path, **kw):
        raise ValueError("cannot open video")

    monkeypatch.setattr(bs, "visual_signals", corrupt)
    with pytest.raises(FootageError, match="could not read video 'A'"):
        service.beat_signals(fp, source="A")


def test_reading_a_videos_beat_never_writes_the_manifest(fp):
    before = fp.manifest()
    service.beat_signals(fp, source="A")
    assert fp.manifest() == before  # a read op; add_clip may be writing it right now


def test_the_cache_key_names_the_estimator_versions():
    assert "mix" in bs.cache_key("audio") and "lr" in bs.cache_key("audio")
    assert bs.cache_key("audio") != bs.cache_key("video")


def test_beat_signals_is_a_read_op_in_the_catalogue():
    spec = next(s for s in service.FOOTAGE_OP_SPECS if s.name == "beat_signals")
    assert (spec.effect, spec.runs) == ("read", "now")


# -- the research follow-up: tempo, structure, region deceleration --------------------


def test_fitted_tempo_is_the_beats_own_not_the_median_interval():
    # Beats that run a touch fast (0.464 s) and skip one now and then, over a true
    # 0.473 s period — librosa's median-interval tempo reads 129.3; the beats fit 126.9.
    true_p = 0.4729
    rng = np.random.default_rng(0)
    beats, t = [], 0.3
    for i in range(300):
        beats.append(t + rng.normal(0, 0.01))
        t += true_p
    beats = np.array(beats)
    assert bs.fitted_tempo(beats) == pytest.approx(60 / true_p, abs=0.1)
    skipped = np.delete(beats, [50, 120, 121, 200])  # dropped beats must not bias it
    assert bs.fitted_tempo(skipped) == pytest.approx(60 / true_p, abs=0.1)
    assert bs.fitted_tempo([0.5, 1.0]) is None


def test_checkerboard_novelty_peaks_at_a_section_change():
    # Two "sections": features constant in each, different between them.
    a = np.tile(np.array([[1.0], [0.0], [0.5]]), (1, 60))
    b = np.tile(np.array([[0.0], [1.0], [0.5]]), (1, 60))
    rng = np.random.default_rng(1)
    feats = np.hstack([a, b]) + rng.normal(0, 0.05, (3, 120))
    nov = bs.checkerboard_novelty(feats, half=10)
    assert np.nanargmax(nov) == pytest.approx(60, abs=2)
    assert np.isnan(nov[:10]).all() and np.isnan(nov[-10:]).all()  # no full kernel at the ends


def test_region_speeds_and_region_impact_brake_per_cell():
    fx = np.zeros((12, 16))
    fx[:6, :8] = 2.0  # only the top-left quarter moves
    speeds = bs.region_speeds(fx, np.zeros_like(fx), grid=(2, 2))
    assert list(speeds) == [2.0, 0.0, 0.0, 0.0]
    # Two dancers braking in DIFFERENT places both count (a whole-frame mean would
    # let one's braking cancel the other's starting).
    cells = np.array([[1.0, 0.0], [0.0, 1.0]])
    assert list(bs.deceleration_flux(cells)[1:]) == [1.0]


def test_the_visual_pass_reports_region_impact(tmp_path):
    cv2 = pytest.importorskip("cv2")
    path = tmp_path / "still.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30.0, (160, 120))
    for i in range(45):
        frame = np.zeros((120, 160, 3), np.uint8)
        frame[40:80, 10 + 2 * i : 40 + 2 * i] = 255
        writer.write(frame)
    writer.release()
    out = bs.visual_signals(path, sample_fps=10)
    assert set(out["signals"]) == {bs.MOTION, bs.VISUAL_IMPACT, bs.REGION_IMPACT}
    assert out["signals"][bs.REGION_IMPACT]["t0"] == 0.0
