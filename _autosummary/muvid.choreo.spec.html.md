# muvid.choreo.spec

The choreo treatment spec — what a director decides, as data; stdlib-only.

The same two-layer split as [`muvid.lyricvid.spec`](muvid.lyricvid.spec.html.md#module-muvid.lyricvid.spec), for the same reason:

`direction`
: WHAT and WHY — palette, background, density, mood, a rationale. The half a
  model (or a person in a hurry) is genuinely good at.

`scenes`
: HOW — an **archetype** from a closed set, which sections it applies to,
  and a small bag of archetype parameters. Never geometry, never timings:
  every coordinate and every time is computed in Python from the event list.

Closed vocabularies everywhere (archetypes, backgrounds, densities), and a
`repair` that projects a nearly-right spec onto the valid space rather than
handing it back — a mechanical substitution renders something good now.

### Module Attributes

| [`ARCHETYPES`](#muvid.choreo.spec.ARCHETYPES)       | How events become objects.                                                     |
|-------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`BACKGROUNDS`](#muvid.choreo.spec.BACKGROUNDS)      | What is behind the objects.                                                    |
| [`DENSITIES`](#muvid.choreo.spec.DENSITIES)        | How many events become objects, and how many objects an event makes.           |
| [`DENSITY_GATE`](#muvid.choreo.spec.DENSITY_GATE)     | an event below it makes no object.                                             |
| [`DENSITY_FACTOR`](#muvid.choreo.spec.DENSITY_FACTOR)   | Count multiplier per density, for the archetypes that spawn several per event. |
| [`ARCHETYPE_PARAMS`](#muvid.choreo.spec.ARCHETYPE_PARAMS) | Archetype parameters, documented once for the schema and the prompt.           |

### Functions

| [`coerce`](#muvid.choreo.spec.coerce)(obj)                    | Whatever a caller produced -> a renderable spec plus the repair notes.                |
|---------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| [`default_treatment`](#muvid.choreo.spec.default_treatment)([archetype]) | A one-scene treatment for `archetype`; the CLI's `--archetype` shortcut.              |
| [`json_schema`](#muvid.choreo.spec.json_schema)()                  | The JSON Schema for a [`TreatmentSpec`](#muvid.choreo.spec.TreatmentSpec). |
| [`repair`](#muvid.choreo.spec.repair)(spec)                   | Project `spec` onto the valid space; return `(spec, notes)`.                          |
| [`validate`](#muvid.choreo.spec.validate)(spec)                 | Human-readable problems; empty means renderable.                                      |
| [`vocabulary`](#muvid.choreo.spec.vocabulary)()                   | Every closed vocabulary, for prompts and UI.                                          |

### Classes

| [`Direction`](#muvid.choreo.spec.Direction)(\*[, mood, palette, background, ...])   | The song-level creative decision.          |
|----------------------------------------------------------------------------------------------------|--------------------------------------------|
| [`Palette`](#muvid.choreo.spec.Palette)(\*[, bg, bg2, fg, low, mid, high])        | Colours as `#rrggbb`.                      |
| [`Scene`](#muvid.choreo.spec.Scene)(\*[, applies_to, archetype, params])        | One archetype applied to part of the song. |
| [`TreatmentSpec`](#muvid.choreo.spec.TreatmentSpec)(\*[, spec_version, title, ...])     | A complete, renderable treatment.          |

### muvid.choreo.spec.ARCHETYPES *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'fischinger': 'Geometric shapes ignite per onset on a grid — band picks the shape class (low: discs, mid: squares, high: triangles), strength the size, and each section a different arrangement. The Study No. 7 look.', 'mclaren': 'White scratches and marks on black, one per onset, jittered, gone within a fraction of a beat. Hand-scratched film.', 'star_guitar': 'A side-scrolling landscape: bass onsets are poles, mids are buildings, highs are wires. Everything scrolls left at one constant speed, so the spacing of the objects IS the rhythm. Sections change the sky.', 'swarm': 'Particles whose count and speed follow band energy, thrown from a band-specific origin and persisting as they slow. Continuous, organic.'}*

How events become objects. Each is a function in [`muvid.choreo.scene`](muvid.choreo.scene.html.md#module-muvid.choreo.scene).

### muvid.choreo.spec.ARCHETYPE_PARAMS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[float](https://docs.python.org/3/builtins/functions.html#float), [float](https://docs.python.org/3/builtins/functions.html#float), [float](https://docs.python.org/3/builtins/functions.html#float), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]]]* *= {'fischinger': {'columns': (6, 2, 24, 'grid columns'), 'hold_beats': (1.0, 0.1, 8.0, 'how long a shape lives, in beats'), 'rows': (3, 1, 12, 'grid rows')}, 'mclaren': {'life_beats': (0.25, 0.05, 2.0, 'how long a scratch lives, in beats')}, 'star_guitar': {'horizon': (0.72, 0.3, 0.95, 'ground line, fraction of height from the top'), 'speed': (0.35, 0.05, 2.0, 'scroll speed, canvas widths per second')}, 'swarm': {'life_s': (1.5, 0.2, 6.0, 'particle lifetime, seconds'), 'particles': (6, 1, 40, 'particles per event at normal density')}}*

Archetype parameters, documented once for the schema and the prompt. Each is
read by its archetype with the default given here and clamped to the range.

### muvid.choreo.spec.BACKGROUNDS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'gradient': 'Vertical gradient from palette.bg (top) to palette.bg2 (bottom).', 'solid': 'One flat colour (palette.bg).', 'vignette': 'palette.bg, darkened toward the corners.'}*

What is behind the objects.

### muvid.choreo.spec.DENSITIES *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'dense': 'Every onset marks, and each makes more.', 'normal': 'Most onsets mark.', 'sparse': 'Only strong onsets mark; few objects at a time.'}*

How many events become objects, and how many objects an event makes.

### muvid.choreo.spec.DENSITY_FACTOR *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [float](https://docs.python.org/3/builtins/functions.html#float)]* *= {'dense': 2.0, 'normal': 1.0, 'sparse': 0.5}*

Count multiplier per density, for the archetypes that spawn several per event.

### muvid.choreo.spec.DENSITY_GATE *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [float](https://docs.python.org/3/builtins/functions.html#float)]* *= {'dense': 0.0, 'normal': 0.15, 'sparse': 0.45}*

an event below it makes no object.

* **Type:**
  Strength gate per density

### *class* muvid.choreo.spec.Direction(\*, mood='', palette=<factory>, background='solid', density='normal', rationale='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The song-level creative decision.

### *class* muvid.choreo.spec.Palette(, bg='#0b0b12', bg2='#1b1b2e', fg='#f4f1e8', low='#e4572e', mid='#f3c623', high='#4cc9f0')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Colours as `#rrggbb`. `low`/`mid`/`high` are the band colours.

### *class* muvid.choreo.spec.Scene(\*, applies_to=('\*', ), archetype='fischinger', params=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One archetype applied to part of the song.

`applies_to` names section tiers (`low`/`mid`/`high`), section
indices as strings (`"2"`), or `"*"` for everything. A named scene wins
its sections over a `"*"` one; sections nobody names fall back to the
first `"*"` scene, and to the first scene if there is none.

### *class* muvid.choreo.spec.TreatmentSpec(\*, spec_version='1.0', title='', direction=<factory>, scenes=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A complete, renderable treatment.

```pycon
>>> s = TreatmentSpec()
>>> validate(s)
[]
>>> TreatmentSpec.from_dict(s.to_dict()) == s
True
```

#### *classmethod* from_dict(d)

Build from a plain mapping — TOTAL over what a model or caller sends.

* **Return type:**
  [`TreatmentSpec`](#muvid.choreo.spec.TreatmentSpec)

```pycon
>>> s = TreatmentSpec.from_dict({'scenes': [None, {'applies_to': 'high',
...     'archetype': 'swarm', 'params': 'nope'}],
...     'direction': {'palette': {'foo': 1, 'bg': '#000000'}, 'density': None}})
>>> s.scenes[1].applies_to, s.scenes[1].params, s.direction.palette.bg
(('high',), {}, '#000000')
```

### muvid.choreo.spec.coerce(obj)

Whatever a caller produced -> a renderable spec plus the repair notes.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`TreatmentSpec`](#muvid.choreo.spec.TreatmentSpec), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### muvid.choreo.spec.default_treatment(archetype='fischinger', \*\*direction)

A one-scene treatment for `archetype`; the CLI’s `--archetype` shortcut.

* **Return type:**
  [`TreatmentSpec`](#muvid.choreo.spec.TreatmentSpec)

```pycon
>>> default_treatment('mclaren').scenes[0].archetype
'mclaren'
```

### muvid.choreo.spec.json_schema()

The JSON Schema for a [`TreatmentSpec`](#muvid.choreo.spec.TreatmentSpec).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> json_schema()['properties']['scenes']['items']['properties']['archetype']['enum']
['fischinger', 'star_guitar', 'mclaren', 'swarm']
```

### muvid.choreo.spec.repair(spec)

Project `spec` onto the valid space; return `(spec, notes)`.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`TreatmentSpec`](#muvid.choreo.spec.TreatmentSpec), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

```pycon
>>> fixed, notes = repair(TreatmentSpec(scenes=(Scene(archetype='swirl',
...     params={'columns': 99, 'bogus': 1}),)))
>>> fixed.scenes[0].archetype, dict(fixed.scenes[0].params)
('fischinger', {'columns': 24})
>>> notes
["scenes[0].archetype 'swirl' -> 'fischinger'", 'scenes[0].params.columns 99 -> 24', "scenes[0].params dropped unknown ['bogus']"]
```

### muvid.choreo.spec.validate(spec)

Human-readable problems; empty means renderable.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

```pycon
>>> validate(TreatmentSpec(scenes=(Scene(archetype='nope'),)))
["scenes[0].archetype 'nope' is not one of the known archetypes"]
```

### muvid.choreo.spec.vocabulary()

Every closed vocabulary, for prompts and UI. One copy, several readers.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]
