# muvid.montage.tools

The SSOT verbs for the montage subgenre: analyze, plan, render.

Plain functions, JSON-able arguments in, JSON-able `dict` out, and
deliberately agnostic about CLI/MCP/HTTP/agent — the same shape as
[`muvid.lyricvid.tools`](muvid.lyricvid.tools.html.md#module-muvid.lyricvid.tools). The CLI (`python -m muvid.montage`), the generic
subgenre MCP transport and a future frontend all call *these*, so there is one
implementation and one place a behaviour changes.

`plan` exists separately from `render` because the plan IS the creative
product: a caller can look at the edit list (which photo where, how the
choruses are paced, what got reserved for the finale) before paying for a
render, and `render` computes the identical plan — it is deterministic.

Nothing here imports numpy or ffmpeg at module scope.

### Functions

| [`analyze_song`](#muvid.montage.tools.analyze_song)(audio, \*[, beats, ...])            | Measure a song: tempo, beat grid, downbeats, bar energy and sections.   |
|---------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`plan_montage`](#muvid.montage.tools.plan_montage)(audio, \*[, photos, clips, ...])    | Plan the montage without rendering it: the edit list, as JSON.          |
| [`render_montage`](#muvid.montage.tools.render_montage)(audio, output, \*[, photos, ...]) | Render a montage.                                                       |
| [`treatment_schema`](#muvid.montage.tools.treatment_schema)()                               | JSON Schema for a treatment spec — also a model's output constraint.    |
| [`validate_treatment`](#muvid.montage.tools.validate_treatment)(treatment)                    | Validate a treatment, and return the repaired version alongside.        |
| [`vocabulary`](#muvid.montage.tools.vocabulary)()                                     | The closed vocabularies a treatment may draw on.                        |

### muvid.montage.tools.analyze_song(audio, , beats='auto', beats_per_bar=4, sections=None)

Measure a song: tempo, beat grid, downbeats, bar energy and sections.

The payload says WHERE the beats came from (`beat_source`) and whether
the sections were supplied or derived from energy — a caller that ignores
that will trust a fixed-tempo grid on a rubato ballad.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.montage.tools.plan_montage(audio, , photos=(), clips=(), cover=None, treatment=None, archetype=None, strict=False, beats='auto', beats_per_bar=4, sections=None, out=None)

Plan the montage without rendering it: the edit list, as JSON.

Identical to what [`render_montage()`](#muvid.montage.tools.render_montage) would render. `out` writes it
to a file as well.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.montage.tools.render_montage(audio, output, , photos=(), clips=(), cover=None, treatment=None, archetype=None, strict=False, beats='auto', beats_per_bar=4, sections=None, width=1920, height=1080, fps=30, workdir=None)

Render a montage. The one verb that produces a file.

Goes through [`muvid.subgenres.render_subgenre()`](muvid.subgenres.html.md#muvid.subgenres.render_subgenre) when the manifest is
registered (so the schemas are enforced), and straight to the pipeline
otherwise — the same renderer either way.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.montage.tools.treatment_schema()

JSON Schema for a treatment spec — also a model’s output constraint.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> treatment_schema()['type']
'object'
```

### muvid.montage.tools.validate_treatment(treatment)

Validate a treatment, and return the repaired version alongside.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> r = validate_treatment({'scenes': [{'archetype': 'swirl'}]})
>>> r['valid'], r['repairs']
(False, ["scenes[0].archetype 'swirl' -> 'beat_cut'"])
```

### muvid.montage.tools.vocabulary()

The closed vocabularies a treatment may draw on.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> sorted(vocabulary())[:3]
['archetype_params', 'archetypes', 'cut_feels']
```
