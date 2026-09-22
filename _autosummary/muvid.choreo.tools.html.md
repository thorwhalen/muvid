# muvid.choreo.tools

The SSOT verbs for the choreo subgenre.

Plain functions, JSON-able arguments in, JSON-able `dict` out, and
deliberately agnostic about CLI/MCP/HTTP/agent — the same shape as
[`muvid.lyricvid.tools`](muvid.lyricvid.tools.html.md#module-muvid.lyricvid.tools). The CLI (`python -m muvid.choreo`) and any
MCP or HTTP surface call *these*, so there is one implementation and one
place a behaviour changes.

Nothing here imports numpy or a renderer at module scope, so
`import muvid.choreo.tools` stays cheap and safe.

### Functions

| [`catalog`](#muvid.choreo.tools.catalog)()                                          | Every installed subgenre, without importing any renderer.         |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| [`manifest`](#muvid.choreo.tools.manifest)()                                         | This subgenre's manifest, as a catalogue would show it.           |
| [`vocabulary`](#muvid.choreo.tools.vocabulary)()                                       | The closed vocabularies a treatment may draw on.                  |
| [`treatment_schema`](#muvid.choreo.tools.treatment_schema)()                                 | JSON Schema for a treatment spec.                                 |
| [`analyze_song`](#muvid.choreo.tools.analyze_song)(audio, \*[, beat_source, max_events]) | Hear a song the way the archetypes will: events, tempo, sections. |
| [`validate_treatment`](#muvid.choreo.tools.validate_treatment)(treatment)                      | Validate a treatment and return the repaired version alongside.   |
| [`render_choreo`](#muvid.choreo.tools.render_choreo)(audio, output, \*[, cover, ...])     | Render a choreo video.                                            |

### muvid.choreo.tools.analyze_song(audio, , beat_source='auto', max_events=200)

Hear a song the way the archetypes will: events, tempo, sections.

The full event list is what `events.json` carries after a render; this
verb truncates it to `max_events` so a terminal or an agent gets the
shape without the flood.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.choreo.tools.catalog()

Every installed subgenre, without importing any renderer.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.choreo.tools.manifest()

This subgenre’s manifest, as a catalogue would show it.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> manifest()['slug']
'choreo'
```

### muvid.choreo.tools.render_choreo(audio, output, , cover=None, treatment=None, archetype=None, seed=0, beat_source='auto', strict=False, width=1920, height=1080, fps=30, workdir=None)

Render a choreo video. The one verb that produces a file.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.choreo.tools.treatment_schema()

JSON Schema for a treatment spec.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> treatment_schema()['type']
'object'
```

### muvid.choreo.tools.validate_treatment(treatment)

Validate a treatment and return the repaired version alongside.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> r = validate_treatment({'scenes': [{'archetype': 'swirl'}]})
>>> r['valid'], r['repairs']
(False, ["scenes[0].archetype 'swirl' -> 'fischinger'"])
```

### muvid.choreo.tools.vocabulary()

The closed vocabularies a treatment may draw on.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> sorted(vocabulary())
['archetype_params', 'archetypes', 'backgrounds', 'densities']
```
