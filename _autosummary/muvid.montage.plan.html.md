# muvid.montage.plan

The planner: a pool of media + a beat grid + sections -> an edit list of slots.

This is the core of the subgenre. Everything creative that CapCut’s “photo beat
sync” templates, Animoto and Rotor sell is a planning decision, and it lives
here as pure functions over measured data: no file is opened, no ffmpeg runs,
and the same inputs always yield the same plan.

Three decisions, each in its own place:

**Where the cuts fall** — an *archetype* (a function in `ARCHETYPE_FNS`,
registered with [`register_archetype()`](#muvid.montage.plan.register_archetype) the way `muvid.lyricvid.scene`
registers its layouts) walks a section’s beats and returns
[`SlotDraft`](#muvid.montage.plan.SlotDraft)s: a span, how many tile regions it has, which of them are
fresh, a motion cycle and a transition. Every cut is a beat, a downbeat or a
subdivision of the measured grid; the archetype only chooses the density, and
the section label and the treatment’s `cut_feel` scale it (a chorus is twice
as dense as a verse, an intro half).

**Which image fills each slot** — the reuse policy ([`assign_media()`](#muvid.montage.plan.assign_media)),
which is what lets twelve photos carry a three-minute song:

* \*\*never the same image twice within `min_gap` cuts\*\* (the gap shrinks to
  `pool - 1` for a small pool, so a two-photo pool alternates);
* among what is allowed, the **least-used** image wins, and ties are broken by
  a golden-ratio stride over the slot index — a closed-form permutation that
  keeps the order from reading as a loop, with no random anywhere;
* every **return uses a different crop/move variant** (the k-th use of an
  image takes the k-th entry of [`CROP_VARIANTS`](#muvid.montage.plan.CROP_VARIANTS); a clip’s k-th use trims
  a different stretch of it);
* the **strongest quarter of the pool is reserved for the final chorus** and
  leads it, strongest first;
* a **cover**, when given, opens and closes the montage and appears nowhere else.

**How each tile moves** — [`tile_path()`](#muvid.montage.plan.tile_path), a piecewise-linear window path
(normalised to the fitted frame) that the renderer compiles to `zoompan`. A
blended boundary samples the same path from both sides, so a Ken Burns move
never restarts on a crossfade (the muvid#73 lesson, honoured by construction).

The output, [`Plan`](#muvid.montage.plan.Plan), is JSON-able and is written next to every render as
`plan.json` — the inspectable, hand-editable artifact the subgenre contract
asks for.

### Module Attributes

| [`CROP_VARIANTS`](#muvid.montage.plan.CROP_VARIANTS)   | The closed set of framings a still may be shown in, as `(cx, cy, size)` of the frame fitted to the canvas (`size` is the visible fraction; the aspect always follows the canvas).   |
|------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

### Functions

| [`assign_media`](#muvid.montage.plan.assign_media)(drafts, media, reuse, \*[, ...])   | Fill every region of every draft with `(media_index, use_ordinal)`.                         |
|--------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| [`cut_times`](#muvid.montage.plan.cut_times)(section, analysis, beats_per_cut)     | Slot boundaries inside `section`: `[start, cut, cut, ..., end]`.                            |
| [`plan_montage`](#muvid.montage.plan.plan_montage)(analysis, media, treatment)        | Pool + measurements + treatment -> a [`Plan`](#muvid.montage.plan.Plan). |
| [`register_archetype`](#muvid.montage.plan.register_archetype)(name)                        | Register an archetype planner under `name`.                                                 |
| [`tile_path`](#muvid.montage.plan.tile_path)(variant, motion, length_s, \*[, ...]) | The window path for one still: piecewise linear, slot-relative seconds.                     |

### Classes

| [`Keyframe`](#muvid.montage.plan.Keyframe)(t, window)                               | `window` at slot-relative time `t` (seconds).                                                |
|----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| [`Plan`](#muvid.montage.plan.Plan)(\*, duration, tempo_bpm, beats_per_bar, ...) | The edit list.                                                                               |
| [`PlanContext`](#muvid.montage.plan.PlanContext)(\*, analysis, direction)              | What every archetype gets: the measurements and the direction.                               |
| [`Slot`](#muvid.montage.plan.Slot)(\*, index, start, end, section, ...[, ...])  | One span of the montage, in song seconds.                                                    |
| [`SlotDraft`](#muvid.montage.plan.SlotDraft)(\*, start, end, section, archetype)     | What an archetype emits: a span with regions, before media assignment.                       |
| [`Tile`](#muvid.montage.plan.Tile)(\*, region, media, motion, variant, path)    | One region of a slot: which media, framed and moved how.                                     |
| [`Window`](#muvid.montage.plan.Window)(x, y, size)                                | A framing: top-left `(x, y)` and visible fraction `size` of the fitted frame, all in `0..1`. |

### muvid.montage.plan.CROP_VARIANTS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[float](https://docs.python.org/3/builtins/functions.html#float), [float](https://docs.python.org/3/builtins/functions.html#float), [float](https://docs.python.org/3/builtins/functions.html#float)]]* *= {'bottom': (0.5, 0.68, 0.78), 'centre': (0.5, 0.5, 0.7), 'full': (0.5, 0.5, 1.0), 'left': (0.32, 0.5, 0.78), 'right': (0.68, 0.5, 0.78), 'top': (0.5, 0.32, 0.78)}*

The closed set of framings a still may be shown in, as `(cx, cy, size)`
of the frame fitted to the canvas (`size` is the visible fraction; the
aspect always follows the canvas). The k-th use of an image takes the k-th
variant, so a revisit is a different picture.

### *class* muvid.montage.plan.Keyframe(t, window)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

`window` at slot-relative time `t` (seconds).

### *class* muvid.montage.plan.Plan(\*, duration, tempo_bpm, beats_per_bar, beat_source, section_source, sections, media, slots, reuse=<factory>, treatment=<factory>, notes=(), plan_version='1.0')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The edit list. JSON-able, canvas-independent (windows are normalised).

#### *classmethod* from_dict(d)

Read a plan back — a hand-edited `plan.json` renders the same way.

* **Return type:**
  [`Plan`](#muvid.montage.plan.Plan)

### *class* muvid.montage.plan.PlanContext(, analysis, direction)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What every archetype gets: the measurements and the direction.

### *class* muvid.montage.plan.Slot(, index, start, end, section, archetype, regions, tiles, transition='cut', transition_s=0.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One span of the montage, in song seconds.

#### transition *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

How this slot ARRIVES. The first slot’s is always a cut.

### *class* muvid.montage.plan.SlotDraft(, start, end, section, archetype, regions=1, fresh=(0,), motions=('none',), amplitude=0.0, transition='cut', transition_s=0.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What an archetype emits: a span with regions, before media assignment.

### *class* muvid.montage.plan.Tile(, region, media, motion, variant, path, source_in=0.0, use=0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One region of a slot: which media, framed and moved how.

#### source_in *: [float](https://docs.python.org/3/builtins/functions.html#float)*

In-point into a clip (seconds). Ignored for stills.

#### use *: [int](https://docs.python.org/3/builtins/functions.html#int)*

The ordinal of this use of the media, 0-based (drives variant/in-point).

### *class* muvid.montage.plan.Window(x, y, size)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A framing: top-left `(x, y)` and visible fraction `size` of the
fitted frame, all in `0..1`. The aspect is the canvas’s.

### muvid.montage.plan.assign_media(drafts, media, reuse, , finale_start=None)

Fill every region of every draft with `(media_index, use_ordinal)`.

Returns one `{region: (media, use)}` per draft, plus the policy’s own
account of what it did (reserved images, uses, the smallest gap it
actually produced), which goes into the plan for inspection.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]]], [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

```pycon
>>> from muvid.montage.analysis import Media
>>> pool = [Media(index=i, path=f'{i}.jpg', kind='photo', width=100, height=100,
...               strength=float(i)) for i in range(3)]
>>> drafts = [SlotDraft(start=i, end=i + 1, section='verse', archetype='beat_cut')
...           for i in range(7)]
>>> picks, stats = assign_media(drafts, pool, spec_mod.Reuse(min_gap=6))
>>> [p[0][0] for p in picks]      # gap shrinks to pool-1 = 2: a forced cycle
[0, 2, 1, 0, 2, 1, 0]
>>> stats['min_gap'], stats['min_observed_gap']
(2, 3)
```

### muvid.montage.plan.cut_times(section, analysis, beats_per_cut)

Slot boundaries inside `section`: `[start, cut, cut, ..., end]`.

Cuts are beats of the measured grid every `round(beats_per_cut)` beats
(anchored on the section’s first downbeat when that keeps them on the bar),
or, below one beat, evenly spaced subdivisions between consecutive beats.
A leading or trailing slot shorter than half the cut interval is folded into
its neighbour — a half-beat pickup is not a slot — and a cut within
`MIN_SLOT_S` of a boundary is dropped.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

```pycon
>>> from muvid.montage.analysis import Analysis, Section
>>> a = Analysis(duration=8.0, tempo_bpm=120.0,
...              beats=tuple(i * 0.5 for i in range(16)),
...              downbeats=tuple(i * 2.0 for i in range(4)))
>>> cut_times(Section(label='verse', start=0.0, end=8.0), a, 4)
[0.0, 2.0, 4.0, 6.0, 8.0]
>>> cut_times(Section(label='chorus', start=1.0, end=4.0), a, 2)
[1.0, 2.0, 3.0, 4.0]
>>> cut_times(Section(label='chorus', start=0.0, end=2.0), a, 0.5)
[0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
>>> cut_times(Section(label='verse', start=1.5, end=6.8), a, 4)   # pickup + tail folded
[1.5, 4.0, 6.8]
```

### muvid.montage.plan.plan_montage(analysis, media, treatment)

Pool + measurements + treatment -> a [`Plan`](#muvid.montage.plan.Plan). Pure and deterministic.

* **Return type:**
  [`Plan`](#muvid.montage.plan.Plan)

### muvid.montage.plan.register_archetype(name)

Register an archetype planner under `name`.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Section`](muvid.montage.analysis.html.md#muvid.montage.analysis.Section), [`PlanContext`](#muvid.montage.plan.PlanContext), [`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]], [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`SlotDraft`](#muvid.montage.plan.SlotDraft)]]], [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Section`](muvid.montage.analysis.html.md#muvid.montage.analysis.Section), [`PlanContext`](#muvid.montage.plan.PlanContext), [`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]], [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`SlotDraft`](#muvid.montage.plan.SlotDraft)]]]

```pycon
>>> @register_archetype('doctest-demo')
... def demo(section, ctx, params): return []
>>> 'doctest-demo' in ARCHETYPE_FNS
True
>>> del ARCHETYPE_FNS['doctest-demo']
```

### muvid.montage.plan.tile_path(variant, motion, length_s, , amplitude=0.08, punch_s=0.25)

The window path for one still: piecewise linear, slot-relative seconds.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Keyframe`](#muvid.montage.plan.Keyframe), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

```pycon
>>> tile_path('full', 'none', 2.0)
(Keyframe(t=0.0, window=Window(x=0.0, y=0.0, size=1.0)),)
>>> [(k.t, k.window.size) for k in tile_path('full', 'zoom_in', 2.0, amplitude=0.1)]
[(0.0, 1.0), (2.0, 0.9)]
>>> [(k.t, k.window.size) for k in tile_path('centre', 'punch', 2.0, amplitude=0.1, punch_s=0.25)]
[(0.0, 0.63), (0.25, 0.7), (2.0, 0.7)]
```
