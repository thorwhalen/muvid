# muvid.lyricvid

Lyric video (kinetic typography) — muvid’s first subgenre plugin.

Turn a song into a typographic music video: the words appear in time with the
singing, laid out by an archetype chosen for the song.

> from muvid.lyricvid import tools
> tools.render_lyric_video(‘song.wav’, ‘out.mp4’, lyrics=’lyrics.md’)

or from the command line:

```default
python -m muvid.lyricvid render song.wav out.mp4 --lyrics lyrics.md
```

The pipeline is four seams, each with a default that genuinely works:

`timed_text`
: measured word times — reuses muvid’s own aligner and lacing store rather
  than growing a second transcription path.

`director`
: the treatment decision. Default is a heuristic that reads the lyrics and
  costs nothing; an LLM creative director is opt-in.

`scene`
: every position and time, computed in Python from measurement. No model ever
  emits a coordinate or a timestamp.

`renderer`
: `ass` by default (frame-exact, no browser, leaves an editable subtitle
  file); `web` for effects ASS cannot express.

This module is a PEP 562 lazy facade, like `muvid/__init__.py`: importing it
pulls nothing heavy, and each attribute is resolved on first use.

### *class* muvid.lyricvid.Canvas(, width=1920, height=1080, fps=30)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Output geometry. Sizes in the scene are relative to `height`.

### *class* muvid.lyricvid.Scene(\*, canvas, duration, background, cues, typography, meta=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything a renderer needs, and nothing it has to interpret.

#### meta *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any)]*

Provenance, for reporting and for tests.

### *class* muvid.lyricvid.TimedText(, sections, duration=0.0, source='unknown')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The whole song’s text, timed.

```pycon
>>> tt = from_words([('hello', 0.0, 0.5), ('world', 0.5, 1.0)], duration=1.0)
>>> [w.text for w in tt.words()]
['hello', 'world']
>>> tt.sections[0].label
'*'
```

#### *property* measured *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True when every word time was measured rather than interpolated.

False for an empty text: “all of nothing was measured” is the kind of
vacuous truth that reads as reassurance in a report.

#### source *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

Where the timing came from, for provenance and for honest reporting.

### *class* muvid.lyricvid.TreatmentSpec(\*, spec_version='1.0', title='', direction=<factory>, scenes=<factory>)

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
cannot be coerced falls to the default and `repair()` reports the
enum-level substitutions afterwards.

* **Return type:**
  [`TreatmentSpec`](muvid.lyricvid.spec.md#muvid.lyricvid.spec.TreatmentSpec)

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
exactly what `json_schema()` validates and what a file round-trips.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.lyricvid.compile_scene(treatment, timed_text, , canvas=None)

Turn a treatment plus timed text into a fully-resolved [`Scene`](#muvid.lyricvid.Scene).

Every number in the result was computed here, from measurement. Nothing the
model wrote reaches a renderer as a coordinate or a time.

* **Return type:**
  [`Scene`](muvid.lyricvid.scene.md#muvid.lyricvid.scene.Scene)

```pycon
>>> from muvid.lyricvid.timed_text import from_words
>>> tt = from_words([('one', 0.0, .5), ('two', .5, 1.0)], duration=1.0)
>>> s = compile_scene(spec_mod.TreatmentSpec(), tt)
>>> len(s.cues), s.cues[0].text
(2, 'one')
```

### muvid.lyricvid.render_lyric_video(audio, output, , lyrics=None, subtitles=None, project=None, treatment=None, renderer='auto', title='', persona=None, aligner=None, width=1920, height=1080, fps=30, workdir=None)

Render a lyric video. The one verb that produces a file.

Everything else in this module exists so that a caller can decide *what* to
render before paying for it.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### Modules

| [`director`](muvid.lyricvid.director.md#module-muvid.lyricvid.director)     | The creative director — a song in, one or more [`TreatmentSpec`](#muvid.lyricvid.TreatmentSpec)s out.                                                                      |
|----------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`manifest`](muvid.lyricvid.manifest.md#module-muvid.lyricvid.manifest)     | The lyric-video subgenre's manifest — muvid's own first plugin.                                                                                                                          |
| [`pipeline`](muvid.lyricvid.pipeline.md#module-muvid.lyricvid.pipeline)     | The lyric-video pipeline — the one path from a song to a finished video.                                                                                                                 |
| [`render_ass`](muvid.lyricvid.render_ass.md#module-muvid.lyricvid.render_ass) | The default lyric-video renderer: a [`Scene`](muvid.lyricvid.scene.md#muvid.lyricvid.scene.Scene) as ASS.                                                            |
| [`render_web`](muvid.lyricvid.render_web.md#module-muvid.lyricvid.render_web) | The web backend: a [`Scene`](muvid.lyricvid.scene.md#muvid.lyricvid.scene.Scene) as a deterministic HTML page, screenshotted frame by frame and muxed with the song. |
| [`scene`](muvid.lyricvid.scene.md#module-muvid.lyricvid.scene)           | The renderer-neutral scene — every number computed, no renderer opinions.                                                                                                                |
| [`shape`](muvid.lyricvid.shape.md#module-muvid.lyricvid.shape)           | Packing words INSIDE a shape — the shape-word-cloud construction.                                                                                                                        |
| [`spec`](muvid.lyricvid.spec.md#module-muvid.lyricvid.spec)             | The treatment spec — what a director (human or model) decides, as data.                                                                                                                  |
| [`timed_text`](muvid.lyricvid.timed_text.md#module-muvid.lyricvid.timed_text) | The timed text tree — song → sections → lines → words, with measured times.                                                                                                              |
| [`tools`](muvid.lyricvid.tools.md#module-muvid.lyricvid.tools)           | The SSOT verbs for the lyric-video subgenre.                                                                                                                                             |
