"""A moving look declares itself, so the assembler can warn about it (muvid#73).

The behaviour being warned about is real and measured here rather than quoted: a
transitioned boundary renders as a separate two-input invocation whose inputs are
input-side-seeked to the blend window, and input-side ``-ss`` rebases the filter
timeline to 0 — so a look whose expressions read that clock starts its ramp
AGAIN for the length of the blend.
``test_the_ramp_really_does_restart_on_a_blended_boundary`` is that measurement,
against decoded pixels; everything else here is the declaration and the warning
built on top of it.

The shape is the maintainer's decision (muvid#73 option 3): the EDL says which
kind a look is, because the seam is a bare string and muvid cannot tell a punch
from a grade by looking at it. Rebasing the fragment — option 4 — is refused for
the reason ``looks``' rule 27 gives: it means rewriting an arbitrary ffmpeg
expression, and a wrong rebase moves the effect to a different second of the clip
at exit 0.

What the warning must NOT do is fire on a static look at a transitioned boundary.
That was the whole obstacle to implementing it before the flag existed, and a
warning on every graded transitioned cut is one nobody reads.
"""

from __future__ import annotations

import subprocess
import warnings

import pytest

from muvid.footage.assemble import _part_filter, _part_plan
from muvid.footage.edl import (
    AssemblyCut,
    CropWindow,
    EdlEntry,
    FootageAlignment,
    Transition,
    derive_cuts,
    validate_edl,
)
from muvid.footage.look import (
    LookFragment,
    chain,
    is_time_varying,
    motion,
    punch_in,
    punch_in_cuts,
)
from tests.ffmpeg_support import needs_ffmpeg

SONG_DUR = 20.0
_A = FootageAlignment("A", 0.0, 0.9, 20.0, (0.0, 20.0))
_GREY = "hue=s=0"
_PUNCH = punch_in(canvas=(640, 360), fps=25, duration_s=3.0)


def _cut(**kw) -> AssemblyCut:
    base = dict(
        song_start=0.0, song_end=4.0, clip_id="A", clip_in=0.0, clip_path="/tmp/a.mp4"
    )
    return AssemblyCut(**{**base, **kw})


def _plan_warnings(cuts, fps=25):
    """Every RuntimeWarning ``_part_plan`` raises for these cuts, as strings."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _part_plan(cuts, fps)
    return [str(w.message) for w in caught]


def _restart_warnings(cuts, fps=25):
    return [m for m in _plan_warnings(cuts, fps) if "time-varying look" in m]


# -- the measurement the warning is about ------------------------------------


@needs_ffmpeg
def test_the_ramp_really_does_restart_on_a_blended_boundary(tmp_path):
    """muvid#73, reproduced against DECODED pixels before anything is claimed.

    A 3.0 s cut at 25 fps, a 0.4 s fade after it, ``punch_in(zoom=1.12)``. Each
    part is rendered twice — with the look and without it — and compared to its
    OWN unlooked twin, because the two parts show different source frames and
    comparing them to each other would measure the footage, not the zoom.

    The solo part's last frame is drawn at zoom 1.109 and differs from its twin
    by tens of levels. The blend part's first frame is drawn at zoom 1.000 and is
    indistinguishable from having no punch at all — which is the bug: the move
    ran to 1.109 and was then redrawn from the start.
    """
    w, h, fps = 320, 180, 25
    src = tmp_path / "a.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={w}x{h}:rate={fps}:d=6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    look = punch_in(canvas=(w, h), fps=fps, duration_s=3.0)
    a = _cut(song_end=3.0, clip_path=str(src), look=look, look_time_varying=True)
    b = _cut(
        song_start=3.0,
        song_end=6.0,
        clip_id="B",
        clip_path=str(src),
        transition=Transition(0.4, "fade"),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        plan = _part_plan([a, b], fps)
    solo, blend = plan[0], plan[1]
    assert (solo.kind, blend.kind) == ("solo", "xfade")

    def render(name, n_frames, clip_in, look_):
        out = tmp_path / f"{name}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                f"{clip_in:.6f}",
                "-t",
                f"{3.0 + 1.0 / fps:.6f}",
                "-i",
                str(src),
                "-vf",
                _part_filter(
                    _cut(song_end=3.0, clip_path=str(src), look=look_),
                    w=w,
                    h=h,
                    fps=fps,
                ),
                "-frames:v",
                str(n_frames),
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "18",
                "-preset",
                "veryfast",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        return out

    def pixels(video, index):
        raw = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(video),
                "-vf",
                f"select=eq(n\\,{index})",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            check=True,
            capture_output=True,
        ).stdout
        return raw

    def mean_abs_diff(x, y):
        assert len(x) == len(y) and len(x) == w * h * 3
        return sum(abs(p - q) for p, q in zip(x, y)) / len(x)

    solo_look = render("solo_look", solo.n_frames, solo.clip_in, look)
    solo_bare = render("solo_bare", solo.n_frames, solo.clip_in, None)
    # The A SIDE of the blend, seeked exactly as `_render_transition` seeks it.
    blend_look = render("blend_look", blend.n_frames, blend.prev_in, look)
    blend_bare = render("blend_bare", blend.n_frames, blend.prev_in, None)

    solo_diff = mean_abs_diff(
        pixels(solo_look, solo.n_frames - 1), pixels(solo_bare, solo.n_frames - 1)
    )
    blend_diff = mean_abs_diff(pixels(blend_look, 0), pixels(blend_bare, 0))
    assert solo_diff > 10, (
        f"the punch barely moved the solo part ({solo_diff:.2f}/255) — this test "
        "cannot show a restart it never measured a move in."
    )
    assert blend_diff < 2, (
        f"the blend's first frame differs from its unlooked twin by "
        f"{blend_diff:.2f}/255, so the ramp did NOT restart and the warning "
        "below is about nothing."
    )


# -- the declaration ---------------------------------------------------------


def test_muvids_own_compilers_declare_what_they_emit():
    assert is_time_varying(punch_in(canvas=(640, 360), fps=25, duration_s=3.0))
    assert is_time_varying(
        motion(
            [(0.0, CropWindow(0, 0, 1, 1)), (2.0, CropWindow(0.2, 0.2, 0.6, 0.6))],
            canvas=(640, 360),
            fps=25,
        )
    )
    # …and the fragment is still a plain string everywhere else.
    assert punch_in(canvas=(640, 360), fps=25, duration_s=3.0).startswith("zoompan=")
    assert isinstance(_PUNCH, str)


@needs_ffmpeg
def test_stylize_answers_from_the_compiled_plan_not_from_an_assumption():
    """A grade is static; a ``motion`` step and a SPANNED step are not.

    The obvious reading — "stylize is the grade door, so it is always static" —
    is wrong twice, and both wrong cases are reachable from ``stylize`` alone.
    """
    import looks

    from muvid.footage.look import stylize

    grade = stylize(
        looks.Look(steps=(looks.Effect(name="saturation", params={"amount": 0.0}),)),
        canvas=(640, 360),
        fps=25,
        duration_s=3.0,
    )
    assert not is_time_varying(grade)

    moving = stylize(
        looks.Look(
            steps=(
                looks.Effect(
                    name="motion",
                    params={
                        "keyframes": [(0.0, (0, 0, 1, 1)), (2.0, (0.2, 0.2, 0.6, 0.6))]
                    },
                ),
            )
        ),
        canvas=(640, 360),
        fps=25,
        duration_s=3.0,
    )
    assert "in_time" in moving, "the premise: the motion effect ramps on the clock"
    assert is_time_varying(moving)

    spanned = stylize(
        looks.Look(steps=(looks.Effect(name="blur", at=looks.Span(0.5, 1.5)),)),
        canvas=(640, 360),
        fps=25,
        duration_s=3.0,
    )
    assert "enable=" in spanned, "the premise: a span compiles to enable='between(t…)'"
    assert is_time_varying(spanned)


def test_the_time_varying_effect_list_is_pinned_against_looks():
    """A new ``looks`` effect must be CLASSIFIED, not silently assumed static.

    Same maintenance rule as ``LOOK_FILTERS``: pinned rather than derived,
    because deriving would let a new moving effect quietly stop being warned
    about. If this fails, decide whether the new effect reads the clock and
    update ``TIME_VARYING_EFFECTS`` (and this list) deliberately.
    """
    looks = pytest.importorskip("looks")

    from muvid.footage.look import TIME_VARYING_EFFECTS

    assert sorted(looks.effects()) == [
        "blur",
        "contrast",
        "fill",
        "fit",
        "flatten",
        "gamma",
        "gradient_map",
        "levels",
        "lut3d",
        "motion",
        "posterize",
        "saturation",
        "sharpen",
        "stretch",
    ]
    assert TIME_VARYING_EFFECTS == {"motion"}


def test_impl_ref_timeline_is_not_this_property():
    """The near-miss worth pinning: ``ImplRef.timeline`` gets every row backwards.

    It says the implementation supports ffmpeg's ``enable=`` option. ``motion``
    — the one moving effect — declares ``False``, and the static grades declare
    ``True``, so a reader reaching for it as "is this time-varying?" would
    produce exactly the inverse classification.
    """
    looks = pytest.importorskip("looks")

    by_effect = {
        e: {
            i.timeline
            for i in looks.REGISTRY.implementations(e)
            if i.backend == "ffmpeg"
        }
        for e in ("motion", "blur", "gamma")
    }
    assert by_effect["motion"] == {False}
    assert by_effect["blur"] == {True} and by_effect["gamma"] == {True}


def test_a_fragment_survives_copy_and_pickle():
    """Not free, and CI runs no doctests — so the guard lives here.

    ``str`` subclasses are reconstructed through ``__new__``, so without
    ``__getnewargs_ex__`` both ``copy.deepcopy`` and ``pickle`` raise
    ``TypeError: __new__() missing 1 required keyword-only argument`` (measured).
    An ``EdlEntry`` carrying one is an ordinary dataclass a caller may well copy.
    """
    import copy
    import pickle

    frag = LookFragment("zoompan=d=1:s=64x48:fps=25", time_varying=True)
    assert copy.deepcopy(frag).time_varying is True
    assert pickle.loads(pickle.dumps(frag)) == frag
    assert pickle.loads(pickle.dumps(frag)).time_varying is True

    entry = EdlEntry(0.0, 4.0, "A", look=frag, look_time_varying=True)
    assert copy.deepcopy(entry) == entry


def test_chain_ors_the_declarations():
    moving = LookFragment("zoompan=d=1:s=64x48:fps=25", time_varying=True)
    assert chain(_GREY, moving).time_varying
    assert chain(moving, _GREY).time_varying  # order must not matter
    assert not chain(_GREY, "unsharp=5:5:1").time_varying
    assert chain(_GREY, moving) == f"{_GREY},{moving}"


def test_punch_in_cuts_sets_the_flag_from_the_fragment():
    entries = [
        EdlEntry(0.0, 4.0, "A"),
        EdlEntry(4.0, 8.0, "B"),
        EdlEntry(8.0, 12.0, ""),
    ]
    out = punch_in_cuts(entries, canvas=(640, 360), fps=25, every=1)
    assert [e.look_time_varying for e in out] == [True, True, False]
    assert out[2].look is None, "a gap is left alone"
    for e in out[:2]:
        assert e.look_time_varying is True and e.look.startswith("zoompan=")


# -- the wire: field, gate, round trips --------------------------------------


def test_the_flag_survives_the_json_round_trip():
    from muvid.mcp.footage_tools import _edl_json

    caller = [
        {"song_start": 0.0, "song_end": 4.0, "clip_id": "A", "look": _GREY},
        {
            "song_start": 4.0,
            "song_end": 9.0,
            "clip_id": "A",
            "look": _PUNCH,
            "look_time_varying": True,
        },
    ]
    first = validate_edl(caller, [_A], SONG_DUR)
    returned = [_edl_json(e) for e in first]
    assert "look_time_varying" not in returned[0], "omit-when-False, so False is absent"
    assert returned[1]["look_time_varying"] is True
    second = validate_edl(returned, [_A], SONG_DUR)
    assert [e.look_time_varying for e in second] == [False, True]
    assert [_edl_json(e) for e in second] == returned


def test_the_flag_survives_the_editor_round_trip():
    pytest.importorskip("lacing")
    from muvid.footage.lacing_bridge import edl_annotations, edl_from_annotations

    entries = validate_edl(
        [
            {
                "song_start": 0.0,
                "song_end": 4.0,
                "clip_id": "A",
                "look": _PUNCH,
                "look_time_varying": True,
            }
        ],
        [_A],
        4.0,
    )
    anns = edl_annotations(entries, song_asset_id="a" * 64, attributed_to="t")
    assert anns[0].body["look_time_varying"] is True
    [back] = edl_from_annotations(anns)
    assert back["look_time_varying"] is True
    assert validate_edl([back], [_A], 4.0)[0].look_time_varying is True


def test_a_static_entry_leaves_the_editor_body_exactly_as_it_was():
    """Omit-when-FALSE, so no existing DECISION body changes shape."""
    pytest.importorskip("lacing")
    from muvid.footage.lacing_bridge import _edl_body

    assert _edl_body(EdlEntry(0.0, 4.0, "A")) == {"clip_id": "A"}
    assert _edl_body(EdlEntry(0.0, 4.0, "A", look=_GREY)) == {
        "clip_id": "A",
        "look": _GREY,
    }


def test_the_editor_read_skips_a_malformed_flag_rather_than_crashing():
    """Untrusted browser output, same posture as the look and crop reads.

    ``bool("false")`` is ``True``, so forwarding a string would arm a warning the
    caller asked to be off — and ``_as_entry`` would then RAISE on it, turning an
    editor bug into a refused export.
    """
    pytest.importorskip("lacing")
    from muvid.footage.lacing_bridge import edl_annotations, edl_from_annotations

    entries = validate_edl(
        [
            {
                "song_start": 0.0,
                "song_end": 4.0,
                "clip_id": "A",
                "look": _PUNCH,
                "look_time_varying": True,
            }
        ],
        [_A],
        4.0,
    )
    anns = edl_annotations(entries, song_asset_id="a" * 64, attributed_to="t")
    anns[0].body["look_time_varying"] = "false"
    [back] = edl_from_annotations(anns)
    assert "look_time_varying" not in back


def test_a_non_bool_flag_raises_rather_than_being_coerced():
    for bad in ("false", 1, 0, {}):
        with pytest.raises(ValueError, match="look_time_varying is malformed"):
            validate_edl(
                [
                    {
                        "song_start": 0.0,
                        "song_end": 4.0,
                        "clip_id": "A",
                        "look": _GREY,
                        "look_time_varying": bad,
                    }
                ],
                [_A],
                4.0,
            )


def test_the_flag_without_a_look_is_refused():
    with pytest.raises(ValueError, match="carries no look"):
        validate_edl(
            [
                {
                    "song_start": 0.0,
                    "song_end": 4.0,
                    "clip_id": "A",
                    "look_time_varying": True,
                }
            ],
            [_A],
            4.0,
        )


def test_the_flag_reaches_the_assembly_cut():
    entries = validate_edl(
        [
            {
                "song_start": 0.0,
                "song_end": 4.0,
                "clip_id": "A",
                "look": _PUNCH,
                "look_time_varying": True,
            }
        ],
        [_A],
        4.0,
    )
    [cut] = derive_cuts(entries, [_A], {"A": "/tmp/a.mp4"})
    assert cut.look_time_varying is True
    assert AssemblyCut(0.0, 1.0, "A", 0.0, "/x").look_time_varying is False


# -- the warning -------------------------------------------------------------


def test_a_moving_look_on_a_transitioned_boundary_warns_naming_the_cut():
    cuts = [
        _cut(song_end=4.0, look=_PUNCH, look_time_varying=True),
        _cut(
            song_start=4.0,
            song_end=8.0,
            clip_id="B",
            transition=Transition(0.4, "fade"),
        ),
    ]
    [msg] = _restart_warnings(cuts)
    assert msg.startswith("cut 0:"), msg
    assert "the next cut blends IN from it" in msg
    assert "muvid#73" in msg


def test_the_incoming_side_warns_too_and_says_which_side_it_is():
    cuts = [
        _cut(song_end=4.0),
        _cut(
            song_start=4.0,
            song_end=8.0,
            clip_id="B",
            transition=Transition(0.4, "fade"),
            look=_PUNCH,
            look_time_varying=True,
        ),
    ]
    [msg] = _restart_warnings(cuts)
    assert msg.startswith("cut 1:") and "blends IN from the previous cut" in msg


def test_a_cut_between_two_transitions_warns_once():
    """One thing wrong with one cut, acted on once — not once per side."""
    cuts = [
        _cut(song_end=4.0),
        _cut(
            song_start=4.0,
            song_end=8.0,
            clip_id="B",
            transition=Transition(0.4, "fade"),
            look=_PUNCH,
            look_time_varying=True,
        ),
        _cut(
            song_start=8.0,
            song_end=12.0,
            clip_id="A",
            transition=Transition(0.4, "fade"),
        ),
    ]
    msgs = _restart_warnings(cuts)
    assert len(msgs) == 1 and "both of its boundaries are blended" in msgs[0]


@pytest.mark.parametrize(
    "look, flag, transition, why",
    [
        (_GREY, False, True, "a static look on a transitioned boundary"),
        (_PUNCH, True, False, "a moving look on a plain cut"),
        (None, False, True, "no look at all"),
        # The DOCUMENTED limit of the chosen shape: undeclared means silent.
        (_PUNCH, False, True, "a moving look the caller did not declare"),
        # `validate_edl` refuses this pair, but `_part_plan` takes AssemblyCuts
        # directly and must not warn about a look that is not there — the same
        # reason it checks BOTH halves rather than trusting the flag alone.
        (None, True, True, "the flag set with no look to describe"),
    ],
)
def test_what_stays_silent(look, flag, transition, why):
    cuts = [
        _cut(song_end=4.0, look=look, look_time_varying=flag),
        _cut(
            song_start=4.0,
            song_end=8.0,
            clip_id="B",
            transition=Transition(0.4, "fade") if transition else None,
        ),
    ]
    assert _restart_warnings(cuts) == [], f"{why} must not warn"


@pytest.mark.parametrize("moving_side", [0, 1])
def test_a_transition_that_rounds_away_takes_its_look_warning_with_it(moving_side):
    """A boundary with zero blended frames is a hard cut, so nothing restarts.

    The condition is the FRAME count, not the presence of a ``Transition``
    record — the same number the zero-frame warning next door is about. Both
    warnings fire from one pass, and this pins that they agree.

    Parameterised over WHICH side carries the moving look, because the two sides
    are read by two different expressions: ``moving_side=1`` is the only case
    that exercises the incoming term, and with the moving look only on cut 0 a
    mutation that reads cut 1's ``Transition`` record instead of its frame count
    survives — measured.
    """
    cuts = [
        _cut(
            song_end=4.0,
            look=_PUNCH if moving_side == 0 else None,
            look_time_varying=moving_side == 0,
        ),
        _cut(
            song_start=4.0,
            song_end=8.0,
            clip_id="B",
            transition=Transition(0.01, "fade"),
            look=_PUNCH if moving_side == 1 else None,
            look_time_varying=moving_side == 1,
        ),
    ]
    msgs = _plan_warnings(cuts, fps=25)
    assert any("rounds to zero frames" in m for m in msgs)
    assert not any("time-varying look" in m for m in msgs)


def test_the_warning_does_not_change_the_plan():
    """It is a warning, not a refusal — the same parts are rendered either way."""
    moving = [
        _cut(song_end=4.0, look=_PUNCH, look_time_varying=True),
        _cut(
            song_start=4.0,
            song_end=8.0,
            clip_id="B",
            transition=Transition(0.4, "fade"),
        ),
    ]
    static = [
        _cut(song_end=4.0, look=_PUNCH, look_time_varying=False),
        _cut(
            song_start=4.0,
            song_end=8.0,
            clip_id="B",
            transition=Transition(0.4, "fade"),
        ),
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = [(p.kind, p.n_frames, p.clip_in, p.prev_in) for p in _part_plan(moving, 25)]
        b = [(p.kind, p.n_frames, p.clip_in, p.prev_in) for p in _part_plan(static, 25)]
    assert a == b


# ---------------------------------------------------------------------------
# ...and the caller has to be able to SEE it
# ---------------------------------------------------------------------------


def test_the_plan_hands_every_finding_to_the_reply_sink_as_well():
    """``warnings.warn`` reaches stderr; ``on_note`` reaches the caller.

    Both, never either. The warning is what a developer and ``pytest.warns`` see;
    the note is the only thing a remote MCP caller can ever see, and until it
    existed a muvid#73 hitch came back as an ``ok`` render with nothing said
    about it. The two carry the SAME text, asserted here rather than assumed, so
    a future edit cannot improve one message and leave the other behind.

    Both kinds of finding are in the corpus -- the zero-frame transition as well
    as the restarted ramp -- because the sink is one path and a test containing
    only one of them would pass with the other left on stderr.
    """
    cuts = [
        _cut(song_end=4.0, look=_PUNCH, look_time_varying=True),
        _cut(
            song_start=4.0,
            song_end=8.0,
            clip_id="B",
            transition=Transition(0.4, "fade"),
        ),
        # 0.02 s at 25 fps rounds to 0 frames: the OTHER finding _part_plan makes
        _cut(
            song_start=8.0,
            song_end=12.0,
            clip_id="C",
            transition=Transition(0.02, "fade"),
        ),
    ]
    notes = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _part_plan(cuts, 25, notes.append)
    assert notes == [str(w.message) for w in caught], (
        "the sink and the warning must carry the same findings, in the same order"
    )
    assert any("time-varying look" in n for n in notes)
    assert any("rounds to zero frames" in n for n in notes)


def test_a_finding_is_an_AssemblyWarning_so_the_reply_half_can_pick_it_out():
    """And it stays a ``RuntimeWarning``, so every existing catcher keeps working."""
    from muvid.footage.assemble import AssemblyWarning

    assert issubclass(AssemblyWarning, RuntimeWarning)
    cuts = [
        _cut(song_end=4.0, look=_PUNCH, look_time_varying=True),
        _cut(
            song_start=4.0,
            song_end=8.0,
            clip_id="B",
            transition=Transition(0.4, "fade"),
        ),
    ]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _part_plan(cuts, 25)
    assert caught and all(w.category is AssemblyWarning for w in caught)


def test_the_mcp_reply_carries_the_findings_the_render_plan_made(tmp_path, monkeypatch):
    """End to end through the live tool, with the REAL assembler and no encoding.

    muvid#73's stated purpose is to warn. It did -- into the server process's
    stderr, which the caller of a per-caller MCP connector has no access to, and
    ``assemble_music_video``'s reply had no key for it at all. So the finding was
    served on the developer path only: a remote caller got ``ok: true`` and a
    video with the hitch in it.

    ``run_ffmpeg`` is stubbed and nothing else is: ``_part_plan`` is the real one,
    so this asserts the whole path from the plan to the reply rather than the
    plumbing of a fake. The positive control is the second half -- a plain edit
    returns the key with an EMPTY list, which is what makes "no warnings" and
    "an older build" different readings.
    """
    pytest.importorskip("fastmcp")
    import muvid.mcp.footage_tools as ft
    import muvid.visualize as V
    import muvid.visualize.ffmpeg as F
    from muvid.footage.workspace import FootageWorkspace
    from muvid.mcp.identity import use_email

    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(F, "require_ffmpeg", lambda *a, **k: None)
    monkeypatch.setattr(F, "probe", lambda *a, **k: {})
    monkeypatch.setattr(F, "run_ffmpeg", lambda args, **k: None)
    monkeypatch.setattr(V, "verify_video", lambda *a, **k: [])
    monkeypatch.setattr(V, "failures", lambda c: [])
    monkeypatch.setattr(V, "report", lambda c: "ok")

    proj = FootageWorkspace.for_email("u@x.com").create_project("warn")
    (proj.root / "song").mkdir()
    (proj.root / "song" / "song.wav").write_bytes(b"x")
    (proj.root / "clips").mkdir()
    for cid in ("A", "B"):
        (proj.root / "clips" / f"{cid}.mp4").write_bytes(b"x")
    m = proj.manifest()
    m.update(
        song="song.wav",
        song_duration=8.0,
        clips=[{"clip_id": c, "file": f"{c}.mp4", "name": c} for c in ("A", "B")],
    )
    proj._write_manifest(m)
    proj.save_alignments(
        [
            FootageAlignment("A", 0.0, 0.9, 20.0, (0.0, 20.0)),
            FootageAlignment("B", 0.0, 0.9, 20.0, (0.0, 20.0)),
        ]
    )

    def _edl(moving):
        return [
            {
                "song_start": 0.0,
                "song_end": 4.0,
                "clip_id": "A",
                "look": _PUNCH,
                "look_time_varying": moving,
            },
            {
                "song_start": 4.0,
                "song_end": 8.0,
                "clip_id": "B",
                "transition": {"duration_s": 0.4, "curve": "fade"},
            },
        ]

    with use_email("u@x.com"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        moving = ft.assemble_music_video("warn", edl=_edl(True))
        static = ft.assemble_music_video("warn", edl=_edl(False))

    assert moving["ok"] is True, "a warning is not a failure -- the render succeeded"
    assert any("time-varying look" in w for w in moving["warnings"]), moving["warnings"]
    assert "warnings" in static, (
        "the key must be PRESENT and empty, never absent -- an absent key makes "
        "'nothing to report' and 'an older build' the same reading"
    )
    assert static["warnings"] == [], (
        "the positive control: a static look on the same boundary reports nothing, "
        "so the key is not simply always populated"
    )
