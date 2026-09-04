"""The frame a caller-supplied ``look`` may ask for is bounded (muvid#75).

``LOOK_FILTERS`` closes the class of look that writes the host's filesystem or
opens a second container. It says nothing about what an allowlisted filter is
asked to **do**, and one of those parameters is memory: measured on ffmpeg 9.0.1,
three frames from a 64x48 source, ``/usr/bin/time -l`` peak RSS —

    ==================================  ========
    look                                peak RSS
    ==================================  ========
    ``scale=64:48``                      18.8 MB
    ``scale=64:48,scale=8000:8000``     327.6 MB
    ``zoompan=d=1:s=8000x8000:fps=25``  312.5 MB
    ``pad=8000:8000``                   306.6 MB
    ``scale=w='iw*80':h='ih*80'``       118.4 MB
    ==================================  ========

— all five accepted before this, from a number a remote OAuth caller writes into
``assemble_music_video``'s free-form ``edl=``, on the box muvid#21/#24 was
OOM-killed on. ``pad`` is the lever the issue did not name; it is allowlisted
because ``looks``' ``fit`` letterboxes with it.

Two properties this file exists to hold, and they pull in opposite directions:

- the bound REFUSES the shapes above, in every spelling ffmpeg accepts — which is
  why there is a test that the gate's reading of a fragment matches the frame the
  binary actually produces, rather than a list of strings someone thought of;
- the bound ACCEPTS every look muvid itself compiles, on every canvas muvid
  offers. A gate that refuses the seam it protects is an outage, so that sweep is
  a first-class test and not an afterthought.

**The corpus below is a hand list, and a hand list is what let the first pass
ship a hole.** Two options move the produced frame while declaring no dimension
the bound can read — ``pad``'s ``aspect`` and ``scale``'s
``force_original_aspect_ratio`` — and on the production 1920x1080 canvas both are
BIGGER than the 403 MB ``scale=8000:8000`` this file refuses: 590 MB and 941 MB.
Neither was in the list; the test that would have caught them had exactly the
right shape and simply did not contain them. They are here now, but the guard
that does not depend on anyone thinking of the next one lives in
``tests/test_edl_look_options.py``, which reads the option list out of the
installed binary and drives every option of every allowlisted filter through it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from muvid.footage.edl import (
    DFLT_LOOK_CANVAS,
    MAX_LOOK_SCALE,
    CropWindow,
    EdlEntry,
    FootageAlignment,
    _look_output_sizes,
    validate_edl,
)
from muvid.footage.look import motion, punch_in
from tests.ffmpeg_support import needs_ffmpeg

_A = FootageAlignment("A", 0.0, 0.9, 10.0, (0.0, 10.0))
CANVAS = (640, 360)
#: The bound for CANVAS, spelled out so a test that asserts "one over" cannot
#: drift with the constant while still looking like it checks the edge.
LIMIT = (MAX_LOOK_SCALE * 640, MAX_LOOK_SCALE * 360)


def _entry(**kw) -> EdlEntry:
    return EdlEntry(0.0, 4.0, "A", **kw)


def _validate(look, canvas=CANVAS):
    return validate_edl([_entry(look=look)], [_A], 4.0, canvas=canvas)


# -- the refusals ------------------------------------------------------------


@pytest.mark.parametrize(
    "look",
    [
        # every spelling of scale's size, all five verified to produce an
        # 8000x8000 frame on the real binary
        "scale=8000:8000",
        "scale=w=8000:h=8000",
        "scale=width=8000:height=8000",
        "scale=s=8000x8000",
        "scale=size=8000x8000",
        "scale=w='8000':h='8000'",
        "scale=8000:8000:flags=bilinear",
        # the lever muvid#75's body did not name: pad, at the same magnitude
        "pad=8000:8000",
        "pad=w=8000:h=8000",
        "pad=width=8000:height=8000",
        # zoompan's s, named and POSITIONAL (s is the fifth slot)
        "zoompan=d=1:s=8000x8000:fps=25",
        "zoompan=1:0:0:1:8000x8000:25",
        "zoompan=d=1:s='8000x8000':fps=25",
        # and after a legitimate link, because the walk is per link
        "hue=s=0,scale=8000:8000",
    ],
)
def test_a_frame_far_larger_than_the_canvas_is_refused(look):
    with pytest.raises(ValueError, match="more than"):
        _validate(look)


@pytest.mark.parametrize(
    "look, produced, peak_mb",
    [
        # `pad`'s `aspect` grows w OR h to satisfy a ratio, so BOTH declared sizes
        # can sit at canvas size and the frame still explodes. Named…
        ("pad=w=1920:h=1080:aspect=1/30", "1920x57600", 590),
        ("pad=w=1920:h=1080:aspect=1000/1", "(refused by ffmpeg AFTER asking)", 1784),
        # …declared alone, w/h left at their `iw`/`ih` defaults…
        ("pad=aspect=4/1", "4x the input width", 0),
        # …and reached POSITIONALLY, which is the half a named-option check
        # misses: `aspect` is pad's SEVENTH slot, and the first pass stopped
        # offering slots after the fourth and silently DROPPED the rest.
        ("pad=1920:1080:0:0:black:init:1/30", "1920x57600", 0),
        # `scale`'s force_original_aspect_ratio derives the frame from the INPUT
        # aspect, which a preceding crop makes extreme — so both declared sizes
        # sit exactly ON the 4x bound and the frame is 5.4x wider than that.
        (
            "crop=w=1920:h=200,scale=w=7680:h=4320:force_original_aspect_ratio=increase",
            "41472x4320",
            941,
        ),
        ("scale=w=7680:h=4320:force_original_aspect_ratio=2", "numeric spelling", 0),
        # …and its rounding companion, which does nothing WITHOUT foar and is
        # therefore invisible to a one-option-at-a-time sweep.
        ("scale=w=7680:h=4320:force_divisible_by=64", "rounds the frame up", 0),
        # eval=frame re-evaluates the size per frame; this gate reads once.
        ("scale=w=1920:h=1080:eval=frame", "re-evaluated per frame", 0),
        ("pad=w=1920:h=1080:eval=frame", "re-evaluated per frame", 0),
        # an option muvid simply has not measured: refused by default, which is
        # what makes the table an allowlist rather than a list of known levers.
        ("scale=w=1920:h=1080:threads=4", "unclassified", 0),
        ("crop=w=1920:h=1080:keep_aspect=1", "unclassified", 0),
        # a bare argument after a named one — ffmpeg refuses it too (rc=234,
        # "No option name near '8000'"), so the gate agreeing is the point.
        ("scale=w=1920:8000", "discarded shorthand", 0),
    ],
)
def test_an_option_that_moves_the_frame_without_declaring_a_size_is_refused(
    look, produced, peak_mb
):
    """The muvid#75 leak, and the shape of it rather than the two instances.

    Every fragment here declares nothing the size bound can read — the first two
    leave ``w``/``h`` AT canvas size, the middle ones sit exactly ON the bound —
    and every one of them was ACCEPTED by the first pass and rendered by the live
    MCP tool. On a 1920x1080 canvas the two headline cases peak at 590 MB and
    941 MB against 110 MB for a look at canvas size and 403 MB for the
    ``scale=8000:8000`` the bound does refuse: **the bypasses were larger than
    the case the bound closed.**

    So the fix is not "add these two names". The four filters that can change the
    output geometry are allowlisted per OPTION, and per positional SLOT, so an
    option nobody has measured is refused rather than read as nothing.
    """
    with pytest.raises(ValueError, match="does not classify"):
        _validate(look, canvas=(1920, 1080))


@pytest.mark.parametrize(
    "look, why",
    [
        # An expression evaluates against whatever is underneath, so a literal
        # cap cannot see it. 112-118 MB measured from a 64x48 source.
        ("scale=w='iw*80':h='ih*80'", "iw*80"),
        ("scale=iw*80:ih*80", "iw*80"),
        ("pad=w='iw*80':h='ih*80'", "iw*80"),
        # -1/-2 derive from the INPUT aspect, which a preceding crop can make
        # extreme: crop=w=64:h=2,scale=-1:4000 asks for 128000x4000.
        ("scale=-1:4000", "-1"),
        ("scale=w=-2:h=1080", "-2"),
        # av_parse_video_size also accepts NAMES, and they are not small:
        # zoompan=d=1:s=whuxga is a real 7680x4800 frame at 321 MB.
        ("zoompan=d=1:s=whuxga:fps=25", "whuxga"),
        ("zoompan=d=1:s=hd1080:fps=25", "hd1080"),
        ("scale=s=hd1080", "hd1080"),
    ],
)
def test_a_size_that_is_not_a_plain_pixel_count_is_refused(look, why):
    with pytest.raises(ValueError, match="not a plain pixel count") as exc:
        _validate(look)
    assert why in str(exc.value), "the refusal must quote what it could not read"


def test_the_edge_is_where_the_constant_says_it_is():
    """Exactly at the bound passes; one pixel over does not, on BOTH axes.

    Two axes because a single-axis test cannot see the two mistakes that matter
    — comparing width against the height limit, or bounding one axis and
    forgetting the other. On a 640x360 canvas the two limits are different
    numbers, which is what makes the swap visible at all.
    """
    _validate(f"scale={LIMIT[0]}:{LIMIT[1]}")
    with pytest.raises(ValueError, match="wide"):
        _validate(f"scale={LIMIT[0] + 1}:{LIMIT[1]}")
    with pytest.raises(ValueError, match="high"):
        _validate(f"scale={LIMIT[0]}:{LIMIT[1] + 1}")


def test_the_bound_is_relative_to_the_canvas_it_is_given():
    """The same fragment passes on one canvas and is refused on another.

    This is the whole reason ``_validate_look`` had to be given the canvas. A
    fixed cap would answer the same on both, so this test is what distinguishes
    the fix from a constant.
    """
    tall = "scale=1080:5000"
    _validate(tall, canvas=(1080, 1920))  # 4 x 1920 = 7680 high: room to spare
    with pytest.raises(ValueError, match="high"):
        _validate(tall, canvas=(1920, 1080))  # 4 x 1080 = 4320: refused


def test_a_non_positive_canvas_is_refused_rather_than_making_every_look_illegal():
    with pytest.raises(ValueError, match="canvas must be positive"):
        _validate("hue=s=0", canvas=(0, 360))


def test_the_default_canvas_is_a_bound_not_an_absence():
    """A caller who names no canvas still gets bounded — loosely, never not at all.

    The tempting default was ``None`` meaning "skip the check", which makes
    "nobody threaded the canvas through" indistinguishable from "this look is
    fine". Pinned as behaviour: the default refuses ``scale=8000:8000``.
    """
    with pytest.raises(ValueError, match="more than"):
        validate_edl([_entry(look="scale=8000:8000")], [_A], 4.0)


def test_the_default_canvas_covers_every_canvas_muvid_offers():
    """...and it is the element-wise maximum of them, which is what makes it loosest.

    Pinned rather than imported: ``muvid.footage.edl`` is on the import-safe
    path. A new canvas larger than this in either axis would silently make the
    default TIGHTER than a real project's, so a look muvid compiles for that
    canvas could be refused by a direct caller of ``validate_edl``.
    """
    from muvid.footage.workspace import CANVASES

    for name, (w, h) in CANVASES.items():
        assert w <= DFLT_LOOK_CANVAS[0] and h <= DFLT_LOOK_CANVAS[1], (
            f"canvas {name!r} ({w}x{h}) is larger than DFLT_LOOK_CANVAS "
            f"{DFLT_LOOK_CANVAS}, so the default bound is no longer the loosest "
            "one. Raise DFLT_LOOK_CANVAS to the element-wise maximum."
        )


# -- what must keep working --------------------------------------------------


def test_crop_is_not_bounded_because_it_cannot_grow_a_frame():
    """``motion``'s constant-size window is ``crop`` with EXPRESSIONS for w/h.

    It is the one muvid-compiled fragment whose size options are not literals,
    and it must keep passing. It does because ``crop``'s ``w``/``h`` are ``free``
    in ``_LOOK_GEOMETRY_FILTERS`` rather than ``sizes`` — see the ffmpeg
    measurement below for why that is safe rather than an omission.
    """
    frag = motion(
        [
            (0.0, CropWindow(0.0, 0.0, 0.5, 0.5)),
            (2.0, CropWindow(0.5, 0.5, 0.5, 0.5)),
        ],
        canvas=CANVAS,
        fps=25,
    )
    assert "crop=w='iw*0.5'" in frag, "the premise: crop's size IS an expression"
    _validate(frag)


@pytest.mark.parametrize(
    "canvas", [(640, 360), (1920, 1080), (1080, 1920), (1080, 1080)]
)
def test_every_geometry_look_muvid_compiles_passes_its_own_bound(canvas):
    """Compiled FOR a canvas, validated AGAINST that same canvas.

    The sibling test in ``test_edl_look.py`` validates against the default, which
    would not see a bound that is too tight for a real project. Here the two
    canvases agree, which is the arrangement a render actually uses.
    """
    _validate(punch_in(canvas=canvas, fps=25, duration_s=3.0), canvas=canvas)
    _validate(
        motion(
            [(0.0, CropWindow(0, 0, 1, 1)), (2.0, CropWindow(0.2, 0.2, 0.6, 0.6))],
            canvas=canvas,
            fps=25,
        ),
        canvas=canvas,
    )


@needs_ffmpeg
@pytest.mark.parametrize("canvas", [(640, 360), (1920, 1080), (1080, 1920)])
def test_every_looks_effect_muvid_compiles_passes_its_own_bound(canvas):
    """The ``stylize`` half — it probes a binary, so it needs one.

    Includes the geometry effects at targets a caller would plausibly ask for.
    ``fill`` to a square target is the tight one: on a 640x360 canvas it emits
    ``scale=1920:1080``, exactly 3x linear, which is why ``MAX_LOOK_SCALE`` is 4
    and not 2 or 3.
    """
    import looks

    from muvid.footage.look import stylize

    frags = []
    for name in sorted(looks.effects()):
        try:
            frags.append(
                stylize(
                    looks.Look(steps=(looks.Effect(name=name),)),
                    canvas=canvas,
                    fps=25,
                    duration_s=3.0,
                )
            )
        except Exception:
            continue  # needs params or an artifact; the geometry ones are next
    assert frags, "no looks effect compiled bare — this test would be vacuous"
    for target in (f"{canvas[0]}x{canvas[1]}", "1280x720", "1080x1080"):
        for name in ("fill", "fit", "stretch"):
            frags.append(
                stylize(
                    looks.Look(
                        steps=(looks.Effect(name=name, params={"target": target}),)
                    ),
                    canvas=canvas,
                    fps=25,
                    duration_s=3.0,
                )
            )
    for frag in frags:
        _validate(frag, canvas=canvas)  # must not raise


@needs_ffmpeg
def test_the_3x_case_that_MAX_LOOK_SCALE_is_sized_for_is_really_in_the_sweep():
    """The justification for ``4`` rather than ``2``, asserted instead of assumed.

    ``MAX_LOOK_SCALE``'s docstring says a bound of 2 would refuse a look muvid
    itself compiles, and names ``stylize(fill, target="1080x1080")`` on a 640x360
    canvas. If that stopped emitting ``scale=1920:1080``, the sweep above would go
    on passing for a reason that no longer holds and the constant's rationale
    would be stale prose. So the shape is pinned where the number is decided.
    """
    import looks

    from muvid.footage.look import stylize

    frag = stylize(
        looks.Look(steps=(looks.Effect(name="fill", params={"target": "1080x1080"}),)),
        canvas=(640, 360),
        fps=25,
        duration_s=3.0,
    )
    assert frag.startswith("scale=1920:1080"), frag
    widths = {px for _, _, axis, _, px in _look_output_sizes(frag) if axis == "width"}
    assert max(widths) == 3 * 640, "no longer 3x the canvas — re-derive MAX_LOOK_SCALE"
    _validate(frag, canvas=(640, 360))  # accepted at 4
    assert MAX_LOOK_SCALE > 3, (
        "a bound of 3 accepts this exactly on the edge and a bound of 2 refuses "
        "it outright — either way the seam is one rounding from an outage."
    )


def test_a_look_with_no_size_option_at_all_is_untouched():
    """The grades — the thing the seam is mostly for — are not in this at all."""
    for look in ("hue=s=0", "eq=contrast=1.2", "lut3d=file=/x/y.cube", "null"):
        _validate(look)


def test_a_size_filter_with_no_arguments_declares_nothing():
    """Bare ``scale``/``pad`` are legal identities (rc=0, measured), not empty widths.

    The first version read an absent ``w`` as the empty string, could not parse
    it as a pixel count, and refused — a fragment ffmpeg accepts, rejected by the
    gate. The neighbouring case must stay refused: ``scale=:100`` names an empty
    width *within* a list, and ffmpeg refuses it too ("Cannot parse expression
    for width: ''", rc=234), so the gate agreeing is correct rather than
    incidental.
    """
    for look in ("scale", "pad", "hue=s=0,scale", "scale="):
        _validate(look)
    with pytest.raises(ValueError, match="not a plain pixel count"):
        _validate("scale=:100")


@needs_ffmpeg
def test_ffmpeg_agrees_about_the_two_empty_size_cases():
    """The premise of the test above, against the binary rather than from the docs."""

    def rc(vf):
        return subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=64x48:rate=25:d=1",
                "-vf",
                vf,
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
        ).returncode

    assert rc("scale") == 0 and rc("pad") == 0
    assert rc("scale=:100") != 0


# -- the gate and the binary must agree about what a fragment SAYS -----------


def _rendered_size(tmp_path, vf) -> "tuple[int, int]":
    """The frame size ffmpeg really produces for ``vf``, from a decoded probe."""
    src = tmp_path / "src.mp4"
    if not src.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=64x48:rate=25:d=1",
                "-frames:v",
                "3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(src),
            ],
            check=True,
            capture_output=True,
        )
    out = tmp_path / "out.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-vf",
            vf,
            "-frames:v",
            "1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    w, h = (int(x) for x in probe.stdout.strip().split(","))
    return w, h


@needs_ffmpeg
@pytest.mark.parametrize(
    "vf",
    [
        "scale=256:192",
        "scale=w=256:h=192",
        "scale=width=256:height=192",
        "scale=s=256x192",
        "scale=size=256x192",
        "scale=256:192:flags=bilinear",
        "pad=256:192",
        "pad=w=256:h=192",
        "zoompan=d=1:s=256x192:fps=25",
        "zoompan=1:0:0:1:256x192:25",
        "hue=s=0,scale=256:192",
        # the passthroughs: nothing declared, nothing bounded
        "hue=s=0",
        "null",
        # …including a size-setting filter with NO arguments, which is legal
        # ffmpeg (rc=0, frame unchanged) and which the gate read as an empty
        # width until this row existed. A false REFUSE is as much a defect as a
        # false accept, and only a probe of the binary can see it.
        "scale",
        "pad",
        "hue=s=0,scale",
    ],
)
def test_the_gate_reads_the_same_frame_size_the_binary_produces(tmp_path, vf):
    """The instrument for the option table, against the real binary.

    A gate that disagrees with ffmpeg about what a fragment SAYS is guessing, and
    a guess in either direction is a defect: a spelling the table does not know
    is an unbounded lever (this is exactly how ``pad`` and ``scale=s=`` were
    nearly missed), and one it over-reads refuses a fragment ffmpeg would have
    accepted. So the assertion is against a DECODED probe of the produced frame,
    not against a string someone expected.

    ``64x48`` source, so a passthrough is distinguishable from the 256x192 the
    declared ones produce — a source that already was the target size would make
    every row agree for the wrong reason.
    """
    declared = _look_output_sizes(vf)
    produced = _rendered_size(tmp_path, vf)
    if not declared:
        assert produced == (64, 48), (
            f"{vf!r} declares no size to this gate, yet ffmpeg resized to "
            f"{produced} — an unbounded lever the option table does not know about."
        )
        return
    by_axis = {axis: px for _, _, axis, _, px in declared}
    assert (by_axis["width"], by_axis["height"]) == produced, (
        f"the gate reads {by_axis} out of {vf!r}, ffmpeg produced {produced}."
    )


@needs_ffmpeg
@pytest.mark.parametrize(
    "look, expected",
    [
        # pad's `aspect`, named — w and h left AT the canvas size
        ("pad=w=64:h=48:aspect=1/30", (64, 1920)),
        # …and reached positionally, at pad's SEVENTH slot
        ("pad=64:48:0:0:black:init:1/30", (64, 1920)),
        # scale's force_original_aspect_ratio after an aspect-changing crop —
        # both declared sizes are plain pixel counts inside the bound
        ("crop=w=64:h=8,scale=w=256:h=192:force_original_aspect_ratio=increase",
         (1536, 192)),
    ],
)
def test_the_refused_options_really_do_grow_the_frame_on_this_binary(
    tmp_path, look, expected
):
    """The premise of the refusals above, against the binary rather than the docs.

    A refusal list is only worth what the proof behind it is worth: if these
    fragments did NOT grow the frame, refusing them would be pure over-refusal
    and the allowlist would be costing the seam something for nothing.

    Deliberately scaled DOWN from the production measurement — a 64x48 source and
    canvas, so the same shapes produce 64x1920 and 1536x192 instead of the
    1920x57600 (590 MB) and 41472x4320 (941 MB) they produce on a 1920x1080
    canvas. The property is that the frame grows past what the gate read; paying
    590 MB inside a test suite to observe it is not part of the property.
    """
    assert _rendered_size(tmp_path, look) == expected
    declared = {axis: px for _, _, axis, _, px in _look_output_sizes(look)}
    ceiling = (max(declared.get("width") or 0, 64), max(declared.get("height") or 0, 48))
    assert expected[0] > ceiling[0] or expected[1] > ceiling[1], (
        f"{look!r} produced {expected}, which is within the {ceiling} the gate "
        "reads — so it is not a lever and refusing it is pure over-refusal."
    )


@needs_ffmpeg
def test_crop_really_cannot_grow_a_frame(tmp_path):
    """The premise behind leaving ``crop`` out of the table, on the real binary.

    Both a literal and an expression are refused by ffmpeg itself, so crop's
    output is bounded by its input — which at the head of a look chain is the
    canvas, and everywhere else is something the table already bounded.
    """
    for vf in ("crop=8000:8000", "crop=w='iw*80':h='ih*80'"):
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=64x48:rate=25:d=1",
                "-vf",
                vf,
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
        )
        assert proc.returncode != 0, f"{vf!r} grew the frame after all"
        assert re.search(rb"too big|non positive", proc.stderr), proc.stderr[:300]


# -- the canvas has to REACH the gate ----------------------------------------


#: The ``validate_edl`` call sites that deliberately pass no canvas, each with
#: the reason — the ``_LOOK_FILE_OPTIONS`` shape, an acknowledgement rather than
#: an exemption. A site here must ALSO satisfy the premise the reason rests on,
#: which the test below checks separately rather than taking on trust.
CANVASLESS_VALIDATE_EDL_SITES = {
    "footage/select_score.py": (
        "the weighted strategy re-validating its OWN output as a self-check "
        '("tautology by construction"). Its entries are built by `_coalesce` '
        "from `EdlEntry(s, e, cid)` — three positional fields, no `look` — so "
        "there is no look for a canvas to bound, and the caller's EDL passes the "
        "real gate in mcp/footage_tools.py immediately afterwards. Threading a "
        "canvas here would mean putting one on the SelectionStrategy protocol "
        "for a value nothing reads."
    ),
}


def test_every_muvid_call_to_validate_edl_passes_the_canvas():
    """``DFLT_LOOK_CANVAS``'s docstring claims this; here is the assertion.

    The claim is about CALL SITES, so the guard has to be about call sites too.
    A behavioural test cannot reach three of the four sites that validate a
    caller-reachable EDL: they carry machine-generated entries from
    ``select_edl``, which has no ``look``, so the canvas is unused there *today*
    and dropping ``canvas=`` from any of them left the whole suite green
    (measured, all 685 tests). That made "asserted by a test" true of one site
    and prose about the rest — and it is exactly the claim a future reader would
    rely on when adding another site, at which point the canvas may well matter.

    Scanned with ``ast`` rather than grepped so a call spread over several lines
    counts, and so the definition itself is not mistaken for a call. Writing it
    that way immediately found a FIFTH site nobody had counted
    (``footage/select_score.py``), which is the argument for the guard.
    """
    import ast

    root = Path(__file__).parents[1] / "muvid"
    sites, missing, seen_canvasless = 0, [], set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name != "validate_edl":
                continue
            sites += 1
            where = str(path.relative_to(root))
            if not any(kw.arg == "canvas" for kw in node.keywords):
                if where not in CANVASLESS_VALIDATE_EDL_SITES:
                    missing.append(f"{where}:{node.lineno}")
                seen_canvasless.add(where)
    assert sites >= 5, (
        f"found only {sites} validate_edl call sites in muvid/ — the scanner has "
        "stopped seeing them, so this test would pass with the canvas dropped "
        "everywhere."
    )
    assert not missing, (
        f"these validate_edl calls do not pass canvas=: {missing}. A look is "
        f"bounded against the DELIVERY canvas, and the default "
        f"{DFLT_LOOK_CANVAS} is the loosest one muvid offers — so an omitted "
        "canvas silently bounds a portrait render against a 1920x1920 square. "
        "Either pass it, or record the site in CANVASLESS_VALIDATE_EDL_SITES "
        "with the reason it cannot carry a look."
    )
    stale = sorted(set(CANVASLESS_VALIDATE_EDL_SITES) - seen_canvasless)
    assert not stale, (
        f"{stale} is recorded as deliberately canvasless and no longer is (or no "
        "longer calls validate_edl at all) — an acknowledgement that outlived "
        "what it acknowledged."
    )


def test_the_canvasless_site_really_cannot_carry_a_look():
    """The premise the exemption above rests on, checked rather than trusted.

    ``select_score``'s self-check needs no canvas only because the entries it
    validates are its own and have no ``look``. If the weighted strategy ever
    started emitting one — a punch-in per beat is an obvious future feature —
    that look would be bounded against ``DFLT_LOOK_CANVAS`` instead of the render
    canvas, silently, and this exemption would be the reason.
    """
    import ast

    src = Path(__file__).parents[1] / "muvid" / "footage" / "select_score.py"
    for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "EdlEntry":
            assert not node.keywords, (
                f"select_score.py:{node.lineno} builds an EdlEntry with keyword "
                f"fields {[k.arg for k in node.keywords]}. If one of them is "
                "`look`, the canvasless validate_edl call there is no longer a "
                "tautology — thread the canvas through or drop the exemption."
            )


def _fake_state(tmp_path, monkeypatch, project_id="p"):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    from muvid.footage.workspace import FootageWorkspace

    proj = FootageWorkspace.for_email("u@x.com").create_project(project_id)
    (proj.root / "song").mkdir()
    (proj.root / "song" / "song.wav").write_bytes(b"x")
    (proj.root / "clips").mkdir()
    (proj.root / "clips" / "A.mp4").write_bytes(b"x")
    m = proj.manifest()
    m.update(
        song="song.wav",
        song_duration=30.0,
        clips=[{"clip_id": "A", "file": "A.mp4", "name": "A"}],
    )
    proj._write_manifest(m)
    proj.save_alignments([FootageAlignment("A", 5.0, 0.9, 10.0, (5.0, 15.0))])
    return proj


def _assemble_with(tmp_path, monkeypatch, look, *, project_id, **kw):
    from pathlib import Path

    pytest.importorskip("fastmcp")
    import muvid.footage.assemble as A
    import muvid.mcp.footage_tools as ft
    import muvid.visualize as V
    from muvid.mcp.identity import use_email

    _fake_state(tmp_path, monkeypatch, project_id)

    def _fake_assemble(cuts, song, out, canvas, on_note=None):
        Path(out).write_bytes(b"v")
        return Path(out)

    monkeypatch.setattr(A, "assemble_music_video", _fake_assemble)
    monkeypatch.setattr(V, "verify_video", lambda *a, **k: [])
    monkeypatch.setattr(V, "failures", lambda c: [])
    monkeypatch.setattr(V, "report", lambda c: "ok")
    with use_email("u@x.com"):
        return ft.assemble_music_video(
            project_id,
            edl=[{"song_start": 5, "song_end": 15, "clip_id": "A", "look": look}],
            **kw,
        )


def test_the_render_canvas_reaches_the_gate_including_the_override(
    tmp_path, monkeypatch
):
    """The `canvas=` override changes the BOUND, not only the render.

    Three fragments, chosen so each distinguishes a different wrong answer. The
    project canvas is landscape (1920x1080), the override is portrait
    (1080x1920), and the module default is 1920x1920 — so all three bounds are
    different rectangles and a fragment can single one out:

    - ``scale=7680:4320`` under ``canvas='portrait'`` is refused only if the
      OVERRIDE reached the gate (it passes under both the project canvas and the
      default). This is the one that fails if ``_resolve_canvas`` is moved back
      below the validation, which is where it used to sit.
    - ``scale=4320:7680`` under ``canvas='portrait'`` must be ACCEPTED — the
      positive control, without which "refuses everything" would pass.
    - ``scale=1920:5000`` with no override is refused only if the PROJECT canvas
      reached it (it passes under the default).
    """
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="more than"):
        _assemble_with(
            tmp_path,
            monkeypatch,
            "scale=7680:4320",
            project_id="override_refused",
            canvas="portrait",
        )
    meta = _assemble_with(
        tmp_path,
        monkeypatch,
        "scale=4320:7680",
        project_id="override_accepted",
        canvas="portrait",
    )
    assert meta["edl"][1]["look"] == "scale=4320:7680"
    with pytest.raises(ToolError, match="more than"):
        _assemble_with(
            tmp_path, monkeypatch, "scale=1920:5000", project_id="project_canvas"
        )
