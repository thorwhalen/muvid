# muvid.subgenres

muvid subgenres — the plugin surface for *another kind of video*.

muvid’s three parts each know how to make one thing. A **subgenre** is how a
fourth kind gets added — by muvid, or by anyone else — without editing muvid.

> from muvid.subgenres import Subgenre, register_subgenre

> MY_THING = Subgenre(
> : slug=”ransom-note”,
>   title=”Ransom note”,
>   description=”Lyrics cut from magazines, one word per beat.”,
>   render=”my_package.render:render”,       # a STRING, imported lazily
>   inputs={“type”: “object”, “required”: [“audio”],
>   <br/>
>   > “properties”: {“audio”: {“type”: “string”, “format”: “path”}}},

> )

Ship it by pointing an entry point at the manifest:

```default
[project.entry-points."muvid.subgenres.v1"]
ransom-note = "my_package.subgenre:MY_THING"
```

Two things make this worth having over a plain dict:

**The entry point’s value is a manifest, not a renderer.** Listing every
installed subgenre imports no rendering code, so a UI or an LLM can choose among
twelve of them in microseconds and a plugin whose heavy dependency is missing
degrades to a recorded error instead of taking the catalogue down.

\*\*It composes with `nw` rather than competing with it.\*\* `nw.Genre` is the
cross-package catalogue a host connector reads; a muvid subgenre is one more
`nw.Template` underneath muvid’s own genre, carrying `params={"subgenre":
slug}`. So a plugin author depends on `muvid` and nothing else, and the
connector picks the plugin up for free.

Where each piece lives:

* `muvid.subgenres._manifest` — the [`Subgenre`](#muvid.subgenres.Subgenre) data, stdlib only.
* `muvid.subgenres._registry` — registration, discovery, resolution.
* `muvid.subgenres._contract` — the [`Renderer`](#muvid.subgenres.Renderer) protocol.
* [`muvid.subgenres.testing`](muvid.subgenres.testing.html.md#module-muvid.subgenres.testing) — a conformance kit for plugin authors.

This module imports nothing heavier than the standard library, so
`import muvid.subgenres` is safe on any import-safe path.

### Functions

| [`get_subgenre`](#muvid.subgenres.get_subgenre)(slug, \*[, refresh])           | The manifest for `slug`.                                                        |
|----------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| [`iter_subgenres`](#muvid.subgenres.iter_subgenres)(\*[, refresh])               | Iterate manifests in slug order.                                                |
| [`list_subgenres`](#muvid.subgenres.list_subgenres)(\*[, refresh])               | Every available subgenre slug, sorted.                                          |
| [`register_subgenre`](#muvid.subgenres.register_subgenre)(subgenre)                 | Register a subgenre in this process.                                            |
| [`render_subgenre`](#muvid.subgenres.render_subgenre)(slug, \*, inputs[, params]) | Render `slug` — the one call that imports plugin code.                          |
| [`resolve_renderer`](#muvid.subgenres.resolve_renderer)(slug)                      | Import and return the renderer for `slug`.                                      |
| [`subgenre_catalog`](#muvid.subgenres.subgenre_catalog)(\*[, refresh])             | JSON-able catalogue: what a CLI, an HTTP route, an MCP tool or an agent serves. |
| [`unregister_subgenre`](#muvid.subgenres.unregister_subgenre)(slug)                   | Remove an in-process registration.                                              |

### Classes

| [`Example`](#muvid.subgenres.Example)(\*, description[, params, inputs, ...])   | One invocation worth showing to a human or an agent.   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------|
| [`RenderRequest`](#muvid.subgenres.RenderRequest)(\*, subgenre, inputs[, params])     | Everything a subgenre renderer gets.                   |
| [`RenderResult`](#muvid.subgenres.RenderResult)(\*, output[, duration_s, ...])       | What a renderer gives back.                            |
| [`Renderer`](#muvid.subgenres.Renderer)(\*args, \*\*kwargs)                      | `(RenderRequest) -> RenderResult`, and nothing else.   |
| [`Subgenre`](#muvid.subgenres.Subgenre)(\*, slug, title, description, render)    | A declared kind of video, and how to render one.       |

### *class* muvid.subgenres.Example(\*, description, params=<factory>, inputs=<factory>, preview=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One invocation worth showing to a human or an agent.

Agents pick better from examples than from prose — the Remotion registry’s
finding, and the reason this is a first-class field rather than something
buried in `description`.

#### inputs *: [Mapping](https://docs.python.org/3/library/typing.html#typing.Mapping)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any)]*

Inputs for this example, when they can be stated without a local path.
The conformance kit uses the first Example’s inputs for its trial render.

#### preview *: [str](https://docs.python.org/3/builtins/stdtypes.html#str) | [None](https://docs.python.org/3/builtins/constants.html#None)*

Optional path, relative to the plugin package, of a short sample render.

### *class* muvid.subgenres.RenderRequest(\*, subgenre, inputs, params=<factory>, workdir, output)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything a subgenre renderer gets.

* **Parameters:**
  * **subgenre** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – the slug being rendered, so one function can serve several.
  * **inputs** ([`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]) – the caller’s files and primitives, validated against the
    manifest’s `inputs` schema before it gets here.
  * **params** ([`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]) – the styling/treatment knobs, validated against
    `params_schema`.
  * **workdir** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – a directory the renderer may write intermediates into. It
    exists, and it belongs to this render — nothing else writes there.
  * **output** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – where the finished artifact must be written.

### *class* muvid.subgenres.RenderResult(\*, output, duration_s=None, artifacts=<factory>, meta=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What a renderer gives back.

* **Parameters:**
  * **output** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – the finished artifact. Must be the request’s `output`.
  * **duration_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – length of the produced media, when it has one.
  * **artifacts** ([`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]) – named side products worth keeping — a `.ass` subtitle
    file, a thumbnail, the resolved treatment spec. These are how a
    subgenre stays inspectable instead of producing an opaque file.
  * **meta** ([`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]) – anything else worth recording; must be JSON-able.

### *class* muvid.subgenres.Renderer(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

`(RenderRequest) -> RenderResult`, and nothing else.

### *class* muvid.subgenres.Subgenre(\*, slug, title, description, render, inputs=<factory>, params_schema=<factory>, produces='video/mp4', examples=(), intake_kinds=(), cost_profile=None, provider=None, api_versions=())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A declared kind of video, and how to render one.

Everything here is JSON-able except `render`, which is a *reference*
to a callable rather than the callable itself. Construct one at module
scope in a stdlib-only module and point an entry point at it.

* **Parameters:**
  * **slug** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – stable id; also a path segment and a public contract value, so
    choose it once. Lowercase, hyphen-separated.
  * **inputs** ([`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]) – JSON Schema for the *files and primitives* the renderer
    needs. One schema serves the UI form, CLI validation and the MCP tool
    definition, which is why it is a schema and not prose.
  * **params_schema** ([`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]) – JSON Schema for the styling/treatment knobs.
  * **render** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – `"package.module:function"`. Imported lazily, once, at
    render time. The function must satisfy [`Renderer`](#muvid.subgenres.Renderer).
  * **api_versions** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – the manifest API versions this plugin is written
    against. Must include the running `API_VERSION`.

#### api_versions *: [Sequence](https://docs.python.org/3/library/typing.html#typing.Sequence)[[str](https://docs.python.org/3/builtins/stdtypes.html#str)]*

a default bound at muvid’s import time
would make a plugin that never stated a version “declare” whichever
version happens to be running — vacuous for the careless, and it left
the careful plugin that pinned “1” refused on a v2 host while the one
that pinned nothing sailed through. A plugin states what it was written
against; the host decides.

* **Type:**
  REQUIRED. No default on purpose

#### cost_profile *: [str](https://docs.python.org/3/builtins/stdtypes.html#str) | [None](https://docs.python.org/3/builtins/constants.html#None)*

`None` means genuinely free. Anything else names a cost estimator, and
an unknown cost must force approval rather than encode as zero — see
muvid’s conjunctive budget gate.

#### intake_kinds *: [Sequence](https://docs.python.org/3/library/typing.html#typing.Sequence)[[str](https://docs.python.org/3/builtins/stdtypes.html#str)]*

Free-form tags a host may use to route “what are you making?” answers.

#### provider *: [str](https://docs.python.org/3/builtins/stdtypes.html#str) | [None](https://docs.python.org/3/builtins/constants.html#None)*

Which distribution provided this, filled in by the loader for
entry-point plugins. `None` for in-process registrations.

#### to_dict()

JSON-able form — what a catalogue, a UI or an agent actually reads.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.subgenres.get_subgenre(slug, , refresh=False)

The manifest for `slug`. Imports no renderer.

* **Return type:**
  [`Subgenre`](#muvid.subgenres.Subgenre)

### muvid.subgenres.iter_subgenres(, refresh=False)

Iterate manifests in slug order.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`Subgenre`](#muvid.subgenres.Subgenre)]

### muvid.subgenres.list_subgenres(, refresh=False)

Every available subgenre slug, sorted. Imports no renderer.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.subgenres.register_subgenre(subgenre)

Register a subgenre in this process. Returns it, so it can be used inline.

Raises [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError) on a slug collision, matching `nw.genres`’
`on_conflict="error"`: a slug is a persisted path segment and a public
contract value, so silently replacing one is never the kind thing to do.

* **Return type:**
  [`Subgenre`](#muvid.subgenres.Subgenre)

```pycon
>>> from muvid.subgenres import Subgenre, register_subgenre, list_subgenres
>>> sg = register_subgenre(Subgenre(
...     slug='demo-doctest', title='Demo', description='.',
...     render='muvid.subgenres.testing:echo_renderer', api_versions=("1",)))
>>> 'demo-doctest' in list_subgenres()
True
>>> unregister_subgenre('demo-doctest')
```

### muvid.subgenres.render_subgenre(slug, , inputs, params=None, workdir, output)

Render `slug` — the one call that imports plugin code.

Deliberately takes paths and primitives rather than a
[`MusicVideoProject`](muvid.project.html.md#muvid.project.MusicVideoProject): a plugin coupled to muvid’s
project schema would break on every schema change, and could not be driven
from a CLI, an HTTP request or an MCP tool without muvid building the
project first. A plugin that needs more asks muvid for it through a narrow
accessor.

* **Return type:**
  [`RenderResult`](#muvid.subgenres.RenderResult)

### muvid.subgenres.resolve_renderer(slug)

Import and return the renderer for `slug`.

This is the only function in the module that imports plugin code, and it is
called at render time, not at listing time.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.subgenres.subgenre_catalog(, refresh=False)

JSON-able catalogue: what a CLI, an HTTP route, an MCP tool or an agent serves.

Imports no renderer, so this stays cheap however many plugins are installed.
`load_errors` is part of the payload on purpose — a broken plugin should be
visible to whoever is choosing, not silently absent.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.subgenres.unregister_subgenre(slug)

Remove an in-process registration. Mostly for tests and doctests.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### Modules

| [`testing`](muvid.subgenres.testing.html.md#module-muvid.subgenres.testing)   | A conformance kit for subgenre plugin authors.   |
|-------------------------------------------------------------------------------------------|--------------------------------------------------|
