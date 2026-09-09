"""The choreo subgenre: event detection, the spec, the scene compiler, the render.

What is defended here, in order of how expensive it would be to lose:

* the manifest module and the listing path import no numpy — asserted in a
  CHILD interpreter, because in-process this module has already imported it;
* the three archetype tables (spec, scene registry, manifest enum) agree, or a
  name the caller chose silently falls back;
* the detector finds the kicks and the hats of a synthetic groove at the right
  times and the right tempo, is scale-invariant, and one attack is one event;
* the scene is deterministic given a seed, and objects are born ON events;
* resource bounds refuse rather than clamp;
* a real render (ffmpeg) produces a YouTube-spec mp4 whose frames contain the
  objects the scene says are alive — a render that succeeds with a black video
  proves nothing, so the frame checks read ``scene.json`` and look.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

import pytest

from muvid.choreo import spec as S
from muvid.choreo.analysis import (
    Analysis,
    Event,
    Section,
    Tempo,
    analyze,
    beat_grid_numpy,
    pick_onsets,
)
from muvid.choreo.manifest import ARCHETYPE_NAMES, CHOREO
from muvid.choreo.scene import ARCHETYPE_FNS, Canvas, compile_scene
from muvid.subgenres import get_subgenre, register_subgenre, unregister_subgenre
from muvid.subgenres.testing import check_subgenre_conformance
from tests.ffmpeg_support import needs_ffmpeg

SR = 22050
BPM = 120.0
PERIOD = 60.0 / BPM
SMALL = dict(width=320, height=180, fps=12)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _groove(duration: float = 16.0, *, sr: int = SR):
    """A synthetic 120 bpm groove: a 60 Hz kick on every beat, a high-passed
    hat on every off-beat, and a 440 Hz pad that gets loud in the second half
    (so there are two energy sections). Deterministic."""
    import numpy as np

    t = np.arange(int(sr * duration)) / sr
    y = np.zeros_like(t, dtype=np.float32)
    rng = np.random.default_rng(0)

    def hipass(x, cutoff):
        X = np.fft.rfft(x)
        X[np.fft.rfftfreq(len(x), 1 / sr) < cutoff] = 0
        return np.fft.irfft(X, len(x))

    for k in range(int(duration / PERIOD)):
        i0, n = int(k * PERIOD * sr), int(0.12 * sr)
        env = np.exp(-np.arange(n) / (0.04 * sr)) * np.hanning(2 * n)[n:]
        y[i0:i0 + n] += 0.8 * np.sin(2 * np.pi * 60 * np.arange(n) / sr) * env
        i1, n2 = int((k * PERIOD + PERIOD / 2) * sr), int(0.03 * sr)
        burst = hipass(rng.standard_normal(n2), 3000) * np.exp(-np.arange(n2) / (0.01 * sr))
        y[i1:i1 + n2] += 0.5 * burst
    y += (0.05 + 0.4 * (t > duration / 2)) * np.sin(2 * np.pi * 440 * t)
    return (y / np.abs(y).max()).astype(np.float32)


def _song_wav(path: Path, seconds: float = 16.0) -> Path:
    """The same groove, rendered by ffmpeg's expression synth: kick (decaying
    60 Hz) on the beat, a 6 kHz tick on the off-beat, a bass drone, and a pad
    that steps up every 8 s so sections exist."""
    expr = (
        "0.12*sin(2*PI*110*t)"
        "+0.8*exp(-mod(t,0.5)*35)*sin(2*PI*60*t)"
        "+0.5*exp(-mod(t+0.25,0.5)*150)*sin(2*PI*6000*t)"
        "+0.5*exp(-mod(t,1.0)*25)*sin(2*PI*330*t)"
        "+(0.05+0.3*mod(floor(t/8),2))*sin(2*PI*440*t)"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"aevalsrc='{expr}':s=44100:d={seconds}", "-ac", "2", str(path)],
        check=True,
    )
    return path


def _cover_png(path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "gradients=s=128x128:c0=0x102040:c1=0xff7f2a:c2=0x20c080", "-frames:v", "1",
         str(path)],
        check=True,
    )
    return path


def _frame(video: Path, at_s: float):
    """One frame as an HxWx3 uint8 array, via a raw rgb24 dump."""
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at_s), "-i", str(video), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    w, h = SMALL["width"], SMALL["height"]
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)


@pytest.fixture
def registered():
    """The manifest, visible to ``render_subgenre`` — registered in-process
    unless an entry point already provides it (the integrator adds one)."""
    try:
        yield get_subgenre("choreo")
        return
    except KeyError:
        pass
    register_subgenre(CHOREO)
    try:
        yield CHOREO
    finally:
        unregister_subgenre("choreo")


@pytest.fixture(scope="module")
def groove_analysis() -> Analysis:
    return analyze(_groove(), beat_source="numpy")


# --------------------------------------------------------------------------
# manifest and import safety
# --------------------------------------------------------------------------


def test_manifest_conforms(tmp_path):
    report = check_subgenre_conformance(CHOREO, workdir=tmp_path, render=False)
    assert report.ok, report.summary()
    assert CHOREO.api_versions == ("1",)
    assert len(CHOREO.examples) >= 3
    json.dumps(CHOREO.to_dict())


def test_listing_path_imports_no_numpy():
    """The load-bearing property, asserted in a CHILD interpreter."""
    code = textwrap.dedent(
        """
        import sys, json
        import muvid.choreo
        import muvid.choreo.manifest, muvid.choreo.spec, muvid.choreo.tools
        import muvid.choreo.analysis, muvid.choreo.scene
        muvid.choreo.tools.manifest(); muvid.choreo.tools.vocabulary()
        heavy = sorted(n for n in sys.modules if n.split('.')[0] in {
            'numpy', 'PIL', 'librosa', 'mixing', 'cv2', 'torch', 'scipy'})
        print(json.dumps(heavy))
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == []


def test_the_three_archetype_tables_agree():
    """spec.ARCHETYPES (what a caller may name), scene.ARCHETYPE_FNS (what
    renders) and the manifest's enum must be one set."""
    enum = CHOREO.params_schema["properties"]["archetype"]["enum"]
    assert set(S.ARCHETYPES) == set(ARCHETYPE_FNS) == set(ARCHETYPE_NAMES) == set(enum)
    assert set(S.ARCHETYPE_PARAMS) == set(S.ARCHETYPES)


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------


def test_analysis_finds_the_tempo_and_the_hits(groove_analysis):
    a = groove_analysis
    assert abs(a.tempo.bpm - BPM) < 2.0, a.tempo
    assert a.tempo.source == "numpy"
    assert abs(a.tempo.beats[0]) < 0.03, "phase should lock to the kicks, not the hats"

    gate = S.DENSITY_GATE["normal"]
    low = [e for e in a.events if e.band == "low" and e.strength >= gate]
    assert 28 <= len(low) <= 33, [e.t for e in low]
    for e in low:
        assert min(abs(e.t - k * PERIOD) for k in range(40)) < 0.03, e
    high = [e for e in a.events if e.band == "high"]
    assert 30 <= len(high) <= 33
    for e in high:
        assert min(abs(e.t - (k * PERIOD + PERIOD / 2)) for k in range(40)) < 0.03, e
    assert max(e.strength for e in a.events) <= 1.0
    assert all(0.0 < e.strength for e in a.events)


def test_analysis_finds_two_energy_sections(groove_analysis):
    secs = groove_analysis.sections
    assert [s.label for s in secs] == ["low", "high"], secs
    assert secs[0].start == 0.0 and secs[-1].end == groove_analysis.duration
    assert abs(secs[1].start - 8.0) < 1.0
    assert secs[1].energy_db > secs[0].energy_db + 3


def test_analysis_is_scale_invariant_and_round_trips(groove_analysis):
    quieter = analyze(_groove() * 0.05, beat_source="numpy")
    assert [(e.t, e.band) for e in quieter.events] == [(e.t, e.band) for e in groove_analysis.events]
    assert Analysis.from_dict(json.loads(groove_analysis.to_json())) == groove_analysis


def test_one_attack_is_one_event():
    """Hysteresis: a rise that stays above threshold for several frames is one
    onset; the min-IOI window keeps the stronger of two close hits."""
    import numpy as np

    f = np.zeros(300, np.float32)
    f[50:56] = [3.0, 5.0, 5.5, 5.2, 4.0, 2.0]   # one attack, several frames above threshold
    f[150], f[153] = 2.0, 6.0                    # a weak trigger then the real hit
    ev = pick_onsets(f, hop_s=0.01, min_ioi_s=0.1)
    assert [round(t, 2) for t, _ in ev] == [0.52, 1.53], ev
    assert ev[1][1] > ev[0][1]


def test_beat_grid_numpy_locks_phase():
    import numpy as np

    env = np.zeros(4000, np.float32)
    env[23::50] = 1.0                                # 120 bpm at 0.01 s hop, phase 0.23 s
    t = beat_grid_numpy(env, hop_s=0.01, duration=40.0)
    assert abs(t.bpm - 120) < 0.5 and abs(t.beats[0] - 0.23) < 0.011


# --------------------------------------------------------------------------
# spec
# --------------------------------------------------------------------------


def test_from_dict_is_total_over_hostile_input_and_repair_is_idempotent():
    hostile = {
        "scenes": [None, "x", {"applies_to": "high", "archetype": 7,
                              "params": {"columns": "99", "nope": 1}}],
        "direction": {"palette": {"foo": 1, "bg": "not-a-colour"}, "density": "loads",
                      "background": None},
    }
    spec = S.TreatmentSpec.from_dict(hostile)
    assert S.validate(spec)  # it IS wrong
    fixed, notes = S.repair(spec)
    assert S.validate(fixed) == []
    assert fixed.scenes[-1].applies_to == ("high",)
    assert fixed.direction.density == "normal"
    assert fixed.direction.palette.bg == S.Palette().bg
    assert notes and S.repair(fixed) == (fixed, [])


def test_json_schema_accepts_what_to_dict_emits():
    jsonschema = pytest.importorskip("jsonschema")
    schema = S.json_schema()
    for spec in (S.TreatmentSpec(), S.default_treatment("swarm"),
                 S.repair(S.TreatmentSpec.from_dict(CHOREO.examples[3].params["treatment"]))[0]):
        jsonschema.validate(spec.to_dict(), schema)


def test_every_example_is_renderable_as_declared():
    for ex in CHOREO.examples:
        if "treatment" in ex.params:
            spec, notes = S.coerce(ex.params["treatment"])
            assert not notes, (ex.description, notes)
            assert S.validate(spec) == []


# --------------------------------------------------------------------------
# scene
# --------------------------------------------------------------------------


@pytest.mark.parametrize("archetype", sorted(S.ARCHETYPES))
def test_scene_is_deterministic_and_born_on_events(groove_analysis, archetype):
    a = groove_analysis
    canvas = Canvas(**SMALL)
    treatment = S.default_treatment(archetype)
    s1 = compile_scene(treatment, a, canvas=canvas, seed=3)
    s2 = compile_scene(treatment, a, canvas=canvas, seed=3)
    assert s1 == s2
    assert s1.objects and s1.meta["archetypes"] == [archetype]
    event_times = {e.t for e in a.events}
    section_starts = {sec.start for sec in a.sections}
    for o in s1.objects:
        assert o.t_born in event_times or o.t_born in section_starts, o
        assert o.t_die > o.t_born
        assert o.kind in ("circle", "rect", "triangle", "line", "diamond", "ring")
    # every event above the density gate made at least one object
    gate = S.DENSITY_GATE["normal"]
    born = {o.t_born for o in s1.objects}
    assert all(e.t in born for e in a.events if e.strength >= gate)
    # backdrops tile the song
    assert s1.backdrops[0].start == 0.0 and s1.backdrops[-1].end == a.duration
    assert s1.to_dict()["meta"]["n_objects"] == len(s1.objects)


def test_seed_changes_the_jitter_but_not_the_timing(groove_analysis):
    canvas = Canvas(**SMALL)
    t = S.default_treatment("mclaren")
    s1 = compile_scene(t, groove_analysis, canvas=canvas, seed=1)
    s2 = compile_scene(t, groove_analysis, canvas=canvas, seed=2)
    assert [o.t_born for o in s1.objects] == [o.t_born for o in s2.objects]
    assert [(o.x, o.y) for o in s1.objects] != [(o.x, o.y) for o in s2.objects]


def test_star_guitar_spacing_is_the_rhythm(groove_analysis):
    """Two poles born a beat apart sit exactly speed*period widths apart."""
    canvas = Canvas(**SMALL)
    speed = 0.4
    t = S.TreatmentSpec(scenes=(S.Scene(archetype="star_guitar", params={"speed": speed}),))
    scene = compile_scene(t, groove_analysis, canvas=canvas)
    poles = [o for o in scene.objects if o.band == "low" and o.kind == "rect"]
    assert len(poles) >= 20
    for p, q in zip(poles, poles[1:]):
        gap = q.t_born - p.t_born
        # at time q.t_born, p has moved speed*gap to the left
        assert abs((p.x + p.vx * gap) - q.x + 0.0) == pytest.approx(speed * gap, abs=0.05)
        assert p.vx == q.vx == -speed
    # the sky is per section: two sections, two different skies
    assert len(scene.backdrops) == 2 and scene.backdrops[0].top != scene.backdrops[1].top


def test_sections_route_to_scenes_and_uncovered_ones_fall_back(groove_analysis):
    only_high = S.TreatmentSpec(scenes=(S.Scene(applies_to=("high",), archetype="mclaren"),))
    scene = compile_scene(only_high, groove_analysis, canvas=Canvas(**SMALL))
    assert scene.meta["uncovered_sections"] == ["0:low"]
    both = S.TreatmentSpec(scenes=(S.Scene(archetype="swarm"),
                                   S.Scene(applies_to=("high",), archetype="fischinger")))
    scene = compile_scene(both, groove_analysis, canvas=Canvas(**SMALL))
    assert "uncovered_sections" not in scene.meta
    assert scene.meta["archetypes"] == ["fischinger", "swarm"]


def test_objects_stay_on_canvas_at_birth(groove_analysis):
    for archetype in ("fischinger", "mclaren", "swarm"):
        scene = compile_scene(S.default_treatment(archetype), groove_analysis,
                              canvas=Canvas(**SMALL), seed=5)
        off = [(o.kind, o.x, o.y) for o in scene.objects if not (0 <= o.x <= 1 and 0 <= o.y <= 1)]
        assert not off, (archetype, off[:5])


# --------------------------------------------------------------------------
# bounds
# --------------------------------------------------------------------------


def test_bounds_refuse_rather_than_clamp():
    from muvid.choreo.pipeline import check_input_counts, check_render_bounds

    check_render_bounds(Canvas(width=3840, height=2160, fps=30), 15 * 60)
    with pytest.raises(ValueError, match="MUVID_CHOREO_MAX_FRAMES"):
        check_render_bounds(Canvas(width=64, height=64, fps=60), 15 * 60)
    with pytest.raises(ValueError, match="MUVID_CHOREO_MAX_PIXELS"):
        check_render_bounds(Canvas(width=3842, height=2160, fps=30), 10)
    with pytest.raises(ValueError, match="MUVID_CHOREO_MAX_FPS"):
        check_render_bounds(Canvas(width=64, height=64, fps=61), 10)
    with pytest.raises(ValueError, match="MUVID_MAX_DURATION_S"):
        check_render_bounds(Canvas(width=64, height=64, fps=30), 15 * 60 + 1)
    with pytest.raises(ValueError, match="too small"):
        check_render_bounds(Canvas(width=8, height=64, fps=30), 1)
    check_input_counts({"audio": "a.wav", "frames": ["f"] * 64})
    with pytest.raises(ValueError, match="MUVID_CHOREO_MAX_INPUT_FILES"):
        check_input_counts({"frames": ["f"] * 65})


def test_unknown_archetype_is_refused_not_defaulted(tmp_path):
    from muvid.choreo.pipeline import _resolve_treatment

    with pytest.raises(ValueError, match="unknown archetype"):
        _resolve_treatment({"archetype": "swirl"})
    with pytest.raises(ValueError, match="strict"):
        _resolve_treatment({"treatment": {"scenes": [{"archetype": "swirl"}]}, "strict": True})


# --------------------------------------------------------------------------
# render — needs ffmpeg
# --------------------------------------------------------------------------


@needs_ffmpeg
def test_conformance_kit_renders_a_real_song(tmp_path):
    song = _song_wav(tmp_path / "song.wav", 6.0)
    report = check_subgenre_conformance(
        CHOREO, workdir=tmp_path,
        inputs={"audio": str(song)}, params={"archetype": "mclaren", **SMALL},
    )
    assert report.ok, report.summary()


@needs_ffmpeg
def test_render_star_guitar_end_to_end_and_look_at_the_frames(tmp_path, registered):
    from muvid.subgenres import render_subgenre
    from muvid.visualize import verify_video

    song = _song_wav(tmp_path / "song.wav", 16.0)
    out = tmp_path / "out.mp4"
    wd = tmp_path / "wd"
    result = render_subgenre(
        "choreo", inputs={"audio": str(song)},
        params={"archetype": "star_guitar", "seed": 1, "beat_source": "numpy", **SMALL},
        workdir=wd, output=out,
    )
    assert result.output == out and out.exists()
    assert result.meta["archetypes"] == ["star_guitar"]
    assert result.meta["beat_source"] == "numpy"
    assert abs(result.meta["tempo_bpm"] - BPM) < 2.0
    assert result.meta["n_sections"] == 2
    for key in ("events", "scene", "treatment"):
        assert Path(result.artifacts[key]).exists(), key
    analysis = Analysis.from_dict(json.loads(Path(result.artifacts["events"]).read_text()))
    assert len(analysis.events) == result.meta["n_events"] > 40
    scene = json.loads(Path(result.artifacts["scene"]).read_text())
    assert len(scene["objects"]) == result.meta["n_objects"]

    failures = [c for c in verify_video(out, audio=song, expected_canvas=(320, 180)) if not c.ok]
    assert not failures, failures

    # the sky changes with the section: the top rows differ between 3 s and 12 s
    top_a = _frame(out, 3.0)[:20].reshape(-1, 3).mean(axis=0)
    top_b = _frame(out, 12.0)[:20].reshape(-1, 3).mean(axis=0)
    assert max(abs(top_a - top_b)) > 20, (top_a, top_b)
    # and there are objects: a frame mid-song is not flat
    mid = _frame(out, 12.0)
    assert mid.std() > 15, mid.std()
    # a pole is drawn in the low-band colour somewhere in the frame
    low_rgb = [int(scene["objects"][2]["colour"][i:i + 2], 16) for i in (1, 3, 5)]
    assert (abs(mid.astype(int) - low_rgb).sum(axis=2) < 40).any()


@needs_ffmpeg
def test_render_mclaren_is_black_between_marks_and_white_on_them(tmp_path, registered):
    from muvid.subgenres import render_subgenre

    song = _song_wav(tmp_path / "song.wav", 8.0)
    out = tmp_path / "out.mp4"
    result = render_subgenre(
        "choreo", inputs={"audio": str(song)},
        params={"treatment": {"direction": {"palette": {"bg": "#000000", "fg": "#ffffff"},
                                            "density": "sparse"},
                              "scenes": [{"archetype": "mclaren", "params": {"life_beats": 0.3}}]},
                "beat_source": "numpy", **SMALL},
        workdir=tmp_path / "wd", output=out,
    )
    scene = json.loads(Path(result.artifacts["scene"]).read_text())
    objs = scene["objects"]
    fps = SMALL["fps"]
    # a frame while a strong mark is alive vs a frame while nothing is alive,
    # chosen from scene.json rather than guessed
    alive_t = max(objs, key=lambda o: o["strength"])["t_born"] + 1.0 / fps
    frames = int(8.0 * fps)
    dead = next(k / fps for k in range(frames)
                if not any(o["t_born"] <= k / fps < o["t_die"] for o in objs))
    lit, dark = _frame(out, alive_t), _frame(out, dead)
    assert dark.max() < 8, dark.max()
    assert lit.max() > 200 and lit.mean() < 40, (lit.max(), lit.mean())


@needs_ffmpeg
def test_cli_render_and_analyze(tmp_path):
    song = _song_wav(tmp_path / "song.wav", 6.0)
    out = tmp_path / "cli.mp4"
    proc = subprocess.run(
        [sys.executable, "-m", "muvid.choreo", "render", str(song), str(out),
         "--archetype", "fischinger", "--width", "320", "--height", "180", "--fps", "12",
         "--beat-source", "numpy", "--workdir", str(tmp_path / "wd")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert Path(payload["output"]) == out and out.exists()
    assert payload["meta"]["archetypes"] == ["fischinger"]

    proc = subprocess.run(
        [sys.executable, "-m", "muvid.choreo", "analyze", str(song), "--beat-source", "numpy"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert abs(payload["tempo"]["bpm"] - BPM) < 2.0 and payload["events"]


@needs_ffmpeg
def test_cover_seeds_the_palette(tmp_path, registered):
    from muvid.choreo.pipeline import palette_from_cover

    cover = _cover_png(tmp_path / "cover.png")
    pal = palette_from_cover(cover, workdir=tmp_path / "wd")
    for name, value in asdict(pal).items():
        assert re.fullmatch(r"#[0-9a-f]{6}", value), (name, value)

    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    assert lum(pal.bg) < 0.25 < 0.6 < lum(pal.fg)
    assert len({pal.low, pal.mid, pal.high}) == 3

    # and through the pipeline: the render records where its palette came from
    from muvid.subgenres import render_subgenre

    song = _song_wav(tmp_path / "song.wav", 4.0)
    result = render_subgenre(
        "choreo", inputs={"audio": str(song), "cover": str(cover)},
        params={"archetype": "swarm", "beat_source": "numpy", **SMALL},
        workdir=tmp_path / "wd2", output=tmp_path / "out.mp4",
    )
    assert result.meta["palette_source"] == "cover"
    treatment = json.loads(Path(result.artifacts["treatment"]).read_text())
    assert treatment["direction"]["palette"]["bg"] == pal.bg
