"""Render a :class:`~muvid.montage.plan.Plan` to a YouTube-spec mp4 with ffmpeg.

Bounded stages, the same shape as ``muvid.footage.assemble``: **one ffmpeg per
part** (a slot's solo span, or the blend window between two slots), a
**stream-copy concat** of the parts, and **one mux** of the song. Memory is
O(1) in the number of cuts, and a 300-slot montage never builds a 300-input
filtergraph — a chained ``xfade`` of that depth is the classic way to make
ffmpeg crawl.

Every part is frame-exact. Slot boundaries are rounded to frames ONCE
(:func:`frame_layout`), a transition of ``n`` frames straddles its boundary
(``n//2`` before, the rest after — centred on the beat, the NLE convention),
and the solo parts are shortened by exactly what the blends take, so the parts
sum to ``round(duration * fps)`` by construction rather than by luck.

A still is turned into motion by ``zoompan=d=N`` over ONE decoded frame — the
image is decoded once per part, not once per output frame — with the window
path compiled to expressions in ``on`` (the output frame counter) plus a frame
offset. That offset is what lets a blend part sample the SAME Ken Burns path
the solo part was on: the motion never restarts on a crossfade. Stills are
pre-fitted to :data:`OVERSAMPLE` times the tile so ``zoompan`` always
downsamples, which is what avoids its well-known sub-pixel jitter.

``muvid.montage`` does not use ``looks.compile_motion`` for this, although
``looks`` is the house motion compiler: its fragment keys on ``in_time`` and
is built for video sources at ``d=1``; over one still frame expanded by
``d=N`` the input time never advances. The expression builder here is the
``on``-keyed twin of it, and it is a pure function with a doctest.

All ffmpeg goes through :mod:`muvid.visualize.ffmpeg`; the encode arguments
are the visualizer's own so a montage is the same H.264/AAC/no-edit-list file
the rest of muvid ships.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from muvid.montage import spec as spec_mod
from muvid.montage.plan import Keyframe, Media, Plan, Slot, Tile
from muvid.subgenres._contract import RenderResult

__all__ = [
    "Canvas",
    "Part",
    "frame_layout",
    "grade_filter",
    "render_plan",
    "zoompan_exprs",
]

#: A still is pre-fitted to this many times the tile before ``zoompan`` so the
#: filter downsamples at every zoom level. Bounded relative to the canvas by
#: the pipeline's pixel bound, so it cannot become a 64-megapixel lever.
OVERSAMPLE = 2.0
#: A transition that rounds below this many frames is a hard cut wearing a
#: label (``muvid.footage.edl.MIN_TRANSITION_S`` reasons the same way).
MIN_TRANSITION_FRAMES = 2
#: How much of the accent colour the ``tint`` grade blends in.
TINT_STRENGTH = 0.35
DEFAULT_CRF = 18
#: Faster than the visualizer's ``medium``: a montage is hundreds of short
#: encodes, and YouTube re-encodes the upload anyway.
DEFAULT_PRESET = "fast"
GOP_SECONDS = 2.0
RENDERER_NAME = "montage-ffmpeg"


@dataclass(frozen=True, slots=True, kw_only=True)
class Canvas:
    """Output geometry."""

    width: int = 1920
    height: int = 1080
    fps: int = 30


@dataclass(frozen=True, slots=True, kw_only=True)
class Part:
    """One ffmpeg invocation's worth of frames.

    ``offset`` is the first frame of this part relative to ``slot``'s start
    (negative on the incoming side of a blend). A ``blend`` also names the
    outgoing ``prev`` slot and its offset.
    """

    kind: str  # "solo" | "blend"
    slot: int
    offset: int
    n_frames: int
    prev: int | None = None
    prev_offset: int = 0
    curve: str = "fade"


# --------------------------------------------------------------------------
# pure planning of frames
# --------------------------------------------------------------------------


def frame_layout(
    starts: Sequence[float], duration: float, transitions_s: Sequence[float], fps: int
) -> tuple[list[int], list[int]]:
    """Slot boundaries in frames, and the transition length into each slot.

    ``starts[i]`` is slot i's start; ``transitions_s[i]`` the crossfade INTO
    slot i (0 for a cut; slot 0's is always 0). A transition is clamped to
    half the shorter neighbour and dropped below :data:`MIN_TRANSITION_FRAMES`.

    >>> frame_layout([0.0, 1.0, 2.0], 3.0, [0.0, 0.5, 0.05], 24)
    ([0, 24, 48, 72], [0, 12, 0])
    >>> frame_layout([0.0, 0.1], 0.2, [0.0, 0.1], 24)   # 2-frame slots: no room
    ([0, 2, 5], [0, 0])
    """
    n_total = int(round(duration * fps))
    bounds = [int(round(s * fps)) for s in starts] + [n_total]
    for i in range(1, len(bounds)):
        bounds[i] = max(bounds[i - 1], min(bounds[i], n_total))
    lens = [b - a for a, b in zip(bounds, bounds[1:])]
    n_t: list[int] = []
    for i, t_s in enumerate(transitions_s):
        n = 0 if i == 0 else int(round(t_s * fps))
        if i > 0:
            n = min(n, lens[i - 1] // 2, lens[i] // 2)
        n_t.append(n if n >= MIN_TRANSITION_FRAMES else 0)
    return bounds, n_t


def parts_for(plan: Plan, fps: int) -> tuple[list[Part], dict[str, Any]]:
    """Every part to render, in output order, plus what got dropped.

    >>> from muvid.montage.plan import Plan, Slot, Tile, Keyframe, Window
    >>> def slot(i, a, b, tr='cut', ts=0.0):
    ...     return Slot(index=i, start=a, end=b, section='verse', archetype='x', regions=1,
    ...                 tiles=(Tile(region=0, media=0, motion='none', variant='full',
    ...                             path=(Keyframe(0.0, Window(0, 0, 1)),)),),
    ...                 transition=tr, transition_s=ts)
    >>> p = Plan(duration=2.0, tempo_bpm=120, beats_per_bar=4, beat_source='', section_source='',
    ...          sections=(), media=(), slots=(slot(0, 0, 1), slot(1, 1, 2, 'fade', 0.5)))
    >>> [(x.kind, x.slot, x.offset, x.n_frames, x.prev, x.prev_offset)
    ...  for x in parts_for(p, 24)[0]]
    [('solo', 0, 0, 18, None, 0), ('blend', 1, -6, 12, 0, 18), ('solo', 1, 6, 18, None, 0)]
    >>> sum(x.n_frames for x in parts_for(p, 24)[0]) == round(2.0 * 24)
    True
    """
    slots = plan.slots
    bounds, n_t = frame_layout(
        [s.start for s in slots],
        plan.duration,
        [s.transition_s if s.transition != "cut" else 0.0 for s in slots],
        fps,
    )
    lens = [b - a for a, b in zip(bounds, bounds[1:])]
    dropped = [
        s.index
        for s, n in zip(slots, n_t)
        if s.transition != "cut" and s.transition_s > 0 and n == 0
    ]
    parts: list[Part] = []
    for i, s in enumerate(slots):
        n_in = n_t[i]
        n_out = n_t[i + 1] if i + 1 < len(slots) else 0
        if n_in > 0:
            parts.append(
                Part(
                    kind="blend",
                    slot=i,
                    offset=-(n_in // 2),
                    n_frames=n_in,
                    prev=i - 1,
                    prev_offset=lens[i - 1] - n_in // 2,
                    curve=s.transition,
                )
            )
        head = n_in - n_in // 2
        n_solo = lens[i] - head - n_out // 2
        if n_solo > 0:
            parts.append(Part(kind="solo", slot=i, offset=head, n_frames=n_solo))
    return parts, {
        "n_frames": bounds[-1],
        "n_parts": len(parts),
        "transitions_dropped": dropped,
        "empty_slots": [s.index for s, n in zip(slots, lens) if n == 0],
    }


# --------------------------------------------------------------------------
# pure filter fragments
# --------------------------------------------------------------------------


def _num(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in {"", "-"} else "0"


def _ramp_exprs(
    path: Sequence[Keyframe], *, frame_offset: int, fps: int
) -> tuple[str, str, str]:
    """``(size, x, y)`` expressions in ``on`` — sums of clamped linear ramps."""
    t = f"((on+{frame_offset})/{fps})"
    comps = []
    for attr in ("size", "x", "y"):
        v0 = getattr(path[0].window, attr)
        expr = _num(v0)
        for a, b in zip(path, path[1:]):
            dt = b.t - a.t
            dv = getattr(b.window, attr) - getattr(a.window, attr)
            if dt <= 0 or abs(dv) < 1e-9:
                continue
            expr += f"+({_num(dv)})*min(max(({t}-{_num(a.t)})/{_num(dt)},0),1)"
        comps.append(expr)
    return comps[0], comps[1], comps[2]


def zoompan_exprs(
    path: Sequence[Keyframe], *, frame_offset: int, fps: int
) -> dict[str, str]:
    """The ``z``/``x``/``y`` expressions for ``zoompan`` over a still.

    ``on`` is the output frame counter; ``frame_offset`` shifts it so a part
    that starts mid-slot (or, negative, before the slot's first frame on the
    incoming side of a blend) samples the slot's own path.

    >>> from muvid.montage.plan import Keyframe, Window
    >>> path = (Keyframe(0.0, Window(0.0, 0.0, 1.0)), Keyframe(2.0, Window(0.1, 0.1, 0.8)))
    >>> e = zoompan_exprs(path, frame_offset=12, fps=24)
    >>> e['z']
    '1/(1+(-0.2)*min(max((((on+12)/24)-0)/2,0),1))'
    >>> e['x']
    'iw*(0+(0.1)*min(max((((on+12)/24)-0)/2,0),1))'
    >>> zoompan_exprs(path[:1], frame_offset=0, fps=24)
    {'z': '1/(1)', 'x': 'iw*(0)', 'y': 'ih*(0)'}
    """
    size, x, y = _ramp_exprs(path, frame_offset=frame_offset, fps=fps)
    return {"z": f"1/({size})", "x": f"iw*({x})", "y": f"ih*({y})"}


def _rgb(colour: str) -> tuple[float, float, float]:
    c = colour.lstrip("#")
    return tuple(int(c[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def grade_filter(
    grade: str, accent: str = "#e0533d", *, strength: float = TINT_STRENGTH
) -> str:
    """The ffmpeg fragment for a grade, or ``""`` for none.

    ``tint`` mixes ``strength`` of ``luma * accent`` into the frame — a closed
    form over the accent colour, never a caller-supplied filter string.

    >>> grade_filter('none')
    ''
    >>> grade_filter('mono')
    'hue=s=0'
    >>> grade_filter('tint', '#ff0000', strength=0.5)
    'colorchannelmixer=rr=0.6495:rg=0.2935:rb=0.057:gr=0:gg=0.5:gb=0:br=0:bg=0:bb=0.5'
    """
    if grade == "mono":
        return "hue=s=0"
    if grade != "tint":
        return ""
    a = min(1.0, max(0.0, strength))
    ar, ag, ab = _rgb(accent)
    luma = (0.299, 0.587, 0.114)
    rows = []
    for row, weight in zip("rgb", (ar, ag, ab)):
        for col, lum in zip("rgb", luma):
            v = a * weight * lum + ((1 - a) if row == col else 0.0)
            rows.append(f"{row}{col}={_num(round(v, 4))}")
    return "colorchannelmixer=" + ":".join(rows)


def _even(v: float) -> int:
    n = int(v)
    return max(2, n - n % 2)


# --------------------------------------------------------------------------
# ffmpeg graphs
# --------------------------------------------------------------------------


def _tile_input(
    tile: Tile, media: Media, *, offset: int, n_frames: int, fps: int
) -> list[str]:
    if media.kind == "clip":
        seek = max(0.0, tile.source_in + offset / fps)
        return [
            "-ss",
            f"{seek:.6f}",
            "-t",
            f"{(n_frames + 1) / fps:.6f}",
            "-i",
            media.path,
        ]
    return ["-i", media.path]


def _tile_chain(
    tile: Tile,
    media: Media,
    *,
    src: str,
    out: str,
    tw: int,
    th: int,
    fps: int,
    offset: int,
    n_frames: int,
) -> str:
    if media.kind == "clip":
        return (
            f"[{src}]scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th},setsar=1,fps={fps},tpad=stop=-1:stop_mode=clone[{out}]"
        )
    fw, fh = _even(tw * OVERSAMPLE), _even(th * OVERSAMPLE)
    e = zoompan_exprs(tile.path, frame_offset=offset, fps=fps)
    return (
        f"[{src}]scale={fw}:{fh}:force_original_aspect_ratio=increase,crop={fw}:{fh},"
        f"setsar=1,zoompan=z='{e['z']}':x='{e['x']}':y='{e['y']}':d={n_frames}"
        f":s={tw}x{th}:fps={fps}[{out}]"
    )


def _slot_graph(
    slot: Slot,
    plan: Plan,
    *,
    canvas: Canvas,
    input_base: int,
    out: str,
    offset: int,
    n_frames: int,
    grade: str,
    bg: str,
) -> tuple[list[str], list[str]]:
    """``(input args, filter chains)`` producing ``[out]`` at canvas size."""
    by_index = {m.index: m for m in plan.media}
    w, h, fps = canvas.width, canvas.height, canvas.fps
    inputs: list[str] = []
    chains: list[str] = []
    # `scale=out_range=tv` CONVERTS a full-range source (every JPEG) to the
    # limited range H.264 players expect; `format=yuv420p` alone keeps the pc
    # range and the file probes as yuvj420p, which the verifier refuses.
    tail = (grade + "," if grade else "") + "scale=out_range=tv,format=yuv420p"
    if slot.regions == 1:
        tile = slot.tiles[0]
        media = by_index[tile.media]
        inputs += _tile_input(tile, media, offset=offset, n_frames=n_frames, fps=fps)
        chains.append(
            _tile_chain(
                tile,
                media,
                src=f"{input_base}:v",
                out=f"{out}_t",
                tw=w,
                th=h,
                fps=fps,
                offset=offset,
                n_frames=n_frames,
            )
        )
        chains.append(f"[{out}_t]{tail}[{out}]")
        return inputs, chains
    tw, th = _even(w / 2), _even(h / 2)
    labels = []
    for k, tile in enumerate(sorted(slot.tiles, key=lambda t: t.region)):
        media = by_index[tile.media]
        inputs += _tile_input(tile, media, offset=offset, n_frames=n_frames, fps=fps)
        label = f"{out}_t{k}"
        chains.append(
            _tile_chain(
                tile,
                media,
                src=f"{input_base + k}:v",
                out=label,
                tw=tw,
                th=th,
                fps=fps,
                offset=offset,
                n_frames=n_frames,
            )
        )
        labels.append(f"[{label}]")
    pad = (
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={bg},"
        if (tw * 2, th * 2) != (w, h)
        else ""
    )
    chains.append(
        "".join(labels)
        + f"xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0,{pad}{tail}[{out}]"
    )
    return inputs, chains


def _part_args(
    part: Part,
    plan: Plan,
    *,
    canvas: Canvas,
    grade: str,
    bg: str,
) -> list[str]:
    """Everything before the encode arguments for one part."""
    slots = plan.slots
    if part.kind == "solo":
        inputs, chains = _slot_graph(
            slots[part.slot],
            plan,
            canvas=canvas,
            input_base=0,
            out="v",
            offset=part.offset,
            n_frames=part.n_frames,
            grade=grade,
            bg=bg,
        )
    else:
        assert part.prev is not None
        in_a, ch_a = _slot_graph(
            slots[part.prev],
            plan,
            canvas=canvas,
            input_base=0,
            out="a",
            offset=part.prev_offset,
            n_frames=part.n_frames,
            grade=grade,
            bg=bg,
        )
        n_a = sum(1 for x in in_a if x == "-i")
        in_b, ch_b = _slot_graph(
            slots[part.slot],
            plan,
            canvas=canvas,
            input_base=n_a,
            out="b",
            offset=part.offset,
            n_frames=part.n_frames,
            grade=grade,
            bg=bg,
        )
        inputs = in_a + in_b
        chains = (
            ch_a
            + ch_b
            + [
                f"[a][b]xfade=transition={part.curve}:duration={part.n_frames / canvas.fps:.6f}"
                ":offset=0[v]"
            ]
        )
    return [
        *inputs,
        "-filter_complex",
        ";".join(chains),
        "-map",
        "[v]",
        "-frames:v",
        str(part.n_frames),
        "-an",
    ]


# --------------------------------------------------------------------------
# the render
# --------------------------------------------------------------------------


def render_plan(
    plan: Plan,
    *,
    canvas: Canvas,
    audio: Path | str,
    output: Path | str,
    workdir: Path | str,
    grade: str = "none",
    palette: spec_mod.Palette | None = None,
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
    keep_parts: bool = False,
) -> RenderResult:
    """Render ``plan`` over ``audio`` to ``output``. Writes exactly ``output``.

    Args:
        plan: The edit list. Its media paths are opened as-is.
        canvas: Output size and frame rate. Even dimensions only.
        audio: The song; the video is exactly as long as it.
        output: The mp4 to write.
        workdir: Where the parts go (``workdir/parts``, removed after a
            successful mux unless ``keep_parts``).
        grade: One of :data:`muvid.montage.spec.GRADES`.
        palette: Supplies the tint accent and the grid pad colour.
        crf, preset: libx264 knobs, per part.
        keep_parts: Leave the intermediate parts on disk.

    Returns:
        A :class:`RenderResult` whose ``meta`` carries the frame accounting,
        the parts count, and the verifier's findings.
    """
    from muvid.visualize.ffmpeg import (
        media_duration,
        require_ffmpeg,
        require_filter,
        run_ffmpeg,
    )
    from muvid.visualize.verify import verify_video
    from muvid.visualize.video import (
        _audio_encode_args,
        _container_args,
        _gop_frames,
        _video_encode_args,
    )

    if canvas.width % 2 or canvas.height % 2:
        raise ValueError(
            f"canvas {canvas.width}x{canvas.height} has an odd dimension; H.264 at "
            "yuv420p needs even ones"
        )
    if grade not in spec_mod.GRADES:
        raise ValueError(f"grade {grade!r} is not one of {sorted(spec_mod.GRADES)}")
    require_ffmpeg()
    require_filter("zoompan", needed_for="montage stills")
    palette = palette or spec_mod.Palette()
    grade_chain = grade_filter(grade, palette.accent)
    if grade_chain:
        require_filter(grade_chain.split("=", 1)[0], needed_for=f"the {grade!r} grade")

    audio, output, workdir = Path(audio), Path(output), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    parts, accounting = parts_for(plan, canvas.fps)
    if not parts:
        raise ValueError("nothing to render: every slot rounds to zero frames")
    if any(p.kind == "blend" for p in parts):
        require_filter("xfade", needed_for="montage crossfades")
    if any(plan.slots[p.slot].regions > 1 for p in parts):
        require_filter("xstack", needed_for="the grid archetype")

    parts_dir = workdir / "parts"
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True)
    encode = _video_encode_args(
        crf=crf, preset=preset, fps=canvas.fps, gop=_gop_frames(canvas.fps, GOP_SECONDS)
    )
    bg = "0x" + palette.bg.lstrip("#")
    names = []
    for i, part in enumerate(parts):
        name = f"part{i:04d}.mp4"
        run_ffmpeg(
            [
                *_part_args(part, plan, canvas=canvas, grade=grade_chain, bg=bg),
                *encode,
                str(parts_dir / name),
            ]
        )
        names.append(name)
    # Relative names in the list, resolved against the list's own directory —
    # the concat demuxer's default "safe" mode is fine and no quoting exists here.
    concat_list = parts_dir / "parts.txt"
    concat_list.write_text("".join(f"file {n}\n" for n in names), encoding="utf-8")
    run_ffmpeg(
        [
            "-f",
            "concat",
            "-i",
            str(concat_list),
            "-i",
            str(audio),
            "-map",
            "0:v",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            *_audio_encode_args("384k"),
            "-t",
            f"{plan.duration:.6f}",
            "-shortest",
            *_container_args(),
            # The manifest promises video/mp4; say so rather than let ffmpeg
            # guess from the extension (the conformance kit renders to `out.bin`).
            "-f",
            "mp4",
            str(output),
        ]
    )
    if not keep_parts:
        shutil.rmtree(parts_dir, ignore_errors=True)

    checks = verify_video(
        output, audio=audio, expected_canvas=(canvas.width, canvas.height)
    )
    meta: dict[str, Any] = {
        "renderer": RENDERER_NAME,
        "canvas": [canvas.width, canvas.height],
        "fps": canvas.fps,
        "crf": crf,
        "preset": preset,
        "grade": grade,
        "n_slots": len(plan.slots),
        **accounting,
        "verify_failures": [f"{c.name}: {c.detail}" for c in checks if not c],
    }
    return RenderResult(output=output, duration_s=media_duration(output), meta=meta)
