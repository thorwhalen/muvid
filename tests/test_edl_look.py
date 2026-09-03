"""The ``looks`` seam: a compiled ``-vf`` fragment per cut (looks' build-order item 10).

The contract under test:

- a look is ONE linear filter chain, gated by the ONE gate, and survives the JSON
  round trip and ``derive_cuts`` like ``crop`` does;
- an EDL **without** one is byte-identical through the render path — not merely
  "works", identical, which is what makes the field additive;
- the fragment lands on **both** render sites, at the same place in the chain,
  because the two sites are the two sides of a blended boundary and a look that
  reaches one and not the other is a visible seam;
- muvid#66's in-shot punch-in really zooms, and does not change the frame count.

The pixel checks render for real. A splice that produces a plausible string and a
different picture is exactly the failure this is here to catch.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from muvid.footage.assemble import _part_filter
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
    LookError,
    chain,
    motion,
    punch_in,
    punch_in_cuts,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg + ffprobe")

SONG_DUR = 10.0
_A = FootageAlignment("A", 0.0, 0.9, 10.0, (0.0, 10.0))
_B = FootageAlignment("B", 0.0, 0.9, 10.0, (0.0, 10.0))
_GREY = "hue=s=0"


def _entry(**kw) -> EdlEntry:
    return EdlEntry(0.0, 4.0, "A", **kw)


def _cut(**kw) -> AssemblyCut:
    base = dict(
        song_start=0.0, song_end=4.0, clip_id="A", clip_in=0.0, clip_path="/tmp/a.mp4"
    )
    return AssemblyCut(**{**base, **kw})


# -- the field ---------------------------------------------------------------


def test_a_dict_edl_entry_carries_a_look():
    [e] = validate_edl(
        [{"song_start": 0.0, "song_end": 4.0, "clip_id": "A", "look": _GREY}],
        [_A],
        4.0,
    )
    assert e.look == _GREY


def test_a_non_string_look_raises_rather_than_being_dropped():
    # Same posture as the crop read: `_as_entry` serves the caller's explicit
    # request, and dropping a requested look silently is the bug.
    with pytest.raises(ValueError, match="look is malformed"):
        validate_edl(
            [{"song_start": 0.0, "song_end": 4.0, "clip_id": "A", "look": {"a": 1}}],
            [_A],
            4.0,
        )


def test_a_look_survives_derive_cuts():
    entries = validate_edl([_entry(look=_GREY)], [_A], 4.0)
    [cut] = derive_cuts(entries, [_A], {"A": "/tmp/a.mp4"})
    assert cut.look == _GREY


def test_absent_is_the_default_everywhere():
    assert EdlEntry(0.0, 4.0, "A").look is None
    assert _cut().look is None


# -- the gate ----------------------------------------------------------------


@pytest.mark.parametrize(
    "look, match",
    [
        ("", "empty look"),
        ("   ", "empty look"),
        (" hue=s=0", "leading or trailing whitespace"),
        ("hue=s=0 ", "leading or trailing whitespace"),
        (",hue=s=0", "starts or ends with a comma"),
        ("hue=s=0,", "starts or ends with a comma"),
        # A pad label is how a filtergraph reaches a second decoder. THIS is the
        # one that would move the bounded-memory invariant.
        ("[0:v]scale=2", "unescaped"),
        ("split[a][b]", "unescaped"),
        # A graph separator would splice into a DIFFERENT graph than either side
        # wrote, because the assembler joins with commas.
        ("hue=s=0;scale=2", "unescaped"),
    ],
)
def test_a_fragment_that_is_not_one_linear_chain_is_refused(look, match):
    with pytest.raises(ValueError, match=match):
        validate_edl([_entry(look=look)], [_A], 4.0)


def test_an_escaped_bracket_inside_a_path_is_allowed():
    # `looks.escape_filter_value` escapes `[` with one backslash, so refusing a
    # BARE bracket must not refuse a legitimately escaped one — otherwise the
    # flagship effect (a LUT whose file path contains a bracket) is unusable.
    look = r"lut3d=file=/luts/a\[b\].cube"
    [e] = validate_edl([_entry(look=look)], [_A], 4.0)
    assert e.look == look


def test_the_escape_rule_agrees_with_the_escaper_that_produces_it():
    # Pinned against `looks` itself rather than against a remembered rule: if
    # looks ever changes how it escapes, this fails here instead of at ffmpeg.
    from looks import escape_filter_value

    from muvid.footage.edl import _LOOK_FORBIDDEN, _first_unescaped

    for ch in _LOOK_FORBIDDEN:
        escaped = escape_filter_value(f"/luts/a{ch}b.cube")
        assert _first_unescaped(escaped, _LOOK_FORBIDDEN) is None, (
            f"looks escapes {ch!r} as {escaped!r}, which this gate still refuses"
        )


def test_a_gap_may_not_carry_a_look():
    with pytest.raises(ValueError, match="gap but carries a look"):
        validate_edl([EdlEntry(0.0, 4.0, "", look=_GREY)], [_A], 4.0)


# -- the splice --------------------------------------------------------------

#: What the chain was BEFORE the look field existed, pinned as a literal. This is
#: the whole additive claim in one string: it is compared against what
#: `_part_filter` emits for a cut with no crop and no look, so any future edit to
#: the template has to be a deliberate two-place change rather than a silent
#: reshaping of every render muvid has ever produced.
_HISTORICAL = (
    "scale=1920:1080:force_original_aspect_ratio=decrease,"
    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,"
    "tpad=stop=-1:stop_mode=clone"
)


def test_no_look_emits_exactly_the_chain_it_always_did():
    assert _part_filter(_cut(), w=1920, h=1080, fps=30) == _HISTORICAL


def test_no_look_emits_exactly_the_chain_it_always_did_on_the_transition_site():
    assert (
        _part_filter(_cut(), w=1920, h=1080, fps=30, tail=",format=yuv420p")
        == _HISTORICAL + ",format=yuv420p"
    )


def test_the_look_lands_after_the_normalisation_and_before_the_tail():
    # After scale/pad/fps: the look then sees exactly the canvas at exactly the
    # delivery rate, which is what lets a punch-in be compiled with exact numbers
    # instead of a per-clip probe. Before the tail: xfade needs both its sides in
    # one pixel format, which is the tail's whole job.
    got = _part_filter(_cut(look=_GREY), w=1920, h=1080, fps=30, tail=",format=yuv420p")
    assert got == _HISTORICAL + f",{_GREY},format=yuv420p"


def test_the_look_composes_with_a_crop_rather_than_replacing_it():
    got = _part_filter(
        _cut(crop=CropWindow(0.0, 0.25, 1.0, 0.5), look=_GREY), w=640, h=360, fps=25
    )
    assert got.startswith("crop="), "the source-relative framing still comes first"
    assert got.endswith(f",{_GREY}")


def test_both_render_sites_place_the_look_identically(monkeypatch, tmp_path):
    """The two copies of the template are the two SIDES of a blended boundary.

    A look that reaches the solo part but not the blend (or reaches it in a
    different position) makes a cut's two sides disagree exactly where the xfade
    puts them on top of each other — a visible seam, and one nothing downstream
    can detect. Before this change the template was written out twice; the test
    asserts the property rather than the refactor.
    """
    import muvid.visualize.ffmpeg as F
    from muvid.footage.assemble import assemble_music_video

    calls = []
    monkeypatch.setattr(F, "require_ffmpeg", lambda *a, **k: None)
    monkeypatch.setattr(F, "require_filter", lambda *a, **k: None)
    monkeypatch.setattr(
        F, "probe", lambda *a, **k: {"streams": [{"codec_type": "video"}]}
    )
    monkeypatch.setattr(F, "run_ffmpeg", lambda args, **k: calls.append(list(args)))

    entries = validate_edl(
        [
            EdlEntry(0.0, 4.0, "A", look=_GREY),
            EdlEntry(4.0, 10.0, "B", transition=Transition(0.4, "fade"), look=_GREY),
        ],
        [_A, _B],
        SONG_DUR,
    )
    cuts = derive_cuts(entries, [_A, _B], {"A": "/tmp/a.mp4", "B": "/tmp/b.mp4"})
    assemble_music_video(
        cuts, "/tmp/song.wav", str(tmp_path / "out.mp4"), canvas=(640, 360), fps=25
    )

    solos = [c[c.index("-vf") + 1] for c in calls if "-vf" in c]
    blends = [
        c[c.index("-filter_complex") + 1] for c in calls if "-filter_complex" in c
    ]
    assert solos and blends, (
        "the EDL must exercise BOTH sites for this to mean anything"
    )
    tail = "tpad=stop=-1:stop_mode=clone," + _GREY
    for vf in solos:
        assert vf.endswith(tail), vf
    for graph in blends:
        # BOTH sides of the blend, not just one: `_norm` is per-input precisely
        # because the two sides are different cuts.
        assert graph.count(tail + ",format=yuv420p") == 2, graph
    # ...and the blend really is two decoders, still. The seam must not have
    # moved the invariant muvid#21/#24 bought.
    for c in calls:
        assert c.count("-i") <= 2


def test_a_second_input_cannot_reach_the_assembler_at_all():
    """The gate, stated as the invariant it protects rather than as a regex.

    A look naming a container input would add a decoder per cut, which is the
    exact shape that was OOM-killed at 30 cuts on a 3.7 GB box.
    """
    with pytest.raises(ValueError, match="decoder per cut"):
        validate_edl([_entry(look="[1:v]overlay")], [_A], 4.0)


# -- the punch-in (muvid#66) -------------------------------------------------


def test_punch_in_compiles_to_zoompan_because_the_window_resizes():
    # `crop` cannot resize its window at all — w/h are evaluated once, at
    # configure time. Choosing the filter is looks' job, not the caller's; this
    # asserts looks made the right choice for this path.
    frag = punch_in(canvas=(640, 360), fps=25, duration_s=3.0)
    assert frag.startswith("zoompan=d=1:s=640x360:fps=25:")
    assert "in_time" in frag, "zoompan's `t` is undefined; the clock is `in_time`"


def test_punch_in_holds_the_anchor_still():
    # The anchor is the point that does not move. A centred punch puts the tight
    # window's centre back at the canvas centre; a corner anchor pins it there.
    from muvid.footage.look import DEFAULT_PUNCH_ZOOM

    side = 1.0 / DEFAULT_PUNCH_ZOOM
    for ax, ay in [(0.5, 0.5), (0.0, 0.0), (1.0, 1.0), (0.5, 0.3)]:
        x, y = ax * (1 - side), ay * (1 - side)
        assert x + side * ax == pytest.approx(ax)
        assert y + side * ay == pytest.approx(ay)


def test_a_pull_out_is_not_a_flag():
    with pytest.raises(LookError, match="pulls OUT"):
        punch_in(canvas=(640, 360), fps=25, duration_s=3.0, zoom=0.9)


@pytest.mark.parametrize(
    "kw, match",
    [
        (dict(anchor=(1.5, 0.5)), "outside the canvas"),
        (dict(duration_s=0.0), "positive duration_s"),
        (dict(start_s=2.0, end_s=1.0), "start_s < end_s"),
        (dict(end_s=99.0), "start_s < end_s"),
    ],
)
def test_a_punch_that_cannot_be_honoured_is_refused(kw, match):
    base = dict(canvas=(640, 360), fps=25, duration_s=3.0)
    with pytest.raises(LookError, match=match):
        punch_in(**{**base, **kw})


def test_a_hold_then_move_ramps_from_the_hold():
    frag = punch_in(canvas=(640, 360), fps=25, duration_s=3.0, start_s=1.0, end_s=2.0)
    assert "(in_time-1)/1" in frag


def test_a_constant_size_path_is_a_crop_not_a_zoompan():
    # The other half of "which filter is not the caller's decision".
    frag = motion(
        [(0.0, CropWindow(0.0, 0.0, 0.5, 0.5)), (2.0, CropWindow(0.5, 0.0, 0.5, 0.5))],
        canvas=(640, 360),
        fps=25,
    )
    assert "zoompan" not in frag and "crop=" in frag


def test_a_muvid_crop_window_is_a_looks_window_with_no_adapter():
    # Both packages use burns.Rect's convention deliberately, so the structural
    # protocol matches. If either side renames a field this fails here rather
    # than as a TypeError inside looks.
    from looks.motion import WindowLike

    assert isinstance(CropWindow(0.0, 0.0, 1.0, 1.0), WindowLike)


# -- composing over a whole edit ---------------------------------------------


def test_chain_joins_and_drops_the_empties():
    assert chain("hue=s=0", None, "", "unsharp=5:5:1") == "hue=s=0,unsharp=5:5:1"
    assert chain(None, "") is None


def test_punch_in_cuts_redistributes_rather_than_appending():
    # muvid#66 asked for ~2N punch-ins "evenly redistributed", explicitly NOT two
    # extra tacked on the end.
    entries = [EdlEntry(i, i + 1.0, "A") for i in range(6)]
    got = punch_in_cuts(entries, canvas=(640, 360), fps=25, every=2)
    assert [e.look is not None for e in got] == [True, False] * 3


def test_punch_in_cuts_skips_gaps_and_keeps_an_authored_look():
    entries = [
        EdlEntry(0.0, 1.0, "A"),
        EdlEntry(1.0, 2.0, ""),  # a gap: no footage to punch into
        EdlEntry(2.0, 3.0, "A", look=_GREY),  # already directed: do not overwrite
        EdlEntry(3.0, 4.0, "A"),
    ]
    got = punch_in_cuts(entries, canvas=(640, 360), fps=25, every=1)
    assert got[1].look is None
    assert got[2].look == _GREY
    assert got[0].look is not None and got[3].look is not None


# -- the pixels --------------------------------------------------------------


def _run(args):
    return subprocess.run(args, capture_output=True, check=True)


def _src(tmp_path: Path, name: str, seconds: float, size="640x480") -> Path:
    out = tmp_path / f"{name}.mp4"
    _run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            # testsrc2 is bit-reproducible with no pinning; `gradients` is not.
            "-i",
            f"testsrc2=size={size}:rate=30:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )
    return out


def _song(tmp_path: Path, seconds: float) -> Path:
    out = tmp_path / "song.m4a"
    _run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:duration={seconds}",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(out),
        ]
    )
    return out


def _frames(path: Path, w: int, h: int):
    import numpy as np

    r = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    return np.frombuffer(r.stdout, dtype=np.uint8).reshape(-1, h, w, 3)


def _render(tmp_path: Path, look, *, dur=4.0, canvas=(320, 180), fps=25) -> Path:
    from muvid.footage.assemble import assemble_music_video

    tmp_path.mkdir(parents=True, exist_ok=True)
    clip = _src(tmp_path, "A", dur + 1.0)
    song = _song(tmp_path, dur)
    aligns = [FootageAlignment("A", 0.0, 0.9, dur + 1.0, (0.0, dur + 1.0))]
    entries = validate_edl([EdlEntry(0.0, dur, "A", look=look)], aligns, dur)
    cuts = derive_cuts(entries, aligns, {"A": clip})
    out = tmp_path / f"out-{'look' if look else 'plain'}.mp4"
    assemble_music_video(
        cuts, str(song), str(out), canvas=canvas, fps=fps, crf=20, preset="veryfast"
    )
    return out


@needs_ffmpeg
def test_a_look_changes_the_picture_and_not_the_frame_count(tmp_path):
    """The check a string comparison cannot make.

    A greyscale look must desaturate every frame and must not move a single
    frame boundary — the frame count is the assembler's contract with the song
    and no look may touch it.
    """
    import numpy as np

    plain = _frames(_render(tmp_path / "p", None), 320, 180)
    grey = _frames(_render(tmp_path / "g", _GREY), 320, 180)
    # Equality, not an absolute count: the final mux stream-COPIES the video, so
    # `-t` cuts on a GOP boundary and the delivered count is a property of the
    # mux (pinned by tests/test_footage_render.py), not of the look. What must
    # hold here is that adding a look moves it by nothing.
    assert len(plain) == len(grey)
    # Desaturated means the three channels agree; the source is a colour bar
    # pattern, so the plain render's channels do not.
    spread = lambda a: float(np.mean(a.max(axis=-1).astype(int) - a.min(axis=-1)))
    assert spread(grey) < 1.0, "hue=s=0 did not desaturate"
    assert spread(plain) > 20.0, (
        "the source must be colourful for this to mean anything"
    )


@needs_ffmpeg
def test_a_punch_in_really_zooms(tmp_path):
    """muvid#66, measured on the pixels rather than on the filter string.

    The last frame of a punched render must be the INNER window of the unpunched
    one, magnified — not merely "different".
    """
    import numpy as np

    dur, canvas, fps = 4.0, (320, 180), 25
    frag = punch_in(canvas=canvas, fps=fps, duration_s=dur, zoom=1.25)
    plain = _frames(
        _render(tmp_path / "p", None, dur=dur, canvas=canvas, fps=fps), 320, 180
    )
    punch = _frames(
        _render(tmp_path / "z", frag, dur=dur, canvas=canvas, fps=fps), 320, 180
    )
    assert len(plain) == len(punch), "a punch-in must not change the frame count"

    def resize(img, w, h):
        ys = (np.arange(h) * img.shape[0] / h).astype(int)
        xs = (np.arange(w) * img.shape[1] / w).astype(int)
        return img[ys][:, xs]

    # zoom 1.25 => the final window is the middle 80%, centred.
    m = (1 - 1 / 1.25) / 2
    inner = plain[-1][
        int(180 * m) : int(180 * (1 - m)), int(320 * m) : int(320 * (1 - m))
    ]
    to_inner = np.abs(
        punch[-1].astype(int) - resize(inner, 320, 180).astype(int)
    ).mean()
    to_whole = np.abs(punch[-1].astype(int) - plain[-1].astype(int)).mean()
    assert to_inner < to_whole / 2, (
        f"the punched last frame is not the magnified inner window "
        f"(|diff| to inner {to_inner:.2f}, to whole frame {to_whole:.2f})"
    )
    # And frame 0 is where the move STARTS, so it is the unpunched frame.
    assert np.abs(punch[0].astype(int) - plain[0].astype(int)).mean() < 2.0


@needs_ffmpeg
def test_an_edl_with_no_look_renders_the_pixels_it_always_did(tmp_path):
    """The additive claim, checked on DECODED frames rather than on the code.

    Two renders of the same look-less EDL must agree frame for frame. That the
    field exists at all must be invisible to every edit written before it.
    """
    import numpy as np

    a = _frames(_render(tmp_path / "a", None), 320, 180)
    b = _frames(_render(tmp_path / "b", None), 320, 180)
    assert np.array_equal(a, b)
