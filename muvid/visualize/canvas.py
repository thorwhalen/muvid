"""Lay a cover image out on a 16:9 canvas, and derive a thumbnail from it.

Cover art is usually square (or, worse, portrait) while video platforms are
16:9. Letting the platform pillarbox the art leaves black bars; instead we fill
the frame with a blurred, darkened copy of the cover and place the sharp cover
centred on top. It reads as intentional, and it is the same treatment whether
the result becomes a still video, the ground truth for a Ken Burns pan, or the
upload thumbnail — one :class:`CoverLayout`, one filtergraph, three uses.

Every filter chain here is built as a *string* rather than executed, so the
same chains compose into the bigger filtergraph that :mod:`muvid.visualize.video`
assembles for audio-reactive visuals.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from muvid.visualize.ffmpeg import FfmpegError, PathLike, require_filter, run_ffmpeg

#: Default 16:9 canvas: 1080p is YouTube's sweet spot for a static music video.
DEFAULT_SIZE = (1920, 1080)

#: YouTube rejects thumbnails over 2 MiB, and wants at least 1280x720.
THUMBNAIL_SIZE = (1280, 720)
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024

#: Fonts we fall back through when the system has no fontconfig.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


@dataclass(frozen=True)
class CoverLayout:
    """How a cover image is placed on the canvas.

    Attributes:
        background: ``"blur"`` (a blurred, darkened copy of the cover fills the
            frame) or ``"color"`` (a flat :attr:`background_color`).
        blur_sigma: Gaussian blur strength for the ``"blur"`` background.
        dim: How much to darken the background, 0 (unchanged) to 1 (black).
            Multiplicative — the background's luma is *scaled* by ``1 - dim``
            about black, so a shadow gets darker rather than being deleted. See
            :func:`dim_saturation_lut`; the constant is not comparable with the
            additive offset that preceded it (muvid#70).
        saturation: Background saturation (< 1 desaturates, so the sharp cover
            stays the focal point).
        cover_fraction: How much of the frame the sharp cover fills. The cover
            is scaled up, keeping its aspect ratio, until it reaches this
            fraction of *either* the frame width or the frame height — whichever
            it hits first (so a wide cover is width-bound, a tall one
            height-bound). ``1.0`` touches the edges; below 1 leaves padding.
        cover_alpha: Opacity of the sharp cover, 0 (invisible) to 1 (opaque).
            Below 1 lets whatever is behind the cover — a reactive visualizer,
            the blurred background — show through it.
        background_color: Fill colour when ``background="color"``.
    """

    background: str = "blur"
    blur_sigma: float = 30.0
    #: Measured, not chosen: the multiplicative dim that lands the plate's mean
    #: DISPLAY luma where the additive `0.25` left it, pooled over four
    #: photographs (muvid#70). The two forms are not comparable at the same
    #: nominal value — see :func:`dim_saturation_lut`.
    dim: float = 0.65
    saturation: float = 0.8
    cover_fraction: float = 0.92
    cover_alpha: float = 1.0
    background_color: str = "black"


@dataclass(frozen=True)
class TitleStyle:
    """How a burnt-in title is drawn (ffmpeg ``drawtext``).

    Attributes:
        size_fraction: Font size as a fraction of canvas height.
        color: Text colour.
        font: Font file path, or ``None`` to auto-detect one.
        margin_fraction: Distance from the bottom edge, as a fraction of height.
        box: Draw a translucent plate behind the text (keeps it legible over
            busy artwork).
        box_color: Colour (with alpha) of that plate.
    """

    size_fraction: float = 0.045
    color: str = "white"
    font: str | None = None
    margin_fraction: float = 0.06
    box: bool = True
    box_color: str = "black@0.45"


def cover_box(size: tuple[int, int], layout: CoverLayout) -> tuple[int, int]:
    """The bounding box the sharp cover is fitted into, for ``size``/``layout``.

    Scales with *both* frame dimensions, so a cover fitted into it with
    ``force_original_aspect_ratio=decrease`` grows until it meets whichever edge
    comes first — filling the frame up to ``cover_fraction``, minus padding.
    """
    width, height = size
    return round(width * layout.cover_fraction), round(height * layout.cover_fraction)


@lru_cache(maxsize=1)
def default_font() -> str | None:
    """Path to a usable TrueType font, or ``None`` if none was found."""
    if shutil.which("fc-match"):
        proc = subprocess.run(
            ["fc-match", "-f", "%{file}", "sans-serif"],
            capture_output=True,
            text=True,
        )
        candidate = proc.stdout.strip()
        if candidate and Path(candidate).exists():
            return candidate
    return next((f for f in _FONT_CANDIDATES if Path(f).exists()), None)


#: Characters ffmpeg's *option* parser gives special meaning to inside a single
#: filter's argument string: ``:`` separates one option from the next, and
#: ``\`` / ``'`` do that parser's quoting.
_OPTION_LEVEL_SPECIALS = ("\\", "'", ":")

#: Characters ffmpeg's *filtergraph* parser gives special meaning to across the
#: graph as a whole: ``,`` chains filters, ``;`` separates chains, ``[]`` delimit
#: pad labels, and ``\`` / ``'`` do that parser's quoting.
_GRAPH_LEVEL_SPECIALS = ("\\", "'", ",", ";", "[", "]")


def _backslash_escape(value: str, specials: tuple[str, ...]) -> str:
    """Prefix each character of ``specials`` found in ``value`` with a backslash.

    ``specials`` must lead with ``\\``: the backslashes this introduces for the
    later characters must not themselves be escaped again by the ``\\`` pass.
    """
    for char in specials:
        value = value.replace(char, "\\" + char)
    return value


def escape_filter_value(value: str) -> str:
    r"""Escape ``value`` for use as a filter option inside an ffmpeg filtergraph.

    ffmpeg unescapes such a value **twice**, so escaping it once is not enough:

    1. The *filtergraph* parser reads the whole graph first, splitting it on
       ``_GRAPH_LEVEL_SPECIALS`` and consuming one layer of quoting.
    2. Each surviving per-filter argument string is only then split into options
       on ``_OPTION_LEVEL_SPECIALS``, consuming a second layer.

    Escaping therefore runs in the mirror order — option level first, then graph
    level over that result — so that each ffmpeg pass peels off exactly one
    layer. A literal ``'`` comes out as ``\\\'`` and a literal ``:`` as ``\\:``.

    Escaping only once is why a title like ``"Song: Part 1"`` used to become a
    filtergraph syntax error, and why a workdir whose name contained ``'`` or
    ``:`` broke every ``sendcmd=f=<path>`` render.

    Note that ``%`` is deliberately *not* escaped: neither parser treats it as
    special, so ``\%`` was simply unescaped back to ``%`` and escaping it never
    did anything. drawtext's ``%{...}`` text expansion is a third level that
    applies to that one filter's ``text`` option only, and is out of scope for a
    general-purpose filtergraph escaper.
    """
    return _backslash_escape(
        _backslash_escape(value, _OPTION_LEVEL_SPECIALS), _GRAPH_LEVEL_SPECIALS
    )


#: The full 8-bit range ``eq`` clips its output to. ``lutyuv`` offers ``minval`` /
#: ``maxval`` / ``clipval`` variables and they are NOT these — they are the
#: *broadcast* range (16–235 luma, 16–240 chroma), so reaching for the named
#: constants instead of these literals would crush blacks and clip highlights that
#: ``eq`` left untouched. The literals are what reproduces ``eq``.
_FULL_RANGE = (0, 255)

#: Chroma neutral, and the pivot saturation scales about. ``vf_eq`` works in 0–1
#: and pivots on 0.5, which is 127.5 in 8 bits — *not* 128. The naive value never
#: costs more than ``0.5 * |1 - saturation|`` LSB, so no max-error bound can catch
#: it; what it does is raise the MEAN chroma error against ``eq`` (~1.3x at the
#: shipped saturation of 0.8, ~2x at 0.6). Free to get right, so it is written
#: once, here — and pinned by a test that measures the mean, not the max.
_CHROMA_PIVOT = 127.5

#: The pivot the DIM scales luma about: ``lutyuv``'s own ``minval``, which is the
#: black level of whichever pixel format the plate chain negotiated.
#:
#: **It is not a constant, and deriving it rather than writing 16 is the point.**
#: Measured by reading it back out of this module's own chain (a LUT whose ``y``
#: expression *is* ``minval``), identically on ffmpeg 9.0.1 and 6.1.6:
#:
#:     cover      the plate chain runs   ``minval``
#:     PNG        ``yuv444p``            16      <- limited range
#:     JPEG       ``yuvj420p`` (pc)      0       <- FULL range
#:
#: Album art is commonly a JPEG, so both halves are real inputs. A hardcoded ``16``
#: is invisible on the PNG half — byte-identical, max\\|diff\\| 0.0 — and hazes the
#: JPEG half: measured end to end on the composed spectrum graph, the plate's mean
#: goes 7.53 -> 21.58 out of 255 and its floor 3 -> 18 (ffmpeg 9.0.1; 22.59 -> 34.53
#: and 18 -> 31 on 6.1.6), i.e. grey exactly where the design wants black.
#:
#: This is the one place :data:`_FULL_RANGE`'s warning does NOT apply, and the
#: distinction is the whole fix. That warning is about reproducing ``eq``, whose
#: clip bounds are 0–255. A dim is not a clip: it is a scaling, and a scaling needs
#: the pivot the picture's black actually sits on. On a limited-range plate
#: ``val * gain`` pivots on a black that is not in the data and pushes the whole
#: picture under the floor, which the next conversion to RGB clamps away. Measured
#: on a real cover at ``dim=0.9``: pivoting on 0 leaves 78.7% of the plate at
#: display black over 6 distinct levels; pivoting here leaves 8.4% over 20. The
#: naive multiplicative form reproduces the bug it was meant to fix — and a
#: hardcoded 16 reproduces it on the other range instead.
_DIM_PIVOT = "minval"


def brightness_saturation_lut(
    *, brightness: float = 0.0, saturation: float = 1.0
) -> dict[str, str]:
    """``lutyuv`` y/u/v expressions reproducing ``eq``'s brightness + saturation.

    ``eq`` is compiled into ffmpeg only under ``--enable-gpl``, so every chain that
    reached for it made muvid require a GPL build for what is arithmetic on three
    planes. ``lutyuv`` is LGPL and expresses the same two knobs exactly as ``vf_eq``
    defines them: brightness is an ADDITIVE offset of ``brightness * 255`` on luma,
    saturation a scaling of chroma about :data:`_CHROMA_PIVOT`.

    Returns a ``{component: expression}`` mapping rather than a filter string
    because the two callers need different shapes — one composes a filtergraph,
    the other emits one ``sendcmd`` command per component — and a second copy of
    this arithmetic is exactly how the two would drift apart.

    Args:
        brightness: Additive luma offset, -1 to 1, in ``eq``'s units (a fraction
            of full scale). ``0`` is a no-op.
        saturation: Chroma scaling about neutral. ``1`` is a no-op.

    Examples:
        >>> lut = brightness_saturation_lut(brightness=-0.25, saturation=0.8)
        >>> lut["y"]
        'clip(val-63.75,0,255)'
        >>> lut["u"] == lut["v"]
        True
        >>> brightness_saturation_lut()["y"], brightness_saturation_lut()["u"]
        ('clip(val+0,0,255)', 'clip((val-127.5)*1+127.5,0,255)')
    """
    lo, hi = _FULL_RANGE
    chroma = _chroma_expr(saturation)
    return {
        "y": f"clip(val{brightness * hi:+g},{lo},{hi})",
        "u": chroma,
        "v": chroma,
    }


def _chroma_expr(saturation: float) -> str:
    """``lutyuv`` u/v expression scaling chroma about :data:`_CHROMA_PIVOT`.

    Both LUT builders need it and it is written once: the two are already the two
    halves of one look, and a second copy of ``eq``'s saturation arithmetic is
    exactly how the darkened plate and the beat flash would start desaturating by
    different amounts.
    """
    lo, hi = _FULL_RANGE
    return f"clip((val-{_CHROMA_PIVOT:g})*{saturation:g}+{_CHROMA_PIVOT:g},{lo},{hi})"


def dim_saturation_lut(*, dim: float = 0.0, saturation: float = 1.0) -> dict[str, str]:
    """``lutyuv`` y/u/v expressions that DARKEN luma and desaturate chroma.

    The sibling of :func:`brightness_saturation_lut`, and deliberately not the same
    arithmetic. ``eq``'s brightness — which this replaces at the one site that wanted
    to *darken* rather than to *shift* — is an additive offset, and subtracting a
    constant does not dim a picture: it slides the histogram down and clamps
    everything below the offset to the floor. The plate's shadows did not get darker,
    they were deleted, and at the reactive constants that took 57–99% of the plate to
    display black (muvid#70).

    So luma is *scaled* by ``1 - dim`` about :data:`_DIM_PIVOT`, which preserves the
    order of every pair of pixels — a shadow stays darker than what is next to it
    instead of joining it at black. Chroma is untouched by the change and still goes
    through :func:`_chroma_expr`, so the desaturation half remains ``eq``'s.

    ``dim`` therefore means something different from the additive offset it replaces,
    and the constants that ship were re-measured rather than converted: see
    :class:`CoverLayout` and :mod:`muvid.visualize.visuals`.

    Args:
        dim: How much to darken, 0 (unchanged) to 1 (black).
        saturation: Chroma scaling about neutral. ``1`` is a no-op.

    Examples:
        >>> lut = dim_saturation_lut(dim=0.65, saturation=0.8)
        >>> lut["y"]
        'clip((val-minval)*0.35+minval,0,255)'
        >>> lut["u"] == lut["v"] == brightness_saturation_lut(saturation=0.8)["u"]
        True
        >>> dim_saturation_lut()["y"]  # 0 is a no-op, and reads as one
        'clip((val-minval)*1+minval,0,255)'
    """
    lo, hi = _FULL_RANGE
    chroma = _chroma_expr(saturation)
    gain = 1 - dim
    return {
        "y": f"clip((val-{_DIM_PIVOT})*{gain:g}+{_DIM_PIVOT},{lo},{hi})",
        "u": chroma,
        "v": chroma,
    }


def lut_filter(exprs: dict[str, str], *, label: str = "") -> str:
    r"""A ``lutyuv`` filter from either LUT builder's expressions.

    The expressions contain ``,``, which the *filtergraph* parser reads as "next
    filter", so every one goes through :func:`escape_filter_value` — the same
    escaper, and the same reason, as a ``sendcmd`` script path.

    Args:
        exprs: ``{component: expression}``.
        label: Optional ``@label`` so ``sendcmd`` can address this filter.

    Examples:
        >>> lut_filter({"y": "clip(val+0,0,255)"}, label="flash")
        'lutyuv@flash=y=clip(val+0\\,0\\,255)'
    """
    at = f"@{label}" if label else ""
    opts = ":".join(f"{k}={escape_filter_value(v)}" for k, v in exprs.items())
    return f"lutyuv{at}={opts}"


def title_chain(
    title: str,
    size: tuple[int, int],
    style: TitleStyle | None = None,
    *,
    src: str,
    out: str,
) -> str:
    """Filter chain burning ``title`` into the bottom of stream ``src``.

    Raises:
        FfmpegError: This ffmpeg has no ``drawtext``, or no font was found.
    """
    style = style or TitleStyle()
    require_filter("drawtext", needed_for="burning in a title")
    font = style.font or default_font()
    if not font:
        raise FfmpegError(
            "No font found for the burnt-in title. Install one (Debian/Ubuntu: "
            "'sudo apt-get install fonts-dejavu-core'), or pass "
            "TitleStyle(font='/path/to/font.ttf'), or render without a title."
        )
    _, height = size
    fontsize = max(12, int(round(height * style.size_fraction)))
    margin = int(round(height * style.margin_fraction))
    opts = [
        f"fontfile={escape_filter_value(font)}",
        f"text={escape_filter_value(title)}",
        f"fontsize={fontsize}",
        f"fontcolor={style.color}",
        "x=(w-text_w)/2",
        f"y=h-text_h-{margin}",
    ]
    if style.box:
        opts += ["box=1", f"boxcolor={style.box_color}", "boxborderw=20"]
    return f"[{src}]drawtext={':'.join(opts)}[{out}]"


def background_chain(
    size: tuple[int, int], layout: CoverLayout, *, src: str, out: str
) -> str:
    """Filter chain turning cover stream ``src`` into a full-frame background.

    The darken/desaturate step is ``lutyuv``, not ``eq``: ``eq`` is GPL-only
    (muvid#69). Its luma half is no longer ``eq``'s arithmetic either — a dim has
    to scale, not subtract, or the plate's shadows are deleted rather than
    darkened. See :func:`dim_saturation_lut`.
    """
    width, height = size
    if layout.background == "color":
        return (
            f"[{src}]scale={width}:{height},"
            f"drawbox=x=0:y=0:w={width}:h={height}:"
            f"color={layout.background_color}:t=fill[{out}]"
        )
    lut = lut_filter(dim_saturation_lut(dim=layout.dim, saturation=layout.saturation))
    return (
        f"[{src}]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma={layout.blur_sigma},"
        f"{lut}[{out}]"
    )


def cover_chain(
    size: tuple[int, int], layout: CoverLayout, *, src: str, out: str
) -> str:
    """Filter chain scaling cover stream ``src`` to the centred sharp cover.

    When ``layout.cover_alpha < 1`` the scaled cover is made semi-transparent
    (``colorchannelmixer=aa=…``, over an ``rgba`` copy so an opaque source gains
    an alpha channel), so a following :func:`overlay_chain` lets the background
    show through it.
    """
    box_w, box_h = cover_box(size, layout)
    scale = f"[{src}]scale={box_w}:{box_h}:force_original_aspect_ratio=decrease"
    if layout.cover_alpha >= 1:
        return f"{scale}[{out}]"
    return f"{scale},format=rgba,colorchannelmixer=aa={layout.cover_alpha}[{out}]"


def overlay_chain(
    *, background: str, cover: str, out: str, shortest: bool = False
) -> str:
    """Filter chain centring the ``cover`` stream over the ``background`` stream.

    Args:
        background: Label of the background video stream.
        cover: Label of the (already scaled) cover stream.
        out: Label to emit.
        shortest: End the overlay when the shortest input ends — required when a
            finite, audio-driven background is overlaid with an endlessly
            looping still cover, or the render would never terminate.
    """
    opts = ":shortest=1" if shortest else ""
    return f"[{background}][{cover}]overlay=(W-w)/2:(H-h)/2{opts},setsar=1[{out}]"


def compose_chain(
    size: tuple[int, int],
    layout: CoverLayout,
    *,
    src: str,
    out: str,
    title: str | None = None,
    title_style: TitleStyle | None = None,
) -> str:
    """The whole cover-on-canvas filtergraph: background, centred cover, title.

    ``src`` is a single cover-image stream; it is split so the same image feeds
    both the blurred background and the sharp foreground.
    """
    composed = out if title is None else "_composed"
    chains = [
        f"[{src}]split=2[_bgsrc][_fgsrc]",
        background_chain(size, layout, src="_bgsrc", out="_bg"),
        cover_chain(size, layout, src="_fgsrc", out="_fg"),
        overlay_chain(background="_bg", cover="_fg", out=composed),
    ]
    if title is not None:
        chains.append(title_chain(title, size, title_style, src=composed, out=out))
    return ";".join(chains)


def canvas_image(
    image: PathLike,
    *,
    saveas: PathLike | None = None,
    size: tuple[int, int] = DEFAULT_SIZE,
    layout: CoverLayout | None = None,
    title: str | None = None,
    title_style: TitleStyle | None = None,
) -> Path:
    """Render the composed canvas (background + centred cover + title) as a PNG.

    Composing once into an image — rather than re-running a 1080p blur on every
    frame — is what makes a still-image music video cheap to render, and it
    gives the thumbnail and the video's first frame a single source of truth.

    Args:
        image: The cover art.
        saveas: Output PNG path (default: ``<image-stem>.canvas.png``).
        size: Canvas size.
        layout: Placement/treatment of the cover (a default one when omitted).
        title: Burn this title into the canvas (omit for no title).
        title_style: How to draw that title.

    Returns:
        Path to the rendered PNG.
    """
    image = Path(image)
    layout = layout or CoverLayout()
    out = Path(saveas) if saveas else image.with_suffix(".canvas.png")
    run_ffmpeg(
        [
            "-i",
            str(image),
            "-filter_complex",
            compose_chain(
                size, layout, src="0:v", out="v", title=title, title_style=title_style
            ),
            "-map",
            "[v]",
            "-frames:v",
            "1",
            str(out),
        ]
    )
    return out


def thumbnail_image(
    image: PathLike,
    *,
    saveas: PathLike | None = None,
    size: tuple[int, int] = THUMBNAIL_SIZE,
    layout: CoverLayout | None = None,
    title: str | None = None,
    title_style: TitleStyle | None = None,
    max_bytes: int = THUMBNAIL_MAX_BYTES,
) -> Path:
    """Render ``image`` as a 16:9 JPEG thumbnail that YouTube will accept.

    Same composition as the video canvas, so the thumbnail matches what the
    viewer sees when they press play. JPEG quality is stepped down until the
    file fits ``max_bytes`` (YouTube's hard limit).

    Args:
        image: The cover art.
        saveas: Output JPEG path (default: ``<image-stem>.thumb.jpg``).
        size: Thumbnail size (YouTube wants >= 1280x720, 16:9).
        layout: Placement/treatment of the cover.
        title: Burn this title into the thumbnail (omit for none).
        title_style: How to draw that title.
        max_bytes: Hard size ceiling.

    Returns:
        Path to the rendered JPEG.
    """
    image = Path(image)
    layout = layout or CoverLayout()
    out = Path(saveas) if saveas else image.with_suffix(".thumb.jpg")
    chain = compose_chain(
        size, layout, src="0:v", out="v", title=title, title_style=title_style
    )
    for quality in (2, 4, 6, 8, 12, 16, 20):  # ffmpeg mjpeg: 2 = best, 31 = worst
        run_ffmpeg(
            [
                "-i",
                str(image),
                "-filter_complex",
                chain,
                "-map",
                "[v]",
                "-frames:v",
                "1",
                "-q:v",
                str(quality),
                str(out),
            ]
        )
        if out.stat().st_size <= max_bytes:
            return out
    return out  # best effort: the smallest we could make it
