"""Gated tests for the extractors + orchestrator.

Import-safety always runs. The end-to-end extractor tests need ffmpeg + cv2 + librosa (the
core torch-free tier); they ``importorskip`` those so the base env skips cleanly. The
lip-sync tier (demucs + syncnet) is NOT exercised here (off by default, deps absent) — only
its skip-cleanly behavior is checked.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys

import numpy as np
import pytest

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAS_CV2 = importlib.util.find_spec("cv2") is not None
HAS_LIBROSA = importlib.util.find_spec("librosa") is not None
core = pytest.mark.skipif(
    not (HAS_FFMPEG and HAS_CV2 and HAS_LIBROSA),
    reason="needs ffmpeg + cv2 + librosa (muvid[scoring] core tier)",
)
SR = 44100


# -- import safety -----------------------------------------------------------


def test_import_scoring_package_is_light():
    code = (
        "import sys, muvid.footage.scoring; "
        "heavy=[m for m in ('cv2','mediapipe','librosa','torch','numpy') if m in sys.modules]; "
        "print(heavy); assert not heavy, heavy"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_lipsync_skips_cleanly_without_deps():
    from muvid.footage.scoring.lipsync import lipsync_available, lipsync_tracks

    ok, reason = lipsync_available()
    assert isinstance(ok, bool) and isinstance(reason, str)
    if not ok:  # the expected case here (no demucs/syncnet/weights)
        out = lipsync_tracks(
            "/nonexistent.mp4",
            clip_id="A",
            offset_s=0.0,
            duration_s=5.0,
            coverage=(0.0, 5.0),
            vocal_stem_path="/nonexistent.wav",
            t0=0.0,
            hop_s=0.1,
            n=50,
        )
        assert out == []


def test_list_available_extractors_shape():
    from muvid.footage.scoring.orchestrator import list_available_extractors

    info = list_available_extractors()
    assert set(info) >= {"quality", "motion_beat", "segment", "lipsync"}
    assert isinstance(info["lipsync"], dict) and "available" in info["lipsync"]


@pytest.mark.skipif(not HAS_CV2, reason="needs cv2")
def test_exposure_penalizes_flat_gray():
    from muvid.footage.scoring._frame_metrics import exposure_quality

    flat = np.full((64, 64), 128, dtype=np.uint8)  # mid-gray, nothing clipped
    contrasty = np.zeros((64, 64), dtype=np.uint8)
    contrasty[:, 32:] = 200  # a well-exposed high-contrast frame
    assert exposure_quality(flat) < 0.2  # flat frame is NOT ~1.0
    assert exposure_quality(contrasty) > exposure_quality(flat)


def test_stability_shake_is_jitter_not_pan_magnitude():
    # A steady constant-velocity pan (large, CONSTANT global motion) must read as STABLE
    # (low jitter), while a randomly-jerking camera reads as unstable.
    from muvid.footage.scoring.frames import FramePass
    from muvid.footage.scoring.quality import quality_tracks
    from muvid.footage.scoring.grid import grid_len

    k = 40
    times = np.arange(k) * 0.2
    pan_dx = np.full(k, 5.0)  # constant 5 px/frame pan → large magnitude, zero jitter
    pan_dx[0] = np.nan
    jerky_dx = np.array([np.nan] + list(np.random.default_rng(0).normal(0, 5, k - 1)))
    zeros = np.zeros(k)

    def _fp(gdx):
        return FramePass(
            clip_times=times, sharpness=np.ones(k), exposure=np.ones(k), face=zeros,
            motion_residual=np.r_[np.nan, np.ones(k - 1)], global_dx=gdx,
            global_dy=np.r_[np.nan, np.zeros(k - 1)], fps=25.0, n_sampled=k,
        )

    n = grid_len(times[-1] + 1, 0.1)
    steady = {t.metric: t for t in quality_tracks(_fp(pan_dx), clip_id="s", offset_s=0.0, t0=0.0, hop_s=0.1, n=n)}["stability_shake"]
    jerky = {t.metric: t for t in quality_tracks(_fp(jerky_dx), clip_id="j", offset_s=0.0, t0=0.0, hop_s=0.1, n=n)}["stability_shake"]
    # raw stability_shake = jitter (lower_better): a steady pan's raw jitter ≈ 0 << jerky's.
    assert np.nanmean(steady.raw_values) < np.nanmean(jerky.raw_values)


# -- synthetic media ---------------------------------------------------------


def _song(tmp_path, dur=6.0):
    from scipy.io import wavfile

    t = np.arange(int(dur * SR)) / SR
    x = np.zeros_like(t)
    for f0, f1 in [(180, 520), (440, 130), (700, 900)]:
        x += np.sin(2 * np.pi * (f0 + (f1 - f0) * (t / t[-1])) * t)
    # a strong 2 Hz pulse so librosa finds beats
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
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"{pattern}=size=320x240:rate=25:duration={b - a}",
            "-i", str(awav), "-shortest", "-pix_fmt", "yuv420p", str(out),
        ],
        check=True,
    )
    return out


# -- frame pass + quality + motionbeat ---------------------------------------


@core
def test_frame_pass_and_quality_tracks(tmp_path):
    from muvid.footage.scoring.frames import sample_clip_frames
    from muvid.footage.scoring.quality import quality_tracks
    from muvid.footage.scoring.grid import grid_len

    song_p, song = _song(tmp_path)
    clip = _clip(tmp_path, song, "A", a=0.0, b=5.0)
    fp = sample_clip_frames(str(clip), sample_fps=5.0)
    assert fp.n_sampled > 5
    assert fp.sharpness.shape == fp.clip_times.shape
    assert np.isnan(fp.motion_residual[0])  # first frame has no prior → NA

    n = grid_len(6.0, 0.1)
    tracks = quality_tracks(fp, clip_id="A", offset_s=0.0, t0=0.0, hop_s=0.1, n=n)
    by_metric = {t.metric: t for t in tracks}
    # The metric axis stays fixed whether or not a detector ran — it is persisted in
    # the score manifest and indexes the tensor, so a column never disappears.
    assert set(by_metric) == {"sharpness", "exposure", "stability_shake", "face_framing"}
    for t in tracks:
        assert t.raw_values.shape == (n,) and t.mask.shape == (n,)

    for metric in ("sharpness", "exposure", "stability_shake"):
        assert by_metric[metric].mask[:50].any()  # covers the clip's span

    # muvid#19: no face detector was injected here, so face_framing measured NOTHING
    # and must be fully MASKED rather than scored. It used to be 0.0 everywhere, which
    # normalized to a flat 0.5 (constant metric → neutral) — a column carrying zero
    # information while still consuming its weight in the composite denominator. The
    # mask is what keeps it out of that denominator.
    assert not by_metric["face_framing"].mask.any()


@core
def test_motionbeat_tracks_on_grid(tmp_path):
    from mixing.audio import beat_grid
    from muvid.footage.scoring.frames import sample_clip_frames
    from muvid.footage.scoring.motionbeat import motionbeat_tracks
    from muvid.footage.scoring.grid import grid_len

    song_p, song = _song(tmp_path)
    clip = _clip(tmp_path, song, "A", a=0.0, b=5.0)
    bg = beat_grid(str(song_p))
    fp = sample_clip_frames(str(clip), sample_fps=5.0)
    n = grid_len(6.0, 0.1)
    tracks = motionbeat_tracks(
        fp, clip_id="A", offset_s=0.0, beat_times=bg.beat_times,
        onset_env=bg.onset_env, onset_hop_s=bg.onset_hop_s, t0=0.0, hop_s=0.1, n=n,
    )
    metrics = {t.metric for t in tracks}
    assert metrics == {"motion_beat_bas", "motion_onset_xcorr"}
    for t in tracks:
        assert t.raw_values.shape == (n,)


# -- orchestrator end to end -------------------------------------------------


@core
def test_score_project_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("MUVID_SCORING_ENABLE_LIPSYNC", "0")
    from muvid.footage.align import align_footage
    from muvid.footage.scoring.grid import load_tensor, scores_present
    from muvid.footage.scoring.orchestrator import score_project
    from muvid.footage.workspace import FootageWorkspace

    song_p, song = _song(tmp_path)
    ca = _clip(tmp_path, song, "A", a=0.0, b=5.0)
    cb = _clip(tmp_path, song, "B", a=1.0, b=6.0, pattern="testsrc2")
    proj = FootageWorkspace.for_email("u@x.com").create_project("p")
    proj.set_song(str(song_p), ext="wav")
    proj.add_clip("A", str(ca), ext="mp4")
    proj.add_clip("B", str(cb), ext="mp4")
    aligns = align_footage(
        str(proj.song_path()), list(proj.clip_paths().items()), song_duration=proj.song_duration()
    )
    proj.save_alignments(aligns)

    events = []
    result = score_project(proj, progress_cb=events.append)
    assert result["status"] == "ok"
    assert "motion_beat_bas" in result["metrics"]
    assert scores_present(proj.root)
    # progress events carry the mirror-contract fields
    assert events and all(
        {"kind", "stage_index", "stage_count", "current_transform"} <= set(e) for e in events
    )
    tensor = load_tensor(proj.root)
    assert tensor is not None
    assert set(tensor.clip_ids) <= {"A", "B"}
    assert tensor.S.shape == (len(tensor.clip_ids), tensor.n, len(tensor.metrics))


@core
def test_score_then_weighted_select_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.align import align_footage
    from muvid.footage.scoring.grid import load_tensor
    from muvid.footage.scoring.orchestrator import score_project
    from muvid.footage.select_score import SelectionContext, resolve_config
    from muvid.footage import strategy as S
    from muvid.footage.edl import validate_edl
    from muvid.footage.workspace import FootageWorkspace

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
    score_project(proj)

    tensor = load_tensor(proj.root)
    manifest = __import__("json").loads((proj.root / "scores" / "manifest.json").read_text())
    ctx = SelectionContext(
        tensor=tensor,
        beat_times=manifest["beats"]["beat_times"],
        config=resolve_config(preset="energetic"),
    )
    edl = S.select_edl("weighted", aligns, proj.song_duration(), context=ctx)
    validate_edl(edl, aligns, proj.song_duration())  # the real assertion: a valid edit
    assert edl[0].song_start == pytest.approx(0.0)
