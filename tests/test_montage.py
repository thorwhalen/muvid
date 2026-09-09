"""The montage subgenre: manifest, spec, the planner's reuse policy, the render.

The properties defended here are the ones the subgenre sells:

* every cut sits on the measured grid, choruses cut denser than verses, and no
  still is held past ``MAX_HOLD_S``;
* the reuse policy never repeats an image within the gap, revisits with a
  different framing, reserves the strongest for the final chorus and pins the
  cover to both ends — and does all of it deterministically;
* the rendered frames are the planned images (sampled by colour), the blends
  are blends and the grid is a grid — a render that "succeeds" with a black
  video proves nothing;
* listing the plugin imports no numpy, and the manifest passes the
  conformance kit against real files.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from muvid.montage import spec as S
from muvid.montage.analysis import Analysis, Media, Section
from muvid.montage.manifest import MONTAGE
from muvid.montage.plan import (
    ARCHETYPE_FNS,
    CROP_VARIANTS,
    MAX_HOLD_S,
    MIN_SLOT_S,
    Plan,
    plan_montage,
)
from tests.ffmpeg_support import needs_ffmpeg, needs_ffmpeg_filter

BPM = 120.0
BEAT = 60.0 / BPM

#: A three-minute song's worth of structure, in seconds.
SONG_SECTIONS = [
    ("intro", 0.0, 8.0), ("verse", 8.0, 40.0), ("chorus", 40.0, 56.0),
    ("verse", 56.0, 88.0), ("chorus", 88.0, 120.0), ("outro", 120.0, 128.0),
]

#: ffmpeg's named colours the fixtures are painted with, as RGB.
COLOURS = {
    "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "magenta": (255, 0, 255),
}


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------


def _analysis(duration: float = 128.0, sections=None, bpb: int = 4) -> Analysis:
    beats = tuple(round(i * BEAT, 6) for i in range(int(duration / BEAT)))
    secs = tuple(
        Section(label=l, start=float(a), end=float(b))
        for l, a, b in (sections or SONG_SECTIONS if duration == 128.0 else [("verse", 0.0, duration)])
    )
    return Analysis(
        duration=duration, tempo_bpm=BPM, beats=beats, downbeats=beats[::bpb],
        beats_per_bar=bpb, sections=secs, beat_source="test", section_source="test",
    )


def _pool(n: int, *, cover: bool = False) -> list[Media]:
    media = [
        Media(index=i, path=f"p{i}.jpg", kind="photo", width=1000, height=750,
              strength=float(i))
        for i in range(n)
    ]
    if cover:
        media.append(Media(index=n, path="cover.jpg", kind="cover", width=1000,
                           height=750, strength=99.0))
    return media


def _fresh(plan: Plan) -> list[int]:
    """Media indices in assignment order: every single-region slot's tile."""
    return [s.tiles[0].media for s in plan.slots if s.regions == 1]


# --------------------------------------------------------------------------
# manifest + spec
# --------------------------------------------------------------------------


def test_manifest_passes_the_conformance_kit_without_rendering(tmp_path):
    from muvid.subgenres.testing import check_subgenre_conformance

    report = check_subgenre_conformance(MONTAGE, workdir=tmp_path, render=False)
    assert report.ok, report.summary()
    assert MONTAGE.slug == "montage" and MONTAGE.api_versions == ("1",)
    assert len(MONTAGE.examples) >= 3
    assert MONTAGE.cost_profile is None


def test_the_four_archetype_tables_agree():
    """spec.ARCHETYPES (what a model may name), spec.ARCHETYPE_PARAMS (what it
    may tune), plan.ARCHETYPE_FNS (what plans) and the manifest's enum (what
    the CLI/MCP accept) must be one set, or a name silently falls back."""
    manifest_enum = set(MONTAGE.params_schema["properties"]["archetype"]["enum"])
    assert set(S.ARCHETYPES) == set(S.ARCHETYPE_PARAMS) == set(ARCHETYPE_FNS) == manifest_enum
    assert set(S.ARCHETYPES) == {"ballad_dissolve", "beat_cut", "grid", "stop_motion"}


def test_listing_path_imports_no_numpy():
    """The load-bearing property, asserted in a CHILD interpreter."""
    code = textwrap.dedent(
        """
        import sys, json
        import muvid.montage, muvid.montage.manifest, muvid.montage.spec
        import muvid.montage.tools, muvid.montage.__main__
        from muvid.montage.manifest import MONTAGE
        assert MONTAGE.render == "muvid.montage.pipeline:render"
        heavy = sorted(n for n in sys.modules if n.split('.')[0] in {
            'numpy', 'PIL', 'cv2', 'librosa', 'torch', 'scipy', 'mixing',
            'moviepy', 'fastmcp', 'looks'})
        print(json.dumps(heavy))
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == []


def test_every_example_satisfies_the_manifest_schemas_and_the_treatment_is_valid():
    from muvid.subgenres._schema import validate as _validate

    for ex in MONTAGE.examples:
        assert _validate(dict(ex.params), MONTAGE.params_schema, where="params") == []
        if "treatment" in ex.params:
            spec, notes = S.coerce(ex.params["treatment"])
            assert notes == [], (ex.description, notes)
            assert S.validate(spec) == []


def test_treatment_round_trips_and_its_dict_satisfies_its_own_schema():
    from muvid.subgenres._schema import validate as _validate

    spec = S.TreatmentSpec(
        direction=S.Direction(cut_feel="driving", grade="tint", reuse=S.Reuse(min_gap=3)),
        scenes=(S.Scene(applies_to=("chorus",), archetype="grid", params={"beats_per_swap": 1}),
                S.Scene(archetype="ballad_dissolve", params={"fade_beats": 2})),
    )
    d = spec.to_dict()
    assert _validate(d, S.json_schema(), where="treatment") == []
    assert S.TreatmentSpec.from_dict(d) == spec
    assert S.TreatmentSpec.from_json(spec.to_json()) == spec


def test_repair_projects_onto_the_vocabulary_and_reports_every_substitution():
    raw = {
        "direction": {"cut_feel": "bonkers", "grade": "sepia", "palette": {"accent": "orange"}},
        "scenes": [{"archetype": "swirl"}, {"archetype": "beat_cut", "params": {"punch": 5, "x": 1}}],
    }
    spec = S.TreatmentSpec.from_dict(raw)
    errors = S.validate(spec)
    # accent, cut_feel, grade, archetype, punch out of range, unknown param
    assert len(errors) == 6, errors
    fixed, notes = S.repair(spec)
    assert S.validate(fixed) == []
    assert fixed.direction.cut_feel == "steady" and fixed.direction.grade == "none"
    assert fixed.scenes[0].archetype == "beat_cut"
    assert dict(fixed.scenes[1].params) == {"punch": 0.3}
    assert any("bonkers" in n for n in notes) and any("dropped unknown ['x']" in n for n in notes)


# --------------------------------------------------------------------------
# the planner
# --------------------------------------------------------------------------


@pytest.mark.parametrize("archetype", sorted(S.ARCHETYPES))
@pytest.mark.parametrize("feel", sorted(S.CUT_FEELS))
def test_slots_tile_the_song_on_the_grid_and_never_hold_too_long(archetype, feel):
    a = _analysis()
    plan = plan_montage(a, _pool(12), S.one_scene(archetype, cut_feel=feel))
    slots = plan.slots
    assert slots[0].start == 0.0 and slots[-1].end == a.duration
    for prev, nxt in zip(slots, slots[1:]):
        assert prev.end == nxt.start, (prev, nxt)
    assert all(s.end - s.start >= MIN_SLOT_S - 1e-9 for s in slots)
    assert max(s.end - s.start for s in slots) <= MAX_HOLD_S + 1e-6
    grid = set(a.beats) | {a.duration}
    # every interior boundary is a beat or an even subdivision of one
    for s in slots[1:]:
        on_beat = any(abs(s.start - b) < 1e-6 for b in grid)
        sub = (s.start / BEAT) % 1.0
        assert on_beat or min(abs(sub - f) for f in (0.25, 0.5, 0.75, 1 / 3, 2 / 3)) < 1e-6, s.start


def test_choruses_cut_denser_than_verses_and_cut_feel_scales_it():
    a = _analysis()
    steady = plan_montage(a, _pool(12), S.one_scene("beat_cut"))
    by = {}
    for s in steady.slots:
        by.setdefault(s.section, []).append(s.end - s.start)
    mean = {k: sum(v) / len(v) for k, v in by.items()}
    assert mean["chorus"] < mean["verse"] < mean["intro"], mean
    driving = plan_montage(a, _pool(12), S.one_scene("beat_cut", cut_feel="driving"))
    slow = plan_montage(a, _pool(12), S.one_scene("beat_cut", cut_feel="slow"))
    assert len(slow.slots) < len(steady.slots) < len(driving.slots)
    assert steady.reuse["slots_per_section"]["chorus"] > steady.reuse["slots_per_section"]["intro"]


def test_named_scene_wins_its_section_and_star_takes_the_rest():
    a = _analysis()
    spec, _ = S.coerce({"scenes": [{"applies_to": ["*"], "archetype": "ballad_dissolve"},
                                   {"applies_to": ["chorus"], "archetype": "grid"}]})
    plan = plan_montage(a, _pool(12), spec)
    assert {s.archetype for s in plan.slots if s.section == "chorus"} == {"grid"}
    assert {s.archetype for s in plan.slots if s.section != "chorus"} == {"ballad_dissolve"}
    only_chorus, _ = S.coerce({"scenes": [{"applies_to": ["chorus"], "archetype": "grid"}]})
    plan = plan_montage(a, _pool(12), only_chorus)
    assert any("fell back" in n for n in plan.notes)


def test_twelve_photos_carry_a_long_song_without_a_repeat_inside_the_gap():
    """The reuse policy's headline promise, on a 128 s beat_cut plan."""
    a = _analysis()
    plan = plan_montage(a, _pool(12), S.one_scene("beat_cut", cut_feel="driving"))
    seq = _fresh(plan)
    gap = plan.reuse["min_gap"]
    assert gap == 6
    assert len(seq) > 40, len(seq)
    for i, m in enumerate(seq):
        window = seq[max(0, i - gap):i]
        assert m not in window, (i, m, window)
    assert plan.reuse["min_observed_gap"] > gap
    uses = plan.reuse["uses"]
    assert set(uses) == {str(i) for i in range(12)} and min(uses.values()) >= 1
    # the reserved three carry only the finale; everyone else shares evenly
    reserved = {str(m) for m in plan.reuse["reserved_for_finale"]}
    regular = [n for m, n in uses.items() if m not in reserved]
    assert max(regular) - min(regular) <= 2, uses
    assert max(uses[m] for m in reserved) < min(regular)


def test_a_revisit_shows_a_different_framing():
    a = _analysis()
    plan = plan_montage(a, _pool(4), S.one_scene("beat_cut"))
    by_media: dict[int, list] = {}
    for s in plan.slots:
        t = s.tiles[0]
        by_media.setdefault(t.media, []).append((t.use, t.variant))
    for m, uses in by_media.items():
        uses.sort()
        assert [u for u, _ in uses] == list(range(len(uses)))
        first = [v for _, v in uses[: len(CROP_VARIANTS)]]
        assert len(set(first)) == len(first), (m, first)


def test_the_strongest_quarter_is_reserved_for_the_final_chorus_and_leads_it():
    a = _analysis()
    plan = plan_montage(a, _pool(12), S.one_scene("beat_cut"))
    reserved = plan.reuse["reserved_for_finale"]
    assert reserved == [11, 10, 9]  # strength == index in the fixture pool
    finale_start = plan.reuse["finale"]["start"]
    assert finale_start == 88.0
    before = [s.tiles[0].media for s in plan.slots if s.start < finale_start]
    assert not set(reserved) & set(before)
    finale = [s.tiles[0].media for s in plan.slots if s.start >= finale_start]
    assert finale[:3] == [11, 10, 9]
    # and switching the policy off spreads them from the start
    off, _ = S.coerce({"direction": {"reuse": {"reserve_for_finale": False}}})
    plan = plan_montage(a, _pool(12), off)
    assert plan.reuse["reserved_for_finale"] == []


def test_a_small_pool_shrinks_the_gap_and_does_not_reserve():
    a = _analysis()
    plan = plan_montage(a, _pool(2), S.one_scene("beat_cut"))
    assert plan.reuse["min_gap"] == 1 and plan.reuse["reserved_for_finale"] == []
    seq = _fresh(plan)
    assert all(x != y for x, y in zip(seq, seq[1:]))
    one = plan_montage(a, _pool(1), S.one_scene("beat_cut"))
    assert set(_fresh(one)) == {0}


def test_the_cover_opens_and_closes_and_appears_nowhere_else():
    a = _analysis()
    plan = plan_montage(a, _pool(6, cover=True), S.one_scene("beat_cut"))
    seq = _fresh(plan)
    assert seq[0] == 6 and seq[-1] == 6
    assert 6 not in seq[1:-1]
    assert plan.reuse["cover"] == 6


def test_grid_swaps_exactly_one_tile_per_slot_and_never_shows_a_tile_twice():
    a = _analysis()
    plan = plan_montage(a, _pool(9), S.one_scene("grid"))
    grids = [s for s in plan.slots if s.regions == 4]
    assert grids and all(len({t.media for t in s.tiles}) == 4 for s in grids)
    resets = 0
    for prev, nxt in zip(grids, grids[1:]):
        before = {t.region: t.media for t in prev.tiles}
        after = {t.region: t.media for t in nxt.tiles}
        swapped = sum(before[r] != after[r] for r in range(4))
        if prev.section != nxt.section:
            resets += 1  # a section change swaps all four: the accent
            assert swapped == 4, (before, after)
        else:
            assert swapped == 1, (before, after)
    assert resets == len(SONG_SECTIONS) - 1


def test_motion_and_transition_vocabulary_per_archetype():
    a = _analysis()
    ballad = plan_montage(a, _pool(8), S.one_scene("ballad_dissolve"))
    assert {s.transition for s in ballad.slots[1:]} == {"fade"}
    assert ballad.slots[0].transition == "cut"
    assert all(0 < s.transition_s <= 0.45 * (s.end - s.start) + 1e-9 for s in ballad.slots[1:])
    assert {t.motion for s in ballad.slots for t in s.tiles} <= {"zoom_in", "zoom_out", "pan_left", "pan_right"}
    stop = plan_montage(a, _pool(8), S.one_scene("stop_motion"))
    assert {t.motion for s in stop.slots for t in s.tiles} == {"none"}
    assert {s.transition for s in stop.slots} == {"cut"}
    beat = plan_montage(a, _pool(8), S.one_scene("beat_cut"))
    assert {t.motion for s in beat.slots for t in s.tiles} == {"punch"}
    assert all(len(t.path) == 3 for s in beat.slots for t in s.tiles)
    assert all(t.motion in S.MOTIONS for p in (ballad, stop, beat) for s in p.slots for t in s.tiles)
    assert all(s.transition in S.TRANSITIONS for p in (ballad, stop, beat) for s in p.slots)


def test_a_clip_is_trimmed_at_a_different_in_point_on_each_use():
    a = _analysis()
    media = _pool(3)
    media.append(Media(index=3, path="c.mp4", kind="clip", width=640, height=480,
                       duration=30.0, strength=1.0))
    plan = plan_montage(a, media, S.one_scene("beat_cut"))
    ins = [t.source_in for s in plan.slots for t in s.tiles if t.media == 3]
    assert len(ins) >= 3 and len(set(round(x, 3) for x in ins)) == len(ins)
    assert all(0 <= x <= 30.0 for x in ins)
    assert {t.motion for s in plan.slots for t in s.tiles if t.media == 3} == {"none"}


def test_the_plan_is_deterministic_json_able_and_round_trips():
    a = _analysis()
    spec = S.one_scene("ballad_dissolve", grade="tint")
    p1, p2 = plan_montage(a, _pool(12, cover=True), spec), plan_montage(a, _pool(12, cover=True), spec)
    d = p1.to_dict()
    assert d == p2.to_dict()
    text = json.dumps(d)
    assert Plan.from_dict(json.loads(text)).to_dict() == d


def test_frame_accounting_is_exact_for_every_archetype():
    from muvid.montage.render import parts_for

    a = _analysis()
    for archetype in S.ARCHETYPES:
        plan = plan_montage(a, _pool(12), S.one_scene(archetype, cut_feel="driving"))
        for fps in (12, 24, 30):
            parts, acc = parts_for(plan, fps)
            assert sum(p.n_frames for p in parts) == acc["n_frames"] == round(a.duration * fps)
            assert all(p.n_frames > 0 for p in parts)
            for prev, nxt in zip(parts, parts[1:]):
                assert (prev.slot, prev.kind) <= (nxt.slot, nxt.kind) or prev.kind == "solo"


# --------------------------------------------------------------------------
# bounds
# --------------------------------------------------------------------------


def test_bounds_refuse_rather_than_clamp(monkeypatch, tmp_path):
    from muvid.montage import pipeline
    from muvid.montage.render import Canvas
    from muvid.subgenres import RenderRequest

    ok = Canvas(width=1920, height=1080, fps=30)
    pipeline.check_render_bounds(ok, 100.0, n_photos=64, n_clips=64)
    with pytest.raises(ValueError, match="MUVID_MONTAGE_MAX_FPS"):
        pipeline.check_render_bounds(Canvas(width=1920, height=1080, fps=61), 10.0)
    monkeypatch.setattr(pipeline, "MAX_DURATION_S", 5)
    with pytest.raises(ValueError, match="MUVID_MAX_DURATION_S"):
        pipeline.check_render_bounds(ok, 6.0)
    with pytest.raises(ValueError, match="MUVID_MONTAGE_MAX_MEDIA"):
        pipeline.check_render_bounds(ok, 1.0, n_clips=65)
    # the pool bound fires BEFORE any file is probed: these photos do not exist
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"")
    req = RenderRequest(
        subgenre="montage", inputs={"audio": str(audio), "photos": [f"{i}.jpg" for i in range(65)]},
        params={}, workdir=tmp_path, output=tmp_path / "o.mp4",
    )
    with pytest.raises(ValueError, match="MUVID_MONTAGE_MAX_MEDIA"):
        pipeline.render(req)
    with pytest.raises(ValueError, match="pool is empty"):
        pipeline.render(RenderRequest(subgenre="montage", inputs={"audio": str(audio)},
                                      params={}, workdir=tmp_path, output=tmp_path / "o.mp4"))


# --------------------------------------------------------------------------
# ffmpeg-backed: analysis, conformance, renders, CLI
# --------------------------------------------------------------------------

SONG_S = 16.0
CANVAS = dict(width=160, height=90, fps=12)


def _ff(*args) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory) -> dict:
    """A 16 s click track at 120 BPM (loud 8-12 s), five solid photos, a clip."""
    if not (needs_ffmpeg.args[0] is False):
        pytest.skip("ffmpeg is not installed")
    d = tmp_path_factory.mktemp("montage")
    song = d / "song.wav"
    _ff("-f", "lavfi", "-i", f"sine=frequency=110:duration={SONG_S}",
        "-f", "lavfi", "-i", f"anoisesrc=color=white:seed=7:duration={SONG_S}:sample_rate=48000",
        "-filter_complex",
        "[1:a]volume='if(lt(mod(t,0.5),0.03),0.8,0)':eval=frame[clk];"
        "[0:a]volume=0.3[bass];[bass][clk]amix=inputs=2:duration=first:normalize=0,"
        "volume='if(between(t,8,12),1.0,0.4)':eval=frame[a]",
        "-map", "[a]", "-ac", "2", "-ar", "48000", str(song))
    photos = {}
    for name in COLOURS:
        p = d / f"{name}.jpg"
        _ff("-f", "lavfi", "-i", f"color=c={name}:s=400x300", "-frames:v", "1", str(p))
        photos[name] = p
    clip = d / "clip.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=4", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(clip))
    return {"dir": d, "song": song, "photos": photos, "clip": clip}


def _mean_rgb(video: Path, at_s: float, *, crop: str | None = None) -> tuple[int, int, int]:
    vf = ["-vf", f"crop={crop}"] if crop else []
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at_s:.3f}", "-i", str(video), "-frames:v", "1",
         *vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    n = max(1, len(raw) // 3)
    return tuple(round(sum(raw[i::3]) / n) for i in range(3))  # type: ignore[return-value]


def _close(a, b, tol=14) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@pytest.fixture
def registered():
    """The manifest on the registry, so render_subgenre validates the schemas.
    Registered by hand only when the entry point has not already done it."""
    from muvid.subgenres import list_subgenres, register_subgenre, unregister_subgenre

    mine = MONTAGE.slug not in list_subgenres()
    if mine:
        register_subgenre(MONTAGE)
    yield MONTAGE.slug
    if mine:
        unregister_subgenre(MONTAGE.slug)


@needs_ffmpeg
def test_the_numpy_beat_estimator_finds_the_click_track_and_the_loud_chorus(fixtures):
    from muvid.montage.analysis import analyze

    a = analyze(fixtures["song"], beats="numpy")
    assert "numpy" in a.beat_source
    assert abs(a.tempo_bpm - BPM) / BPM < 0.01, a.tempo_bpm
    assert len(a.beats) >= 28
    errs = [abs(b - round(b / BEAT) * BEAT) for b in a.beats]
    assert max(errs) < 0.03, max(errs)
    assert a.section_source == "energy"
    chorus = [s for s in a.sections if s.label == "chorus"]
    assert chorus and chorus[0].start <= 9.0 and chorus[-1].end >= 11.0, a.sections
    assert a.sections[0].start == 0.0 and a.sections[-1].end == SONG_S
    assert a.beats == analyze(fixtures["song"], beats="numpy").beats  # deterministic


@needs_ffmpeg
def test_the_mixing_beat_grid_is_preferred_when_librosa_is_installed(fixtures):
    pytest.importorskip("librosa")
    from muvid.montage.analysis import analyze

    a = analyze(fixtures["song"], beats="auto")
    assert a.beat_source.startswith("mixing.audio.beat_grid")
    assert abs(a.tempo_bpm - BPM) / BPM < 0.05, a.tempo_bpm
    errs = [abs(b - round(b / BEAT) * BEAT) for b in a.beats]
    assert max(errs) < 0.08, max(errs)


@needs_ffmpeg
def test_naming_the_mixing_source_never_falls_back(fixtures, monkeypatch):
    """`auto` degrades to numpy when librosa is absent; `mixing` raises.

    ``sys.modules['librosa'] = None`` is how the import system says "this
    module is known to be absent": ``importlib.import_module`` (what mixing's
    ``require_package`` calls) then raises ImportError, whether or not a real
    librosa is installed on the machine running the suite.
    """
    from muvid.montage.analysis import analyze

    monkeypatch.setitem(sys.modules, "librosa", None)
    a = analyze(fixtures["song"], beats="auto")
    assert "numpy" in a.beat_source
    with pytest.raises(ImportError):
        analyze(fixtures["song"], beats="mixing")


@needs_ffmpeg
def test_supplied_sections_are_used_verbatim(fixtures):
    from muvid.montage.analysis import analyze

    a = analyze(fixtures["song"], beats="numpy",
                sections=[{"label": "chorus", "start": 4, "end": 12}])
    assert a.section_source == "supplied"
    assert [(s.label, s.start, s.end) for s in a.sections] == [
        ("verse", 0.0, 4.0), ("chorus", 4.0, 12.0), ("verse", 12.0, SONG_S)]


@needs_ffmpeg_filter("zoompan")
def test_conformance_kit_with_real_files(fixtures, tmp_path):
    from muvid.subgenres.testing import check_subgenre_conformance

    report = check_subgenre_conformance(
        MONTAGE, workdir=tmp_path,
        inputs={"audio": str(fixtures["song"]),
                "photos": [str(p) for p in fixtures["photos"].values()]},
        params={**CANVAS, "beats": "numpy", "archetype": "beat_cut"},
    )
    assert report.ok, report.summary()
    assert (tmp_path / "conformance" / "out.bin").stat().st_size > 1000


@needs_ffmpeg_filter("zoompan")
def test_beat_cut_render_frames_are_the_planned_photos(fixtures, tmp_path, registered):
    from muvid.subgenres import render_subgenre
    from muvid.visualize import verify_video

    photos = fixtures["photos"]
    names = list(photos)
    out = tmp_path / "out.mp4"
    result = render_subgenre(
        registered,
        inputs={"audio": str(fixtures["song"]), "photos": [str(photos[n]) for n in names]},
        params={**CANVAS, "beats": "numpy", "archetype": "beat_cut"},
        workdir=tmp_path / "wd", output=out,
    )
    assert result.output == out and out.exists()
    assert result.meta["verify_failures"] == []
    assert not [c for c in verify_video(out, audio=fixtures["song"],
                                        expected_canvas=(CANVAS["width"], CANVAS["height"]))
                if not c]
    assert abs(result.duration_s - SONG_S) < 0.1
    plan_path = Path(result.artifacts["plan"])
    assert plan_path.exists()
    plan = json.loads(plan_path.read_text())
    assert plan["slots"] and plan["beat_source"] and result.meta["tempo_bpm"] > 100
    # chorus slots are shorter than verse/intro slots
    chorus = [s for s in plan["slots"] if s["section"] == "chorus"]
    others = [s for s in plan["slots"] if s["section"] != "chorus"]
    assert chorus and others
    assert max(s["end"] - s["start"] for s in chorus) <= min(s["end"] - s["start"] for s in others)
    # the frame at each slot's midpoint IS the planned photo
    seen = set()
    for s in plan["slots"]:
        mid = (s["start"] + s["end"]) / 2
        expected = COLOURS[names[s["tiles"][0]["media"]]]
        got = _mean_rgb(out, mid)
        assert _close(got, expected), (s["index"], mid, expected, got)
        seen.add(s["tiles"][0]["media"])
    assert len(seen) == len(names)


@needs_ffmpeg_filter("zoompan", "xfade")
def test_ballad_blends_are_real_blends_and_the_move_does_not_restart(fixtures, tmp_path):
    from muvid.montage.tools import render_montage

    photos = fixtures["photos"]
    names = list(photos)
    r = render_montage(
        str(fixtures["song"]), str(tmp_path / "b.mp4"), photos=[str(photos[n]) for n in names],
        archetype="ballad_dissolve", beats="numpy", workdir=str(tmp_path / "wd"), **CANVAS,
    )
    assert r["meta"]["verify_failures"] == []
    plan = json.loads(Path(r["artifacts"]["plan"]).read_text())
    blended = [s for s in plan["slots"][1:] if s["transition"] == "fade" and s["transition_s"] > 0]
    assert blended and r["meta"]["n_parts"] > r["meta"]["n_slots"]
    assert r["meta"]["transitions_dropped"] == []
    s = blended[0]
    prev = plan["slots"][s["index"] - 1]
    ca = COLOURS[names[prev["tiles"][0]["media"]]]
    cb = COLOURS[names[s["tiles"][0]["media"]]]
    out = tmp_path / "b.mp4"

    def dist(x, y):
        return sum(abs(p - q) for p, q in zip(x, y))

    # The blend is centred on the cut: pure A before it, pure B after it, and
    # at the boundary itself a frame that is neither — xfade blends in YUV, so
    # the midpoint is not the RGB average, but it is strictly between.
    half = s["transition_s"] / 2
    before = _mean_rgb(out, s["start"] - half - 1 / CANVAS["fps"])
    at_boundary = _mean_rgb(out, s["start"])
    after = _mean_rgb(out, s["start"] + half + 1 / CANVAS["fps"])
    assert _close(before, ca, tol=30), (ca, before)
    assert _close(after, cb, tol=30), (cb, after)
    assert dist(at_boundary, ca) > 60 and dist(at_boundary, cb) > 60, (ca, cb, at_boundary)
    assert dist(before, ca) < dist(at_boundary, ca) < dist(after, ca)


@needs_ffmpeg_filter("zoompan", "xstack")
def test_grid_render_has_four_different_tiles_and_a_clip_in_the_pool(fixtures, tmp_path):
    from muvid.montage.tools import render_montage

    photos = fixtures["photos"]
    names = list(photos)
    r = render_montage(
        str(fixtures["song"]), str(tmp_path / "g.mp4"), photos=[str(photos[n]) for n in names],
        clips=[str(fixtures["clip"])], archetype="grid", beats="numpy",
        workdir=str(tmp_path / "wd"), **CANVAS,
    )
    assert r["meta"]["verify_failures"] == []
    plan = json.loads(Path(r["artifacts"]["plan"]).read_text())
    assert plan["media"][-1]["kind"] == "clip" and plan["media"][-1]["duration"] == 4.0
    w, h = CANVAS["width"] // 2, CANVAS["height"] // 2
    quadrants = {0: f"{w}:{h}:0:0", 1: f"{w}:{h}:{w}:0", 2: f"{w}:{h}:0:{h}", 3: f"{w}:{h}:{w}:{h}"}
    out = tmp_path / "g.mp4"
    checked = 0
    for s in plan["slots"][:6]:
        mid = (s["start"] + s["end"]) / 2
        for t in s["tiles"]:
            m = t["media"]
            if m >= len(names):
                continue  # the clip's colour is not a constant
            got = _mean_rgb(out, mid, crop=quadrants[t["region"]])
            assert _close(got, COLOURS[names[m]], tol=20), (s["index"], t, got)
            checked += 1
    assert checked >= 12


@needs_ffmpeg_filter("zoompan")
def test_stop_motion_with_mono_grade_and_cover(fixtures, tmp_path):
    from muvid.montage.tools import render_montage

    photos = fixtures["photos"]
    names = [n for n in photos if n != "red"]
    r = render_montage(
        str(fixtures["song"]), str(tmp_path / "s.mp4"), photos=[str(photos[n]) for n in names],
        cover=str(photos["red"]),
        treatment={"direction": {"grade": "mono"}, "scenes": [{"archetype": "stop_motion"}]},
        beats="numpy", workdir=str(tmp_path / "wd"), **CANVAS,
    )
    assert r["meta"]["verify_failures"] == [] and r["meta"]["grade"] == "mono"
    plan = json.loads(Path(r["artifacts"]["plan"]).read_text())
    cover = plan["reuse"]["cover"]
    seq = [s["tiles"][0]["media"] for s in plan["slots"]]
    assert seq[0] == cover == seq[-1] and cover not in seq[1:-1]
    rgb = _mean_rgb(tmp_path / "s.mp4", 0.1)
    assert max(rgb) - min(rgb) <= 6, rgb  # grey: the red cover, desaturated
    assert rgb[0] > 20


@needs_ffmpeg
def test_the_plan_is_written_before_rendering_so_a_failed_render_leaves_it(fixtures, tmp_path, monkeypatch):
    import muvid.montage.render as render_mod
    from muvid.montage import pipeline
    from muvid.subgenres import RenderRequest

    def boom(*a, **k):
        raise RuntimeError("ffmpeg is on strike")

    monkeypatch.setattr(render_mod, "render_plan", boom)
    req = RenderRequest(
        subgenre="montage",
        inputs={"audio": str(fixtures["song"]), "photos": [str(p) for p in fixtures["photos"].values()]},
        params={**CANVAS, "beats": "numpy"}, workdir=tmp_path, output=tmp_path / "o.mp4",
    )
    with pytest.raises(RuntimeError, match="strike"):
        pipeline.render(req)
    assert (tmp_path / "plan.json").exists()


@needs_ffmpeg
def test_strict_refuses_a_repaired_treatment(fixtures, tmp_path):
    from muvid.montage import pipeline
    from muvid.subgenres import RenderRequest

    req = RenderRequest(
        subgenre="montage",
        inputs={"audio": str(fixtures["song"]), "photos": [str(p) for p in fixtures["photos"].values()]},
        params={"treatment": {"scenes": [{"archetype": "swirl"}]}, "strict": True},
        workdir=tmp_path, output=tmp_path / "o.mp4",
    )
    with pytest.raises(ValueError, match="strict=True"):
        pipeline.render(req)


@needs_ffmpeg_filter("zoompan")
def test_the_cli_renders_and_plans(fixtures, tmp_path):
    photos = [str(p) for p in fixtures["photos"].values()]
    out = tmp_path / "cli.mp4"
    proc = subprocess.run(
        [sys.executable, "-m", "muvid.montage", "render", str(fixtures["song"]), str(out),
         "--photos", *photos, "--width", "160", "--height", "90", "--fps", "12",
         "--beats", "numpy", "--workdir", str(tmp_path / "wd")],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert Path(payload["output"]) == out and out.exists()
    assert payload["meta"]["verify_failures"] == []

    proc = subprocess.run(
        [sys.executable, "-m", "muvid.montage", "plan", str(fixtures["song"]),
         "--photos", *photos, "--archetype", "grid", "--beats", "numpy",
         "--out", str(tmp_path / "plan.json")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    plan = json.loads(proc.stdout)
    assert plan["slots"][0]["regions"] == 4 and (tmp_path / "plan.json").exists()
    assert plan["treatment_source"] == "archetype"

    proc = subprocess.run(
        [sys.executable, "-m", "muvid.montage", "validate", '{"scenes": [{"archetype": "nope"}]}'],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0 and json.loads(proc.stdout)["valid"] is False
