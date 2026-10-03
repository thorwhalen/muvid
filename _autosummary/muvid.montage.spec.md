# muvid.montage.spec

The montage treatment spec — what a director (human or model) decides, as data.

Two layers, the same split as [`muvid.lyricvid.spec`](muvid.lyricvid.spec.md#module-muvid.lyricvid.spec) and for the same
reason: the half a model is good at is separated from the half it is not.

`direction`
: WHAT and WHY — a mood, the accent colour and grade, how the cutting should
  *feel* (a multiplier over the archetype’s own pacing), and the reuse policy
  that decides how a small pool carries a long song.

`scenes`
: HOW — an **archetype** from a closed set applied to named sections, plus a
  small closed set of parameters. Never a cut time, never a crop rectangle.

Three rules follow:

* **No timings from the model.** Every cut sits on a beat, a bar or a
  subdivision of the song’s measured grid; the spec names a density, Python
  applies it ([`muvid.montage.plan`](muvid.montage.plan.md#module-muvid.montage.plan)).
* **No geometry from the model.** Crops and moves come from a closed set of
  variants, chosen by the planner’s reuse policy so a revisited photo shows a
  different framing. A model that emitted rectangles would emit wrong ones.
* **Closed vocabularies everywhere**, so an invalid spec can be *projected*
  onto the valid space ([`repair()`](#muvid.montage.spec.repair)) rather than bounced back for a retry.

This module is stdlib-only and import-safe on purpose: it is on the listing
path of the plugin surface.

### Module Attributes

| [`ARCHETYPES`](#muvid.montage.spec.ARCHETYPES)       | How the pool is cut to the song.                                       |
|-------------------------------------------------------------------|------------------------------------------------------------------------|
| [`ARCHETYPE_PARAMS`](#muvid.montage.spec.ARCHETYPE_PARAMS) | The parameters each archetype accepts, as a small JSON Schema per key. |
| [`CUT_FEELS`](#muvid.montage.spec.CUT_FEELS)        | How the cutting should feel.                                           |
| [`CUT_FEEL_FACTORS`](#muvid.montage.spec.CUT_FEEL_FACTORS) | Multiplier on beats-per-cut per cut feel.                              |
| [`GRADES`](#muvid.montage.spec.GRADES)           | A colour grade applied to every frame.                                 |
| [`MOTIONS`](#muvid.montage.spec.MOTIONS)          | How a still moves within a slot.                                       |
| [`TRANSITIONS`](#muvid.montage.spec.TRANSITIONS)      | How a slot arrives.                                                    |
| [`SECTION_LABELS`](#muvid.montage.spec.SECTION_LABELS)   | Section labels the planner knows how to pace.                          |

### Functions

| [`coerce`](#muvid.montage.spec.coerce)(obj)            | Take whatever a caller or a model produced and return a plannable spec.               |
|-------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| [`json_schema`](#muvid.montage.spec.json_schema)()          | The JSON Schema for a [`TreatmentSpec`](#muvid.montage.spec.TreatmentSpec). |
| [`one_scene`](#muvid.montage.spec.one_scene)([archetype]) | The treatment a caller gets from `archetype=` alone.                                  |
| [`repair`](#muvid.montage.spec.repair)(spec)           | Project `spec` onto the valid space.                                                  |
| [`validate`](#muvid.montage.spec.validate)(spec)         | Return a list of human-readable problems.                                             |
| [`vocabulary`](#muvid.montage.spec.vocabulary)()           | Every closed vocabulary, for prompts and UI.                                          |

### Classes

| [`Direction`](#muvid.montage.spec.Direction)(\*[, mood, palette, cut_feel, ...])   | The song-level creative decision.                        |
|--------------------------------------------------------------------------------------------------|----------------------------------------------------------|
| [`Palette`](#muvid.montage.spec.Palette)(\*[, bg, accent])                       | Colours, as `#rrggbb`.                                   |
| [`Reuse`](#muvid.montage.spec.Reuse)(\*[, min_gap, reserve_for_finale])        | The reuse POLICY — how a small pool carries a long song. |
| [`Scene`](#muvid.montage.spec.Scene)(\*[, applies_to, archetype, params])      | One archetype, applied to part of the song.              |
| [`TreatmentSpec`](#muvid.montage.spec.TreatmentSpec)(\*[, spec_version, title, ...])   | A complete, plannable treatment.                         |

### muvid.montage.spec.ARCHETYPES *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'ballad_dissolve': 'Cut every 2-4 bars on a downbeat, one-beat crossfades, a slow Ken Burns drift on each still. For slow songs and quiet sections.', 'beat_cut': "Hard cuts on the grid — every beat or half-bar in a chorus, every bar in a verse — with a punch-zoom on each cut. The 'photo beat sync' look.", 'grid': 'A 2x2 grid of tiles; one tile swaps on every beat. Dense and busy; wants a pool of eight or more.', 'stop_motion': 'Stills held for a beat subdivision with no motion at all — a flip-book. Mechanical, playful.'}*

How the pool is cut to the song. The planner owns the timing and geometry;
the spec only names the family and its knobs.

### muvid.montage.spec.ARCHETYPE_PARAMS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any)]]]* *= {'ballad_dissolve': {'bars_per_cut': {'default': 4, 'description': 'Bars between cuts in a verse; a chorus halves it.', 'maximum': 16, 'minimum': 1, 'type': 'number'}, 'drift': {'default': 0.08, 'description': 'Ken Burns amplitude as a fraction of the frame.', 'maximum': 0.3, 'minimum': 0, 'type': 'number'}, 'fade_beats': {'default': 1, 'description': 'Crossfade length in beats. 0 is a hard cut.', 'maximum': 4, 'minimum': 0, 'type': 'number'}}, 'beat_cut': {'beats_per_cut': {'default': 4, 'description': 'Beats between cuts in a verse; a chorus halves it.', 'maximum': 16, 'minimum': 1, 'type': 'number'}, 'punch': {'default': 0.12, 'description': 'Punch-zoom amount on each cut (0 disables).', 'maximum': 0.3, 'minimum': 0, 'type': 'number'}}, 'grid': {'beats_per_swap': {'default': 2, 'description': 'Beats between tile swaps in a verse; a chorus halves it.', 'maximum': 16, 'minimum': 1, 'type': 'number'}}, 'stop_motion': {'subdivision': {'default': 2, 'description': 'Holds per beat (2 = eighth notes). A chorus doubles it, up to 4.', 'maximum': 4, 'minimum': 1, 'type': 'integer'}}}*

The parameters each archetype accepts, as a small JSON Schema per key. A key
outside this table is a validation error and is dropped by [`repair()`](#muvid.montage.spec.repair);
a value outside its range is clamped. Closed, so a plugin UI can render it.

### muvid.montage.spec.CUT_FEELS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'driving': 'Twice as many cuts. Pushes forward.', 'frantic': "Four times as many cuts, floored at the archetype's minimum.", 'slow': "Half as many cuts as the archetype's default. Contemplative.", 'steady': "The archetype's own pacing."}*

How the cutting should feel. A MULTIPLIER over each archetype’s own pacing,
so the same treatment reads “slow” on a ballad and “driving” on a banger
without the model naming a number of beats.

### muvid.montage.spec.CUT_FEEL_FACTORS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [float](https://docs.python.org/3/builtins/functions.html#float)]* *= {'driving': 0.5, 'frantic': 0.25, 'slow': 2.0, 'steady': 1.0}*

Multiplier on beats-per-cut per cut feel. Smaller is more cuts.

### *class* muvid.montage.spec.Direction(\*, mood='', palette=<factory>, cut_feel='steady', grade='none', reuse=<factory>, rationale='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The song-level creative decision.

### muvid.montage.spec.GRADES *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'mono': 'Desaturate to black and white.', 'none': 'The pool as shot.', 'tint': "Blend the palette's accent colour into the frame (a duotone-ish wash)."}*

A colour grade applied to every frame. Closed because the grade is
EXECUTABLE ffmpeg; the palette’s accent parameterises it, never a filter string.

### muvid.montage.spec.MOTIONS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'none': 'Held still.', 'pan_left': 'A slow drift leftward.', 'pan_right': 'A slow drift rightward.', 'punch': 'Arrives zoomed in and relaxes to rest within half a beat.', 'zoom_in': 'A slow push in.', 'zoom_out': 'A slow pull out.'}*

How a still moves within a slot. Assigned by the ARCHETYPE, never by the
spec — listed here so the plan’s vocabulary is closed and documented.

### *class* muvid.montage.spec.Palette(, bg='#000000', accent='#e0533d')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Colours, as `#rrggbb`. `accent` parameterises the `tint` grade;
`bg` is the letterbox/pad colour when a tile does not fill its region.

### *class* muvid.montage.spec.Reuse(, min_gap=6, reserve_for_finale=True)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The reuse POLICY — how a small pool carries a long song.

`min_gap` is how many cuts must pass before an image may return (it
shrinks automatically to `pool - 1` for a small pool). Every return uses
a different crop/move variant. `reserve_for_finale` holds the strongest
quarter of the pool back for the last chorus.

### muvid.montage.spec.SECTION_LABELS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'bridge': 'Chorus density.', 'chorus': 'Dense cuts; the last chorus gets the strongest images.', 'intro': 'Sparse cuts.', 'outro': 'Sparse cuts.', 'verse': "The archetype's verse pacing."}*

Section labels the planner knows how to pace. Others are paced as a verse.

### *class* muvid.montage.spec.Scene(\*, applies_to=('\*', ), archetype='beat_cut', params=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One archetype, applied to part of the song.

`applies_to` names sections by label, or `"*"` for the whole song. A
named scene wins its section; sections no scene names fall back to the
first `"*"` scene, so a one-scene spec is valid and complete.

### muvid.montage.spec.TRANSITIONS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'cut': 'A hard cut.', 'dissolve': 'A noisy dissolve.', 'fade': 'A crossfade.', 'fadeblack': 'Dip to black.'}*

How a slot arrives. A curated subset of ffmpeg’s `xfade` transitions, the
same posture as `muvid.footage.edl.TRANSITION_CURVES`: a name outside it is
refused here rather than discovered as an ffmpeg error three stages later.

### *class* muvid.montage.spec.TreatmentSpec(\*, spec_version='1.0', title='', direction=<factory>, scenes=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A complete, plannable treatment.

```pycon
>>> s = TreatmentSpec()
>>> validate(s)
[]
>>> TreatmentSpec.from_dict(s.to_dict()) == s
True
```

#### *classmethod* from_dict(d)

Build from a plain mapping — TOTAL over what a model or a remote caller sends.

Missing keys default; wrong-shaped values are coerced toward the
field’s type rather than raised on; unknown keys are dropped. What
cannot be coerced falls to the default and [`repair()`](#muvid.montage.spec.repair) reports the
vocabulary-level substitutions afterwards.

* **Return type:**
  [`TreatmentSpec`](#muvid.montage.spec.TreatmentSpec)

```pycon
>>> s = TreatmentSpec.from_dict({'scenes': [None, {'applies_to': 'chorus',
...     'archetype': 'grid', 'params': 'fast'}],
...     'direction': {'palette': {'foo': 1, 'accent': '#00ff00'},
...                   'reuse': {'min_gap': '3'}}})
>>> s.scenes[1].applies_to, s.scenes[1].archetype, dict(s.scenes[1].params)
(('chorus',), 'grid', {})
>>> s.direction.palette.accent, s.direction.reuse.min_gap
('#00ff00', 3)
```

#### to_dict()

A JSON-native dict: tuples become lists, so what this emits is
exactly what [`json_schema()`](#muvid.montage.spec.json_schema) validates and what a file round-trips.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.montage.spec.coerce(obj)

Take whatever a caller or a model produced and return a plannable spec.

Accepts a [`TreatmentSpec`](#muvid.montage.spec.TreatmentSpec), a mapping, or a JSON string — and repairs
it. This is the one entry point production code should use.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`TreatmentSpec`](#muvid.montage.spec.TreatmentSpec), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

```pycon
>>> spec, notes = coerce('{"scenes": [{"archetype": "grid"}]}')
>>> spec.scenes[0].archetype, notes
('grid', [])
```

### muvid.montage.spec.json_schema()

The JSON Schema for a [`TreatmentSpec`](#muvid.montage.spec.TreatmentSpec).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> s = json_schema()
>>> s['properties']['scenes']['items']['properties']['archetype']['enum'][0]
'ballad_dissolve'
```

### muvid.montage.spec.one_scene(archetype='beat_cut', \*\*direction)

The treatment a caller gets from `archetype=` alone.

* **Return type:**
  [`TreatmentSpec`](#muvid.montage.spec.TreatmentSpec)

```pycon
>>> one_scene('grid', cut_feel='driving').scenes[0].archetype
'grid'
```

### muvid.montage.spec.repair(spec)

Project `spec` onto the valid space. Returns `(spec, notes)`.

A model that names a grade that does not exist has made a small, mechanical
mistake; projecting it onto the default renders something good now, where a
retry costs a round trip. Every substitution is reported.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`TreatmentSpec`](#muvid.montage.spec.TreatmentSpec), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

```pycon
>>> fixed, notes = repair(TreatmentSpec(scenes=(Scene(archetype='swirl'),)))
>>> fixed.scenes[0].archetype
'beat_cut'
>>> notes
["scenes[0].archetype 'swirl' -> 'beat_cut'"]
>>> fixed, notes = repair(TreatmentSpec(scenes=(
...     Scene(archetype='beat_cut', params={'punch': 9, 'nope': 1}),)))
>>> dict(fixed.scenes[0].params), notes
({'punch': 0.3}, ['scenes[0].params.punch 9 -> 0.3', "scenes[0].params dropped unknown ['nope']"])
```

### muvid.montage.spec.validate(spec)

Return a list of human-readable problems. Empty means plannable.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

```pycon
>>> validate(TreatmentSpec(scenes=(Scene(archetype='nope'),)))
["scenes[0].archetype 'nope' is not one of the known archetypes"]
>>> validate(TreatmentSpec(scenes=(Scene(archetype='grid', params={'punch': 1}),)))
["scenes[0].params 'punch' is not a parameter of 'grid' (allowed: ['beats_per_swap'])"]
```

### muvid.montage.spec.vocabulary()

Every closed vocabulary, for prompts and UI. One copy, several readers.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]
