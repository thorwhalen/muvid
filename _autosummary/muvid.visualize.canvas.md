# muvid.visualize.canvas

Lay a cover image out on a 16:9 canvas, and derive a thumbnail from it.

Cover art is usually square (or, worse, portrait) while video platforms are
16:9. Letting the platform pillarbox the art leaves black bars; instead we fill
the frame with a blurred, darkened copy of the cover and place the sharp cover
centred on top. It reads as intentional, and it is the same treatment whether
the result becomes a still video, the ground truth for a Ken Burns pan, or the
upload thumbnail — one [`CoverLayout`](#muvid.visualize.canvas.CoverLayout), one filtergraph, three uses.

Every filter chain here is built as a *string* rather than executed, so the
same chains compose into the bigger filtergraph that [`muvid.visualize.video`](muvid.visualize.video.md#module-muvid.visualize.video)
assembles for audio-reactive visuals.

### Module Attributes

| [`DEFAULT_SIZE`](#muvid.visualize.canvas.DEFAULT_SIZE)   | 1080p is YouTube's sweet spot for a static music video.             |
|-----------------------------------------------------------------|---------------------------------------------------------------------|
| [`THUMBNAIL_SIZE`](#muvid.visualize.canvas.THUMBNAIL_SIZE) | YouTube rejects thumbnails over 2 MiB, and wants at least 1280x720. |

### Functions

| [`background_chain`](#muvid.visualize.canvas.background_chain)(size, layout, \*, src, out)     | Filter chain turning cover stream `src` into a full-frame background.     |
|---------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`brightness_saturation_lut`](#muvid.visualize.canvas.brightness_saturation_lut)(\*[, brightness, ...]) | `lutyuv` y/u/v expressions reproducing `eq`'s brightness + saturation.    |
| [`canvas_image`](#muvid.visualize.canvas.canvas_image)(image, \*[, saveas, size, ...])     | Render the composed canvas (background + centred cover + title) as a PNG. |
| [`compose_chain`](#muvid.visualize.canvas.compose_chain)(size, layout, \*, src, out[, ...]) | The whole cover-on-canvas filtergraph: background, centred cover, title.  |
| [`cover_box`](#muvid.visualize.canvas.cover_box)(size, layout)                          | The bounding box the sharp cover is fitted into, for `size`/`layout`.     |
| [`cover_chain`](#muvid.visualize.canvas.cover_chain)(size, layout, \*, src, out)          | Filter chain scaling cover stream `src` to the centred sharp cover.       |
| [`default_font`](#muvid.visualize.canvas.default_font)()                                   | Path to a usable TrueType font, or `None` if none was found.              |
| [`dim_saturation_lut`](#muvid.visualize.canvas.dim_saturation_lut)(\*[, dim, saturation])        | `lutyuv` y/u/v expressions that DARKEN luma and desaturate chroma.        |
| [`escape_filter_value`](#muvid.visualize.canvas.escape_filter_value)(value)                       | Escape `value` for use as a filter option inside an ffmpeg filtergraph.   |
| [`lut_filter`](#muvid.visualize.canvas.lut_filter)(exprs, \*[, label])                   | A `lutyuv` filter from either LUT builder's expressions.                  |
| [`overlay_chain`](#muvid.visualize.canvas.overlay_chain)(\*, background, cover, out[, ...]) | Filter chain centring the `cover` stream over the `background` stream.    |
| [`thumbnail_image`](#muvid.visualize.canvas.thumbnail_image)(image, \*[, saveas, size, ...])  | Render `image` as a 16:9 JPEG thumbnail that YouTube will accept.         |
| [`title_chain`](#muvid.visualize.canvas.title_chain)(title, size[, style])                | Filter chain burning `title` into the bottom of stream `src`.             |

### Classes

| [`CoverLayout`](#muvid.visualize.canvas.CoverLayout)([background, blur_sigma, dim, ...])   | How a cover image is placed on the canvas.         |
|----------------------------------------------------------------------------------------------------|----------------------------------------------------|
| [`TitleStyle`](#muvid.visualize.canvas.TitleStyle)([size_fraction, color, font, ...])     | How a burnt-in title is drawn (ffmpeg `drawtext`). |

### *class* muvid.visualize.canvas.CoverLayout(background='blur', blur_sigma=30.0, dim=0.65, saturation=0.8, cover_fraction=0.92, cover_alpha=1.0, background_color='black')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

How a cover image is placed on the canvas.

#### background

`"blur"` (a blurred, darkened copy of the cover fills the
frame) or `"color"` (a flat [`background_color`](#muvid.visualize.canvas.CoverLayout.background_color)).

#### blur_sigma

Gaussian blur strength for the `"blur"` background.

#### dim

How much to darken the background, 0 (unchanged) to 1 (black).
Multiplicative — the background’s luma is *scaled* by `1 - dim`
about black, so a shadow gets darker rather than being deleted. See
[`dim_saturation_lut()`](#muvid.visualize.canvas.dim_saturation_lut); the constant is not comparable with the
additive offset that preceded it (muvid#70).

#### saturation

Background saturation (< 1 desaturates, so the sharp cover
stays the focal point).

#### cover_fraction

How much of the frame the sharp cover fills. The cover
is scaled up, keeping its aspect ratio, until it reaches this
fraction of *either* the frame width or the frame height — whichever
it hits first (so a wide cover is width-bound, a tall one
height-bound). `1.0` touches the edges; below 1 leaves padding.

#### cover_alpha

Opacity of the sharp cover, 0 (invisible) to 1 (opaque).
Below 1 lets whatever is behind the cover — a reactive visualizer,
the blurred background — show through it.

#### background_color

Fill colour when `background="color"`.

#### dim *: [float](https://docs.python.org/3/builtins/functions.html#float)* *= 0.65*

the multiplicative dim that lands the plate’s mean
DISPLAY luma where the additive `0.25` left it, pooled over four
photographs (muvid#70). The two forms are not comparable at the same
nominal value — see [`dim_saturation_lut()`](#muvid.visualize.canvas.dim_saturation_lut).

* **Type:**
  Measured, not chosen

### muvid.visualize.canvas.DEFAULT_SIZE *= (1920, 1080)*

1080p is YouTube’s sweet spot for a static music video.

* **Type:**
  Default 16
* **Type:**
  9 canvas

### muvid.visualize.canvas.THUMBNAIL_SIZE *= (1280, 720)*

YouTube rejects thumbnails over 2 MiB, and wants at least 1280x720.

### *class* muvid.visualize.canvas.TitleStyle(size_fraction=0.045, color='white', font=None, margin_fraction=0.06, box=True, box_color='black@0.45')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

How a burnt-in title is drawn (ffmpeg `drawtext`).

#### size_fraction

Font size as a fraction of canvas height.

#### color

Text colour.

#### font

Font file path, or `None` to auto-detect one.

#### margin_fraction

Distance from the bottom edge, as a fraction of height.

#### box

Draw a translucent plate behind the text (keeps it legible over
busy artwork).

#### box_color

Colour (with alpha) of that plate.

### muvid.visualize.canvas.background_chain(size, layout, , src, out)

Filter chain turning cover stream `src` into a full-frame background.

The darken/desaturate step is `lutyuv`, not `eq`: `eq` is GPL-only
(muvid#69). Its luma half is no longer `eq`’s arithmetic either — a dim has
to scale, not subtract, or the plate’s shadows are deleted rather than
darkened. See [`dim_saturation_lut()`](#muvid.visualize.canvas.dim_saturation_lut).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.visualize.canvas.brightness_saturation_lut(, brightness=0.0, saturation=1.0)

`lutyuv` y/u/v expressions reproducing `eq`’s brightness + saturation.

`eq` is compiled into ffmpeg only under `--enable-gpl`, so every chain that
reached for it made muvid require a GPL build for what is arithmetic on three
planes. `lutyuv` is LGPL and expresses the same two knobs exactly as `vf_eq`
defines them: brightness is an ADDITIVE offset of `brightness * 255` on luma,
saturation a scaling of chroma about `_CHROMA_PIVOT_SCALE`.

Returns a `{component: expression}` mapping rather than a filter string
because the two callers need different shapes — one composes a filtergraph,
the other emits one `sendcmd` command per component — and a second copy of
this arithmetic is exactly how the two would drift apart.

* **Parameters:**
  * **brightness** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Additive luma offset, -1 to 1, in `eq`’s units (a fraction
    of full scale). `0` is a no-op.
  * **saturation** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Chroma scaling about neutral. `1` is a no-op.
* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### Examples

```pycon
>>> lut = brightness_saturation_lut(brightness=-0.25, saturation=0.8)
>>> lut["y"]
'clip(val-63.75,0,if(eq(minval,0),maxval,maxval*255/235))'
>>> lut["u"] == lut["v"]
True
>>> brightness_saturation_lut()["y"], brightness_saturation_lut()["u"]
('clip(val+0,0,if(eq(minval,0),maxval,maxval*255/235))', 'clip(val*1+(if(eq(minval,0),maxval,maxval*255/240))*0,0,if(eq(minval,0),maxval,maxval*255/240))')
```

### muvid.visualize.canvas.canvas_image(image, , saveas=None, size=(1920, 1080), layout=None, title=None, title_style=None)

Render the composed canvas (background + centred cover + title) as a PNG.

Composing once into an image — rather than re-running a 1080p blur on every
frame — is what makes a still-image music video cheap to render, and it
gives the thumbnail and the video’s first frame a single source of truth.

* **Parameters:**
  * **image** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The cover art.
  * **saveas** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Output PNG path (default: `<image-stem>.canvas.png`).
  * **size** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]) – Canvas size.
  * **layout** ([`CoverLayout`](#muvid.visualize.canvas.CoverLayout) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Placement/treatment of the cover (a default one when omitted).
  * **title** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Burn this title into the canvas (omit for no title).
  * **title_style** ([`TitleStyle`](#muvid.visualize.canvas.TitleStyle) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – How to draw that title.
* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
* **Returns:**
  Path to the rendered PNG.

### muvid.visualize.canvas.compose_chain(size, layout, , src, out, title=None, title_style=None)

The whole cover-on-canvas filtergraph: background, centred cover, title.

`src` is a single cover-image stream; it is split so the same image feeds
both the blurred background and the sharp foreground.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.visualize.canvas.cover_box(size, layout)

The bounding box the sharp cover is fitted into, for `size`/`layout`.

Scales with *both* frame dimensions, so a cover fitted into it with
`force_original_aspect_ratio=decrease` grows until it meets whichever edge
comes first — filling the frame up to `cover_fraction`, minus padding.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]

### muvid.visualize.canvas.cover_chain(size, layout, , src, out)

Filter chain scaling cover stream `src` to the centred sharp cover.

When `layout.cover_alpha < 1` the scaled cover is made semi-transparent
(`colorchannelmixer=aa=…`, over an `rgba` copy so an opaque source gains
an alpha channel), so a following [`overlay_chain()`](#muvid.visualize.canvas.overlay_chain) lets the background
show through it.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.visualize.canvas.default_font()

Path to a usable TrueType font, or `None` if none was found.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.visualize.canvas.dim_saturation_lut(, dim=0.0, saturation=1.0)

`lutyuv` y/u/v expressions that DARKEN luma and desaturate chroma.

The sibling of [`brightness_saturation_lut()`](#muvid.visualize.canvas.brightness_saturation_lut), and deliberately not the same
arithmetic. `eq`’s brightness — which this replaces at the one site that wanted
to *darken* rather than to *shift* — is an additive offset, and subtracting a
constant does not dim a picture: it slides the histogram down and clamps
everything below the offset to the floor. The plate’s shadows did not get darker,
they were deleted, and at the reactive constants that took 57–99% of the plate to
display black (muvid#70).

So luma is *scaled* by `1 - dim` about `_DIM_PIVOT`, which preserves the
order of every pair of pixels — a shadow stays darker than what is next to it
instead of joining it at black. Chroma is untouched by the change and still goes
through `_chroma_expr()`, so the desaturation half remains `eq`’s.

`dim` therefore means something different from the additive offset it replaces,
and the constants that ship were re-measured rather than converted: see
[`CoverLayout`](#muvid.visualize.canvas.CoverLayout) and [`muvid.visualize.visuals`](muvid.visualize.visuals.md#module-muvid.visualize.visuals).

* **Parameters:**
  * **dim** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – How much to darken, 0 (unchanged) to 1 (black).
  * **saturation** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Chroma scaling about neutral. `1` is a no-op.
* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### Examples

```pycon
>>> lut = dim_saturation_lut(dim=0.65, saturation=0.8)
>>> lut["y"]
'clip((val-minval)*0.35+minval,0,if(eq(minval,0),maxval,maxval*255/235))'
>>> lut["u"] == lut["v"] == brightness_saturation_lut(saturation=0.8)["u"]
True
>>> dim_saturation_lut()["y"]  # 0 is a no-op, and reads as one
'clip((val-minval)*1+minval,0,if(eq(minval,0),maxval,maxval*255/235))'
```

### muvid.visualize.canvas.escape_filter_value(value)

Escape `value` for use as a filter option inside an ffmpeg filtergraph.

ffmpeg unescapes such a value **twice**, so escaping it once is not enough:

1. The *filtergraph* parser reads the whole graph first, splitting it on
   `_GRAPH_LEVEL_SPECIALS` and consuming one layer of quoting.
2. Each surviving per-filter argument string is only then split into options
   on `_OPTION_LEVEL_SPECIALS`, consuming a second layer.

Escaping therefore runs in the mirror order — option level first, then graph
level over that result — so that each ffmpeg pass peels off exactly one
layer. A literal `'` comes out as `\\\'` and a literal `:` as `\\:`.

Escaping only once is why a title like `"Song: Part 1"` used to become a
filtergraph syntax error, and why a workdir whose name contained `'` or
`:` broke every `sendcmd=f=<path>` render.

Note that `%` is deliberately *not* escaped: neither parser treats it as
special, so `\%` was simply unescaped back to `%` and escaping it never
did anything. drawtext’s `%{...}` text expansion is a third level that
applies to that one filter’s `text` option only, and is out of scope for a
general-purpose filtergraph escaper.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.visualize.canvas.lut_filter(exprs, , label='')

A `lutyuv` filter from either LUT builder’s expressions.

The expressions contain `,`, which the *filtergraph* parser reads as “next
filter”, so every one goes through [`escape_filter_value()`](#muvid.visualize.canvas.escape_filter_value) — the same
escaper, and the same reason, as a `sendcmd` script path.

* **Parameters:**
  * **exprs** ([`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – `{component: expression}`.
  * **label** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Optional `@label` so `sendcmd` can address this filter.
* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### Examples

```pycon
>>> lut_filter({"y": "clip(val+0,0,255)"}, label="flash")
'lutyuv@flash=y=clip(val+0\\,0\\,255)'
```

### muvid.visualize.canvas.overlay_chain(, background, cover, out, shortest=False)

Filter chain centring the `cover` stream over the `background` stream.

* **Parameters:**
  * **background** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Label of the background video stream.
  * **cover** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Label of the (already scaled) cover stream.
  * **out** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Label to emit.
  * **shortest** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – End the overlay when the shortest input ends — required when a
    finite, audio-driven background is overlaid with an endlessly
    looping still cover, or the render would never terminate.
* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.visualize.canvas.thumbnail_image(image, , saveas=None, size=(1280, 720), layout=None, title=None, title_style=None, max_bytes=2097152)

Render `image` as a 16:9 JPEG thumbnail that YouTube will accept.

Same composition as the video canvas, so the thumbnail matches what the
viewer sees when they press play. JPEG quality is stepped down until the
file fits `max_bytes` (YouTube’s hard limit).

* **Parameters:**
  * **image** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The cover art.
  * **saveas** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Output JPEG path (default: `<image-stem>.thumb.jpg`).
  * **size** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]) – Thumbnail size (YouTube wants >= 1280x720, 16:9).
  * **layout** ([`CoverLayout`](#muvid.visualize.canvas.CoverLayout) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Placement/treatment of the cover.
  * **title** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Burn this title into the thumbnail (omit for none).
  * **title_style** ([`TitleStyle`](#muvid.visualize.canvas.TitleStyle) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – How to draw that title.
  * **max_bytes** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Hard size ceiling.
* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
* **Returns:**
  Path to the rendered JPEG.

### muvid.visualize.canvas.title_chain(title, size, style=None, , src, out)

Filter chain burning `title` into the bottom of stream `src`.

* **Raises:**
  [**FfmpegError**](muvid.visualize.md#muvid.visualize.FfmpegError) – This ffmpeg has no `drawtext`, or no font was found.
* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
