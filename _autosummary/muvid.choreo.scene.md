# muvid.choreo.scene

The scene compiler: events + sections -> a list of drawable OBJECTS.

An [`Obj`](#muvid.choreo.scene.Obj) is one thing on screen with a life: born at `t_born`, dead at
`t_die`, a shape `kind`, a position, a size, a colour and a `motion` that
says how it moves in between. Every number is computed here, in Python, from
the event list and the treatment; the renderer only draws.

Each **archetype** is a function `(events, section, ...) -> [Obj]` in a
registry (`ARCHETYPE_FNS`), the same seam [`muvid.lyricvid.scene`](muvid.lyricvid.scene.md#module-muvid.lyricvid.scene) uses:
adding a look is adding a function and a vocabulary entry. The four shipped
ones are the closed set in [`muvid.choreo.spec.ARCHETYPES`](muvid.choreo.spec.md#muvid.choreo.spec.ARCHETYPES).

**Determinism is a design rule, not a hope.** The only “randomness” is
[`unit_hash()`](#muvid.choreo.scene.unit_hash), a closed-form integer hash of `(seed, index, ...)` — so
the same audio and seed produce the same objects on any machine, a re-render
after a palette change moves nothing, and a test can pin a coordinate.

Positions are normalised (`x` in fractions of width, `y` of height, origin
top-left) and sizes are fractions of canvas **height**, so one scene renders at
any resolution. Velocities are in the same fractions per second.

### Module Attributes

| [`KINDS`](#muvid.choreo.scene.KINDS)   | Shape classes the renderer knows how to draw.   |
|----------------------------------------------------------|-------------------------------------------------|
| [`MOTIONS`](#muvid.choreo.scene.MOTIONS) | How an object moves between birth and death.    |

### Functions

| [`compile_scene`](#muvid.choreo.scene.compile_scene)(treatment, analysis, \*[, ...])   | Every object and backdrop for the whole song.                         |
|--------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`register_archetype`](#muvid.choreo.scene.register_archetype)(name)                        | Register an archetype under `name`.                                   |
| [`unit_hash`](#muvid.choreo.scene.unit_hash)(seed, \*keys)                         | A float in `[0, 1)` that is a pure function of its integer arguments. |

### Classes

| [`Backdrop`](#muvid.choreo.scene.Backdrop)(\*, start, end, kind, top, bottom)       | What is behind the objects during `[start, end)`.               |
|----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------|
| [`Canvas`](#muvid.choreo.scene.Canvas)(\*[, width, height, fps])                  | Output geometry.                                                |
| [`ChoreoScene`](#muvid.choreo.scene.ChoreoScene)(\*, canvas, duration, backdrops, ...) | Everything the renderer needs, and nothing it has to interpret. |
| [`Obj`](#muvid.choreo.scene.Obj)(\*, t_born, t_die, kind, x, y, size, colour)  | One drawable thing with a life.                                 |

### *class* muvid.choreo.scene.Backdrop(, start, end, kind, top, bottom)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What is behind the objects during `[start, end)`.

### *class* muvid.choreo.scene.Canvas(, width=1920, height=1080, fps=30)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Output geometry.

### *class* muvid.choreo.scene.ChoreoScene(\*, canvas, duration, backdrops, objects, meta=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything the renderer needs, and nothing it has to interpret.

### muvid.choreo.scene.KINDS *= ('circle', 'rect', 'triangle', 'line', 'diamond', 'ring')*

Shape classes the renderer knows how to draw.

### muvid.choreo.scene.MOTIONS *= ('hold', 'scroll', 'pulse', 'drift', 'flicker')*

How an object moves between birth and death.

### *class* muvid.choreo.scene.Obj(, t_born, t_die, kind, x, y, size, colour, motion='hold', band='', strength=1.0, aspect=1.0, angle=0.0, vx=0.0, vy=0.0, attack_s=0.0, release_s=0.0, layer=0, seed=0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One drawable thing with a life.

* **Parameters:**
  * **y** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – centre, normalised (x of width, y of height), at birth.
  * **size** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – the shape’s main dimension as a fraction of canvas height —
    diameter for `circle`/`ring`/`diamond`, height for `rect`/
    `triangle`, length for `line`.
  * **aspect** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – width/height for `rect`/`triangle`; length/thickness
    for `line`.
  * **angle** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – degrees, for `line`.
  * **vy** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – velocity (fractions per second) for `scroll`/`drift`.
  * **release_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – alpha ramps at the start and end of life.
  * **seed** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – per-object key for `flicker`’s per-frame jitter.

### muvid.choreo.scene.compile_scene(treatment, analysis, , canvas=None, seed=0)

Every object and backdrop for the whole song. Deterministic given `seed`.

* **Return type:**
  [`ChoreoScene`](#muvid.choreo.scene.ChoreoScene)

```pycon
>>> from muvid.choreo.analysis import Analysis, Event, Section, Tempo
>>> a = Analysis(duration=4.0, tempo=Tempo(bpm=120, beats=(0, .5, 1, 1.5), source='numpy'),
...              events=(Event(t=1.0, band='low', strength=0.9),
...                      Event(t=2.0, band='high', strength=0.5)),
...              sections=(Section(index=0, label='mid', start=0.0, end=4.0, energy_db=-10),))
>>> s = compile_scene(spec_mod.default_treatment('fischinger'), a, seed=1)
>>> [(o.t_born, o.kind, o.band) for o in s.objects]
[(1.0, 'circle', 'low'), (2.0, 'triangle', 'high')]
>>> compile_scene(spec_mod.default_treatment('fischinger'), a, seed=1) == s
True
```

### muvid.choreo.scene.register_archetype(name)

Register an archetype under `name`.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Obj`](#muvid.choreo.scene.Obj)]]], [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Obj`](#muvid.choreo.scene.Obj)]]]

```pycon
>>> @register_archetype('doctest-demo')
... def _demo(events, **kw): return []
>>> 'doctest-demo' in ARCHETYPE_FNS
True
>>> del ARCHETYPE_FNS['doctest-demo']
```

### muvid.choreo.scene.unit_hash(seed, \*keys)

A float in `[0, 1)` that is a pure function of its integer arguments.

splitmix64-style mixing; no state, no platform dependence, so it is the
only “random” this package allows itself.

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)

```pycon
>>> unit_hash(0, 1) == unit_hash(0, 1), 0.0 <= unit_hash(3, 4, 5) < 1.0
(True, True)
>>> unit_hash(0, 1) != unit_hash(1, 1)
True
```
