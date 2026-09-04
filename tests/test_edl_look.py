"""The ``looks`` seam: a compiled ``-vf`` fragment per cut (looks' build-order item 10).

The contract under test:

- a look is ONE lexically-closed linear filter chain naming only filters
  ``LOOK_FILTERS`` offers, gated by the ONE gate, and survives the JSON *and*
  editor round trips like ``crop`` does;
- **the gate is a trust boundary, not a tidiness rule.** ``assemble_music_video``
  is a live per-caller MCP tool whose ``edl`` argument is free-form dicts, so a
  look is executable ffmpeg from a remote caller: before the allowlist,
  ``metadata=mode=print:file=<path>`` passed the gate and truncated that file
  while the render returned a success payload;
- an EDL **without** a look is byte-identical through the render path — not merely
  "works", identical, which is what makes the field additive;
- the fragment lands on **both** render sites, at the same place in the chain, and
  **each side carries its OWN cut's look**, because the two sites are the two
  sides of a blended boundary and a look that reaches one and not the other, or
  that reaches both from one cut, is a visible seam;
- muvid#66's in-shot punch-in really zooms, and does not change the frame count.

The pixel checks render for real. A splice that produces a plausible string and a
different picture is exactly the failure this is here to catch. Where a rule is a
REFUSAL, there is a measurement of the thing being refused actually misbehaving —
a gate that refuses something harmless is a gate nobody can evaluate.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from muvid.footage.assemble import _part_filter
from muvid.footage.edl import (
    LOOK_FILTERS,
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
#: A second, DIFFERENT look. The per-side assertions below are only worth anything
#: if the two sides carry different fragments: with one look on both, "the look
#: reached the blend" and "one cut's look reached BOTH blend inputs" emit the same
#: string, and the guard cannot tell them apart. `punch_in_cuts(every=2)` produces
#: exactly this asymmetry by construction, so it is the normal shape, not a corner.
_WARM = "hue=h=90"


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


def _record_ffmpeg(monkeypatch):
    """Run the assembler with every ffmpeg call RECORDED instead of run.

    Returns the list the calls land in. Shared by the two argv-level guards below
    — the per-side look placement and the look-less argv snapshot — because they
    need the same harness and the setup is four monkeypatches nobody should copy.
    """
    import muvid.visualize.ffmpeg as F

    calls = []
    monkeypatch.setattr(F, "require_ffmpeg", lambda *a, **k: None)
    monkeypatch.setattr(F, "require_filter", lambda *a, **k: None)
    monkeypatch.setattr(
        F, "probe", lambda *a, **k: {"streams": [{"codec_type": "video"}]}
    )
    monkeypatch.setattr(F, "run_ffmpeg", lambda args, **k: calls.append(list(args)))
    return calls


def test_both_render_sites_place_each_cut_s_OWN_look(monkeypatch, tmp_path):
    """The two copies of the template are the two SIDES of a blended boundary.

    A look that reaches the solo part but not the blend, reaches it in a different
    position, or reaches it from the WRONG CUT, makes a boundary's two sides
    disagree exactly where the xfade puts them on top of each other — a visible
    seam, and one nothing downstream can detect.

    The two entries carry DIFFERENT looks on purpose. That is the whole difference
    between this and the version it replaces, which gave both entries the same
    string and asserted ``graph.count(tail) == 2``: that count is satisfied just as
    well by ``_norm`` reading the look from one fixed side, i.e. by the exact
    collapse ``_norm``'s own comment says the per-input call prevents. Measured
    under that mutation: the whole suite stayed green, while the rendered blend
    showed a hard 0.000 -> 125.572 saturation cliff one frame in.
    """
    from muvid.footage.assemble import assemble_music_video

    calls = _record_ffmpeg(monkeypatch)
    entries = validate_edl(
        [
            EdlEntry(0.0, 4.0, "A", look=_GREY),
            EdlEntry(4.0, 10.0, "B", transition=Transition(0.4, "fade"), look=_WARM),
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
    assert len(solos) == 2 and len(blends) == 1, (
        f"the EDL must be solo(A) xfade solo(B) for this to mean anything, "
        f"got {len(solos)} solos and {len(blends)} blends"
    )
    norm = "tpad=stop=-1:stop_mode=clone,"
    # The solo parts, in plan order: A's look on A, B's look on B.
    assert solos[0].endswith(norm + _GREY), solos[0]
    assert solos[1].endswith(norm + _WARM), solos[1]
    # ...and PER BRANCH of the blend. `[0:v]` is the OUTGOING cut (A), `[1:v]` the
    # incoming (B); a look read from a fixed side lands the same fragment twice.
    a_branch, rest = blends[0].split("[a];", 1)
    b_branch = rest.split("[b];", 1)[0]
    assert a_branch.endswith(norm + _GREY + ",format=yuv420p"), a_branch
    assert b_branch.endswith(norm + _WARM + ",format=yuv420p"), b_branch
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
def test_a_look_less_render_is_deterministic(tmp_path):
    """Two renders of the same look-less EDL agree frame for frame.

    Named for what it asserts. It used to be called
    ``test_an_edl_with_no_look_renders_the_pixels_it_always_did`` and its
    docstring promised that "the field exists at all must be invisible to every
    edit written before it" — but it renders twice with the SAME build, so it can
    only measure determinism. Measured: ``-crf`` +12, and shifting
    ``_render_part``'s input seek by three frames, each moved every look-less
    render's decoded pixels and left this test green.

    The historical claim is pinned where it can be pinned build-independently, by
    :func:`test_a_look_less_assemble_emits_the_argv_it_always_did` below. A decoded
    digest cannot do that job: an x264 intermediate's output varies with encoder
    build and thread count, so a committed pixel hash would fail on a different
    machine for a reason that has nothing to do with muvid.
    """
    import numpy as np

    a = _frames(_render(tmp_path / "a", None), 320, 180)
    b = _frames(_render(tmp_path / "b", None), 320, 180)
    assert np.array_equal(a, b)


#: EXACTLY the ffmpeg muvid ran for a look-less two-cut edit with one transition,
#: captured before the ``look`` field existed and pinned as a literal. Four
#: invocations: solo(A), the xfade, solo(B), and the concat+mux.
#:
#: This is the additive claim in the one form that survives a change of machine.
#: The per-cut chain has its own literal (``_HISTORICAL``), but the chain is only
#: part of the render — the seek arithmetic, the exact frame counts, the encoder
#: settings and the mux contract are the rest of it, and nothing pinned them. Both
#: of the mutations that defeated the old pixel test (``-crf`` +12; the input seek
#: shifted by 3/fps) are visible right here, and neither needs ffmpeg to detect.
#:
#: Paths are normalised because two of them are a temp directory. Everything else
#: is verbatim — including argument ORDER, which is what makes it a snapshot
#: rather than a set of spot checks.
_HISTORICAL_ARGV = (
    # solo(A): input-side seek, one spare frame of input, exact -frames:v cap
    [
        "-ss",
        "0.000000",
        "-t",
        "4.040000",
        "-i",
        "/tmp/a.mp4",
        "-vf",
        "scale=640:360:force_original_aspect_ratio=decrease,"
        "pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,tpad=stop=-1:stop_mode=clone",
        "-frames:v",
        "95",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "20",
        "-preset",
        "veryfast",
        "<parts>/part0000.mp4",
    ],
    # the blended boundary: TWO decoders, never more, each seeked to the window
    [
        "-ss",
        "3.800000",
        "-t",
        "0.440000",
        "-i",
        "/tmp/a.mp4",
        "-ss",
        "3.800000",
        "-t",
        "0.440000",
        "-i",
        "/tmp/b.mp4",
        "-filter_complex",
        "[0:v]scale=640:360:force_original_aspect_ratio=decrease,"
        "pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,tpad=stop=-1:stop_mode=clone,"
        "format=yuv420p[a];"
        "[1:v]scale=640:360:force_original_aspect_ratio=decrease,"
        "pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,tpad=stop=-1:stop_mode=clone,"
        "format=yuv420p[b];"
        "[a][b]xfade=transition=fade:duration=0.400000:offset=0[v]",
        "-map",
        "[v]",
        "-frames:v",
        "10",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "20",
        "-preset",
        "veryfast",
        "<parts>/part0001.mp4",
    ],
    # solo(B), shortened at the head by its own incoming transition
    [
        "-ss",
        "4.200000",
        "-t",
        "6.040000",
        "-i",
        "/tmp/b.mp4",
        "-vf",
        "scale=640:360:force_original_aspect_ratio=decrease,"
        "pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,tpad=stop=-1:stop_mode=clone",
        "-frames:v",
        "145",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "20",
        "-preset",
        "veryfast",
        "<parts>/part0002.mp4",
    ],
    # concat by STREAM COPY + the clean song, encoded to the delivery contract
    [
        "-f",
        "concat",
        "-i",
        "<parts>/parts.txt",
        "-i",
        "/tmp/song.wav",
        "-map",
        "0:v",
        "-map",
        "1:a:0",
        "-t",
        "10.000000",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-use_editlist",
        "0",
        "-movflags",
        "+faststart",
        "<out>",
    ],
)


def test_a_look_less_assemble_emits_the_argv_it_always_did(monkeypatch, tmp_path):
    """The additive claim, pinned against a RECORDING rather than against itself.

    ``_HISTORICAL`` pins the filter chain; this pins the whole invocation, which
    is where the two mutations that beat the old pixel test live. It needs no
    ffmpeg (the runner is recorded), so it runs everywhere and is stable across
    ffmpeg builds — the two properties a decoded-pixel digest cannot have.
    """
    from muvid.footage.assemble import assemble_music_video

    calls = _record_ffmpeg(monkeypatch)
    entries = validate_edl(
        [
            EdlEntry(0.0, 4.0, "A"),
            EdlEntry(4.0, 10.0, "B", transition=Transition(0.4, "fade")),
        ],
        [_A, _B],
        SONG_DUR,
    )
    cuts = derive_cuts(entries, [_A, _B], {"A": "/tmp/a.mp4", "B": "/tmp/b.mp4"})
    out = tmp_path / "out.mp4"
    assemble_music_video(
        cuts,
        "/tmp/song.wav",
        str(out),
        canvas=(640, 360),
        fps=25,
        crf=20,
        preset="veryfast",
    )

    def normalise(arg: str) -> str:
        # The parts dir carries a random suffix and lives under a temp root; the
        # output path is the caller's. Neither is a fact about the render.
        if arg == str(out):
            return "<out>"
        parent = Path(arg).parent
        if parent.name.startswith(".parts-"):
            return f"<parts>/{Path(arg).name}"
        return arg

    got = tuple([normalise(a) for a in c] for c in calls)
    assert got == _HISTORICAL_ARGV


# -- the trust boundary: a look is executable ffmpeg from a REMOTE caller ------
#
# `assemble_music_video` is a live MCP tool on the per-caller reelee AV connector
# (muvid/mcp/__init__.py's FOOTAGE_TOOLS) and its `edl` argument is free-form
# dicts, from which `_as_entry` reads `look` by name. So every string below is
# reachable over the wire, and each one was ACCEPTED by `validate_edl` on this
# branch before `LOOK_FILTERS` existed.

#: One entry per PRIMITIVE, not one per filter — the point is that the dangerous
#: filters have nothing lexical in common, which is why a blocklist cannot work.
_HOSTILE_LOOKS = [
    # create/truncate any path the renderer can write (measured: a 34-byte canary
    # went to 0 bytes and the render still returned a success payload)
    ("metadata=mode=print:file=/tmp/muvid-canary", "metadata"),
    # a second, structurally different write primitive — its own CSV log
    ("deshake=filename=/tmp/muvid-canary", "deshake"),
    ("signature=filename=/tmp/muvid-canary", "signature"),
    ("ssim=stats_file=/tmp/muvid-canary", "ssim"),
    ("psnr=f=/tmp/muvid-canary", "psnr"),
    ("removelogo=filename=/tmp/muvid-canary", "removelogo"),
    ("curves=plot=/tmp/muvid-canary", "curves"),
    # read a path chosen by the caller
    ("sendcmd=f=/etc/hosts", "sendcmd"),
    # a second CONTAINER opened from inside the fragment — the muvid#21/#24
    # decoder accounting leaving by the back door
    ("movie=/tmp/whatever.mp4", "movie"),
    ("amovie=/tmp/whatever.m4a", "amovie"),
    # arbitrary per-pixel expression evaluation, and a graph-shape change
    ("geq=r=0", "geq"),
    ("concat=n=2", "concat"),
]


@pytest.mark.parametrize("look, filter_name", _HOSTILE_LOOKS)
def test_a_look_may_name_only_a_filter_muvid_offers(look, filter_name):
    """The gate is an ALLOWLIST, and this is the population that makes it one."""
    with pytest.raises(ValueError, match=f"names the filter {filter_name!r}"):
        validate_edl([_entry(look=look)], [_A], 4.0)


@pytest.mark.parametrize(
    "spelling",
    [
        r"\m\e\t\a\d\a\t\a=mode=print:file=/tmp/muvid-canary",
        "'metadata'=mode=print:file=/tmp/muvid-canary",
        "m'e'tadata=mode=print:file=/tmp/muvid-canary",
        "metadata@instance=mode=print:file=/tmp/muvid-canary",
        "hue=s=0, metadata =mode=print:file=/tmp/muvid-canary",
        "hue=s=0,metadata=mode=print:file=/tmp/muvid-canary",
    ],
)
def test_the_allowlist_resolves_a_name_the_way_ffmpeg_does(spelling):
    """An allowlist that compared RAW text would be bypassed by five spellings.

    ffmpeg's ``av_get_token`` applies escapes and strips quotes before it looks a
    filter up, and it allows a ``@instance`` label and whitespace around the name.
    Each spelling here was measured against ffmpeg 9.0.1 *outside* muvid: every one
    resolves to ``metadata`` and truncated a canary file to 0 bytes on exit 0. So
    the check has to resolve the token, not grep the string — that is what
    ``_unquote`` in the gate is for, and this test is the reason it exists.
    """
    with pytest.raises(ValueError, match="names the filter 'metadata'"):
        validate_edl([_entry(look=spelling)], [_A], 4.0)


@needs_ffmpeg
def test_the_gate_is_what_stops_a_look_writing_a_file(tmp_path):
    """The whole finding, end to end: the payload WORKS, and muvid refuses it.

    A refusal test alone proves nothing about the danger — it could be refusing
    something harmless. So this runs the exact chain ``_part_filter`` builds
    through real ffmpeg first, as a CONTROL, and only then asserts the gate.
    """
    canary = tmp_path / "canary.txt"
    body = "canary contents that must survive\n"
    payload = f"metadata=mode=print:file={canary}"

    # CONTROL — the payload is real on this binary. Same chain, same order.
    canary.write_text(body)
    src = _src(tmp_path, "src", 1.0, size="64x48")
    chain_ = _part_filter(_cut(clip_path=str(src), look=payload), w=64, h=48, fps=25)
    r = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(src),
            "-vf",
            chain_,
            "-frames:v",
            "3",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr.decode()[:400]
    assert canary.read_text() == "", (
        "the control did not fire — this ffmpeg build does not have the write "
        "primitive, so the assertion below would be vacuous"
    )

    # THE GATE — same string, refused before anything runs.
    canary.write_text(body)
    with pytest.raises(ValueError, match="names the filter 'metadata'"):
        validate_edl([_entry(look=payload)], [_A], 4.0)
    assert canary.read_text() == body


@needs_ffmpeg
def test_no_allowlisted_filter_can_name_a_file():
    """What earns a place on the allowlist, asked of the real binary.

    A name allowlist is only as good as the names on it: allow one filter with a
    writable path option and the whole class is back. So this reads each
    allowlisted filter's option table out of ffmpeg rather than trusting the
    curation, and the single exception is recorded by name with its reason.

    Sanity-checked in the same run against filters that are NOT allowlisted, so a
    probe that silently returned nothing cannot make this pass.
    """
    from muvid.footage.edl import _LOOK_FILE_OPTIONS

    def path_options(name):
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", f"filter={name}"],
            capture_output=True,
            text=True,
        )
        opts = {
            m.group(1)
            for m in (
                re.match(r"^\s{2,}([A-Za-z0-9_]+)\s+<", line)
                for line in (r.stdout + r.stderr).splitlines()
            )
            if m
        }
        return {o for o in opts if o in ("f", "plot") or "file" in o or "path" in o}

    # The instrument works: filters that DO name a file are seen to.
    seen = {n: path_options(n) for n in ("metadata", "movie", "curves", "sendcmd")}
    assert all(seen.values()), f"the option probe returned nothing for {seen}"

    for name in sorted(LOOK_FILTERS):
        found = path_options(name)
        allowed = {o for f, o in _LOOK_FILE_OPTIONS if f == name}
        assert found <= allowed, (
            f"{name!r} declares the path option(s) {sorted(found - allowed)}, so a "
            f"caller-supplied look could name a file through it. Either drop it "
            f"from LOOK_FILTERS or record the option in _LOOK_FILE_OPTIONS with "
            f"the reason it is safe — reading, not writing, is the only reason so "
            f"far (lut3d loads a .cube)."
        )


def test_the_allowlist_covers_every_filter_looks_declares():
    """Drift guard: a new ``looks`` effect must be a DECISION here, not a surprise.

    Pinned against ``looks``' registry rather than derived from it, and the
    direction matters both ways. Derived, a new ``looks`` effect would silently
    widen muvid's remote-input surface. Unpinned, a new effect would compile
    happily and then be refused by muvid's own gate at render time — a failure the
    caller cannot act on.

    ``requires_filters`` is a FLOOR, not the whole set: ``fill``/``fit``/``stretch``
    each declare ``("scale",)`` and were measured to also emit ``null`` (target
    equals the clip) and ``pad`` (letterbox). Both are on the allowlist for that
    measured reason; this test pins the declared half, which is the half that can
    be checked without running ffmpeg.
    """
    import looks

    declared = {
        f
        for effect in looks.effects()
        for impl in looks.REGISTRY.implementations(effect)
        if impl.backend == "ffmpeg"
        for f in impl.requires_filters
    }
    assert declared, "looks declared no ffmpeg filters at all — the probe is wrong"
    # The MEASURED half, pinned as a literal. Discovering that `fit`/`fill` emit
    # `pad` and `null` needs ffmpeg; the CONSEQUENCE — that the allowlist must
    # contain them — does not, and an ffmpeg-less run should still catch their
    # removal. Without this, dropping "pad" left the whole suite green on a
    # machine with no ffmpeg (measured: 63 passed / 8 skipped either way), so
    # half the allowlist had no guard in exactly the environment where the
    # ffmpeg-backed tests skip.
    measured = {"null", "pad"}
    assert measured <= LOOK_FILTERS, (
        f"{sorted(measured - LOOK_FILTERS)} was removed from LOOK_FILTERS. "
        "These are emitted by looks' geometry effects but not DECLARED by them, "
        "so the declared-set check above cannot see their absence — and the "
        "seam breaks only at render time, on a letterboxed cut."
    )
    missing = declared - LOOK_FILTERS
    assert not missing, (
        f"looks can now emit {sorted(missing)}, which muvid's gate refuses. Add "
        f"each to muvid.footage.edl.LOOK_FILTERS deliberately — and only after "
        f"checking it names no file (test_no_allowlisted_filter_can_name_a_file)."
    )


def test_every_geometry_look_muvid_compiles_passes_its_own_gate():
    """The allowlist must not refuse the seam it exists to protect — the half that
    needs no binary.

    ``muvid.footage.look``'s geometry compiles from pure arithmetic (``punch_in``
    -> ``zoompan``, ``motion`` -> ``setpts,crop``), so it is checked everywhere,
    including the Windows leg where muvid installs no ffmpeg. The ``looks``-effect
    half probes a binary and is next door.
    """
    frags = [
        punch_in(canvas=(640, 360), fps=25, duration_s=3.0),
        motion(
            [
                (0.0, CropWindow(0.0, 0.0, 0.5, 0.5)),
                (2.0, CropWindow(0.5, 0.5, 0.5, 0.5)),
            ],
            canvas=(640, 360),
            fps=25,
        ),
    ]
    for frag in frags:
        validate_edl([_entry(look=frag)], [_A], 4.0)  # must not raise


@needs_ffmpeg
def test_every_looks_effect_muvid_compiles_passes_its_own_gate():
    """The other half: every ``looks`` effect, compiled against the real binary.

    ``stylize`` PROBES ffmpeg — that is the whole point of it, since a filter's
    licence tier is a property of the build — so this cannot run where there is no
    binary. Marked rather than allowed to fall into the ``except`` below, which
    would turn "no ffmpeg" into "nothing compiled" and then into a vacuity failure
    (it did, on the Windows leg).
    """
    import looks

    from muvid.footage.look import stylize

    frags, compiled = [], []
    for name in sorted(looks.effects()):
        try:
            frag = stylize(
                looks.Look(steps=(looks.Effect(name=name),)),
                canvas=(640, 360),
                fps=25,
                duration_s=3.0,
            )
        except Exception:
            continue  # needs params or an artifact; the geometry ones are below
        compiled.append(name)
        frags.append(frag)
    assert compiled, "no looks effect compiled bare — this test would be vacuous"
    for target in ("640x360", "1280x720", "1080x1080"):  # null / scale / scale,pad
        for name in ("fill", "fit", "stretch"):
            frags.append(
                stylize(
                    looks.Look(
                        steps=(looks.Effect(name=name, params={"target": target}),)
                    ),
                    canvas=(640, 360),
                    fps=25,
                    duration_s=3.0,
                )
            )
    for frag in frags:
        validate_edl([_entry(look=frag)], [_A], 4.0)  # must not raise


# -- the lexer: "one linear chain" has to survive ffmpeg's OWN escaping --------


@pytest.mark.parametrize(
    "look, why",
    [
        ("hue=s='0", "inside a single-quoted run"),
        (r"hue=s=0\ ".strip(), "on a dangling backslash"),
    ],
)
def test_a_look_that_is_not_lexically_closed_is_refused(look, why):
    """An open quote and a trailing backslash both EAT what is spliced after them.

    Neither is a forbidden character, which is why the character walk missed both:
    ``hue=s='0`` carries no ``[``, ``]`` or ``;`` at all. What it does carry is an
    unterminated quote, and ``av_get_token`` copies everything after it literally —
    including the ``,format=yuv420p[a];[1:`` the transition site appends. Measured:
    that exact look renders fine on a solo cut and aborts the whole render at the
    first blended boundary with ``No option name near 'v]scale=...'``, after N
    parts have already been encoded.
    """
    with pytest.raises(ValueError, match="not lexically closed"):
        validate_edl([_entry(look=look)], [_A], 4.0)
    with pytest.raises(ValueError, match=why):
        validate_edl([_entry(look=look)], [_A], 4.0)


@needs_ffmpeg
def test_an_unclosed_quote_really_does_restructure_the_transition_graph(tmp_path):
    """The measurement behind the rule above, on real ffmpeg — and the asymmetry.

    Refusing something is only right if it is actually broken, and the specific
    danger here is that it is broken at ONE of the two render sites. A gate that
    let it through would be discovered by a caller as a render that worked until
    they added a transition.

    Three commands, because two of them are the controls that make the third mean
    something: the same look renders at the solo site, fails at the transition
    site, and the transition site accepts the identical graph once the quote is
    CLOSED. The third is what isolates the quote as the cause rather than the
    graph shape.

    **Deliberately no assertion on ffmpeg's wording.** The diagnostic is
    build-specific — 9.0.1 says ``No option name near 'v]scale=…'`` while the CI
    runner's older build says ``Error applying option
    'force_original_aspect_ratio' to filter 'hue'``. Both are the same restructure
    (the quote swallowed ``,format=yuv420p[a];[1:v]scale=64:48:``, so an option
    from the far side of the splice landed on ``hue``), and pinning either string
    would make this test a fact about one machine.
    """
    src = _src(tmp_path, "src", 1.0, size="64x48")
    norm = _part_filter(_cut(clip_path=str(src)), w=64, h=48, fps=25)

    def solo(look):
        return subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(src),
                "-vf",
                f"{norm},{look}",
                "-frames:v",
                "3",
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
        )

    def blend(look):
        return subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(src),
                "-i",
                str(src),
                "-filter_complex",
                f"[0:v]{norm},{look},format=yuv420p[a];"
                f"[1:v]{norm},{look},format=yuv420p[b];"
                f"[a][b]xfade=transition=fade:duration=0.1:offset=0[v]",
                "-map",
                "[v]",
                "-frames:v",
                "2",
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
        )

    open_quote, closed = "hue=s='0", "hue=s=0"
    a, b, c = solo(open_quote), blend(open_quote), blend(closed)
    assert a.returncode == 0, (
        f"the solo site refused it, so there is no asymmetry to demonstrate: "
        f"{a.stderr.decode()[:300]}"
    )
    assert c.returncode == 0, (
        f"the CLOSED control failed, so the graph shape is wrong and the test "
        f"proves nothing about the quote: {c.stderr.decode()[:300]}"
    )
    assert b.returncode != 0, "the transition site accepted the unclosed quote"


def test_a_quoted_comma_is_not_a_filter_separator():
    """The other half of the same lexer bug, in the direction that REFUSES work.

    ``looks.compile_motion`` writes ``min(max(t,0),1)`` inside single quotes, so a
    splitter that treats every comma as a separator reads one ``crop`` as four
    filters with names like ``0)`` — and refuses a fragment muvid itself produced.
    """
    from muvid.footage.edl import _look_filter_names

    frag = motion(
        [(0.0, CropWindow(0.0, 0.0, 0.5, 0.5)), (2.0, CropWindow(0.5, 0.5, 0.5, 0.5))],
        canvas=(640, 360),
        fps=25,
    )
    quoted = frag.split("'")[1::2]
    assert any("," in q for q in quoted), (
        f"the fixture must contain a QUOTED comma; got {quoted}"
    )
    assert _look_filter_names(frag) == ["setpts", "crop", "scale"]
    validate_edl([_entry(look=frag)], [_A], 4.0)  # must not raise


@needs_ffmpeg
def test_a_zero_input_source_cannot_reach_a_look_at_all(tmp_path):
    """Why ``movie=`` is refused rather than advised, measured at the solo site.

    ``_validate_look``'s error message used to end with "A second SOURCE is
    reachable as ``movie=``, which is a filter" — advice that cannot be followed.
    ``movie=`` takes zero inputs, so in one unlabelled chain the preceding chain's
    output is left unconsumed and ffmpeg refuses the whole SIMPLE filtergraph
    before decoding anything. The forms that do render all need ``[``, ``]`` and
    ``;`` — exactly the characters the gate refuses for the graph-shape reason.
    """
    src = _src(tmp_path, "src", 1.0, size="64x48")
    norm = _part_filter(_cut(clip_path=str(src)), w=64, h=48, fps=25)
    for tail in (
        f"movie={src}",
        f"movie={src},overlay=10:10",
        "color=black",
        "nullsrc",
    ):
        r = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(src),
                "-vf",
                f"{norm},{tail}",
                "-frames:v",
                "3",
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
        )
        assert r.returncode != 0, f"{tail!r} rendered at the solo site after all"
        assert b"exactly 1 input and 1 output" in r.stderr, r.stderr.decode()[:300]
        # ...and muvid refuses it before it can get that far.
        with pytest.raises(ValueError, match="names the filter"):
            validate_edl([_entry(look=tail)], [_A], 4.0)


# -- the round trip: a field the renderer honours must come BACK --------------


def test_every_optional_edl_field_is_carried_by_the_returned_edl():
    """The guard that stops the NEXT field repeating this.

    ``assemble_music_video``'s own contract says the ``edl`` it returns "must feed
    straight back as the edl= argument and reproduce the same render", and
    ``renders/{id}/meta.json["edl"]`` is fed by the same function — the
    compatibility surface ``.claude/CLAUDE.md`` names, since these bodies carry no
    schema version. ``transition`` was hand-written into ``_edl_json`` and
    survived; ``crop``, ``crop_end`` and ``look`` were each added to ``EdlEntry``
    and forgotten, so a caller's grade was accepted, honoured, and absent from
    both the reply and the record beside the file it graded.

    Parameterised over ``EdlEntry``'s OWN dataclass fields, deliberately — not over
    ``_edl_json``'s table, which is the thing that forgets. The literal list is the
    second half: a new field fails here until someone classifies it.
    """
    import dataclasses

    from muvid.mcp.footage_tools import _edl_json

    required = {"song_start", "song_end", "clip_id"}
    optional = [f.name for f in dataclasses.fields(EdlEntry) if f.name not in required]
    assert optional == [
        "transition",
        "crop",
        "crop_end",
        "look",
        "look_time_varying",
    ], (
        f"EdlEntry grew or lost an optional field ({optional}). Decide whether it "
        "belongs in the returned/persisted edit, add it to "
        "footage_tools._EDL_OPTIONAL_FIELDS and to lacing_bridge._edl_body, then "
        "update this list."
    )
    values = {
        "transition": Transition(0.4, "fade"),
        "crop": CropWindow(0.0, 0.25, 1.0, 0.5),
        "crop_end": CropWindow(0.0, 0.25, 1.0, 0.5),
        "look": _GREY,
        # A bool field's ABSENT value is False, not None — so the value that has
        # to survive the trip is `True`, and a `look` has to accompany it or
        # `validate_edl` refuses the pair.
        "look_time_varying": True,
    }
    for field in optional:
        kwargs = {field: values[field]}
        if field == "look_time_varying":
            kwargs["look"] = _GREY
        e = EdlEntry(0.0, 4.0, "A", **kwargs)
        assert field in _edl_json(e), (
            f"{field!r} is accepted by validate_edl and honoured by the renderer, "
            "but the edl the tool returns and persists does not carry it — so "
            "feeding that edl back, which the tool's own note instructs, silently "
            "renders something else."
        )
    # ...and an entry that sets none of them is byte-identical to what it always
    # was. Omit-when-None is what keeps every existing meta.json readable.
    assert _edl_json(EdlEntry(0.0, 1.0, "A")) == {
        "song_start": 0.0,
        "song_end": 1.0,
        "clip_id": "A",
    }


def test_a_look_survives_the_json_round_trip_verbatim():
    """Accepted -> returned -> fed back -> the SAME render, which is the contract."""
    from muvid.mcp.footage_tools import _edl_json

    caller = [
        {"song_start": 0.0, "song_end": 4.0, "clip_id": "A", "look": _GREY},
        {
            "song_start": 4.0,
            "song_end": 10.0,
            "clip_id": "A",
            "look": _WARM,
            "transition": {"duration_s": 0.4, "curve": "fade"},
        },
    ]
    first = validate_edl(caller, [_A], SONG_DUR)
    returned = [_edl_json(e) for e in first]
    second = validate_edl(returned, [_A], SONG_DUR)
    assert [e.look for e in second] == [_GREY, _WARM]
    assert [_edl_json(e) for e in second] == returned


def test_a_look_survives_the_editor_round_trip():
    """EDL -> annotations -> EDL is identity, and a look is part of the identity.

    ``.claude/CLAUDE.md``: "A field the bridge does not carry is a field the editor
    silently DROPS on the way back." Same shape as the transition guard next door.
    """
    pytest.importorskip("lacing")
    from muvid.footage.lacing_bridge import edl_annotations, edl_from_annotations

    entries = validate_edl([_entry(look=_GREY)], [_A], 4.0)
    anns = edl_annotations(entries, song_asset_id="a" * 64, attributed_to="t")
    [back] = edl_from_annotations(anns)
    assert back["look"] == _GREY
    assert validate_edl([back], [_A], 4.0)[0].look == _GREY


def test_the_editor_read_skips_a_malformed_look_rather_than_crashing():
    """Untrusted browser output: skip-shaped, matching ``crop`` and ``transition``."""
    pytest.importorskip("lacing")
    from muvid.footage.lacing_bridge import edl_annotations, edl_from_annotations

    entries = validate_edl([_entry(look=_GREY)], [_A], 4.0)
    anns = edl_annotations(entries, song_asset_id="a" * 64, attributed_to="t")
    anns[0].body["look"] = {"not": "a string"}
    [back] = edl_from_annotations(anns)
    assert "look" not in back


class TestTheNameResolvesTheWayFfmpegResolvesIt:
    """The gate and the binary must agree about what a fragment SAYS. Where
    they disagree the gate is guessing, and a guess in either direction is a
    defect — a false accept lets ffmpeg produce the error instead of the gate,
    and a false refuse rejects a fragment muvid itself could have written.
    """

    @pytest.mark.parametrize(
        "look,expected",
        [
            ("hue=s=0", ["hue"]),
            ("hue =s=0", ["hue"]),  # unescaped space: ffmpeg trims it
            ("hue\\ =s=0", ["hue "]),  # ESCAPED space: part of the name
            ("\\ hue=s=0", [" hue"]),
            ("h\\ue=s=0", ["hue"]),
            ("'hue'=s=0", ["hue"]),
            ("hue@grade=s=0", ["hue"]),
        ],
    )
    def test_it_matches(self, look, expected):
        from muvid.footage.edl import _look_filter_names

        assert _look_filter_names(look) == expected

    @pytest.mark.parametrize("look", ["hue\\ =s=0", "\\ hue=s=0"])
    def test_ffmpeg_agrees_that_escaped_whitespace_is_part_of_the_name(self, look):
        """The premise, against the real binary rather than from the docs."""
        if shutil.which("ffmpeg") is None:
            pytest.skip("no ffmpeg")
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=64x48:rate=5:d=1",
                "-vf",
                f"fps=25,{look}",
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
        )
        assert proc.returncode != 0
        assert b"No such filter" in proc.stderr

    def test_an_escaped_space_is_refused_by_the_DESIGNED_message(self):
        """It used to raise `_LookSyntaxError` whose whole message was a single
        backslash — `.strip()` removed the escaped space and left the backslash
        that escaped it, and the lexer then raised from OUTSIDE the try/except
        that produces the readable refusal. Through the MCP tool that backslash
        reached the caller as the entire explanation."""
        from muvid.footage.edl import EdlEntry, _validate_look

        with pytest.raises(ValueError, match="which muvid does not offer"):
            _validate_look(0, EdlEntry(0.0, 1.0, "A", look="hue\\ =s=0"), (640, 360))

    def test_and_a_lone_escaped_space_does_not_crash_the_lexer(self):
        from muvid.footage.edl import EdlEntry, _validate_look

        with pytest.raises(ValueError, match="which muvid does not offer"):
            _validate_look(0, EdlEntry(0.0, 1.0, "A", look="\\ =b"), (640, 360))
