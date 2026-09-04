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
"""

from __future__ import annotations

import re
import subprocess

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
    and it must keep passing. It does because ``crop`` is deliberately absent
    from ``_LOOK_SIZE_OPTIONS`` — see the ffmpeg measurement below for why that
    is safe rather than an omission.
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

    def _fake_assemble(cuts, song, out, canvas):
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
