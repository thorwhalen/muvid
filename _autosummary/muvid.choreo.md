# muvid.choreo

Choreo — event-driven visual music, muvid’s second subgenre plugin.

Audio in, nothing else. Onsets in three frequency bands become objects that
appear ON the event and then live — Fischinger’s igniting shapes, Gondry’s
*Star Guitar* landscape whose spacing is the rhythm, McLaren’s scratches on
black, or a swarm — and the song’s sections change the arrangement.

> from muvid.choreo import tools
> tools.render_choreo(‘song.wav’, ‘out.mp4’, archetype=’star_guitar’)

or from the command line:

```default
python -m muvid.choreo render song.wav out.mp4 --archetype star_guitar
```

The pipeline is four seams, each with a default that genuinely works:

`analysis`
: the event list, beat grid and sections — numpy only; `mixing`’s
  librosa beat tracker when installed, a built-in estimate otherwise.

`spec`
: the treatment: direction {palette, background, density} + scenes[]
  {applies_to, archetype, params}. Closed vocabularies; repair, not reject.

`scene`
: every object’s birth, death, shape, place and motion — computed in Python,
  deterministic given a seed.

`render`
: numpy frames piped into ffmpeg; a YouTube-spec mp4 with the song muxed.

This module is a PEP 562 lazy facade, like `muvid/__init__.py`: importing it
pulls nothing heavy, and each attribute is resolved on first use.

### *class* muvid.choreo.Analysis(\*, duration, tempo, events, sections, bands=<factory>, meta=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything an archetype needs, and the JSON artifact a render leaves behind.

```pycon
>>> a = Analysis(duration=2.0, tempo=Tempo(bpm=120.0, beats=(0.0, 0.5), source='numpy'),
...              events=(Event(t=0.0, band='low', strength=1.0),),
...              sections=(Section(index=0, label='mid', start=0.0, end=2.0, energy_db=-12.0),))
>>> Analysis.from_dict(a.to_dict()) == a
True
```

#### events_in(start, end, , band=None)

Events with `start <= t < end` (optionally one band).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Event`](muvid.choreo.analysis.md#muvid.choreo.analysis.Event)]

### *class* muvid.choreo.Canvas(, width=1920, height=1080, fps=30)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Output geometry.

### *class* muvid.choreo.ChoreoScene(\*, canvas, duration, backdrops, objects, meta=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything the renderer needs, and nothing it has to interpret.

### *class* muvid.choreo.TreatmentSpec(\*, spec_version='1.0', title='', direction=<factory>, scenes=<factory>)

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
  [`TreatmentSpec`](muvid.choreo.spec.md#muvid.choreo.spec.TreatmentSpec)

```pycon
>>> s = TreatmentSpec.from_dict({'scenes': [None, {'applies_to': 'high',
...     'archetype': 'swarm', 'params': 'nope'}],
...     'direction': {'palette': {'foo': 1, 'bg': '#000000'}, 'density': None}})
>>> s.scenes[1].applies_to, s.scenes[1].params, s.direction.palette.bg
(('high',), {}, '#000000')
```

### muvid.choreo.analyze(audio, , sample_rate=22050, beat_source='auto', bands={'high': (2000.0, 11025.0), 'low': (20.0, 200.0), 'mid': (200.0, 2000.0)}, min_ioi_beats={'high': 0.25, 'low': 0.5, 'mid': 0.25})

Analyse a song (a path, or an already-decoded mono array at `sample_rate`).

An array input never consults `mixing` (it needs a file), so its beat
grid is always the numpy one; this is the path unit tests use.

* **Return type:**
  [`Analysis`](muvid.choreo.analysis.md#muvid.choreo.analysis.Analysis)

### muvid.choreo.compile_scene(treatment, analysis, , canvas=None, seed=0)

Every object and backdrop for the whole song. Deterministic given `seed`.

* **Return type:**
  [`ChoreoScene`](muvid.choreo.scene.md#muvid.choreo.scene.ChoreoScene)

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

### muvid.choreo.render_choreo(audio, output, , cover=None, treatment=None, archetype=None, seed=0, beat_source='auto', strict=False, width=1920, height=1080, fps=30, workdir=None)

Render a choreo video. The one verb that produces a file.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### Modules

| [`analysis`](muvid.choreo.analysis.md#module-muvid.choreo.analysis)   | Turn a song into an EVENT LIST — the musical facts every archetype draws from.                                                   |
|------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| [`manifest`](muvid.choreo.manifest.md#module-muvid.choreo.manifest)   | The choreo subgenre's manifest — visual music, declared without importing it.                                                    |
| [`pipeline`](muvid.choreo.pipeline.md#module-muvid.choreo.pipeline)   | The choreo pipeline — the one path from a song to a finished video.                                                              |
| [`render`](muvid.choreo.render.md#module-muvid.choreo.render)       | Draw a [`ChoreoScene`](muvid.choreo.scene.md#muvid.choreo.scene.ChoreoScene) frame by frame and encode it. |
| [`scene`](muvid.choreo.scene.md#module-muvid.choreo.scene)         | The scene compiler: events + sections -> a list of drawable OBJECTS.                                                             |
| [`spec`](muvid.choreo.spec.md#module-muvid.choreo.spec)           | The choreo treatment spec — what a director decides, as data; stdlib-only.                                                       |
| [`tools`](muvid.choreo.tools.md#module-muvid.choreo.tools)         | The SSOT verbs for the choreo subgenre.                                                                                          |
