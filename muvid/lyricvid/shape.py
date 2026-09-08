"""Packing words INSIDE a shape — the shape-word-cloud construction.

The ``shape_fill`` archetype has exactly one hard question: given the words of a
song, in the order they are sung, *where* does each one go so that the set of
them draws an apple? :func:`pack_words_into_shape` answers it the way the
word-cloud literature does — Wordle's outward spiral, ShapeWordle's distance
field — and that construction is worth naming, because the two properties this
package cares about fall out of it rather than being bolted on afterwards:

* **It is deterministic.** Same words, same shape, same picture — always. There
  is no ``random`` here and there must never be: a song that renders differently
  on the second run is a song whose video cannot be reviewed. Where the packing
  wants variety it takes it from the word's *index* through a closed form (the
  golden angle), which is the same trick :func:`muvid.lyricvid.scene._scatter`
  uses one layer up.
* **It never overlaps.** A word is placed only where its whole box is inside the
  outline and clear of every word already placed; a word that has nowhere to go
  is **dropped** (``None`` at its slot) rather than laid on top of its
  neighbour. Dropping a word is visible and survivable; overlapping two is the
  failure that makes a lyric video look broken.

The construction is four steps:

1. rasterise the shape to a boolean mask on a canvas-shaped pixel grid
   (:func:`shape_mask`);
2. take a chamfer distance transform of that mask — how *deep* inside the
   outline each pixel is;
3. for each word, seed at the deepest still-free pixel (pulled gently toward the
   shape's centroid, so the first words land in the middle) and walk an
   Archimedean spiral outward, testing an axis-aligned box for "wholly inside
   the mask" (O(1) per candidate, through a summed-area table) and "clear of
   everything already placed";
4. size each word by a small salience weight — earlier and longer words a little
   larger — so the picture has some typographic rhythm without any word
   dominating.

Coordinates out are exactly :class:`muvid.lyricvid.scene.Cue`'s: ``x``/``y`` are
normalised to the canvas (``0..1``, origin top-left, anchor at the text's
centre) and ``size`` is a fraction of canvas **height**, so one packing renders
at 1080p, at 4K and in portrait without recomputation.

Three shape sources, one seam each (:class:`muvid.lyricvid.spec.ShapeRef`
names them): ``'named'`` resolves a built-in outline from the
:data:`NAMED_SHAPES` registry (``circle``, ``heart``, ``star``, ``apple``,
``square`` — add one with :func:`register_shape`), ``'svg_path'`` flattens an
SVG path's ``M/L/H/V/C/S/Q/T/Z`` subset and scanline-fills it (arcs, ``A``, are
refused by name rather than ignored), and ``'mask_image'`` thresholds an image
through Pillow.

numpy is imported *inside* the functions rather than at module scope. This
module hangs off an import-safe path, and ``import muvid`` is asserted by
subprocess to pull no numpy; the packing is the only thing here that needs it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

# SSOT for the width metric: the packer must reserve the same room the scene
# compiler and the renderers assume a word takes, so it reads scene's constant
# rather than growing a second one that can drift from it.
from muvid.lyricvid.scene import _CHAR_W

__all__ = [
    "Placement",
    "pack_words_into_shape",
    "bounding_box",
    "shape_mask",
    "register_shape",
    "list_shapes",
    "resolve_shape",
    "NAMED_SHAPES",
    "SHAPE_KINDS",
]

# --------------------------------------------------------------------------
# tunables. Every one of them is named because a magic number in a packer is a
# number nobody can retune later.
# --------------------------------------------------------------------------

#: Default canvas aspect (width / height) when the caller does not say.
DEFAULT_ASPECT = 16 / 9

#: Rows in the rasterised mask. Columns follow from the aspect. 192 rows put a
#: 0.06-height word at ~12 px, which is enough resolution to pack against and
#: cheap enough that the distance transform stays well under a tenth of a second.
GRID_HEIGHT = 192

#: Fraction of canvas height kept clear all round the shape, so a word packed
#: hard against the outline still has air between it and the frame edge.
SHAPE_MARGIN = 0.04

#: Layout box per word, as multiples of the text's cap height and approximate
#: advance width. The extra is leading and side bearings: the packer reserves the
#: box, the renderer draws the glyphs inside it.
BOX_HEIGHT = 1.28
BOX_WIDTH = 1.06

#: Pixels of clearance forced between two reserved boxes. It buys legibility,
#: and it also makes the analytic box of :func:`bounding_box` provably
#: non-overlapping despite the mask grid's rounding.
GAP_PX = 1

#: Salience multiplies the base size. Kept deliberately narrow — a word cloud
#: whose sizes shout stops reading as one picture.
SALIENCE_MIN = 0.88
SALIENCE_MAX = 1.22
#: How salience splits between "sung earlier" and "is a longer word".
SALIENCE_ORDER_WEIGHT = 0.6

#: When a word does not fit, try it this many times more, this much smaller each
#: time, before dropping it.
SHRINK_STEPS = 2
SHRINK_FACTOR = 0.82

#: Spiral geometry, as fractions of the word box's smaller side: how far the
#: spiral grows per radian, and how far apart consecutive candidates sit.
SPIRAL_PITCH_FRAC = 0.55
SPIRAL_STEP_FRAC = 0.35
#: Hard cap on candidates per word, so a pathological shape cannot spin forever.
MAX_SPIRAL_STEPS = 4000

#: Weight of the centroid pull in the seed score, in units of "pixels of depth
#: per canvas height of distance from the centroid". Small on purpose: the
#: distance field decides where a word goes, the centroid only breaks near-ties
#: (which is what puts the first word in the middle of the shape).
CENTROID_PULL = 0.35

#: Deterministic per-word phase for the spiral, so consecutive words do not all
#: set off along the same ray. Closed form in the word's index — never `random`.
GOLDEN_ANGLE = math.pi * (3 - math.sqrt(5))

#: Points sampled per Bezier segment when flattening an SVG path.
CURVE_SAMPLES = 12


@dataclass(frozen=True, slots=True, kw_only=True)
class Placement:
    """Where one word goes, in :class:`muvid.lyricvid.scene.Cue`'s coordinates.

    Keyword-only and frozen, like the :class:`~muvid.lyricvid.scene.Cue` it
    becomes: four of its five fields are geometry, and geometry read off a
    positional tuple is geometry nobody can check at the call site.

    :param text: the word, exactly as it should be drawn.
    :param x, y: centre of the text, normalised to the canvas (0..1, top-left).
    :param size: cap height as a fraction of canvas height.
    :param rotated: the word is set at 90 degrees (bottom-up). Only ever True
        when the caller asked for ``allow_rotation``; ``Cue`` cannot express a
        rotation, so ``shape_fill`` does not ask for one.
    """

    text: str
    x: float
    y: float
    size: float
    rotated: bool = False


# --------------------------------------------------------------------------
# named shapes — a registry, not a conditional
# --------------------------------------------------------------------------

#: A shape function maps two float arrays (the canvas, expressed in the shape
#: box's own ``0..1`` coordinates, ``v`` growing downward) to a boolean array.
#: It may return True outside the unit box; :func:`shape_mask` clips it there.
ShapeFn = Callable[[Any, Any], Any]

NAMED_SHAPES: dict[str, ShapeFn] = {}


def register_shape(name: str) -> Callable[[ShapeFn], ShapeFn]:
    """Register a named outline, muvid's house registry idiom.

    The function receives ``(u, v)`` arrays in the shape box's coordinates and
    returns a boolean array. Implicit functions and polygons both fit; see
    :func:`polygon_shape` for the latter.

    >>> @register_shape('doctest-blob')
    ... def _blob(u, v): return (u - 0.5) ** 2 + (v - 0.5) ** 2 <= 0.1
    >>> 'doctest-blob' in list_shapes()
    True
    >>> del NAMED_SHAPES['doctest-blob']
    """

    def deco(fn: ShapeFn) -> ShapeFn:
        NAMED_SHAPES[name] = fn
        return fn

    return deco


def list_shapes() -> list[str]:
    """Every registered outline name, sorted.

    >>> list_shapes()
    ['apple', 'circle', 'heart', 'square', 'star']
    """
    return sorted(NAMED_SHAPES)


def resolve_shape(name: str) -> ShapeFn:
    """The outline function for ``name``, or a ValueError naming the options.

    >>> resolve_shape('banana')
    Traceback (most recent call last):
        ...
    ValueError: unknown named shape 'banana'; known: apple, circle, heart, square, star
    """
    try:
        return NAMED_SHAPES[name]
    except KeyError:
        raise ValueError(
            f"unknown named shape {name!r}; known: {', '.join(list_shapes())}"
        ) from None


def polygon_shape(vertices: Sequence[tuple[float, float]]) -> ShapeFn:
    """Turn a closed polygon in unit-box coordinates into a :data:`ShapeFn`.

    Filled by the even-odd rule, so a vertex list that self-crosses (a
    five-pointed star drawn in one stroke) fills the way it looks.
    """
    poly = [(float(x), float(y)) for x, y in vertices]

    def fn(u: Any, v: Any) -> Any:
        return _even_odd(u, v, [poly])

    return fn


def _even_odd(u: Any, v: Any, polygons: Iterable[Sequence[tuple[float, float]]]) -> Any:
    """Even-odd point-in-polygon over a whole grid at once."""
    import numpy as np

    inside = np.zeros(u.shape, dtype=bool)
    for poly in polygons:
        n = len(poly)
        if n < 3:
            continue
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            if y0 == y1:
                continue
            crosses = (y0 > v) != (y1 > v)
            cut = x0 + (v - y0) * (x1 - x0) / (y1 - y0)
            inside ^= crosses & (u < cut)
    return inside


@register_shape("square")
def _square(u: Any, v: Any) -> Any:
    """The shape box itself — a square, centred, as tall as the canvas allows."""
    import numpy as np

    return np.ones(u.shape, dtype=bool)


@register_shape("circle")
def _circle(u: Any, v: Any) -> Any:
    """The inscribed circle."""
    return (u - 0.5) ** 2 + (v - 0.5) ** 2 <= 0.25


@register_shape("heart")
def _heart(u: Any, v: Any) -> Any:
    """The classic ``(x^2 + y^2 - 1)^3 <= x^2 y^3`` heart, fitted to the box.

    The curve spans x in [-1.138, 1.138] and y in [-1.0, 1.236] (measured, not
    guessed — an earlier fit clipped the cleft between the lobes clean off), and
    both axes take the same scale so the heart is not stretched to fill a square.
    """
    x = (u - 0.5) * 2.28
    y = 1.258 - v * 2.28
    return (x * x + y * y - 1.0) ** 3 <= x * x * y * y * y


@register_shape("star")
def _star(u: Any, v: Any) -> Any:
    """A five-pointed star, one point up."""
    return _STAR_FILL(u, v)


def _star_vertices(
    *, points: int = 5, inner: float = 0.42
) -> list[tuple[float, float]]:
    """Alternating outer/inner vertices of a star, in unit-box coordinates."""
    verts: list[tuple[float, float]] = []
    for i in range(2 * points):
        r = 0.5 if i % 2 == 0 else 0.5 * inner
        a = -math.pi / 2 + i * math.pi / points
        verts.append((0.5 + r * math.cos(a), 0.5 + r * math.sin(a)))
    return verts


_STAR_FILL = polygon_shape(_star_vertices())


@register_shape("apple")
def _apple(u: Any, v: Any) -> Any:
    """An apple: fat body, dimple at the top and the bottom, stalk and leaf.

    The motivating example, and built the way an implicit shape wants to be
    built — one body minus two dimples, union the two decorations — so it stays
    a dozen readable lines rather than a hand-digitised outline.
    """
    x = (u - 0.5) * 2.0
    y = (0.5 - v) * 2.0  # y up, so the words below read like the fruit
    body = (x / 0.92) ** 2 + ((y + 0.06) / 0.86) ** 2 <= 1.0
    top_dimple = x * x + (y - 0.86) ** 2 <= 0.30**2
    base_dimple = x * x + (y + 1.05) ** 2 <= 0.22**2
    stalk = (abs(x) <= 0.045) & (y >= 0.55) & (y <= 0.98)
    leaf = _rotated_ellipse(x, y, cx=0.30, cy=0.88, a=0.28, b=0.11, angle=-0.6)
    return (body & ~top_dimple & ~base_dimple) | stalk | leaf


def _rotated_ellipse(
    x: Any, y: Any, *, cx: float, cy: float, a: float, b: float, angle: float
) -> Any:
    """An ellipse rotated by ``angle`` radians, as a boolean array."""
    ca, sa = math.cos(angle), math.sin(angle)
    dx, dy = x - cx, y - cy
    xr = dx * ca + dy * sa
    yr = -dx * sa + dy * ca
    return (xr / a) ** 2 + (yr / b) ** 2 <= 1.0


# --------------------------------------------------------------------------
# the three shape kinds
# --------------------------------------------------------------------------


def _named_ink(u: Any, v: Any, value: str) -> Any:
    """``shape_kind='named'`` — a built-in outline from :data:`NAMED_SHAPES`."""
    return resolve_shape(value)(u, v)


def _svg_ink(u: Any, v: Any, value: str, *, curve_samples: int = CURVE_SAMPLES) -> Any:
    """``shape_kind='svg_path'`` — flatten the path, then scanline-fill it."""
    polys = parse_svg_path(value, curve_samples=curve_samples)
    if not polys:
        raise ValueError(f"svg_path {value[:40]!r} closed no region to fill")
    return _even_odd(u, v, polys)


def _image_ink(
    u: Any, v: Any, value: str, *, threshold: int = 128, invert: bool = False
) -> Any:
    """``shape_kind='mask_image'`` — threshold an image file to a mask.

    Alpha wins where an image has it (an ``RGBA`` cut-out is the usual way a
    designer hands one over); otherwise dark ink on light paper is the shape.
    ``invert=True`` swaps that. The image keeps its own aspect inside the box.
    """
    try:
        from PIL import Image
    except ImportError as e:  # actionable, not a bare ImportError from deep inside
        raise RuntimeError(
            "shape_kind='mask_image' needs Pillow, which muvid does not require: "
            "install it with `pip install Pillow`, or use shape_kind='named' "
            f"(one of {', '.join(list_shapes())}) or shape_kind='svg_path'."
        ) from e
    import numpy as np

    with Image.open(value) as im:
        im.load()
        if "A" in im.getbands():
            ink = np.asarray(im.getchannel("A"), dtype=np.uint8) >= threshold
        else:
            ink = np.asarray(im.convert("L"), dtype=np.uint8) < threshold
    if invert:
        ink = ~ink
    h, w = ink.shape
    side = float(max(w, h))
    ix = (u - 0.5) * side + w / 2.0
    iy = (v - 0.5) * side + h / 2.0
    on_image = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    rows = np.clip(iy, 0, h - 1).astype(np.int64)
    cols = np.clip(ix, 0, w - 1).astype(np.int64)
    return ink[rows, cols] & on_image


#: The three sources a shape can come from. Closed, because
#: :class:`muvid.lyricvid.spec.ShapeRef` is: the extension seam for a new
#: *outline* is :func:`register_shape`, not a fourth kind.
SHAPE_KINDS: dict[str, Callable[..., Any]] = {
    "named": _named_ink,
    "svg_path": _svg_ink,
    "mask_image": _image_ink,
}


# --------------------------------------------------------------------------
# SVG paths
# --------------------------------------------------------------------------

_SVG_TOKEN = re.compile(
    r"([MmLlHhVvCcSsQqTtZzAa])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)"
)

#: What :func:`parse_svg_path` understands. Arcs are the deliberate omission.
SVG_COMMANDS = "MLHVCSQTZ"


def parse_svg_path(
    d: str, *, curve_samples: int = CURVE_SAMPLES
) -> list[list[tuple[float, float]]]:
    """Flatten an SVG path into polygons, normalised into the unit box.

    Understands ``M L H V C S Q T Z`` (absolute and relative), flattening every
    Bezier into ``curve_samples`` segments. **Arcs (``A``/``a``) are refused**
    with an error naming them rather than skipped — a shape that silently loses
    half its outline is worse than one that will not build. SVG's y axis grows
    downward, which is also this module's, so nothing is flipped.

    >>> parse_svg_path('M 0 0 L 10 0 L 10 10 L 0 10 Z')
    [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]]
    >>> parse_svg_path('M 0 0 A 1 1 0 0 1 1 1')
    Traceback (most recent call last):
        ...
    ValueError: svg_path: arc commands ('A'/'a') are not supported; convert them to cubic curves ('C'). Supported: MLHVCSQTZ
    """
    tokens: list[tuple[str, Any]] = []
    pos = 0
    for m in _SVG_TOKEN.finditer(d or ""):
        if m.start() != pos and (d[pos : m.start()].strip(" ,\t\r\n")):
            raise ValueError(f"svg_path: cannot parse {d[pos : m.start()]!r}")
        pos = m.end()
        if m.group(1):
            if m.group(1) in "Aa":
                raise ValueError(
                    "svg_path: arc commands ('A'/'a') are not supported; convert "
                    f"them to cubic curves ('C'). Supported: {SVG_COMMANDS}"
                )
            tokens.append(("cmd", m.group(1)))
        else:
            tokens.append(("num", float(m.group(2))))
    if (d or "")[pos:].strip(" ,\t\r\n"):
        raise ValueError(f"svg_path: cannot parse {d[pos:]!r}")

    polys: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x = y = sx = sy = 0.0
    ctrl: tuple[float, float] | None = None
    quad: tuple[float, float] | None = None
    i, cmd = 0, ""

    def close() -> None:
        nonlocal cur
        if len(cur) >= 3:
            polys.append(cur)
        cur = []

    def take(n: int) -> list[float]:
        nonlocal i
        if i + n > len(tokens) or any(t[0] != "num" for t in tokens[i : i + n]):
            raise ValueError(f"svg_path: command {cmd!r} wants {n} numbers")
        vals = [float(t[1]) for t in tokens[i : i + n]]
        i += n
        return vals

    while i < len(tokens):
        kind, tok = tokens[i]
        if kind == "cmd":
            cmd = tok
            i += 1
            if cmd in "Zz":
                close()
                x, y = sx, sy
                ctrl = quad = None
                continue
        elif not cmd:
            raise ValueError("svg_path: numbers before any command")
        elif cmd in "Mm":  # implicit repeats of an M are line-tos
            cmd = "L" if cmd == "M" else "l"
        rel = cmd.islower()
        up = cmd.upper()
        if up == "M":
            (px, py) = take(2)
            close()
            x, y = (x + px, y + py) if rel else (px, py)
            sx, sy = x, y
            cur = [(x, y)]
            ctrl = quad = None
        elif up == "L":
            (px, py) = take(2)
            x, y = (x + px, y + py) if rel else (px, py)
            cur.append((x, y))
            ctrl = quad = None
        elif up == "H":
            (px,) = take(1)
            x = x + px if rel else px
            cur.append((x, y))
            ctrl = quad = None
        elif up == "V":
            (py,) = take(1)
            y = y + py if rel else py
            cur.append((x, y))
            ctrl = quad = None
        elif up in "CS":
            if up == "C":
                x1, y1, x2, y2, px, py = take(6)
                if rel:
                    x1, y1, x2, y2, px, py = (
                        x1 + x,
                        y1 + y,
                        x2 + x,
                        y2 + y,
                        px + x,
                        py + y,
                    )
            else:
                x2, y2, px, py = take(4)
                if rel:
                    x2, y2, px, py = x2 + x, y2 + y, px + x, py + y
                x1, y1 = (2 * x - ctrl[0], 2 * y - ctrl[1]) if ctrl else (x, y)
            cur.extend(_cubic(x, y, x1, y1, x2, y2, px, py, samples=curve_samples))
            ctrl, quad = (x2, y2), None
            x, y = px, py
        elif up in "QT":
            if up == "Q":
                x1, y1, px, py = take(4)
                if rel:
                    x1, y1, px, py = x1 + x, y1 + y, px + x, py + y
            else:
                px, py = take(2)
                if rel:
                    px, py = px + x, py + y
                x1, y1 = (2 * x - quad[0], 2 * y - quad[1]) if quad else (x, y)
            cur.extend(_quadratic(x, y, x1, y1, px, py, samples=curve_samples))
            quad, ctrl = (x1, y1), None
            x, y = px, py
        else:  # pragma: no cover - the token regex admits nothing else
            raise ValueError(f"svg_path: unsupported command {cmd!r}")
    close()
    return _normalise_polygons(polys)


def _cubic(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    *,
    samples: int,
) -> list[tuple[float, float]]:
    """A cubic Bezier, flattened (the start point is left to the caller)."""
    out = []
    for k in range(1, samples + 1):
        t = k / samples
        s = 1.0 - t
        out.append(
            (
                s**3 * x0 + 3 * s * s * t * x1 + 3 * s * t * t * x2 + t**3 * x3,
                s**3 * y0 + 3 * s * s * t * y1 + 3 * s * t * t * y2 + t**3 * y3,
            )
        )
    return out


def _quadratic(
    x0: float, y0: float, x1: float, y1: float, x2: float, y2: float, *, samples: int
) -> list[tuple[float, float]]:
    """A quadratic Bezier, flattened."""
    out = []
    for k in range(1, samples + 1):
        t = k / samples
        s = 1.0 - t
        out.append(
            (
                s * s * x0 + 2 * s * t * x1 + t * t * x2,
                s * s * y0 + 2 * s * t * y1 + t * t * y2,
            )
        )
    return out


def _normalise_polygons(
    polys: list[list[tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    """Scale/centre polygons into the unit box, keeping their aspect ratio."""
    pts = [p for poly in polys for p in poly]
    if not pts:
        return []
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    side = max(w, h) or 1.0
    ox = (1.0 - w / side) / 2.0 - min(xs) / side
    oy = (1.0 - h / side) / 2.0 - min(ys) / side
    return [[(x / side + ox, y / side + oy) for x, y in poly] for poly in polys]


# --------------------------------------------------------------------------
# rasterising and measuring the shape
# --------------------------------------------------------------------------


def shape_mask(
    kind: str = "named",
    value: str = "circle",
    *,
    aspect: float = DEFAULT_ASPECT,
    grid_height: int = GRID_HEIGHT,
    margin: float = SHAPE_MARGIN,
    **options: Any,
) -> Any:
    """Rasterise a shape to a boolean mask over the whole canvas.

    The result has shape ``(grid_height, round(grid_height * aspect))``, so a
    normalised canvas point ``(x, y)`` is the pixel ``[int(y * H), int(x * W)]``.
    The outline is drawn into the largest centred SQUARE the canvas allows (less
    ``margin``), which is what keeps a circle round on a 16:9 frame.

    >>> m = shape_mask('named', 'circle', aspect=1.0, grid_height=32)
    >>> m.shape, bool(m[16, 16]), bool(m[0, 0])
    ((32, 32), True, False)
    """
    import numpy as np

    try:
        builder = SHAPE_KINDS[kind]
    except KeyError:
        raise ValueError(
            f"unknown shape kind {kind!r}; known: {', '.join(sorted(SHAPE_KINDS))}"
        ) from None
    height = int(grid_height)
    width = max(1, int(round(height * float(aspect))))
    side = (1.0 - 2.0 * float(margin)) * height
    if side <= 0:
        raise ValueError(f"margin {margin!r} leaves no room for a shape")
    x0 = (width - side) / 2.0
    y0 = (height - side) / 2.0
    u = ((np.arange(width) + 0.5) - x0) / side
    v = ((np.arange(height) + 0.5) - y0) / side
    uu, vv = np.meshgrid(u, v)
    in_box = (uu >= 0.0) & (uu <= 1.0) & (vv >= 0.0) & (vv <= 1.0)
    return np.asarray(builder(uu, vv, value, **options), dtype=bool) & in_box


def _distance_transform(mask: Any, *, max_iterations: int) -> Any:
    """Chamfer distance to the nearest background pixel, in pixels.

    Written as a fixed-point iteration of the 3x3 chamfer stencil (1 orthogonal,
    sqrt(2) diagonal) rather than the two raster scans, because the iteration is
    a handful of whole-array minima — vectorised, and scipy is not a muvid
    dependency. It converges in as many passes as the deepest point is deep and
    stops the moment nothing moves.
    """
    import numpy as np

    ortho, diag = 1.0, math.sqrt(2.0)
    far = float(mask.shape[0] + mask.shape[1])
    d = np.where(mask, far, 0.0)
    for _ in range(max_iterations):
        p = np.pad(d, 1, mode="constant", constant_values=0.0)
        nxt = np.minimum.reduce(
            [
                d,
                p[:-2, 1:-1] + ortho,
                p[2:, 1:-1] + ortho,
                p[1:-1, :-2] + ortho,
                p[1:-1, 2:] + ortho,
                p[:-2, :-2] + diag,
                p[:-2, 2:] + diag,
                p[2:, :-2] + diag,
                p[2:, 2:] + diag,
            ]
        )
        if np.array_equal(nxt, d):
            break
        d = nxt
    return d


def _summed_area(mask: Any) -> Any:
    """Summed-area table, so "is this box wholly inside?" is four lookups."""
    import numpy as np

    H, W = mask.shape
    table = np.zeros((H + 1, W + 1), dtype=np.int64)
    table[1:, 1:] = np.cumsum(np.cumsum(mask.astype(np.int64), axis=0), axis=1)
    return table


@dataclass(frozen=True, slots=True)
class _Field:
    """Everything the placement loop reads about the shape, computed once.

    ``occupied`` is the one mutable thing here: each placement writes its box
    into it, which is what makes the next word's search see it.
    """

    mask: Any
    distance: Any
    summed: Any
    occupied: Any
    seed_penalty: Any


def _build_field(mask: Any) -> _Field:
    import numpy as np

    H, W = mask.shape
    rows, cols = np.nonzero(mask)
    cy, cx = float(rows.mean()), float(cols.mean())
    ry = (np.arange(H)[:, None] - cy) / H
    rx = (np.arange(W)[None, :] - cx) / H
    return _Field(
        mask=mask,
        distance=_distance_transform(mask, max_iterations=max(H, W)),
        summed=_summed_area(mask),
        occupied=np.zeros_like(mask),
        seed_penalty=CENTROID_PULL * H * np.sqrt(ry * ry + rx * rx),
    )


# --------------------------------------------------------------------------
# the packing
# --------------------------------------------------------------------------


def _text_width(text: str, size: float) -> float:
    """Approximate rendered width, in the same units as ``size`` (scene.py's metric)."""
    return len(text) * size * _CHAR_W


def bounding_box(
    placement: Placement, *, aspect: float = DEFAULT_ASPECT
) -> tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)`` of a placement's layout box, normalised to the canvas.

    The LAYOUT box — the room the packer reserved, glyphs plus leading and side
    bearings — which is what a caller wants for a hit test, a debug overlay, or
    for asserting that two placements do not collide.

    >>> p = Placement(text='apple', x=0.5, y=0.5, size=0.1)
    >>> [round(c, 3) for c in bounding_box(p, aspect=16 / 9)]
    [0.408, 0.436, 0.592, 0.564]
    """
    height = placement.size * BOX_HEIGHT
    width = _text_width(placement.text, placement.size) * BOX_WIDTH
    if placement.rotated:
        width, height = height, width
    half_x = width / 2.0 / float(aspect)
    half_y = height / 2.0
    return (
        placement.x - half_x,
        placement.y - half_y,
        placement.x + half_x,
        placement.y + half_y,
    )


def _salience_sizes(texts: Sequence[str], *, base_size: float) -> list[float]:
    """One size per word: earlier and longer words a little larger."""
    n = len(texts)
    lengths = [len(t.strip()) for t in texts]
    lo, hi = min(lengths), max(lengths)
    sizes = []
    for i, length in enumerate(lengths):
        order = 1.0 - i / (n - 1) if n > 1 else 1.0
        span = (length - lo) / (hi - lo) if hi > lo else 0.5
        weight = SALIENCE_ORDER_WEIGHT * order + (1.0 - SALIENCE_ORDER_WEIGHT) * span
        sizes.append(
            base_size * (SALIENCE_MIN + (SALIENCE_MAX - SALIENCE_MIN) * weight)
        )
    return sizes


def _seed_pixel(field: _Field) -> tuple[int, int] | None:
    """The free pixel to start a spiral from: deepest, centre-most on a tie."""
    import numpy as np

    free = field.mask & ~field.occupied
    if not free.any():
        return None
    score = np.where(free, field.distance - field.seed_penalty, -np.inf)
    flat = int(np.argmax(score))
    return divmod(flat, field.mask.shape[1])


def _spiral_hit(
    field: _Field, *, cx: float, cy: float, half_w: float, half_h: float, index: int
) -> tuple[float, float, int, int, int, int] | None:
    """Walk the spiral from ``(cx, cy)``; return the first box that fits.

    Two tests, cheapest first: the summed-area table says whether the whole box
    is inside the outline (four lookups, vectorised over every candidate at
    once), and only the survivors of that are checked against what is already
    placed.
    """
    import numpy as np

    H, W = field.mask.shape
    span = 2.0 * min(half_w, half_h)
    pitch = max(1.0, SPIRAL_PITCH_FRAC * span)
    step = max(1.0, SPIRAL_STEP_FRAC * span)
    reach = math.hypot(H, W)
    n = min(MAX_SPIRAL_STEPS, int(reach * reach / (2.0 * step * pitch)) + 2)
    # Arc-length parametrisation of r = pitch * theta, so candidates are evenly
    # spaced along the spiral instead of bunching at the centre.
    theta = np.sqrt(2.0 * step * np.arange(n) / pitch)
    angle = theta + GOLDEN_ANGLE * index
    xs = cx + pitch * theta * np.cos(angle)
    ys = cy + pitch * theta * np.sin(angle)
    x0 = np.floor(xs - half_w).astype(np.int64)
    x1 = np.ceil(xs + half_w).astype(np.int64)
    y0 = np.floor(ys - half_h).astype(np.int64)
    y1 = np.ceil(ys + half_h).astype(np.int64)
    on_grid = (x0 >= 0) & (y0 >= 0) & (x1 <= W) & (y1 <= H) & (x1 > x0) & (y1 > y0)
    idx = np.flatnonzero(on_grid)
    if idx.size == 0:
        return None
    s = field.summed
    covered = (
        s[y1[idx], x1[idx]]
        - s[y0[idx], x1[idx]]
        - s[y1[idx], x0[idx]]
        + s[y0[idx], x0[idx]]
    )
    area = (y1[idx] - y0[idx]) * (x1[idx] - x0[idx])
    for j in idx[covered == area]:
        gx0, gy0 = max(0, int(x0[j]) - GAP_PX), max(0, int(y0[j]) - GAP_PX)
        gx1, gy1 = min(W, int(x1[j]) + GAP_PX), min(H, int(y1[j]) + GAP_PX)
        if not field.occupied[gy0:gy1, gx0:gx1].any():
            return (
                float(xs[j]),
                float(ys[j]),
                int(x0[j]),
                int(y0[j]),
                int(x1[j]),
                int(y1[j]),
            )
    return None


def _place_word(
    field: _Field, text: str, *, index: int, size: float, allow_rotation: bool
) -> Placement | None:
    """Place one word at one size, upright first, or return None."""
    H, W = field.mask.shape
    seed = _seed_pixel(field)
    if seed is None:
        return None
    cy, cx = seed
    box_w = _text_width(text, size) * H * BOX_WIDTH
    box_h = size * H * BOX_HEIGHT
    for rotated in (False, True) if allow_rotation else (False,):
        w, h = (box_h, box_w) if rotated else (box_w, box_h)
        hit = _spiral_hit(
            field, cx=cx + 0.5, cy=cy + 0.5, half_w=w / 2.0, half_h=h / 2.0, index=index
        )
        if hit is None:
            continue
        px, py, x0, y0, x1, y1 = hit
        field.occupied[y0:y1, x0:x1] = True
        return Placement(text=text, x=px / W, y=py / H, size=size, rotated=rotated)
    return None


def pack_words_into_shape(
    texts: Sequence[str],
    *,
    shape_kind: str = "named",
    shape_value: str = "circle",
    aspect: float = DEFAULT_ASPECT,
    base_size: float = 0.06,
    grid_height: int = GRID_HEIGHT,
    margin: float = SHAPE_MARGIN,
    allow_rotation: bool = False,
    shrink_steps: int = SHRINK_STEPS,
    mask_options: Mapping[str, Any] | None = None,
) -> list[Placement | None]:
    """Pack ``texts`` inside a shape, in the order given.

    :param texts: the words, in the order they are sung. Order matters twice:
        it decides who gets the roomy middle of the shape, and it feeds the
        salience weight.
    :param shape_kind: ``'named'``, ``'svg_path'`` or ``'mask_image'`` — see
        :data:`SHAPE_KINDS` and :class:`muvid.lyricvid.spec.ShapeRef`.
    :param shape_value: the outline's name, path data, or image path.
    :param aspect: canvas width / height.
    :param base_size: cap height as a fraction of canvas height, before salience.
    :param allow_rotation: also try each word turned 90 degrees.
        ``muvid.lyricvid.scene.Cue`` cannot express a rotation, so ``shape_fill``
        leaves this off; a renderer that can should turn it on.
    :param shrink_steps: how many progressively smaller retries a word gets
        before it is dropped.
    :param mask_options: forwarded to the kind's builder (``threshold`` and
        ``invert`` for ``'mask_image'``, ``curve_samples`` for ``'svg_path'``).
    :returns: one entry per input, in the same order. ``None`` is "did not fit"
        — dropping a word beats overlapping two.

    >>> words = 'apple yum juicy crunchy red yellow green delicious'.split()
    >>> placed = pack_words_into_shape(words, shape_value='apple')
    >>> len(placed) == len(words)
    True

    Same input, same picture — every time, forever:

    >>> placed == pack_words_into_shape(words, shape_value='apple')
    True

    Every placed word is wholly inside the outline:

    >>> mask = shape_mask('named', 'apple')
    >>> H, W = mask.shape
    >>> def corners_in_mask(p):
    ...     x0, y0, x1, y1 = bounding_box(p)
    ...     xs = (int(x0 * W), int(x1 * W) - 1)
    ...     ys = (int(y0 * H), int(y1 * H) - 1)
    ...     return all(mask[y][x] for y in ys for x in xs)
    >>> all(corners_in_mask(p) for p in placed if p is not None)
    True

    ...and no two of them collide:

    >>> def collide(a, b):
    ...     ax0, ay0, ax1, ay1 = bounding_box(a)
    ...     bx0, by0, bx1, by1 = bounding_box(b)
    ...     return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1
    >>> ok = [p for p in placed if p is not None]
    >>> any(collide(a, b) for i, a in enumerate(ok) for b in ok[i + 1:])
    False

    An SVG outline works the same way (``M/L/H/V/C/S/Q/T/Z``; arcs are refused):

    >>> square = pack_words_into_shape(
    ...     ['one', 'two'], shape_kind='svg_path',
    ...     shape_value='M 0 0 L 10 0 L 10 10 L 0 10 Z')
    >>> [p.text for p in square if p is not None]
    ['one', 'two']
    """
    words = list(texts)
    if not words:
        return []
    mask = shape_mask(
        shape_kind,
        shape_value,
        aspect=aspect,
        grid_height=grid_height,
        margin=margin,
        **dict(mask_options or {}),
    )
    if not mask.any():
        return [None] * len(words)
    field = _build_field(mask)
    sizes = _salience_sizes(words, base_size=base_size)
    placed: list[Placement | None] = []
    for i, text in enumerate(words):
        if not text.strip():
            placed.append(None)
            continue
        spot = None
        for attempt in range(max(1, int(shrink_steps) + 1)):
            spot = _place_word(
                field,
                text,
                index=i,
                size=sizes[i] * SHRINK_FACTOR**attempt,
                allow_rotation=allow_rotation,
            )
            if spot is not None:
                break
        placed.append(spot)
    return placed
