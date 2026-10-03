# muvid.footage.named_looks

Named looks — the camera moves and grades a person can pick for a cut.

A cut’s `look` is one ffmpeg filter chain ([`muvid.footage.edl.EdlEntry.look`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry.look)),
which is the right thing to RENDER and the wrong thing to OFFER: a screen or an
assistant can choose among named effects, not write filter strings. This is the menu,
each entry a function of a few bounded parameters compiled through
[`muvid.footage.look`](muvid.footage.look.md#module-muvid.footage.look) (so every fragment is `looks`’ and passes
`validate_edl`’s allowlist):

- camera moves, which read the clock (`look_time_varying`): `punch_in` (a steady
  tighter framing), `slow_push` / `slow_pull` (zoom in / out across the cut),
  `pan_left` / `pan_right` (drift sideways at a slight zoom);
- grades, which do not: `vivid`, `black_and_white`, `posterize` and `cartoon`
  (flatten + posterize + a little colour — in the spirit of Que Calor’s V2, whose real
  stylizer was a Python mean-shift and a palette LUT that no filter chain reproduces).

**Tasteful by construction**: every zoom is bounded at [`MAX_ZOOM`](#muvid.footage.named_looks.MAX_ZOOM) (1.15) by the
parameter’s own schema and again when compiled — a move bigger than that reads as a
mistake on phone footage, where the frame has no resolution to spare.

A move is compiled for the cut’s length and the project’s canvas at the moment it is
set, and carried as the fragment: lengthening the cut later holds the last framing;
rendering on another canvas re-uses the fragment as compiled.

Import-light: the menu is data, and `looks` is imported only to compile.

### Module Attributes

| [`MAX_ZOOM`](#muvid.footage.named_looks.MAX_ZOOM)   | The largest magnification any named move may ask for.   |
|-------------------------------------------------------------|---------------------------------------------------------|

### Functions

| [`named_look_catalogue`](#muvid.footage.named_looks.named_look_catalogue)()                         | The menu as JSON rows (name, title, description, kind, params_schema).                                                                                      |
|-------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`resolve_named_look`](#muvid.footage.named_looks.resolve_named_look)(spec)                       | `{"name": ..., **params}` checked, with every parameter's value filled in (the defaults included) — the spec a cut records so a screen can show the choice. |
| [`compile_named_look`](#muvid.footage.named_looks.compile_named_look)(spec, \*, canvas, fps, ...) | `{"name": ..., **params}` → the cut's filter fragment (a `LookFragment`, which says whether it is time-varying).                                            |

### Classes

| [`NamedLook`](#muvid.footage.named_looks.NamedLook)(name, title, description, kind, build)   | A menu entry: `build(canvas=, fps=, duration_s=, **params) -> fragment`.   |
|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|

### Exceptions

| [`NamedLookError`](#muvid.footage.named_looks.NamedLookError)   | A named look that does not exist, or parameters it does not take.   |
|-------------------------------------------------------------------|---------------------------------------------------------------------|

### muvid.footage.named_looks.MAX_ZOOM *= 1.15*

The largest magnification any named move may ask for.

### *class* muvid.footage.named_looks.NamedLook(name, title, description, kind, build, params=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A menu entry: `build(canvas=, fps=, duration_s=, **params) -> fragment`.

### *exception* muvid.footage.named_looks.NamedLookError

Bases: [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError)

A named look that does not exist, or parameters it does not take.

### muvid.footage.named_looks.compile_named_look(spec, , canvas, fps, duration_s)

`{"name": ..., **params}` → the cut’s filter fragment (a `LookFragment`,
which says whether it is time-varying). Unknown names and parameters are refused.

### muvid.footage.named_looks.named_look_catalogue()

The menu as JSON rows (name, title, description, kind, params_schema).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### muvid.footage.named_looks.resolve_named_look(spec)

`{"name": ..., **params}` checked, with every parameter’s value filled in (the
defaults included) — the spec a cut records so a screen can show the choice.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
