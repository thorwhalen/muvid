# muvid.lyricvid.shape

Packing words INSIDE a shape — the shape-word-cloud construction.

The `shape_fill` archetype has exactly one hard question: given the words of a
song, in the order they are sung, *where* does each one go so that the set of
them draws an apple? [`pack_words_into_shape()`](#muvid.lyricvid.shape.pack_words_into_shape) answers it the way the
word-cloud literature does — Wordle’s outward spiral, ShapeWordle’s distance
field — and that construction is worth naming, because the two properties this
package cares about fall out of it rather than being bolted on afterwards:

* **It is deterministic.** Same words, same shape, same picture — always. There
  is no `random` here and there must never be: a song that renders differently
  on the second run is a song whose video cannot be reviewed. Where the packing
  wants variety it takes it from the word’s *index* through a closed form (the
  golden angle), which is the same trick `muvid.lyricvid.scene._scatter()`
  uses one layer up.
* **It never overlaps.** A word is placed only where its whole box is inside the
  outline and clear of every word already placed; a word that has nowhere to go
  is **dropped** (`None` at its slot) rather than laid on top of its
  neighbour. Dropping a word is visible and survivable; overlapping two is the
  failure that makes a lyric video look broken.

The construction is four steps:

1. rasterise the shape to a boolean mask on a canvas-shaped pixel grid
   ([`shape_mask()`](#muvid.lyricvid.shape.shape_mask));
2. take a chamfer distance transform of that mask — how *deep* inside the
   outline each pixel is;
3. for each word, seed at the deepest still-free pixel (pulled gently toward the
   shape’s centroid, so the first words land in the middle) and walk an
   Archimedean spiral outward, testing an axis-aligned box for “wholly inside
   the mask” (O(1) per candidate, through a summed-area table) and “clear of
   everything already placed”;
4. size each word by a small salience weight — earlier and longer words a little
   larger — so the picture has some typographic rhythm without any word
   dominating.

Coordinates out are exactly [`muvid.lyricvid.scene.Cue`](muvid.lyricvid.scene.html.md#muvid.lyricvid.scene.Cue)’s: `x`/`y` are
normalised to the canvas (`0..1`, origin top-left, anchor at the text’s
centre) and `size` is a fraction of canvas **height**, so one packing renders
at 1080p, at 4K and in portrait without recomputation.

Three shape sources, one seam each ([`muvid.lyricvid.spec.ShapeRef`](muvid.lyricvid.spec.html.md#muvid.lyricvid.spec.ShapeRef)
names them): `'named'` resolves a built-in outline from the
`NAMED_SHAPES` registry (`circle`, `heart`, `star`, `apple`,
`square` — add one with [`register_shape()`](#muvid.lyricvid.shape.register_shape)), `'svg_path'` flattens an
SVG path’s `M/L/H/V/C/S/Q/T/Z` subset and scanline-fills it (arcs, `A`, are
refused by name rather than ignored), and `'mask_image'` thresholds an image
through Pillow.

numpy is imported *inside* the functions rather than at module scope. This
module hangs off an import-safe path, and `import muvid` is asserted by
subprocess to pull no numpy; the packing is the only thing here that needs it.

### Module Attributes

| [`SHAPE_KINDS`](#muvid.lyricvid.shape.SHAPE_KINDS)   | The three sources a shape can come from.   |
|----------------------------------------------------------------|--------------------------------------------|

### Functions

| [`pack_words_into_shape`](#muvid.lyricvid.shape.pack_words_into_shape)(texts, \*[, ...])   | Pack `texts` inside a shape, in the order given.                          |
|--------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`bounding_box`](#muvid.lyricvid.shape.bounding_box)(placement, \*[, aspect])     | `(x0, y0, x1, y1)` of a placement's layout box, normalised to the canvas. |
| [`shape_mask`](#muvid.lyricvid.shape.shape_mask)([kind, value, aspect, ...])    | Rasterise a shape to a boolean mask over the whole canvas.                |
| [`register_shape`](#muvid.lyricvid.shape.register_shape)(name)                      | Register a named outline, muvid's house registry idiom.                   |
| [`list_shapes`](#muvid.lyricvid.shape.list_shapes)()                             | Every registered outline name, sorted.                                    |
| [`resolve_shape`](#muvid.lyricvid.shape.resolve_shape)(name)                       | The outline function for `name`, or a ValueError naming the options.      |

### Classes

| [`Placement`](#muvid.lyricvid.shape.Placement)(\*, text, x, y, size[, rotated])   | Where one word goes, in [`muvid.lyricvid.scene.Cue`](muvid.lyricvid.scene.html.md#muvid.lyricvid.scene.Cue)'s coordinates.   |
|-----------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|

### *class* muvid.lyricvid.shape.Placement(, text, x, y, size, rotated=False)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Where one word goes, in [`muvid.lyricvid.scene.Cue`](muvid.lyricvid.scene.html.md#muvid.lyricvid.scene.Cue)’s coordinates.

Keyword-only and frozen, like the [`Cue`](muvid.lyricvid.scene.html.md#muvid.lyricvid.scene.Cue) it
becomes: four of its five fields are geometry, and geometry read off a
positional tuple is geometry nobody can check at the call site.

* **Parameters:**
  * **text** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – the word, exactly as it should be drawn.
  * **y** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – centre of the text, normalised to the canvas (0..1, top-left).
  * **size** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – cap height as a fraction of canvas height.
  * **rotated** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – the word is set at 90 degrees (bottom-up). Only ever True
    when the caller asked for `allow_rotation`; `Cue` cannot express a
    rotation, so `shape_fill` does not ask for one.

### muvid.lyricvid.shape.SHAPE_KINDS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Callable](https://docs.python.org/3/library/typing.html#typing.Callable)[[...], [Any](https://docs.python.org/3/library/typing.html#typing.Any)]]* *= {'mask_image': <function \_image_ink>, 'named': <function \_named_ink>, 'svg_path': <function \_svg_ink>}*

The three sources a shape can come from. Closed, because
[`muvid.lyricvid.spec.ShapeRef`](muvid.lyricvid.spec.html.md#muvid.lyricvid.spec.ShapeRef) is: the extension seam for a new
*outline* is [`register_shape()`](#muvid.lyricvid.shape.register_shape), not a fourth kind.

### muvid.lyricvid.shape.bounding_box(placement, , aspect=1.7777777777777777)

`(x0, y0, x1, y1)` of a placement’s layout box, normalised to the canvas.

The LAYOUT box — the room the packer reserved, glyphs plus leading and side
bearings — which is what a caller wants for a hit test, a debug overlay, or
for asserting that two placements do not collide.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float)]

```pycon
>>> p = Placement(text='apple', x=0.5, y=0.5, size=0.1)
>>> [round(c, 3) for c in bounding_box(p, aspect=16 / 9)]
[0.408, 0.436, 0.592, 0.564]
```

### muvid.lyricvid.shape.list_shapes()

Every registered outline name, sorted.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

```pycon
>>> list_shapes()
['apple', 'circle', 'heart', 'square', 'star']
```

### muvid.lyricvid.shape.pack_words_into_shape(texts, , shape_kind='named', shape_value='circle', aspect=1.7777777777777777, base_size=0.06, grid_height=192, margin=0.04, allow_rotation=False, shrink_steps=2, mask_options=None)

Pack `texts` inside a shape, in the order given.

* **Parameters:**
  * **texts** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – the words, in the order they are sung. Order matters twice:
    it decides who gets the roomy middle of the shape, and it feeds the
    salience weight.
  * **shape_kind** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – `'named'`, `'svg_path'` or `'mask_image'` — see
    [`SHAPE_KINDS`](#muvid.lyricvid.shape.SHAPE_KINDS) and [`muvid.lyricvid.spec.ShapeRef`](muvid.lyricvid.spec.html.md#muvid.lyricvid.spec.ShapeRef).
  * **shape_value** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – the outline’s name, path data, or image path.
  * **aspect** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – canvas width / height.
  * **base_size** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – cap height as a fraction of canvas height, before salience.
  * **allow_rotation** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – also try each word turned 90 degrees.
    `muvid.lyricvid.scene.Cue` cannot express a rotation, so `shape_fill`
    leaves this off; a renderer that can should turn it on.
  * **shrink_steps** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – how many progressively smaller retries a word gets
    before it is dropped.
  * **mask_options** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]) – forwarded to the kind’s builder (`threshold` and
    `invert` for `'mask_image'`, `curve_samples` for `'svg_path'`).
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Placement`](#muvid.lyricvid.shape.Placement) | [`None`](https://docs.python.org/3/builtins/constants.html#None)]
* **Returns:**
  one entry per input, in the same order. `None` is “did not fit”
  — dropping a word beats overlapping two.

```pycon
>>> words = 'apple yum juicy crunchy red yellow green delicious'.split()
>>> placed = pack_words_into_shape(words, shape_value='apple')
>>> len(placed) == len(words)
True
```

Same input, same picture — every time, forever:

```pycon
>>> placed == pack_words_into_shape(words, shape_value='apple')
True
```

Every placed word is wholly inside the outline:

```pycon
>>> mask = shape_mask('named', 'apple')
>>> H, W = mask.shape
>>> def corners_in_mask(p):
...     x0, y0, x1, y1 = bounding_box(p)
...     xs = (int(x0 * W), int(x1 * W) - 1)
...     ys = (int(y0 * H), int(y1 * H) - 1)
...     return all(mask[y][x] for y in ys for x in xs)
>>> all(corners_in_mask(p) for p in placed if p is not None)
True
```

…and no two of them collide:

```pycon
>>> def collide(a, b):
...     ax0, ay0, ax1, ay1 = bounding_box(a)
...     bx0, by0, bx1, by1 = bounding_box(b)
...     return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1
>>> ok = [p for p in placed if p is not None]
>>> any(collide(a, b) for i, a in enumerate(ok) for b in ok[i + 1:])
False
```

An SVG outline works the same way (`M/L/H/V/C/S/Q/T/Z`; arcs are refused):

```pycon
>>> square = pack_words_into_shape(
...     ['one', 'two'], shape_kind='svg_path',
...     shape_value='M 0 0 L 10 0 L 10 10 L 0 10 Z')
>>> [p.text for p in square if p is not None]
['one', 'two']
```

### muvid.lyricvid.shape.register_shape(name)

Register a named outline, muvid’s house registry idiom.

The function receives `(u, v)` arrays in the shape box’s coordinates and
returns a boolean array. Implicit functions and polygons both fit; see
`polygon_shape()` for the latter.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Any`](https://docs.python.org/3/library/typing.html#typing.Any), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)], [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]], [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Any`](https://docs.python.org/3/library/typing.html#typing.Any), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)], [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

```pycon
>>> @register_shape('doctest-blob')
... def _blob(u, v): return (u - 0.5) ** 2 + (v - 0.5) ** 2 <= 0.1
>>> 'doctest-blob' in list_shapes()
True
>>> del NAMED_SHAPES['doctest-blob']
```

### muvid.lyricvid.shape.resolve_shape(name)

The outline function for `name`, or a ValueError naming the options.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Any`](https://docs.python.org/3/library/typing.html#typing.Any), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)], [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> resolve_shape('banana')
Traceback (most recent call last):
    ...
ValueError: unknown named shape 'banana'; known: apple, circle, heart, square, star
```

### muvid.lyricvid.shape.shape_mask(kind='named', value='circle', , aspect=1.7777777777777777, grid_height=192, margin=0.04, \*\*options)

Rasterise a shape to a boolean mask over the whole canvas.

The result has shape `(grid_height, round(grid_height * aspect))`, so a
normalised canvas point `(x, y)` is the pixel `[int(y * H), int(x * W)]`.
The outline is drawn into the largest centred SQUARE the canvas allows (less
`margin`), which is what keeps a circle round on a 16:9 frame.

* **Return type:**
  [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)

```pycon
>>> m = shape_mask('named', 'circle', aspect=1.0, grid_height=32)
>>> m.shape, bool(m[16, 16]), bool(m[0, 0])
((32, 32), True, False)
```
