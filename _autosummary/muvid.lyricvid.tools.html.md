# muvid.lyricvid.tools

The SSOT verbs for the lyric-video subgenre.

Plain functions, JSON-able arguments in, JSON-able `dict` out, and
deliberately agnostic about CLI/MCP/HTTP/agent — following `ir.tools`, which
muvid’s design record already names as the reference for this shape. The CLI
(`python -m muvid.lyricvid`), the MCP tools, the shipped skill and a
production frontend all call *these*, so there is one implementation and one
place a behaviour changes.

Nothing here imports a renderer, an LLM client or numpy at module scope, so
`import muvid.lyricvid.tools` stays cheap and safe.

### Functions

| [`catalog`](#muvid.lyricvid.tools.catalog)()                                         | Every installed subgenre, without importing any renderer.              |
|----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| [`vocabulary`](#muvid.lyricvid.tools.vocabulary)()                                      | The closed vocabularies a treatment may draw on.                       |
| [`treatment_schema`](#muvid.lyricvid.tools.treatment_schema)()                                | JSON Schema for a treatment spec — also the model's output constraint. |
| [`analyze_song`](#muvid.lyricvid.tools.analyze_song)(audio, \*[, lyrics, subtitles, ...]) | Measure a song's words and report what a director needs to know.       |
| [`propose_treatments`](#muvid.lyricvid.tools.propose_treatments)(audio, \*[, lyrics, ...])      | Propose `n` treatments, ranked, each with its rationale.               |
| [`validate_treatment`](#muvid.lyricvid.tools.validate_treatment)(treatment)                     | Validate a treatment, and return the repaired version alongside.       |
| [`render_lyric_video`](#muvid.lyricvid.tools.render_lyric_video)(audio, output, \*[, ...])      | Render a lyric video.                                                  |

### muvid.lyricvid.tools.analyze_song(audio, , lyrics=None, subtitles=None, project=None, aligner=None, max_lines=40)

Measure a song’s words and report what a director needs to know.

Returns the section/line structure, the duration, the words-per-second the
treatment has to keep up with, and — importantly — whether the word times
were **measured** or interpolated from line times. A caller that ignores
that flag will ship a video that looks subtly out of sync.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.lyricvid.tools.catalog()

Every installed subgenre, without importing any renderer.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> c = catalog()
>>> 'lyric-video' in [s['slug'] for s in c['subgenres']]
True
```

### muvid.lyricvid.tools.propose_treatments(audio, , lyrics=None, subtitles=None, project=None, n=3, title='', reference_image=None, use_llm=False, model=None)

Propose `n` treatments, ranked, each with its rationale.

`use_llm=False` (the default) runs the heuristic director: no network, no
API key, no cost. `use_llm=True` asks a model, and the returned payload
then carries a `cost` block — unknown cost is reported as unknown, never
as zero.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.lyricvid.tools.render_lyric_video(audio, output, , lyrics=None, subtitles=None, project=None, treatment=None, renderer='auto', title='', persona=None, aligner=None, width=1920, height=1080, fps=30, workdir=None)

Render a lyric video. The one verb that produces a file.

Everything else in this module exists so that a caller can decide *what* to
render before paying for it.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.lyricvid.tools.treatment_schema()

JSON Schema for a treatment spec — also the model’s output constraint.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> treatment_schema()['type']
'object'
```

### muvid.lyricvid.tools.validate_treatment(treatment)

Validate a treatment, and return the repaired version alongside.

A treatment that is *nearly* right is repaired rather than rejected, because
a mechanical substitution renders something good now where a retry costs a
round trip and may fail the same way. Every substitution is reported.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> r = validate_treatment({'scenes': [{'archetype': 'swirl'}]})
>>> r['valid'], r['repairs']
(False, ["scenes[0].archetype 'swirl' -> 'one_word_centred'"])
```

### muvid.lyricvid.tools.vocabulary()

The closed vocabularies a treatment may draw on.

One copy, read by the prompt, the JSON Schema, the UI and the docs.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> v = vocabulary()
>>> sorted(v)[:2]
['archetypes', 'cases']
```
