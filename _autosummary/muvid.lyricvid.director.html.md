# muvid.lyricvid.director

The creative director — a song in, one or more `TreatmentSpec`s out.

This is the half of the subgenre that has taste, and it has to work in two
runtimes that look nothing alike:

**agentic**
: inside Claude Code or claude.ai, where a skill hands the *host* model the
  lyrics and the vocabularies and the host model writes the treatment;

**transactional**
: in production, as one bounded structured-output call from a web frontend,
  with a cost, a timeout and a schema constraint.

Duplicating the taste between a `SKILL.md` and a Python system prompt is how
those two runtimes silently diverge — one gets a new archetype, the other keeps
recommending the old one, and nobody notices until a render looks wrong. So the
shared artefacts are ranked, and there are only three of them:

1. the **schema** ([`muvid.lyricvid.spec`](muvid.lyricvid.spec.html.md#module-muvid.lyricvid.spec)), which constrains both runtimes;
2. the **pure functions** in this module, which have no runtime opinions at all;
3. the **prompt text**, which lives in `muvid/data/prompts/*.md` and is read
   from there by both. There is exactly one copy of every opinion, and the
   vocabularies are *interpolated into* the prompt from
   [`muvid.lyricvid.spec.vocabulary()`](muvid.lyricvid.spec.html.md#muvid.lyricvid.spec.vocabulary) rather than restated in it.

`llm` is the seam, and it is one keyword argument: a callable
`(messages, schema) -> dict`. Its default is not a stub. [`heuristic_llm()`](#muvid.lyricvid.director.heuristic_llm)
is a real director that reads the lyrics — density, repetition, line shape,
vocabulary richness, section structure — and picks an archetype, a palette and a
motion vocabulary from measurement alone. The whole subgenre therefore runs end
to end with no API key, no network and no cost, and [`anthropic_llm()`](#muvid.lyricvid.director.anthropic_llm) is an
upgrade rather than a prerequisite.

Diversity is the other design decision worth stating. Asking one director for
`n` options and turning the temperature up yields `n` blurred copies of one
idea; the differences become noise rather than intent. So options come from
[`PERSONAS`](#muvid.lyricvid.director.PERSONAS) — several art directors who genuinely disagree about how much
of the frame belongs to reading and how much to feeling — each asked once, each
told which archetypes the other options already took.

Everything degrades honestly. A model that returns something unusable falls back
to the heuristic director, and the fallback is *recorded*: [`director_meta()`](#muvid.lyricvid.director.director_meta)
reads it back off the returned spec, and the reason is appended to the human-
facing rationale. A silent fallback that returns a plausible artifact is the
failure mode muvid has been bitten by before (muvid#46, muvid#38) and it is not
repeated here.

Import-safe: stdlib only at module scope. `anthropic` is imported inside
[`anthropic_llm()`](#muvid.lyricvid.director.anthropic_llm)’s returned callable.

### Module Attributes

| [`PERSONAS`](#muvid.lyricvid.director.PERSONAS)   | The persona registry.   |
|-------------------------------------------------------------|-------------------------|

### Functions

| [`register_persona`](#muvid.lyricvid.director.register_persona)(persona)                         | Register a persona under its slug (returns it, for inline use).                                                                             |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|
| [`list_personas`](#muvid.lyricvid.director.list_personas)()                                   | Every registered persona, packaged ones first, in file order.                                                                               |
| [`resolve_persona`](#muvid.lyricvid.director.resolve_persona)(persona)                          | Resolve a slug or a [`Persona`](#muvid.lyricvid.director.Persona) to a [`Persona`](#muvid.lyricvid.director.Persona). |
| [`director_prompt`](#muvid.lyricvid.director.director_prompt)()                                 | The director's system prompt, with the vocabularies and schema filled in.                                                                   |
| [`output_schema`](#muvid.lyricvid.director.output_schema)()                                   | The JSON Schema a model generates against — a closed form of the spec's.                                                                    |
| [`prompt_context`](#muvid.lyricvid.director.prompt_context)(timed_text, \*[, song_title, ...]) | Everything the director should see, and nothing else.                                                                                       |
| [`build_messages`](#muvid.lyricvid.director.build_messages)(context, \*[, persona, ...])       | Assemble the director's messages: prompt files plus this song's context.                                                                    |
| [`heuristic_director`](#muvid.lyricvid.director.heuristic_director)(context, \*[, persona])        | Pick a treatment from measurement alone.                                                                                                    |
| [`heuristic_llm`](#muvid.lyricvid.director.heuristic_llm)()                                   | The zero-cost director, as an `llm` seam implementation.                                                                                    |
| [`anthropic_llm`](#muvid.lyricvid.director.anthropic_llm)(\*[, model, api_key, ...])          | The production `llm` seam: one structured-output call to Claude.                                                                            |
| [`propose_treatments`](#muvid.lyricvid.director.propose_treatments)(timed_text, \*[, n, ...])      | Propose `n` treatments for one song.                                                                                                        |
| [`rank_treatments`](#muvid.lyricvid.director.rank_treatments)(specs, timed_text)                | Order options without a human.                                                                                                              |
| [`director_meta`](#muvid.lyricvid.director.director_meta)(spec)                               | Read this module's provenance back off a spec it produced.                                                                                  |

### Classes

| [`Persona`](#muvid.lyricvid.director.Persona)(\*, slug, name[, doctrine, ...])   | One art director's doctrine, plus the dials that encode it.   |
|---------------------------------------------------------------------------------------------|---------------------------------------------------------------|

### muvid.lyricvid.director.PERSONAS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Persona](#muvid.lyricvid.director.Persona)]* *= {}*

The persona registry. Populated from the packaged markdown on first use;
[`register_persona()`](#muvid.lyricvid.director.register_persona) adds more. Same idiom as `register_visual` /
`register_selection_strategy` / `register_archetype`.

### *class* muvid.lyricvid.director.Persona(\*, slug, name, doctrine='', legibility=0.5, prefers=(), mood='', palette=<factory>, typography=<factory>, motion=('fade', ), persistence='clear_on_line', quantize='word', cut_style='hard', attack_s=0.12, lead_s=0.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One art director’s doctrine, plus the dials that encode it.

* **Parameters:**
  * **doctrine** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – the prose a model reads. Never restated in Python.
  * **legibility** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – 0..1. How far this director bends toward “the words are
    the picture” (1.0) versus “the words are the texture of a picture” (0.0).
    It is the axis the personas actually disagree on, and it is what makes
    two of them choose differently from the *same* measurements.
  * **prefers** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]) – archetypes in order of preference.

#### block()

The persona as prompt text: heading, doctrine, dials.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.lyricvid.director.anthropic_llm(, model='claude-opus-5', api_key=None, max_tokens=8000, effort=None, client=None)

The production `llm` seam: one structured-output call to Claude.

The response is constrained by [`output_schema()`](#muvid.lyricvid.director.output_schema) through
`output_config.format`, so the returned text is guaranteed-parseable JSON
in the treatment’s shape — the model cannot invent an archetype or hand back
prose, which removes the whole retry-on-malformed-JSON layer.

Usage rides back on the returned mapping under `"_usage"` (and accumulates
on the callable’s own `.usage` list, for a caller that drives the seam
directly). It carries `estimated_cost_usd` **and** `has_unknown_costs`,
because a price this module cannot determine is unknown, not zero.

`anthropic` is imported inside the call, not at module scope: `import
muvid` must not pull an SDK, and this seam is optional by construction.

* **Parameters:**
  **client** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – an already-built SDK client, mostly for tests. When given,
  `api_key` is ignored.
* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]], [`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]], [`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

### muvid.lyricvid.director.build_messages(context, , persona=None, reference_image=None)

Assemble the director’s messages: prompt files plus this song’s context.

The system prompt is emitted as *two* messages — the shared director prompt
then the persona — so a transactional caller can cache the first across all
`n` options while only the second varies. [`anthropic_llm()`](#muvid.lyricvid.director.anthropic_llm) does
exactly that.

The context travels as a fenced JSON block, which is also how
[`heuristic_llm()`](#muvid.lyricvid.director.heuristic_llm) reads it back: the zero-cost director and the model are
given literally the same message rather than two views of one idea.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

```pycon
>>> from muvid.lyricvid.timed_text import from_words
>>> tt = from_words([('we', 0.0, .4), ('go', .4, .9)], duration=1.0)
>>> msgs = build_messages(prompt_context(tt), persona='minimalist')
>>> [m['role'] for m in msgs]
['system', 'system', 'user']
>>> 'The Minimalist' in msgs[1]['content']
True
>>> _context_from_messages(msgs)['signals']['n_words']
2
>>> img = build_messages(prompt_context(tt),
...                      reference_image={'type': 'image', 'source': {}})
>>> [b['type'] for b in img[-1]['content']]
['image', 'text']
```

### muvid.lyricvid.director.director_meta(spec)

Read this module’s provenance back off a spec it produced.

`source` is `'heuristic'`, `'llm'` or `'heuristic-fallback'` — the
last meaning a model was asked and its answer could not be used. That case
also carries `fallback_reason` and appends it to the rationale, because a
degraded result that presents as an intended one is the failure muvid keeps
paying for elsewhere.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> from muvid.lyricvid.timed_text import from_words
>>> tt = from_words([('go', 0.0, .5)], duration=1.0)
>>> director_meta(propose_treatments(tt, n=1)[0])['source']
'heuristic'
```

### muvid.lyricvid.director.director_prompt()

The director’s system prompt, with the vocabularies and schema filled in.

The same text is the skill’s guidance and the API call’s `system`. Nothing
in it is written twice: the vocabularies come from
[`muvid.lyricvid.spec.vocabulary()`](muvid.lyricvid.spec.html.md#muvid.lyricvid.spec.vocabulary) and the schema from
[`output_schema()`](#muvid.lyricvid.director.output_schema).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> text = director_prompt()
>>> VOCABULARIES_MARKER in text or SCHEMA_MARKER in text
False
>>> '`karaoke_wipe`' in text and '"one_word_centred"' in text
True
```

### muvid.lyricvid.director.heuristic_director(context, , persona=None)

Pick a treatment from measurement alone. No model, no network, no key.

The decision is two linear scores and one rule. `ARCHETYPE_CHARACTER`
asks what the lyric *is* — repetitive, long-lined, shaped, image-dominated,
sparse and various. `ARCHETYPE_CAPACITY_WPS` asks how fast it goes
past. The persona’s `legibility` decides how much each of those counts,
and its `prefers` order breaks the remaining ties — which is why six
personas over one song give six genuinely different treatments rather than
six samples of one. `context['avoid_archetypes']` (set by
[`propose_treatments()`](#muvid.lyricvid.director.propose_treatments)) then keeps a set of options from converging.

Sections are honoured when the lyrics carry real labels: a chorus-ish
section gets the runner-up archetype as a lift. In that case *every* section
gets its own scene and none is `"*"`, because the compiler renders a
`"*"` scene over the whole song and the words would double up.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> from muvid.lyricvid.timed_text import from_words
>>> words = [(w, i * .5, i * .5 + .45) for i, w in
...          enumerate('hold on hold on hold on'.split())]
>>> d = heuristic_director(prompt_context(from_words(words, duration=4.0)),
...                        persona='karaoke_host')
>>> d['scenes'][0]['archetype']
'karaoke_wipe'
>>> d['direction']['palette']['bg']
'#101828'
>>> spec_mod.validate(spec_mod.TreatmentSpec.from_dict(d))
[]
```

### muvid.lyricvid.director.heuristic_llm()

The zero-cost director, as an `llm` seam implementation.

Conforming to the seam rather than sitting beside it means the default path
and the production path run the *same* code in [`propose_treatments()`](#muvid.lyricvid.director.propose_treatments) —
same messages, same schema, same repair, same meta — so the free path is
exercised by every test the paid one would be.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]], [`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]], [`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

```pycon
>>> from muvid.lyricvid.timed_text import from_words
>>> tt = from_words([('all', 0.0, .5), ('night', .5, 1.2)], duration=2.0)
>>> call = heuristic_llm()
>>> out = call(build_messages(prompt_context(tt), persona='club_vj'),
...            output_schema())
>>> out['scenes'][0]['archetype']
'one_word_centred'
```

### muvid.lyricvid.director.list_personas()

Every registered persona, packaged ones first, in file order.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Persona`](#muvid.lyricvid.director.Persona)]

```pycon
>>> [p.slug for p in list_personas()]
['typographer', 'atmospherist', 'concrete_poet', 'club_vj', 'karaoke_host', 'minimalist']
>>> sorted({p.legibility for p in list_personas()}) ==         [0.2, 0.35, 0.55, 0.7, 0.9, 1.0]
True
```

### muvid.lyricvid.director.output_schema()

The JSON Schema a model generates against — a closed form of the spec’s.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> s = output_schema()
>>> s['required']
['title', 'direction', 'scenes']
>>> 'params' in s['properties']['scenes']['items']['properties']
False
>>> s['properties']['scenes']['items']['properties']['archetype']['enum'][0]
'one_word_centred'
```

### muvid.lyricvid.director.prompt_context(timed_text, , song_title='', extra=None)

Everything the director should see, and nothing else. No audio bytes.

The lyrics with their section labels, the measurements, whether the word
times were *measured* or interpolated, and the closed vocabularies. Anything
a caller has already measured elsewhere — tempo, key, a beat count — goes in
through `extra` and lands under `measurements`, so the director can use
a real number without this module growing an audio dependency.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
>>> from muvid.lyricvid.timed_text import from_words
>>> tt = from_words([('hold', 0.0, .5), ('on', .5, 1.0)], duration=2.0)
>>> ctx = prompt_context(tt, song_title='Demo', extra={'tempo_bpm': 128})
>>> ctx['song_title'], ctx['measurements']['tempo_bpm']
('Demo', 128)
>>> ctx['sections'][0]['lines'][0]['text']
'hold on'
>>> ctx['timing']['measured'], sorted(ctx['vocabularies'])[0]
(True, 'archetypes')
```

### muvid.lyricvid.director.propose_treatments(timed_text, , n=3, song_title='', reference_image=None, llm=None, personas=None)

Propose `n` treatments for one song. Every one of them is renderable.

`llm=None` is the working default, not a stub: [`heuristic_llm()`](#muvid.lyricvid.director.heuristic_llm) reads
the lyrics and directs from measurement, so the subgenre runs end to end with
zero AI and zero cost. Pass [`anthropic_llm()`](#muvid.lyricvid.director.anthropic_llm) to upgrade the taste; pass
anything `(messages, schema) -> dict` to substitute your own.

Each option is directed by a different [`Persona`](#muvid.lyricvid.director.Persona) and is told which
archetypes the earlier options took, which is what makes `n` options
genuinely different rather than `n` samples of one.

Every returned spec has been through [`coerce()`](muvid.lyricvid.spec.html.md#muvid.lyricvid.spec.coerce), so
`validate` is empty by construction; the substitutions repair made are
recorded in [`director_meta()`](#muvid.lyricvid.director.director_meta) under `repairs`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`TreatmentSpec`](muvid.lyricvid.spec.html.md#muvid.lyricvid.spec.TreatmentSpec)]

```pycon
>>> from muvid.lyricvid.timed_text import from_words
>>> words = 'we are the champions my friends'.split()
>>> tt = from_words([(w, i * .45, i * .45 + .4) for i, w in enumerate(words)],
...                 duration=4.0)
>>> options = propose_treatments(tt, n=3, song_title='Demo')
>>> [s.scenes[0].archetype for s in options]
['stacked_lines', 'scatter', 'concrete_page']
>>> [spec_mod.validate(s) for s in options]
[[], [], []]
>>> options[0].title
'Demo'
```

A model that returns something unusable degrades to the heuristic, and says
so rather than passing off a fallback as a decision:

```pycon
>>> import warnings
>>> with warnings.catch_warnings():
...     warnings.simplefilter('ignore')
...     broken = propose_treatments(tt, n=1, llm=lambda m, s: 'not a treatment')
>>> director_meta(broken[0])['source']
'heuristic-fallback'
>>> director_meta(broken[0])['fallback_reason']
'the director returned str, not a mapping'
>>> 'fell back to the heuristic director' in broken[0].direction.rationale
True
```

### muvid.lyricvid.director.rank_treatments(specs, timed_text)

Order options without a human. Deterministic, and it explains itself.

Four terms, combined by `_combine()` (a weighted *geometric* mean, so a
single disqualifying term cannot be averaged away by three good ones):
**legibility** (the song’s
word rate against the archetype’s capacity, penalised for word-level
quantisation the timing cannot support), **contrast** (WCAG-style ratios for
`fg`/`accent`/`dim`), **coverage** (does every section actually get a
scene) and **coherence** (declared motions, archetype count, and pairings
that defeat themselves).

Returns `(spec, score, why)`, best first, with ties broken on the leading
archetype so the order is stable across runs.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`TreatmentSpec`](muvid.lyricvid.spec.html.md#muvid.lyricvid.spec.TreatmentSpec), [`float`](https://docs.python.org/3/builtins/functions.html#float), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

```pycon
>>> from muvid.lyricvid.timed_text import from_words
>>> tt = from_words([(w, i * .4, i * .4 + .35) for i, w in
...                  enumerate('one two three four'.split())], duration=3.0)
>>> readable = spec_mod.TreatmentSpec(
...     direction=spec_mod.Direction(mood='m', rationale='r'),
...     scenes=(spec_mod.Scene(archetype='karaoke_wipe', motion='fade'),))
>>> murky = spec_mod.TreatmentSpec(
...     direction=spec_mod.Direction(
...         mood='m', rationale='r',
...         palette=spec_mod.Palette(bg='#202020', fg='#2a2a2a',
...                                  accent='#242424', dim='#606060')),
...     scenes=(spec_mod.Scene(archetype='scatter', motion='fade'),))
>>> ordered = rank_treatments([murky, readable], tt)
>>> [round(score, 3) for _, score, _ in ordered]
[1.0, 0.358]
>>> ordered[0][0] is readable
True
>>> ordered[1][2]
'legibility 0.70 · contrast 0.03 · coverage 1.00 · coherence 1.00 — weakest is contrast'
```

### muvid.lyricvid.director.register_persona(persona)

Register a persona under its slug (returns it, for inline use).

* **Return type:**
  [`Persona`](#muvid.lyricvid.director.Persona)

### muvid.lyricvid.director.resolve_persona(persona)

Resolve a slug or a [`Persona`](#muvid.lyricvid.director.Persona) to a [`Persona`](#muvid.lyricvid.director.Persona).

* **Return type:**
  [`Persona`](#muvid.lyricvid.director.Persona)

```pycon
>>> resolve_persona('karaoke_host').name
'The Karaoke Host'
```
