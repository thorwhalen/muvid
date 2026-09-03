"""The EDL's spatial half (muvid#60): a per-cut crop window.

The contract under test: a crop is a normalised rectangle, it is validated by the
ONE gate, it survives the JSON round trip and `derive_cuts`, an EDL without one is
byte-identical through the render path, and — the part a frame count cannot check —
**the pixels that come out are actually the requested rectangle**.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from muvid.footage.assemble import _crop_filter
from muvid.footage.edl import (
    AssemblyCut,
    CropWindow,
    EdlEntry,
    FootageAlignment,
    derive_cuts,
    validate_edl,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg + ffprobe")

SONG_DUR = 10.0
_A = FootageAlignment("A", 0.0, 0.9, 10.0, (0.0, 10.0))
_FULL = CropWindow(0.0, 0.0, 1.0, 1.0)


# -- the type ---------------------------------------------------------------


def test_crop_window_round_trips_through_json_shape():
    c = CropWindow(0.1, 0.25, 0.8, 0.5)
    assert CropWindow.from_dict(c.to_dict()) == c


def test_a_dict_edl_entry_carries_crop_and_crop_end():
    [e] = validate_edl(
        [
            {
                "song_start": 0.0,
                "song_end": 4.0,
                "clip_id": "A",
                "crop": {"x": 0.0, "y": 0.3, "w": 1.0, "h": 0.4},
                "crop_end": {"x": 0.0, "y": 0.5, "w": 1.0, "h": 0.4},
            }
        ],
        [_A],
        SONG_DUR,
    )
    assert e.crop == CropWindow(0.0, 0.3, 1.0, 0.4)
    assert e.crop_end == CropWindow(0.0, 0.5, 1.0, 0.4)


def test_a_malformed_crop_raises_rather_than_being_dropped():
    # Same posture as a malformed transition: `edl=` is the caller's REQUEST, and a
    # framing that silently becomes the whole letterboxed frame is undetectable.
    with pytest.raises(ValueError, match="crop is malformed"):
        validate_edl(
            [{"song_start": 0.0, "song_end": 4.0, "clip_id": "A", "crop": {"x": 0.0}}],
            [_A],
            SONG_DUR,
        )


# -- the gate ---------------------------------------------------------------


def _entry(**kw):
    return EdlEntry(0.0, 4.0, "A", **kw)


@pytest.mark.parametrize(
    "crop, match",
    [
        (CropWindow(0.0, 0.0, 0.0, 0.5), "non-positive size"),
        (CropWindow(0.0, 0.0, 0.5, -0.1), "non-positive size"),
        (CropWindow(-0.1, 0.0, 0.5, 0.5), "outside the source frame"),
        (CropWindow(0.6, 0.0, 0.5, 0.5), "outside the source frame"),
        (CropWindow(0.0, 0.6, 0.5, 0.5), "outside the source frame"),
    ],
)
def test_a_window_that_is_not_inside_the_frame_is_refused(crop, match):
    with pytest.raises(ValueError, match=match):
        validate_edl([_entry(crop=crop)], [_A], SONG_DUR)


def test_the_whole_frame_is_a_legal_window():
    [e] = validate_edl([_entry(crop=_FULL)], [_A], SONG_DUR)
    assert e.crop == _FULL


def test_crop_end_without_crop_is_refused():
    with pytest.raises(ValueError, match="crop_end but no crop"):
        validate_edl([_entry(crop_end=_FULL)], [_A], SONG_DUR)


def test_crop_end_may_not_resize_the_window():
    # `crop` evaluates `w`/`h` once, at configure time, so it cannot vary its
    # output size mid-cut at all — it either refuses to configure or freezes at one
    # wrong size. A push-in is a different fixed window on the next cut.
    with pytest.raises(ValueError, match="same SIZE"):
        validate_edl(
            [
                _entry(
                    crop=CropWindow(0, 0, 1.0, 0.5), crop_end=CropWindow(0, 0, 0.8, 0.4)
                )
            ],
            [_A],
            SONG_DUR,
        )


def test_a_gap_may_not_carry_a_crop():
    with pytest.raises(ValueError, match="gap but carries a crop"):
        validate_edl([EdlEntry(0.0, 4.0, "", crop=_FULL)], [_A], SONG_DUR)


# -- carry-through ----------------------------------------------------------


def test_derive_cuts_carries_the_window_unchanged():
    c, e = CropWindow(0.0, 0.3, 1.0, 0.4), CropWindow(0.0, 0.5, 1.0, 0.4)
    entries = validate_edl([_entry(crop=c, crop_end=e)], [_A], SONG_DUR)
    [cut] = derive_cuts(entries, [_A], {"A": "/nonexistent.mp4"})
    assert (cut.crop, cut.crop_end) == (c, e)


# -- the compiler -----------------------------------------------------------


def _cut(**kw):
    return AssemblyCut(0.0, 4.0, "A", 0.0, "/x.mp4", **kw)


def test_no_crop_emits_no_filter_at_all():
    # This is what keeps every EDL written before this field existed byte-identical
    # through the render path.
    assert _crop_filter(_cut()) == ""


def test_a_static_window_emits_no_time_dependence():
    f = _crop_filter(_cut(crop=CropWindow(0.0, 0.25, 1.0, 0.5)))
    assert f.startswith("crop=")
    assert "t/" not in f and "setpts" not in f


def test_a_moving_window_ramps_in_the_filters_own_time():
    f = _crop_filter(
        _cut(
            crop=CropWindow(0.0, 0.2, 1.0, 0.5), crop_end=CropWindow(0.0, 0.4, 1.0, 0.5)
        )
    )
    # t must start at 0 for the ramp to mean anything
    assert f.startswith("setpts=PTS-STARTPTS,")
    assert "min(max(t/4.000000,0),1)" in f


def test_a_crop_end_equal_to_crop_is_treated_as_static():
    c = CropWindow(0.0, 0.2, 1.0, 0.5)
    assert "t/" not in _crop_filter(_cut(crop=c, crop_end=c))


# -- the pixels -------------------------------------------------------------


def _probe(path, key):
    return subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            f"stream={key}",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _mean_rgb(path, at=0.5):
    """Mean colour of one frame, as three floats."""
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=8:8",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    px = list(out)
    return tuple(sum(px[i::3]) / (len(px) / 3) for i in range(3))


@needs_ffmpeg
def test_the_rendered_pixels_are_the_requested_rectangle(tmp_path: Path):
    """The check a frame count cannot make.

    A source whose top half is pure RED and bottom half pure GREEN. Cropping the
    bottom half must produce a green frame; cropping the top, a red one. Asserting
    only the frame count would pass with the crop silently dropped.
    """
    from muvid.footage.assemble import _render_part

    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:size=64x64:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "color=lime:size=64x64:rate=30:duration=3",
            "-filter_complex",
            "[0:v][1:v]vstack=2[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
    )
    assert _probe(src, "height") == "128"

    common = dict(w=64, h=64, fps=30, n_frames=30, crf=20, preset="ultrafast")

    top = tmp_path / "top.mp4"
    _render_part(
        _cut2 := AssemblyCut(
            0, 1, "A", 0.0, str(src), crop=CropWindow(0.0, 0.0, 1.0, 0.5)
        ),
        top,
        **common,
    )
    bot = tmp_path / "bot.mp4"
    _render_part(
        AssemblyCut(0, 1, "A", 0.0, str(src), crop=CropWindow(0.0, 0.5, 1.0, 0.5)),
        bot,
        **common,
    )
    none = tmp_path / "none.mp4"
    _render_part(AssemblyCut(0, 1, "A", 0.0, str(src)), none, **common)

    r_top, r_bot, r_none = _mean_rgb(top), _mean_rgb(bot), _mean_rgb(none)
    assert r_top[0] > 150 and r_top[1] < 90, f"top crop should be red, got {r_top}"
    assert r_bot[1] > 150 and r_bot[0] < 90, f"bottom crop should be green, got {r_bot}"
    # and the uncropped render is neither — it letterboxes BOTH halves in
    assert abs(r_none[0] - r_none[1]) < 60, f"uncropped should hold both, got {r_none}"
    # frame counts are equal, which is exactly why they cannot be the test
    assert (
        _probe(top, "nb_frames")
        == _probe(bot, "nb_frames")
        == _probe(none, "nb_frames")
    )


@needs_ffmpeg
def test_a_moving_window_actually_moves(tmp_path: Path):
    """A pan from the top half to the bottom half: the first frame is red, the last green."""
    from muvid.footage.assemble import _render_part

    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:size=64x64:rate=30:duration=4",
            "-f",
            "lavfi",
            "-i",
            "color=lime:size=64x64:rate=30:duration=4",
            "-filter_complex",
            "[0:v][1:v]vstack=2[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
    )
    out = tmp_path / "pan.mp4"
    _render_part(
        AssemblyCut(
            0,
            2,
            "A",
            0.0,
            str(src),
            crop=CropWindow(0.0, 0.0, 1.0, 0.5),
            crop_end=CropWindow(0.0, 0.5, 1.0, 0.5),
        ),
        out,
        w=64,
        h=64,
        fps=30,
        n_frames=60,
        crf=20,
        preset="ultrafast",
    )
    first, last = _mean_rgb(out, at=0.0), _mean_rgb(out, at=1.9)
    assert first[0] > last[0], f"should start redder: {first} -> {last}"
    assert last[1] > first[1], f"should end greener: {first} -> {last}"


@needs_ffmpeg
def test_each_side_of_a_transition_gets_its_OWN_framing(tmp_path: Path):
    """The two sides of a blend are different cuts and may carry different crops.

    Before this, `_render_transition` built ONE normalisation string and applied it
    to both inputs, so the A-side framing silently governed the B side: the blend
    still rendered, at the wrong framing, and nothing downstream could see it. A
    frame-count assertion cannot catch this either — the counts are identical.
    """
    from muvid.footage.assemble import _Part, _render_transition

    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:size=64x64:rate=30:duration=4",
            "-f",
            "lavfi",
            "-i",
            "color=lime:size=64x64:rate=30:duration=4",
            "-filter_complex",
            "[0:v][1:v]vstack=2[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
    )

    # A shows the RED top half; B shows the GREEN bottom half. Same source file, so
    # only the crop distinguishes them.
    a = AssemblyCut(0, 2, "A", 0.0, str(src), crop=CropWindow(0.0, 0.0, 1.0, 0.5))
    b = AssemblyCut(2, 4, "A", 2.0, str(src), crop=CropWindow(0.0, 0.5, 1.0, 0.5))
    out = tmp_path / "xf.mp4"
    _render_transition(
        _Part(
            kind="xfade",
            cut=b,
            n_frames=30,
            clip_in=2.0,
            prev=a,
            prev_in=1.0,
            curve="fade",
        ),
        out,
        w=64,
        h=64,
        fps=30,
        crf=20,
        preset="ultrafast",
    )

    # The blend must END on B's framing (green). If both inputs took A's crop it
    # would stay red throughout.
    first, last = _mean_rgb(out, at=0.0), _mean_rgb(out, at=0.95)
    assert first[0] > first[1], f"blend should start on A (red), got {first}"
    assert last[1] > last[0], f"blend should end on B (green), got {last}"


def test_the_persisted_body_carries_a_crop_and_omits_it_when_unset():
    """Additive in the same sense as `transition`: absent unless set, so no lacing
    migration — every document written before this field is still exactly itself."""
    from muvid.footage.lacing_bridge import _edl_body

    assert _edl_body(EdlEntry(0, 4, "A")) == {"clip_id": "A"}
    body = _edl_body(_entry(crop=CropWindow(0.0, 0.3, 1.0, 0.4)))
    assert body["crop"] == {"x": 0.0, "y": 0.3, "w": 1.0, "h": 0.4}
    assert "crop_end" not in body


# --------------------------------------------------------------------------
# The skill's ffmpeg note is the copy with the widest blast radius (muvid#68)
# --------------------------------------------------------------------------
#
# `muvid-choose-footage-segments/SKILL.md` is the text an agent LOADS before
# authoring a crop window, so a wrong claim in it does not merely sit there — it
# gets repeated into other repos, which is exactly how the old "zoompan has no
# time variable and duplicates frames" line escaped muvid. Prose drifts silently
# (a call-site sweep cannot see it), so pin the two claims that were false.
#
# Measured on ffmpeg 8.1: `pon` does not exist in zoompan at all ("Undefined
# constant"); `in_time` / `it` do; and the frame duplication is entirely the
# default `d=90` (20 in, 1800 out) — `d=1` is 1:1.

FOOTAGE_SKILL = (
    Path(__file__).parents[1]
    / ".claude"
    / "skills"
    / "muvid-choose-footage-segments"
    / "SKILL.md"
)


def test_the_skill_does_not_name_a_zoompan_variable_that_does_not_exist():
    import re

    text = FOOTAGE_SKILL.read_text()
    assert not re.search(r"\bpon\b", text), (
        f"{FOOTAGE_SKILL.name} names `pon` as a zoompan variable; ffmpeg answers "
        "'Undefined constant'. An agent reading this writes an expression that "
        "cannot configure."
    )


def test_the_skill_does_not_claim_zoompan_has_no_time_variable():
    """The positive form of the claim, because that is the checkable one.

    A note saying only "zoompan has no `t`" is true and misleading: the filter is
    not time-blind. Requiring it to NAME the variable that does work is what stops
    the bare, discouraging half-truth from coming back.
    """
    text = FOOTAGE_SKILL.read_text()
    if "zoompan" not in text:
        return
    assert "in_time" in text, (
        f"{FOOTAGE_SKILL.name} discusses zoompan without naming `in_time` — its "
        "real elapsed-time variable. Saying only that it lacks `t` reads as 'this "
        "filter cannot do time', which is how muvid#68 happened."
    )


def test_the_skill_states_the_root_cause_of_crops_fixed_size():
    """Naming one symptom gets the note reopened by whoever measures the other.

    `crop` refuses to configure for some expression shapes and silently freezes at
    one size for others; both are real, and both follow from `w`/`h` being
    evaluated once, at configure time, with `t` still NAN.
    """
    text = FOOTAGE_SKILL.read_text().lower()
    assert "configure time" in text, (
        f"{FOOTAGE_SKILL.name} no longer states WHY crop cannot resize (its `w`/`h` "
        "are evaluated once, at configure time). Without the cause, the half of the "
        "behaviour it does not describe reads as a contradiction."
    )
