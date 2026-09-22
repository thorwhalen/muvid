# muvid.lyricvid.scene

The renderer-neutral scene — every number computed, no renderer opinions.

A [`Scene`](#muvid.lyricvid.scene.Scene) is a flat list of [`Cue`](#muvid.lyricvid.scene.Cue)s: a piece of text, where it
sits, how big it is, what colour, and its time envelope. That is all. It says
nothing about ASS tags or CSS, which is exactly what lets `render_ass` and
`render_web` be two backends over one compiler instead of two half-products.

Positions are **normalised** (`0..1` of the canvas, origin top-left, anchor at
the text’s centre) and sizes are a **fraction of canvas height**, so one scene
renders correctly at 1080p, at 4K and in portrait without recomputation.

The compiler is where the treatment spec stops being advice and becomes
geometry. Each archetype is a small function `(spec, scene_spec, timed_text,
canvas) -> list[Cue]`; adding one is adding a function and a vocabulary entry,
which is the seam a plugin author or a future muvid uses to grow the vocabulary.

### Functions

| [`compile_scene`](#muvid.lyricvid.scene.compile_scene)(treatment, timed_text, \*[, canvas])   | Turn a treatment plus timed text into a fully-resolved [`Scene`](#muvid.lyricvid.scene.Scene).   |
|-------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| [`register_archetype`](#muvid.lyricvid.scene.register_archetype)(name)                             | Register a layout archetype.                                                                                     |

### Classes

| [`Canvas`](#muvid.lyricvid.scene.Canvas)(\*[, width, height, fps])                   | Output geometry.                                              |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------------|
| [`Cue`](#muvid.lyricvid.scene.Cue)(\*, text, x, y, size, t_in, t_full[, ...])     | One piece of text, placed and timed.                          |
| [`Scene`](#muvid.lyricvid.scene.Scene)(\*, canvas, duration, background, cues, ...) | Everything a renderer needs, and nothing it has to interpret. |

### *class* muvid.lyricvid.scene.Canvas(, width=1920, height=1080, fps=30)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Output geometry. Sizes in the scene are relative to `height`.

### *class* muvid.lyricvid.scene.Cue(\*, text, x, y, size, t_in, t_full, t_out=None, t_gone=None, colour='#ffffff', dim_colour=None, dim_from=None, motion='fade', layer=0, extra=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One piece of text, placed and timed.

* **Parameters:**
  * **y** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – centre of the text, normalised to the canvas (0..1).
  * **size** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – cap height as a fraction of canvas height.
  * **t_in** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – when it starts arriving.
  * **t_full** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – when it is fully arrived. `t_in == t_full` is a hard cut.
  * **t_out** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – when it starts leaving; `None` means it never leaves.
  * **t_gone** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – when it has fully left.
  * **dim_from** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – when it recedes to `dim_colour` (`persistence='dim'`).

#### extra *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any)]*

Free-form, for a renderer that can use it (e.g. per-word wipe fraction).

### *class* muvid.lyricvid.scene.Scene(\*, canvas, duration, background, cues, typography, meta=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything a renderer needs, and nothing it has to interpret.

#### meta *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any)]*

Provenance, for reporting and for tests.

### muvid.lyricvid.scene.compile_scene(treatment, timed_text, , canvas=None)

Turn a treatment plus timed text into a fully-resolved [`Scene`](#muvid.lyricvid.scene.Scene).

Every number in the result was computed here, from measurement. Nothing the
model wrote reaches a renderer as a coordinate or a time.

* **Return type:**
  [`Scene`](#muvid.lyricvid.scene.Scene)

```pycon
>>> from muvid.lyricvid.timed_text import from_words
>>> tt = from_words([('one', 0.0, .5), ('two', .5, 1.0)], duration=1.0)
>>> s = compile_scene(spec_mod.TreatmentSpec(), tt)
>>> len(s.cues), s.cues[0].text
(2, 'one')
```

### muvid.lyricvid.scene.register_archetype(name)

Register a layout archetype.

Follows muvid’s house registry idiom (`register_visual`,
`register_aligner`, `register_selection_strategy`). A new archetype is a
function plus an entry in [`muvid.lyricvid.spec.ARCHETYPES`](muvid.lyricvid.spec.md#muvid.lyricvid.spec.ARCHETYPES) so the
model knows it exists.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Cue`](#muvid.lyricvid.scene.Cue)]]], [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Cue`](#muvid.lyricvid.scene.Cue)]]]

```pycon
>>> @register_archetype('doctest-demo')
... def _demo(**kw): return []
>>> 'doctest-demo' in ARCHETYPE_FNS
True
>>> del ARCHETYPE_FNS['doctest-demo']
```
