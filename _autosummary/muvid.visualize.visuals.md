# muvid.visualize.visuals

Visual strategies: how the *picture* of an audio-driven video is produced.

A strategy is a function from a [`VisualContext`](#muvid.visualize.visuals.VisualContext) (the audio, the optional
cover, the duration, the canvas) to a [`VisualPlan`](#muvid.visualize.visuals.VisualPlan) (the ffmpeg inputs
and filter chains that yield one video stream). [`muvid.visualize.video`](muvid.visualize.video.md#module-muvid.visualize.video) owns the
muxing, encoding, and loudness; a strategy only says what the frames look like.

That split is what keeps this open-closed: the built-ins are registered by name
(`"still"`, `"ken_burns"`, `"cqt"`, …), and anything else you can express
as a callable — a librosa/matplotlib animation, a projectM render, a shader —
plugs in through the same seam, either by returning a [`VisualPlan`](#muvid.visualize.visuals.VisualPlan) or by
returning the path of a silent video it rendered itself.

```pycon
>>> sorted(list_visuals())
['bars', 'cqt', 'ken_burns', 'scope', 'spectrum', 'still', 'waves']
```

Conventions a strategy must honour:

- **ffmpeg input 0 is always the audio.** Inputs a plan adds are numbered from
  1, in the order they appear in [`VisualPlan.inputs`](#muvid.visualize.visuals.VisualPlan.inputs).
- To react to the audio, set `uses_audio=True` and consume the `[aviz]`
  label — a dedicated copy of the audio, split off so the output track stays
  untouched.
- Emit exactly one video stream, labelled [`VisualPlan.video`](#muvid.visualize.visuals.VisualPlan.video).

### Module Attributes

| [`REACTIVE_COVER_FRACTION`](#muvid.visualize.visuals.REACTIVE_COVER_FRACTION)   | Over a reactive background the cover fills nearly the whole frame (with a little padding), and is made slightly transparent so the visualizer plays on through it rather than being hidden behind an opaque card.    |
|----------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`SCOPE_COVER_ALPHA`](#muvid.visualize.visuals.SCOPE_COVER_ALPHA)         | The vectorscope fills the whole frame, so it keeps the same big cover as the other visuals but a touch more transparent, letting the line-work read both through the cover and in the space around it.               |
| [`SCOPE_BG_DIM`](#muvid.visualize.visuals.SCOPE_BG_DIM)              | The vectorscope's plate is darker still, so its sparse line-work has the least competition of any visual.                                                                                                            |
| [`DEFAULT_TINT`](#muvid.visualize.visuals.DEFAULT_TINT)              | Default accent for the line/bar visualizers, applied as a *tint*.                                                                                                                                                    |
| [`REACTIVE_BG_SATURATION`](#muvid.visualize.visuals.REACTIVE_BG_SATURATION)    | Reactive backgrounds are pushed dark and near-neutral so the teal accent reads (a bright, saturated blurred cover both tints everything its own colour and, under the screen blend, washes the accent out to white). |
| [`Visual`](#muvid.visualize.visuals.Visual)                    | context in, plan out.                                                                                                                                                                                                |

### Functions

| [`bars_visual`](#muvid.visualize.visuals.bars_visual)(ctx)            | Frequency bars (the classic EQ look), via ffmpeg's `showfreqs`.                                             |
|------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| [`cqt_visual`](#muvid.visualize.visuals.cqt_visual)(ctx)             | Constant-Q transform bars — pitch-aligned, the most *musical* reactive look.                                |
| [`ken_burns_visual`](#muvid.visualize.visuals.ken_burns_visual)(ctx)       | A slow pan/zoom across the cover, lasting exactly as long as the song.                                      |
| [`list_visuals`](#muvid.visualize.visuals.list_visuals)()              | The names of every registered visual strategy.                                                              |
| [`register_visual`](#muvid.visualize.visuals.register_visual)(name)       | Register a visual strategy under `name` (the open-closed seam).                                             |
| [`resolve_visual`](#muvid.visualize.visuals.resolve_visual)(visual, ctx) | Turn `visual` (a name, or any callable) into a [`VisualPlan`](#muvid.visualize.visuals.VisualPlan). |
| [`scope_visual`](#muvid.visualize.visuals.scope_visual)(ctx)           | The stereo Lissajous figure, via ffmpeg's `avectorscope`.                                                   |
| [`spectrum_visual`](#muvid.visualize.visuals.spectrum_visual)(ctx)        | A scrolling spectrogram, via ffmpeg's `showspectrum`.                                                       |
| [`still_visual`](#muvid.visualize.visuals.still_visual)(ctx)           | The cover, composed on a 16:9 canvas, held for the whole song.                                              |
| [`waves_visual`](#muvid.visualize.visuals.waves_visual)(ctx)           | The waveform, via ffmpeg's `showwaves`.                                                                     |

### Classes

| [`VisualContext`](#muvid.visualize.visuals.VisualContext)(audio, image, duration, size, fps)   | Everything a visual strategy needs to know about the render.   |
|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| [`VisualPlan`](#muvid.visualize.visuals.VisualPlan)([inputs, filters, video, ...])          | The ffmpeg fragments that render one strategy's video stream.  |

### muvid.visualize.visuals.DEFAULT_TINT *= 'colorchannelmixer=rr=0.16:gg=0.80:bb=0.85'*

Default accent for the line/bar visualizers, applied as a *tint*. Several
ffmpeg visualizers ignore their `colors` option (`showfreqs` draws white
whatever you ask for), so rather than fight each filter we render them white
and recolour the whole visualization here — one knob, identical accent across
every method, so a whole album’s videos read as one release. It is a
`colorchannelmixer` mapping white (r=g=b) to a light teal; override per call
with the `tint` option (an empty string leaves the visualizer’s own colour).

### muvid.visualize.visuals.REACTIVE_BG_SATURATION *= 0.18*

Reactive backgrounds are pushed dark and near-neutral so the teal accent reads
(a bright, saturated blurred cover both tints everything its own colour and,
under the screen blend, washes the accent out to white). The sharp centred
cover still carries the artwork’s colour; only the surround is muted.

The dim is MULTIPLICATIVE (muvid#70), so this value is not comparable with the
additive `0.5` it replaces — it is the constant that lands the plate’s mean
display luma where the offset left it, measured over four photographs (as PNGs;
on JPEG covers the OLD chain landed somewhere else, because an additive offset
is range-dependent and a scaling is not — see `canvas._DIM_PIVOT`).

The washout this comment warns about was then measured rather than assumed: under
`screen`, out = P + V(1 - P), so the accent’s contribution is what a brighter
plate would eat. Isolated by rendering the real composed graph twice, once with
the visualizer branch removed — which under `screen` leaves the plate alone — and
differencing: over six covers (three photographs, each as PNG and as JPEG) the
accent’s frame-mean added luma moves by at most 0.10/255. State the instrument
with any number here: the accent is sparse, so a frame mean is small and a
mean over the visualizer’s own pixels is not, and only the ratio is portable.
It still reads.

### muvid.visualize.visuals.REACTIVE_COVER_FRACTION *= 0.95*

Over a reactive background the cover fills nearly the whole frame (with a
little padding), and is made slightly transparent so the visualizer plays on
through it rather than being hidden behind an opaque card.

### muvid.visualize.visuals.SCOPE_BG_DIM *= 0.965*

The vectorscope’s plate is darker still, so its sparse line-work has the least
competition of any visual. Named rather than spelled at the call site for the
same reason `spectrum_visual`’s is: muvid#70 named three constants to retune and
there were four sites, the fourth being a bare literal nothing pointed at.
Multiplicative like the other two, and measured the same way — the value that
lands the plate where the additive `0.55` left it.

### muvid.visualize.visuals.SCOPE_COVER_ALPHA *= 0.72*

The vectorscope fills the whole frame, so it keeps the same big cover as the
other visuals but a touch more transparent, letting the line-work read both
through the cover and in the space around it.

### muvid.visualize.visuals.Visual

context in, plan out. Returning a path to an already-rendered
silent video is also accepted (see [`resolve_visual()`](#muvid.visualize.visuals.resolve_visual)).

* **Type:**
  A strategy

alias of `Callable`[[[`VisualContext`](#muvid.visualize.visuals.VisualContext)], `VisualPlan | Path | str`]

### *class* muvid.visualize.visuals.VisualContext(audio, image, duration, size, fps, layout=<factory>, title=None, title_style=None, workdir=<factory>, options=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything a visual strategy needs to know about the render.

#### audio

The audio file (ffmpeg input 0).

#### image

The cover art, if the caller supplied one.

#### duration

Audio duration in seconds.

#### size

Canvas size (width, height).

#### fps

Output frame rate.

#### layout

How the cover sits on the canvas.

#### title

Title to burn in, if any.

#### title_style

How to draw that title.

#### workdir

A directory the strategy may write intermediate files into.

#### options

Strategy-specific knobs, passed straight through by the caller.

#### require_image(visual)

The cover image, or a [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError) naming what to do instead.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### *class* muvid.visualize.visuals.VisualPlan(inputs=<factory>, filters=<factory>, video='vbg', uses_audio=False, has_cover=False, has_title=False, still=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The ffmpeg fragments that render one strategy’s video stream.

#### inputs

Extra ffmpeg input argument groups (each ends with `-i PATH`),
numbered from input 1.

#### filters

`filter_complex` chains, joined with `;` by the renderer.

#### video

Label of the video stream the chains emit.

#### uses_audio

The plan consumes the `[aviz]` audio copy.

#### has_cover

The plan already placed the cover; the renderer must not
overlay it again.

#### has_title

The plan already burnt in the title; the renderer must not
draw it again.

#### still

When set, the video *is* this static image — the renderer takes a
much cheaper path (encode one short segment, then loop it) and
ignores `inputs`/`filters`.

### muvid.visualize.visuals.bars_visual(ctx)

Frequency bars (the classic EQ look), via ffmpeg’s `showfreqs`.

Rendered white; colour comes from the accent `tint` (see [`DEFAULT_TINT`](#muvid.visualize.visuals.DEFAULT_TINT)).

* **Return type:**
  [`VisualPlan`](#muvid.visualize.visuals.VisualPlan)

### muvid.visualize.visuals.cqt_visual(ctx)

Constant-Q transform bars — pitch-aligned, the most *musical* reactive look.

Rendered white; colour comes from the accent `tint` (recolour it with the
`tint` option — *not* a per-filter colour, which the tint would multiply).

`showcqt` can draw two panes: the bargraph, and beneath it a *sonogram*
that keeps every past frame and scrolls it downward, so the music leaves a
trail of where it has been. The sonogram is off by default (bars only, the
cleanest read). Give `sono_fraction` a value in (0, 1) to hand that share
of the frame’s height to the trail — the bargraph keeps the rest.

* **Return type:**
  [`VisualPlan`](#muvid.visualize.visuals.VisualPlan)

Options:
: `sono_fraction`: share of the height given to the scrolling sonogram,
  : 0 (default, bars only) to just under 1.
  <br/>
  `sono_v` / `bar_v`: sonogram and bargraph volume (sensitivity).
  `sono_g` / `bar_g`: sonogram and bargraph gamma (contrast).
  Plus the shared background/cover keys of `_reactive_plan()`.

### Examples

```pycon
>>> ctx = VisualContext(Path("a.wav"), None, 10.0, (1920, 1080), 24)
>>> "sono_h=0" in cqt_visual(ctx).filters[0]  # bars only, by default
True
>>> trail = replace(ctx, options={"sono_fraction": 0.6})
>>> f = cqt_visual(trail).filters[0]
>>> "bar_h=432" in f and "sono_h=648" in f  # 40% bars / 60% trail
True
```

### muvid.visualize.visuals.ken_burns_visual(ctx)

A slow pan/zoom across the cover, lasting exactly as long as the song.

Renders through the `burns` package (via `mixing.video`). By default it
pans across the *composed canvas* rather than the raw cover, so a square or
portrait image still fills a 16:9 frame instead of being letterboxed by the
pan.

Frames are rendered in Python (Pillow), so this is by far the slowest
visual — budget several times the song’s duration. The ffmpeg-native
visuals are an order of magnitude faster.

* **Return type:**
  [`VisualPlan`](#muvid.visualize.visuals.VisualPlan)

Options:
: `source`: `"canvas"` (default) or `"image"` — what to pan across.

### muvid.visualize.visuals.list_visuals()

The names of every registered visual strategy.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.visualize.visuals.register_visual(name)

Register a visual strategy under `name` (the open-closed seam).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`VisualContext`](#muvid.visualize.visuals.VisualContext)], [`VisualPlan`](#muvid.visualize.visuals.VisualPlan) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]], [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`VisualContext`](#muvid.visualize.visuals.VisualContext)], [`VisualPlan`](#muvid.visualize.visuals.VisualPlan) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### Examples

```pycon
>>> @register_visual("black")
... def _black(ctx):
...     w, h = ctx.size
...     return VisualPlan(filters=[f"color=c=black:s={w}x{h}[vbg]"])
>>> "black" in list_visuals()
True
>>> _ = _VISUALS.pop("black")  # (keep the registry tidy for the next doctest)
```

### muvid.visualize.visuals.resolve_visual(visual, ctx)

Turn `visual` (a name, or any callable) into a [`VisualPlan`](#muvid.visualize.visuals.VisualPlan).

`"auto"` picks the cheapest strategy that suits the inputs: a still cover
when there is an image, an audio-reactive CQT when there is not.

A callable may return a [`VisualPlan`](#muvid.visualize.visuals.VisualPlan), or the path of a silent video
it rendered itself — the latter is the escape hatch for backends that do not
express themselves as an ffmpeg filtergraph (librosa/matplotlib, projectM,
a headless-browser capture…).

* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – `visual` names a strategy that is not registered.
* **Return type:**
  [`VisualPlan`](#muvid.visualize.visuals.VisualPlan)

### muvid.visualize.visuals.scope_visual(ctx)

The stereo Lissajous figure, via ffmpeg’s `avectorscope`.

Tuned for drama *and* dynamics. Anti-aliased lines, `mirror=xy` to fill all
four quadrants symmetrically, and a generous `zoom` so the figure spills
into the space around the cover. Crucially the amplitude scale is **linear**,
not `sqrt`: a compressive scale keeps a steady mix pinned wide the whole
time (a constant scribble), whereas linear lets the figure *breathe* — wide
and full when the music swells, small and calm when it settles. The cover
stays the same big size as the other visuals, a touch more transparent so
the line-work reads through it too.

Options: `zoom`, `scale` (`lin`/`sqrt`/`cbrt`/`log`), `mirror`
(`none`/`x`/`y`/`xy`), `draw`, plus the shared background/cover keys.

* **Return type:**
  [`VisualPlan`](#muvid.visualize.visuals.VisualPlan)

### muvid.visualize.visuals.spectrum_visual(ctx)

A scrolling spectrogram, via ffmpeg’s `showspectrum`.

A `log` *frequency* axis spreads out the low-mid range where music lives
(a linear axis crams it into the bottom edge), lifted gain makes the detail
read, and `overlap` quickens the right-to-left scroll. Rendered over a
darker background so it stands out. The default `green` colormap is tinted
to the teal accent; pass a `color` to use ffmpeg’s colormap instead.

The whole spectrogram also **pulses with the beat**: a precomputed onset
envelope (see [`muvid.visualize.reactive`](muvid.visualize.reactive.md#module-muvid.visualize.reactive)) drives a `sendcmd`-controlled
brightness/saturation flash, so attacks in the music read as flashes at the
live leading edge instead of the display feeling merely synced to playback.
The flash is appended last, after the recolour, so it modulates the colours
the frame actually shows.

Options: `color` (colormap — the teal tint applies only to the default),
`gain`, `saturation`, `overlap` (scroll speed, 0–1), `flash` (bool,
default on), `flash_brightness`, `flash_saturation`, plus the shared
background/cover keys.

* **Return type:**
  [`VisualPlan`](#muvid.visualize.visuals.VisualPlan)

### muvid.visualize.visuals.still_visual(ctx)

The cover, composed on a 16:9 canvas, held for the whole song.

* **Return type:**
  [`VisualPlan`](#muvid.visualize.visuals.VisualPlan)

### muvid.visualize.visuals.waves_visual(ctx)

The waveform, via ffmpeg’s `showwaves`.

Rendered white; colour comes from the accent `tint`. `options={"mode":
...}` sets the waveform *shape* (`cline`/`line`/`p2p`/`point`).

* **Return type:**
  [`VisualPlan`](#muvid.visualize.visuals.VisualPlan)
