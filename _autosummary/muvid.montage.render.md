# muvid.montage.render

Render a [`Plan`](muvid.montage.plan.md#muvid.montage.plan.Plan) to a YouTube-spec mp4 with ffmpeg.

Bounded stages, the same shape as `muvid.footage.assemble`: \*\*one ffmpeg per
part\*\* (a slot’s solo span, or the blend window between two slots), a
**stream-copy concat** of the parts, and **one mux** of the song. Memory is
O(1) in the number of cuts, and a 300-slot montage never builds a 300-input
filtergraph — a chained `xfade` of that depth is the classic way to make
ffmpeg crawl.

Every part is frame-exact. Slot boundaries are rounded to frames ONCE
([`frame_layout()`](#muvid.montage.render.frame_layout)), a transition of `n` frames straddles its boundary
(`n//2` before, the rest after — centred on the beat, the NLE convention),
and the solo parts are shortened by exactly what the blends take, so the parts
sum to `round(duration * fps)` by construction rather than by luck.

A still is turned into motion by `zoompan=d=N` over ONE decoded frame — the
image is decoded once per part, not once per output frame — with the window
path compiled to expressions in `on` (the output frame counter) plus a frame
offset. That offset is what lets a blend part sample the SAME Ken Burns path
the solo part was on: the motion never restarts on a crossfade. Stills are
pre-fitted to `OVERSAMPLE` times the tile so `zoompan` always
downsamples, which is what avoids its well-known sub-pixel jitter.

`muvid.montage` does not use `looks.compile_motion` for this, although
`looks` is the house motion compiler: its fragment keys on `in_time` and
is built for video sources at `d=1`; over one still frame expanded by
`d=N` the input time never advances. The expression builder here is the
`on`-keyed twin of it, and it is a pure function with a doctest.

All ffmpeg goes through [`muvid.visualize.ffmpeg`](muvid.visualize.ffmpeg.md#module-muvid.visualize.ffmpeg); the encode arguments
are the visualizer’s own so a montage is the same H.264/AAC/no-edit-list file
the rest of muvid ships.

### Functions

| [`frame_layout`](#muvid.montage.render.frame_layout)(starts, duration, ...)               | Slot boundaries in frames, and the transition length into each slot.   |
|----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| [`grade_filter`](#muvid.montage.render.grade_filter)(grade[, accent, strength])           | The ffmpeg fragment for a grade, or `""` for none.                     |
| [`render_plan`](#muvid.montage.render.render_plan)(plan, \*, canvas, audio, output, ...) | Render `plan` over `audio` to `output`.                                |
| [`zoompan_exprs`](#muvid.montage.render.zoompan_exprs)(path, \*, frame_offset, fps)        | The `z`/`x`/`y` expressions for `zoompan` over a still.                |

### Classes

| [`Canvas`](#muvid.montage.render.Canvas)(\*[, width, height, fps])              | Output geometry.                         |
|------------------------------------------------------------------------------------------------|------------------------------------------|
| [`Part`](#muvid.montage.render.Part)(\*, kind, slot, offset, n_frames[, ...]) | One ffmpeg invocation's worth of frames. |

### *class* muvid.montage.render.Canvas(, width=1920, height=1080, fps=30)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Output geometry.

### *class* muvid.montage.render.Part(, kind, slot, offset, n_frames, prev=None, prev_offset=0, curve='fade')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One ffmpeg invocation’s worth of frames.

`offset` is the first frame of this part relative to `slot`’s start
(negative on the incoming side of a blend). A `blend` also names the
outgoing `prev` slot and its offset.

### muvid.montage.render.frame_layout(starts, duration, transitions_s, fps)

Slot boundaries in frames, and the transition length into each slot.

`starts[i]` is slot i’s start; `transitions_s[i]` the crossfade INTO
slot i (0 for a cut; slot 0’s is always 0). A transition is clamped to
half the shorter neighbour and dropped below `MIN_TRANSITION_FRAMES`.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`int`](https://docs.python.org/3/builtins/functions.html#int)], [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`int`](https://docs.python.org/3/builtins/functions.html#int)]]

```pycon
>>> frame_layout([0.0, 1.0, 2.0], 3.0, [0.0, 0.5, 0.05], 24)
([0, 24, 48, 72], [0, 12, 0])
>>> frame_layout([0.0, 0.1], 0.2, [0.0, 0.1], 24)   # 2-frame slots: no room
([0, 2, 5], [0, 0])
```

### muvid.montage.render.grade_filter(grade, accent='#e0533d', , strength=0.35)

The ffmpeg fragment for a grade, or `""` for none.

`tint` mixes `strength` of `luma * accent` into the frame — a closed
form over the accent colour, never a caller-supplied filter string.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> grade_filter('none')
''
>>> grade_filter('mono')
'hue=s=0'
>>> grade_filter('tint', '#ff0000', strength=0.5)
'colorchannelmixer=rr=0.6495:rg=0.2935:rb=0.057:gr=0:gg=0.5:gb=0:br=0:bg=0:bb=0.5'
```

### muvid.montage.render.render_plan(plan, , canvas, audio, output, workdir, grade='none', palette=None, crf=18, preset='fast', keep_parts=False)

Render `plan` over `audio` to `output`. Writes exactly `output`.

* **Parameters:**
  * **plan** ([`Plan`](muvid.montage.plan.md#muvid.montage.plan.Plan)) – The edit list. Its media paths are opened as-is.
  * **canvas** ([`Canvas`](#muvid.montage.render.Canvas)) – Output size and frame rate. Even dimensions only.
  * **audio** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The song; the video is exactly as long as it.
  * **output** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The mp4 to write.
  * **workdir** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Where the parts go (`workdir/parts`, removed after a
    successful mux unless `keep_parts`).
  * **grade** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – One of [`muvid.montage.spec.GRADES`](muvid.montage.spec.md#muvid.montage.spec.GRADES).
  * **palette** ([`Palette`](muvid.montage.spec.md#muvid.montage.spec.Palette) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Supplies the tint accent and the grid pad colour.
  * **crf** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – libx264 knobs, per part.
  * **preset** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – libx264 knobs, per part.
  * **keep_parts** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Leave the intermediate parts on disk.
* **Return type:**
  [`RenderResult`](muvid.subgenres.md#muvid.subgenres.RenderResult)
* **Returns:**
  A `RenderResult` whose `meta` carries the frame accounting,
  the parts count, and the verifier’s findings.

### muvid.montage.render.zoompan_exprs(path, , frame_offset, fps)

The `z`/`x`/`y` expressions for `zoompan` over a still.

`on` is the output frame counter; `frame_offset` shifts it so a part
that starts mid-slot (or, negative, before the slot’s first frame on the
incoming side of a blend) samples the slot’s own path.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

```pycon
>>> from muvid.montage.plan import Keyframe, Window
>>> path = (Keyframe(0.0, Window(0.0, 0.0, 1.0)), Keyframe(2.0, Window(0.1, 0.1, 0.8)))
>>> e = zoompan_exprs(path, frame_offset=12, fps=24)
>>> e['z']
'1/(1+(-0.2)*min(max((((on+12)/24)-0)/2,0),1))'
>>> e['x']
'iw*(0+(0.1)*min(max((((on+12)/24)-0)/2,0),1))'
>>> zoompan_exprs(path[:1], frame_offset=0, fps=24)
{'z': '1/(1)', 'x': 'iw*(0)', 'y': 'ih*(0)'}
```
