# muvid.montage

Montage — a beat-cut video from a pool of stills and clips (subgenre plugin).

The STANDARD kind of music video for material that has no timeline of its
own: photos and short clips cut to the song’s beat grid and section
structure — what CapCut’s “photo beat sync” templates, Animoto and Rotor sell.

> from muvid.montage import tools
> tools.render_montage(‘song.wav’, ‘out.mp4’, photos=[‘a.jpg’, ‘b.jpg’, ‘c.jpg’])

or from the command line:

```default
python -m muvid.montage render song.wav out.mp4 --photos a.jpg b.jpg c.jpg
```

Four seams, each with a default that genuinely works:

`analysis`
: the beat grid (`mixing.audio.beat_grid` where librosa is installed, a
  numpy estimator otherwise — and the plan says which), downbeats, and
  coarse sections from energy when none are supplied.

`spec`
: the treatment: a direction (accent, grade, cut feel, reuse policy) and
  scenes naming an archetype from a closed set per section.

`plan`
: the PLANNER — the core of the subgenre. Pool + grid + sections -> an edit
  list, with an explicit reuse policy so twelve photos carry a three-minute
  song. Pure and deterministic; written out as `plan.json` beside every render.

`render`
: ffmpeg in bounded stages: per-slot `zoompan`/trim parts, `xfade`
  blends, `xstack` grids, stream-copy concat, one YouTube-spec mux.

This module is a PEP 562 lazy facade, like `muvid/__init__.py`: importing it
pulls nothing heavy, and each attribute is resolved on first use.

### *class* muvid.montage.Analysis(\*, duration, tempo_bpm, beats, downbeats, beats_per_bar=4, sections=(), beat_source='', section_source='', bar_energy_db=(), notes=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What the planner knows about the song.

#### *property* beat_s *: [float](https://docs.python.org/3/builtins/functions.html#float)*

Seconds per beat at the estimated tempo.

### *class* muvid.montage.Canvas(, width=1920, height=1080, fps=30)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Output geometry.

### *class* muvid.montage.Plan(\*, duration, tempo_bpm, beats_per_bar, beat_source, section_source, sections, media, slots, reuse=<factory>, treatment=<factory>, notes=(), plan_version='1.0')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The edit list. JSON-able, canvas-independent (windows are normalised).

#### *classmethod* from_dict(d)

Read a plan back — a hand-edited `plan.json` renders the same way.

* **Return type:**
  [`Plan`](muvid.montage.plan.html.md#muvid.montage.plan.Plan)

### *class* muvid.montage.TreatmentSpec(\*, spec_version='1.0', title='', direction=<factory>, scenes=<factory>)

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
cannot be coerced falls to the default and `repair()` reports the
vocabulary-level substitutions afterwards.

* **Return type:**
  [`TreatmentSpec`](muvid.montage.spec.html.md#muvid.montage.spec.TreatmentSpec)

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
exactly what `json_schema()` validates and what a file round-trips.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.montage.analyze_song(audio, , beats='auto', beats_per_bar=4, sections=None)

Measure a song: tempo, beat grid, downbeats, bar energy and sections.

The payload says WHERE the beats came from (`beat_source`) and whether
the sections were supplied or derived from energy — a caller that ignores
that will trust a fixed-tempo grid on a rubato ballad.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.montage.plan_montage(audio, , photos=(), clips=(), cover=None, treatment=None, archetype=None, strict=False, beats='auto', beats_per_bar=4, sections=None, out=None)

Plan the montage without rendering it: the edit list, as JSON.

Identical to what [`render_montage()`](#muvid.montage.render_montage) would render. `out` writes it
to a file as well.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.montage.render_montage(audio, output, , photos=(), clips=(), cover=None, treatment=None, archetype=None, strict=False, beats='auto', beats_per_bar=4, sections=None, width=1920, height=1080, fps=30, workdir=None)

Render a montage. The one verb that produces a file.

Goes through [`muvid.subgenres.render_subgenre()`](muvid.subgenres.html.md#muvid.subgenres.render_subgenre) when the manifest is
registered (so the schemas are enforced), and straight to the pipeline
otherwise — the same renderer either way.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### Modules

| [`analysis`](muvid.montage.analysis.html.md#module-muvid.montage.analysis)   | Measure the song and the pool: beat grid, bars, sections, and media strength.                                             |
|-------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| [`manifest`](muvid.montage.manifest.html.md#module-muvid.montage.manifest)   | The montage subgenre's manifest — a beat-cut montage from a pool of stills and clips.                                     |
| [`pipeline`](muvid.montage.pipeline.html.md#module-muvid.montage.pipeline)   | The montage pipeline — the one path from a song and a pool to a finished video.                                           |
| [`plan`](muvid.montage.plan.html.md#module-muvid.montage.plan)           | The planner: a pool of media + a beat grid + sections -> an edit list of slots.                                           |
| [`render`](muvid.montage.render.html.md#module-muvid.montage.render)       | Render a [`Plan`](muvid.montage.plan.html.md#muvid.montage.plan.Plan) to a YouTube-spec mp4 with ffmpeg. |
| [`spec`](muvid.montage.spec.html.md#module-muvid.montage.spec)           | The montage treatment spec — what a director (human or model) decides, as data.                                           |
| [`tools`](muvid.montage.tools.html.md#module-muvid.montage.tools)         | The SSOT verbs for the montage subgenre: analyze, plan, render.                                                           |
