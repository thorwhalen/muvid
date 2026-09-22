# muvid.lyricvid.spec

The treatment spec — what a director (human or model) decides, as data.

This is the SSOT both runtimes share: the Claude Code skill and the production
LLM call produce *this*, and every renderer consumes *this*. It is stdlib-only
and import-safe on purpose.

The shape is two layers, and the split is the single most important decision in
the whole subgenre:

`direction`
: WHAT and WHY — mood, palette, typography, motion vocabulary, and a
  rationale a human can read when choosing between options. This is the part
  a model is genuinely good at.

`scenes`
: HOW — an **archetype** from a closed set, plus parameters. Never raw
  geometry, never raw timings.

Three rules follow, and they are not stylistic:

* **No coordinates from the model.** Every archetype computes its own geometry
  in Python. The *Visual Lyrics* authors reached this the hard way and reported
  that LLM-generated bounding boxes “often result in layouts with misalignment
  and overlap issues”; the archetype removes the need to generate one at all.
* **No timings from the model.** Word onsets, the beat grid and section bounds
  come from muvid’s aligner. The model picks a *quantisation policy*
  (`Quantize`) and Python applies it. This kills the entire class of
  “the words drift out of sync” bugs by construction.
* **Closed vocabularies everywhere.** Archetypes, motion families, easings and
  cut styles are enums. Closed sets are what make the schema constrainable, two
  options diffable, and a renderer total rather than best-effort.

An invalid spec is not an error to hand back to a model when it can be
*projected* onto the valid space instead — see [`repair()`](#muvid.lyricvid.spec.repair). Deterministic
repair is cheaper, faster and more predictable than a retry, and it means a
slightly-wrong model output still renders.

### Module Attributes

| [`ARCHETYPES`](#muvid.lyricvid.spec.ARCHETYPES)         | How words are placed on screen.                                       |
|---------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`MOTIONS`](#muvid.lyricvid.spec.MOTIONS)            | How a single word arrives.                                            |
| [`PERSISTENCE`](#muvid.lyricvid.spec.PERSISTENCE)        | What a word does when it is no longer current.                        |
| [`QUANTIZE`](#muvid.lyricvid.spec.QUANTIZE)           | What the animation clock is quantised to.                             |
| [`SHAPE_KINDS`](#muvid.lyricvid.spec.SHAPE_KINDS)        | Where a shape outline may come from.                                  |
| [`INLINE_SHAPE_KINDS`](#muvid.lyricvid.spec.INLINE_SHAPE_KINDS) | The shape kinds that carry no reference to anything outside the spec. |

### Functions

| [`coerce`](#muvid.lyricvid.spec.coerce)(obj)    | Take whatever a caller or a model produced and return a renderable spec.              |
|-----------------------------------------------------------------|---------------------------------------------------------------------------------------|
| [`json_schema`](#muvid.lyricvid.spec.json_schema)()  | The JSON Schema for a [`TreatmentSpec`](#muvid.lyricvid.spec.TreatmentSpec). |
| [`repair`](#muvid.lyricvid.spec.repair)(spec)   | Project `spec` onto the valid space.                                                  |
| [`validate`](#muvid.lyricvid.spec.validate)(spec) | Return a list of human-readable problems.                                             |
| [`vocabulary`](#muvid.lyricvid.spec.vocabulary)()   | Every closed vocabulary, for prompts and UI.                                          |

### Classes

| [`Direction`](#muvid.lyricvid.spec.Direction)(\*[, mood, palette, typography, ...])   | The song-level creative decision.                          |
|----------------------------------------------------------------------------------------------------|------------------------------------------------------------|
| [`Palette`](#muvid.lyricvid.spec.Palette)(\*[, bg, fg, accent, dim])                | Colours, as `#rrggbb`.                                     |
| [`Scene`](#muvid.lyricvid.spec.Scene)(\*[, applies_to, archetype, motion, ...])   | One treatment, applied to part of the song.                |
| [`ShapeRef`](#muvid.lyricvid.spec.ShapeRef)(\*[, kind, value])                       | Where a `shape_fill` / `concrete_page` outline comes from. |
| [`Timing`](#muvid.lyricvid.spec.Timing)(\*[, quantize_to, cut_style, ...])         | The quantisation POLICY.                                   |
| [`TreatmentSpec`](#muvid.lyricvid.spec.TreatmentSpec)(\*[, spec_version, title, ...])     | A complete, renderable treatment.                          |
| [`Typography`](#muvid.lyricvid.spec.Typography)(\*[, family, weight, case, ...])       | Type choices.                                              |

### muvid.lyricvid.spec.ARCHETYPES *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'calligram': "Each line becomes a slanting streak of UPRIGHT letters, one letter per slot, the streaks fanning open as they descend — the Apollinaire 'Il pleut' construction. Use for a calligram or concrete poem whose shape is made by the run of the text itself rather than by an outline; prefer 'shape_fill' when the shape is a picture the words pour into, and 'concrete_page' when the layout is simply lines on a page.", 'concrete_page': "The whole lyric is typeset as a fixed page — one CENTRED HORIZONTAL ROW per line — and each word ignites in reading order as it is sung. The page never reflows. Use when the poem is lines on a page. It cannot slant, indent or shape anything: for a calligram or a concrete poem whose picture is made by the run of the text, use 'calligram'; for words poured into an outline, use 'shape_fill'.", 'karaoke_wipe': 'Two lines at the bottom, the current one wiped syllable by syllable as it is sung. The classic karaoke treatment; the most legible option.', 'one_word_centred': 'One word at a time, large, centred. The default lyric-video look: unmissable, works at any aspect ratio, reads on a phone.', 'scatter': 'Words appear away from centre and drift, density rising with energy. Use for chaos, crowds, or an instrumental-heavy chorus.', 'shape_fill': 'Words packed into the outline of a shape, filling it as the song proceeds. Use when the song has one strong concrete image.', 'stacked_lines': 'Lines accumulate down the frame and hold, so the viewer can read back what has already been sung. Good for narrative or dense lyrics.', 'text_on_path': 'Words follow a curve across the frame. Cheap, distinctive, and good for a single repeated hook.'}*

How words are placed on screen. The renderer owns the geometry; the spec
only names the family and its knobs.

### *class* muvid.lyricvid.spec.Direction(\*, mood='', palette=<factory>, typography=<factory>, motion_vocabulary=('fade', ), rationale='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The song-level creative decision. The half a model is actually good at.

### muvid.lyricvid.spec.INLINE_SHAPE_KINDS *: [frozenset](https://docs.python.org/3/builtins/stdtypes.html#frozenset)[[str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= frozenset({'named', 'svg_path'})*

The shape kinds that carry no reference to anything outside the spec. A
surface serving untrusted callers (the MCP tools) admits ONLY these unless it
has itself fetched and scoped the image.

### muvid.lyricvid.spec.MOTIONS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'cut': 'Appears instantly. Hardest, most rhythmic.', 'fade': 'Fades up over a fraction of a beat.', 'pop': 'Fades up with a slight overshoot in scale, then settles.', 'rise': 'Fades up while moving a short distance upward.', 'typewriter': "Letters appear one at a time across the word's duration.", 'wipe': 'Revealed left-to-right, like a karaoke wipe.'}*

How a single word arrives. Composable with the archetype rather than part of it.

### muvid.lyricvid.spec.PERSISTENCE *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'clear_on_line': 'Cleared when its line ends.', 'clear_on_section': 'Cleared when its section ends.', 'dim': 'Stays but recedes, so the current word leads. Keeps context readable.', 'hold': 'Stays exactly as it arrived, forever. The page fills up.'}*

What a word does when it is no longer current.

### *class* muvid.lyricvid.spec.Palette(, bg='#101014', fg='#f4f4f0', accent='#e0533d', dim='#4a4a52')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Colours, as `#rrggbb`. `dim` is the un-sung state where one exists.

### muvid.lyricvid.spec.QUANTIZE *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'beat': 'Snapped to the nearest beat. Rhythmic, forgiving of alignment error.', 'downbeat': 'Snapped to the nearest bar start. Slow, deliberate.', 'line': "The whole line arrives together, at the line's start.", 'syllable': 'Sub-word timing, where the aligner provides it.', 'word': 'Each word ignites at its own measured onset. Tightest sync.'}*

What the animation clock is quantised to. The MODEL picks one of these; the
numbers behind them always come from measurement, never from the model.

### muvid.lyricvid.spec.SHAPE_KINDS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= {'mask_image': 'An image whose dark ink (or alpha) is the outline. Trusted callers only: the value names a file.', 'named': 'A built-in outline: circle, heart, star, apple, square.', 'svg_path': 'An SVG path string (M/L/H/V/C/S/Q/T/Z) supplied inline.'}*

Where a shape outline may come from. A closed set for the same reason the
others are — and additionally because `value` is INTERPRETED by the
renderer, which makes `kind` a trust boundary: `mask_image` names a file
to open, and a treatment that arrives from a remote caller must not be able
to point that at a host path. The spec only admits the kinds; where a
`mask_image` value is allowed to come FROM is the caller-facing surface’s
decision (see `MASK_IMAGE_TRUSTED`).

### *class* muvid.lyricvid.spec.Scene(\*, applies_to=('\*', ), archetype='one_word_centred', motion='fade', persistence='clear_on_line', timing=<factory>, shape=None, params=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One treatment, applied to part of the song.

`applies_to` names sections by label (as the lyrics document labels them),
or `"*"` for the whole song. Sections muvid found but the spec does not
mention fall back to the first `"*"` scene, so a one-scene spec is valid
and complete.

### *class* muvid.lyricvid.spec.ShapeRef(, kind='named', value='circle')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Where a `shape_fill` / `concrete_page` outline comes from.

`kind='named'` uses a built-in outline; `kind='mask_image'` traces an
image the caller supplied; `kind='svg_path'` takes a path directly. The
model may name a shape, but it never draws one.

### *class* muvid.lyricvid.spec.Timing(, quantize_to='word', cut_style='hard', attack_s=0.12, lead_s=0.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The quantisation POLICY. Never actual times.

#### attack_s *: [float](https://docs.python.org/3/builtins/functions.html#float)*

Seconds a word takes to arrive. Small, or it stops reading as on-the-beat.

#### lead_s *: [float](https://docs.python.org/3/builtins/functions.html#float)*

Seconds before a word’s onset to start it. Compensates for perceived lag.

### *class* muvid.lyricvid.spec.TreatmentSpec(\*, spec_version='1.0', title='', direction=<factory>, scenes=<factory>)

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

Build from a plain mapping — TOTAL over what a model or a remote caller sends.

Missing keys default. Wrong-shaped values are coerced toward the field’s
type rather than raised on: a string where a list was expected becomes
a one-element list (`applies_to: "chorus"` used to become
`('c','h','o','r','u','s')`), a numeric string becomes a number, an
unknown key is dropped, a `None` sub-object is the default. What
cannot be coerced falls to the default and [`repair()`](#muvid.lyricvid.spec.repair) reports the
enum-level substitutions afterwards.

* **Return type:**
  [`TreatmentSpec`](#muvid.lyricvid.spec.TreatmentSpec)

```pycon
>>> s = TreatmentSpec.from_dict({'scenes': [None, {'applies_to': 'chorus',
...     'timing': 'fast', 'shape': 'circle'}],
...     'direction': {'palette': {'foo': 1, 'bg': '#000000'},
...                   'motion_vocabulary': 'pop',
...                   'typography': {'weight': '700', 'tracking': None}}})
>>> s.scenes[1].applies_to, s.direction.motion_vocabulary
(('chorus',), ('pop',))
>>> s.direction.typography.weight, s.direction.typography.tracking
(700, 0.0)
>>> s.direction.palette.bg, s.scenes[1].shape
('#000000', None)
```

#### to_dict()

A JSON-native dict: tuples become lists, so what this emits is
exactly what [`json_schema()`](#muvid.lyricvid.spec.json_schema) validates and what a file round-trips.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### *class* muvid.lyricvid.spec.Typography(, family='DejaVu Sans', weight=700, case='as_written', tracking=0.0, max_line_chars=28)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Type choices. `family` is resolved against installed/bundled fonts, and
an unavailable family falls back rather than failing the render.

### muvid.lyricvid.spec.coerce(obj)

Take whatever a caller or a model produced and return a renderable spec.

Accepts a [`TreatmentSpec`](#muvid.lyricvid.spec.TreatmentSpec), a mapping, or a JSON string — and repairs
it. This is the one entry point production code should use.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`TreatmentSpec`](#muvid.lyricvid.spec.TreatmentSpec), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### muvid.lyricvid.spec.json_schema()

The JSON Schema for a [`TreatmentSpec`](#muvid.lyricvid.spec.TreatmentSpec).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> s = json_schema()
>>> s['properties']['scenes']['items']['properties']['archetype']['enum'][0]
'one_word_centred'
```

### muvid.lyricvid.spec.repair(spec)

Project `spec` onto the valid space. Returns `(spec, notes)`.

A model that names a motion that doesn’t exist has made a small, mechanical
mistake; projecting it onto the nearest legal value renders something good
now, where a retry costs a round trip and may fail the same way. Every
substitution is reported so the caller can show or log it.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`TreatmentSpec`](#muvid.lyricvid.spec.TreatmentSpec), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

```pycon
>>> fixed, notes = repair(TreatmentSpec(scenes=(Scene(archetype='swirl'),)))
>>> fixed.scenes[0].archetype
'one_word_centred'
>>> notes
["scenes[0].archetype 'swirl' -> 'one_word_centred'"]
```

### muvid.lyricvid.spec.validate(spec)

Return a list of human-readable problems. Empty means renderable.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

```pycon
>>> validate(TreatmentSpec(scenes=(Scene(archetype='nope'),)))
["scenes[0].archetype 'nope' is not one of the known archetypes"]
```

### muvid.lyricvid.spec.vocabulary()

Every closed vocabulary, for prompts and UI. One copy, several readers.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]
