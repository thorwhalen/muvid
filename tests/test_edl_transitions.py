"""Transitions: an optional blend on an EDL entry's ENTRANCE (muvid#34).

The data-model choice and its two rejected alternatives are argued in
:class:`muvid.footage.edl.Transition`'s docstring. What this file pins is the
part of it that is easy to get wrong and expensive to notice.

**The part plan is per CUT, not per boundary.** The per-boundary reading is the
one a reader reaches for first — "a transition joins two cuts, so emit
solo/xfade/solo for each boundary" — and it double-emits the middle cut's solo on
any chain: for A->B->C with a transition at each boundary it renders 240 frames of
a 180-frame song, showing B's material twice. The counts are the guard, because
the assembler's whole contract is that total frames equal
``round(song_duration*fps)``.

**A solo part's in-point is not its cut's.** A cut whose head was consumed by an
incoming transition starts LATER in its clip by exactly the frames the blend
already showed. Without that advance the solo replays them and then runs behind
the song for the rest of the cut — a drift that grows with every transitioned
boundary and that no frame count would catch, because the count is still right.

**A transitioned boundary reads PAST its own span**, into source material the EDL
does not name — which is legitimate, and is not the overlap violation
``validate_edl`` already catches. So the coverage rule is evaluated per SIDE, and
gap sides are skipped: a gap's ``color`` source is synthetic and can always supply
the window.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from muvid.footage.assemble import _part_plan
from muvid.footage.edl import (
    MIN_TRANSITION_S,
    TRANSITION_CURVES,
    AssemblyCut,
    EdlEntry,
    FootageAlignment,
    Transition,
    _as_entry,
    derive_cuts,
    fill_gaps,
    validate_edl,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg + ffprobe")

SONG = 6.0
FPS = 30
#: Three clips, each long enough to supply a centred blend on both sides.
_A = FootageAlignment("A", 0.0, 0.9, 6.0, (0.0, 6.0))
_B = FootageAlignment("B", 1.0, 0.9, 6.0, (1.0, 6.0))
_C = FootageAlignment("C", 3.8, 0.9, 6.0, (3.8, 6.0))
ALIGNS = [_A, _B, _C]

T = Transition(0.4)


def _chain(*, t1=T, t2=T) -> list[EdlEntry]:
    """A->B->C, 2 s each, with a transition on each of the two boundaries."""
    return [
        EdlEntry(0.0, 2.0, "A"),
        EdlEntry(2.0, 4.0, "B", t1),
        EdlEntry(4.0, 6.0, "C", t2),
    ]


# --------------------------------------------------------------------------
# The field is additive, in both directions
# --------------------------------------------------------------------------


def test_an_edl_written_before_transitions_existed_is_still_valid():
    plain = [EdlEntry(0.0, 3.0, "A"), EdlEntry(3.0, 6.0, "A")]
    assert all(e.transition is None for e in validate_edl(plain, ALIGNS, SONG))


def test_a_json_entry_with_no_transition_key_reads_as_a_hard_cut():
    assert (
        _as_entry({"song_start": 0, "song_end": 1, "clip_id": "A"}).transition is None
    )
    assert (
        _as_entry(
            {"song_start": 0, "song_end": 1, "clip_id": "A", "transition": None}
        ).transition
        is None
    )


def test_an_untransitioned_entry_serialises_byte_identically():
    """Omit-when-None is what keeps every existing render meta.json unchanged."""
    from muvid.mcp.footage_tools import _edl_json

    assert _edl_json(EdlEntry(0.0, 1.0, "A")) == {
        "song_start": 0.0,
        "song_end": 1.0,
        "clip_id": None if not "A" else "A",
    }
    assert "transition" not in _edl_json(EdlEntry(0.0, 1.0, "A"))


def test_a_transition_survives_the_json_round_trip_as_a_plain_dict():
    """A dataclass here would make ``write_render_meta``'s json.dumps raise."""
    from muvid.mcp.footage_tools import _edl_json

    d = _edl_json(EdlEntry(2.0, 4.0, "B", Transition(0.4, "dissolve")))
    json.dumps(d)  # would TypeError on a dataclass
    assert d["transition"] == {"duration_s": 0.4, "curve": "dissolve"}
    assert _as_entry(d).transition == Transition(0.4, "dissolve")


def test_a_malformed_transition_from_a_caller_raises():
    """`_as_entry` serves the explicit `edl=` argument — a request, not a document."""
    for bad in ({}, {"curve": "fade"}, {"duration_s": "soon"}, 0.4, "0.4"):
        with pytest.raises(ValueError, match="malformed"):
            _as_entry(
                {"song_start": 2, "song_end": 4, "clip_id": "B", "transition": bad}
            )


# --------------------------------------------------------------------------
# validate_edl — the ONE gate, extended
# --------------------------------------------------------------------------


def test_a_transition_on_the_first_entry_is_rejected_not_ignored():
    edl = [EdlEntry(0.0, 3.0, "A", T), EdlEntry(3.0, 6.0, "A")]
    with pytest.raises(ValueError, match="nothing to blend in FROM"):
        validate_edl(edl, ALIGNS, SONG)


def test_an_unknown_curve_is_refused_here_not_discovered_in_ffmpeg():
    edl = _chain(t1=Transition(0.4, "swirl"))
    with pytest.raises(ValueError, match="unknown transition curve"):
        validate_edl(edl, ALIGNS, SONG)
    assert "swirl" not in TRANSITION_CURVES


def test_a_sub_floor_transition_is_refused_rather_than_rendering_as_a_hard_cut():
    edl = _chain(t1=Transition(MIN_TRANSITION_S / 2))
    with pytest.raises(ValueError, match="below the"):
        validate_edl(edl, ALIGNS, SONG)


def test_two_transitions_that_each_fit_can_together_overrun_one_span():
    """The per-transition rule is not enough, and this is the case that shows it.

    A 1.2 s span between two 1.0 s transitions: each needs only 0.5 s of it (its
    own head, the next one's tail), so a per-transition check passes both — while
    together they claim 1.0 s of head and tail from a span that has 1.2 s, leaving
    0.2 s. Push it to 1.6 s each and the halves are 0.8 + 0.8 = 1.6 > 1.2.
    """
    aligns = [
        FootageAlignment("A", 0.0, 0.9, 6.0, (0.0, 6.0)),
        FootageAlignment("B", 0.0, 0.9, 6.0, (0.0, 6.0)),
        FootageAlignment("C", 0.0, 0.9, 6.0, (0.0, 6.0)),
    ]
    edl = [
        EdlEntry(0.0, 2.4, "A"),
        EdlEntry(2.4, 3.6, "B", Transition(1.6)),  # 0.8 s of B's head
        EdlEntry(3.6, 6.0, "C", Transition(1.6)),  # 0.8 s of B's tail
    ]
    with pytest.raises(ValueError, match="Together they exceed it"):
        validate_edl(edl, aligns, SONG)


def test_a_transition_longer_than_the_span_it_blends_from():
    """The backward term on its own — the predecessor carries no transition."""
    aligns = [FootageAlignment(c, 0.0, 0.9, 6.0, (0.0, 6.0)) for c in "AB"]
    edl = [EdlEntry(0.0, 0.2, "A"), EdlEntry(0.2, 6.0, "B", Transition(1.0))]
    with pytest.raises(ValueError, match="from the END of entry 0"):
        validate_edl(edl, aligns, SONG)


def test_a_transition_longer_than_the_span_it_blends_into():
    """The forward term on its own — nothing follows to claim the tail."""
    aligns = [FootageAlignment(c, 0.0, 0.9, 6.0, (0.0, 6.0)) for c in "AB"]
    edl = [EdlEntry(0.0, 5.8, "A"), EdlEntry(5.8, 6.0, "B", Transition(1.0))]
    with pytest.raises(ValueError, match="from the START of its own"):
        validate_edl(edl, aligns, SONG)


def test_the_outgoing_clip_must_hold_the_post_roll():
    """A blend reads PAST the outgoing entry's span — that clip has to have it."""
    short = FootageAlignment("A", 0.0, 0.9, 2.05, (0.0, 2.05))  # ends 0.05 s after
    with pytest.raises(ValueError, match="PAST the end"):
        validate_edl(_chain(t2=None), [short, _B, _C], SONG)


def test_the_incoming_clip_must_hold_the_pre_roll():
    """...and reads BEFORE the incoming entry's span starts."""
    tight = FootageAlignment("B", 1.95, 0.9, 6.0, (1.95, 6.0))  # span begins 0.05 s in
    with pytest.raises(ValueError, match="BEFORE its span starts"):
        validate_edl(_chain(t2=None), [_A, tight, _C], SONG)


def test_a_gap_side_never_fails_the_coverage_rule():
    """A gap's black source is synthetic — it can always supply the window."""
    edl = [EdlEntry(0.0, 2.0, ""), EdlEntry(2.0, 6.0, "B", T)]
    assert validate_edl(edl, ALIGNS, SONG)[1].transition == T


def test_a_valid_chain_passes_and_keeps_its_transitions():
    got = validate_edl(_chain(), ALIGNS, SONG)
    assert [e.transition for e in got] == [None, T, T]


# --------------------------------------------------------------------------
# fill_gaps: whose predecessor is it?
# --------------------------------------------------------------------------


def test_a_transition_on_the_first_footage_is_rejected_when_it_starts_at_zero():
    edl = fill_gaps([EdlEntry(0.0, 6.0, "A", T)], SONG)
    with pytest.raises(ValueError, match="nothing to blend in FROM"):
        validate_edl(edl, ALIGNS, SONG)


def test_the_same_transition_becomes_a_fade_from_black_after_a_head_gap():
    """Both follow from one rule: a transition blends in from whatever precedes it.

    Pinned together with the test above so the asymmetry stays a deliberate
    consequence rather than something a later reader 'fixes'.
    """
    edl = fill_gaps([EdlEntry(2.0, 6.0, "B", T)], SONG)
    got = validate_edl(edl, ALIGNS, SONG)
    assert got[0].is_gap and got[1].transition == T


# --------------------------------------------------------------------------
# derive_cuts carries it, and gains no arithmetic
# --------------------------------------------------------------------------


def test_derive_cuts_carries_the_transition_on_both_branches():
    edl = validate_edl(
        [
            EdlEntry(0.0, 2.0, "A"),
            EdlEntry(2.0, 4.0, "", T),
            EdlEntry(4.0, 6.0, "C", T),
        ],
        ALIGNS,
        SONG,
    )
    cuts = derive_cuts(edl, ALIGNS, {"A": "a.mp4", "C": "c.mp4"})
    assert [c.transition for c in cuts] == [None, T, T]
    # ...and clip_in is untouched by the transition: the extra material is measured
    # in FRAMES, which only the assembler knows.
    assert cuts[2].clip_in == pytest.approx(4.0 - _C.offset_s)


# --------------------------------------------------------------------------
# The part plan (pure) — the frame arithmetic
# --------------------------------------------------------------------------


def _cuts(*, t1=T, t2=T) -> list[AssemblyCut]:
    return derive_cuts(
        validate_edl(_chain(t1=t1, t2=t2), ALIGNS, SONG),
        ALIGNS,
        {"A": "a.mp4", "B": "b.mp4", "C": "c.mp4"},
    )


def test_an_untransitioned_plan_is_one_solo_per_cut_at_full_length():
    plan = _part_plan(_cuts(t1=None, t2=None), FPS)
    assert [(p.kind, p.n_frames) for p in plan] == [("solo", 60)] * 3
    assert [p.clip_in for p in plan] == [c.clip_in for c in _cuts(t1=None, t2=None)]


def test_the_plan_is_per_cut_so_a_chain_does_not_double_emit_the_middle_solo():
    """The 240-frames-for-a-180-frame-song bug, pinned by shape and by total."""
    plan = _part_plan(_cuts(), FPS)
    assert [(p.kind, p.n_frames) for p in plan] == [
        ("solo", 54),
        ("xfade", 12),
        ("solo", 48),
        ("xfade", 12),
        ("solo", 54),
    ]
    assert sum(p.n_frames for p in plan) == round(SONG * FPS) == 180


def test_a_solo_after_a_transition_starts_later_in_its_clip():
    """Otherwise it replays the blended frames and runs behind for the whole cut."""
    cuts = _cuts()
    plan = _part_plan(cuts, FPS)
    solo_b = plan[2]
    assert solo_b.kind == "solo"
    # 6 frames of B's head were shown by the blend (12-frame centred transition).
    assert solo_b.clip_in == pytest.approx(cuts[1].clip_in + 6 / FPS)


def test_both_xfade_inputs_are_seeked_to_the_same_instant():
    """Which is why the filter's ``offset=0`` is right and nothing is duplicated."""
    cuts = _cuts()
    x = _part_plan(cuts, FPS)[1]
    assert x.kind == "xfade"
    assert x.prev_in == pytest.approx(cuts[0].clip_in + cuts[0].duration - 6 / FPS)
    assert x.clip_in == pytest.approx(cuts[1].clip_in - 6 / FPS)


def _raw_cuts(*, t1, t2) -> list[AssemblyCut]:
    """Cuts built directly, bypassing `validate_edl`.

    The part plan takes ``AssemblyCut``s, so these tests are about ITS arithmetic;
    routing them through validate would make them fail on the coverage rule for
    long transitions instead of measuring what they name.
    """
    return [
        AssemblyCut(0.0, 2.0, "A", 2.0, "a.mp4"),
        AssemblyCut(2.0, 4.0, "B", 2.0, "b.mp4", t1),
        AssemblyCut(4.0, 6.0, "C", 2.0, "c.mp4", t2),
    ]


def test_the_counts_telescope_for_every_transition_length():
    for d in (0.1, 0.2, 0.4, 0.5, 0.9, 1.3):
        cuts = _raw_cuts(t1=Transition(d), t2=Transition(d))
        assert sum(p.n_frames for p in _part_plan(cuts, FPS)) == round(SONG * FPS), d


def test_a_transition_that_does_not_fit_at_this_fps_fails_before_any_encoding():
    cuts = [
        AssemblyCut(0.0, 2.0, "A", 0.0, "a.mp4"),
        AssemblyCut(2.0, 2.2, "B", 0.0, "b.mp4", Transition(0.4)),
        AssemblyCut(2.2, 6.0, "C", 0.0, "c.mp4", Transition(0.4)),
    ]
    with pytest.raises(ValueError, match="transitions claim"):
        _part_plan(cuts, FPS)


def test_a_transition_that_rounds_to_zero_frames_warns_rather_than_no_opping():
    """`MIN_TRANSITION_S` is a song-time floor and cannot know the render rate.

    A 0.05 s transition clears it, and still rounds away at 10 fps. The renderer
    is the only place that can see this, so it is the one that must say so.
    """
    cuts = _raw_cuts(t1=Transition(0.05), t2=None)
    with pytest.warns(RuntimeWarning, match="rounds to zero frames"):
        plan = _part_plan(cuts, 10)
    assert [p.kind for p in plan] == ["solo", "solo", "solo"]


# --------------------------------------------------------------------------
# The lacing bridge — the one place the annotation body actually changes
# --------------------------------------------------------------------------


def test_an_untransitioned_documents_bodies_are_unchanged():
    from muvid.footage.lacing_bridge import _edl_body

    assert _edl_body(EdlEntry(0.0, 2.0, "A")) == {"clip_id": "A"}
    assert _edl_body(EdlEntry(0.0, 2.0, "")) == {"clip_id": None}


def test_a_transition_survives_the_editor_round_trip():
    """Without this the editor would silently DROP a transition it round-tripped."""
    lacing = pytest.importorskip("lacing")  # noqa: F841
    from muvid.footage.lacing_bridge import edl_annotations, edl_from_annotations

    entries = validate_edl(_chain(), ALIGNS, SONG)
    anns = edl_annotations(entries, song_asset_id="s" * 64, attributed_to="t")
    back = edl_from_annotations(anns, expected_song_asset_id="s" * 64)
    assert [validate_edl(back, ALIGNS, SONG)[i].transition for i in (0, 1, 2)] == [
        None,
        T,
        T,
    ]


def test_a_malformed_transition_from_the_editor_is_skipped_not_raised():
    """Opposite posture to `_as_entry` — this reads a browser's output."""
    pytest.importorskip("lacing")
    from muvid.footage.lacing_bridge import edl_annotations, edl_from_annotations

    anns = edl_annotations(
        validate_edl(_chain(), ALIGNS, SONG),
        song_asset_id="s" * 64,
        attributed_to="t",
    )
    anns[1].body["transition"] = {"curve": "fade"}  # no duration_s
    back = edl_from_annotations(anns)
    assert "transition" not in back[1]
    assert back[2]["transition"] == {"duration_s": 0.4, "curve": "fade"}


# --------------------------------------------------------------------------
# End to end, through ffmpeg
# --------------------------------------------------------------------------


def _clip(tmp_path, name, seconds, *, src="testsrc2"):
    out = tmp_path / f"{name}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"{src}=size=320x180:rate=30:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            str(out),
        ],
        check=True,
    )
    return out


def _nb_frames(path) -> int:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(r.stdout.strip())


@needs_ffmpeg
def test_a_transitioned_chain_renders_exactly_the_songs_frames(tmp_path):
    """The assembler's contract, with transitions: frames == round(song*fps)."""
    from muvid.footage.assemble import assemble_music_video
    from muvid.visualize.ffmpeg import has_filter

    if not has_filter("xfade"):
        pytest.skip("this ffmpeg build has no 'xfade' filter")
    paths = {n: str(_clip(tmp_path, n, 7.0)) for n in ("A", "B", "C")}
    song = tmp_path / "song.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={SONG}",
            str(song),
        ],
        check=True,
    )
    cuts = derive_cuts(validate_edl(_chain(), ALIGNS, SONG), ALIGNS, paths)
    out = assemble_music_video(
        cuts,
        song_path=str(song),
        out_path=str(tmp_path / "final.mp4"),
        canvas=(320, 180),
        fps=FPS,
    )
    assert _nb_frames(out) == round(SONG * FPS)


@needs_ffmpeg
def test_the_blend_is_a_monotone_ramp_from_the_outgoing_clip_to_the_incoming(tmp_path):
    """Asserts MONOTONICITY, not endpoint identity — see `_render_transition`.

    With ``offset=0`` and ``duration=n/fps`` the filter's progress runs
    ``0 .. (n-1)/n`` across the emitted frames, so the first frame is not
    byte-identical to pure A nor the last to pure B. An earlier draft of this
    design claimed the seams were "seamless"; the SSIM numbers it cited (0.997,
    0.964) already said otherwise. What IS true, and what a broken offset or a
    swapped input would destroy, is that the ramp is monotone in both directions.
    """
    from muvid.footage.assemble import _render_transition, _part_plan
    from muvid.visualize.ffmpeg import has_filter

    if not has_filter("xfade"):
        pytest.skip("this ffmpeg build has no 'xfade' filter")
    a = _clip(tmp_path, "A", 7.0, src="testsrc2")
    b = _clip(tmp_path, "B", 7.0, src="smptebars")
    cuts = derive_cuts(
        validate_edl(_chain(t2=None), ALIGNS, SONG),
        ALIGNS,
        {"A": str(a), "B": str(b), "C": str(a)},
    )
    x = [p for p in _part_plan(cuts, FPS) if p.kind == "xfade"][0]
    blend = tmp_path / "x.mp4"
    _render_transition(x, blend, w=320, h=180, fps=FPS, crf=18, preset="veryfast")
    assert _nb_frames(blend) == x.n_frames

    def _pure(src, clip_in, out):
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{clip_in:.6f}",
                "-t",
                f"{(x.n_frames + 1) / FPS:.6f}",
                "-i",
                str(src),
                "-vf",
                "scale=320:180:force_original_aspect_ratio=decrease,"
                "pad=320:180:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,"
                "tpad=stop=-1:stop_mode=clone,format=yuv420p",
                "-frames:v",
                str(x.n_frames),
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "18",
                str(out),
            ],
            check=True,
        )
        return out

    pa = _pure(a, x.prev_in, tmp_path / "pa.mp4")
    pb = _pure(b, x.clip_in, tmp_path / "pb.mp4")

    def _ssim(u, v):
        r = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(u),
                "-i",
                str(v),
                "-lavfi",
                "ssim=stats_file=-",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return [float(m) for m in re.findall(r"All:([0-9.]+)", r.stdout)]

    to_a, to_b = _ssim(blend, pa), _ssim(blend, pb)
    assert len(to_a) == len(to_b) == x.n_frames
    assert all(to_a[i] >= to_a[i + 1] - 1e-6 for i in range(len(to_a) - 1)), to_a
    assert all(to_b[i] <= to_b[i + 1] + 1e-6 for i in range(len(to_b) - 1)), to_b
    assert to_a[0] > to_b[0] and to_b[-1] > to_a[-1]
