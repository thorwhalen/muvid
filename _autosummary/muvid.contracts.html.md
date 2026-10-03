# muvid.contracts

Adapters between muvid’s SSOT and sibling-package shapes.

muvid’s source of truth is `project.json` + a folder of cards. The
sibling packages (`falaw`, `an`, `lacing`) have their own typed
shapes for related concepts — `falaw.Character` carries inline URLs,
`an.audio.WordTiming` is a tuple, etc. Each package having its own
abstraction is intentional (different concerns, different lifecycles),
but **translation** between them belongs in one place — here — so the
seams are inspectable and a single change in any sibling’s type only
ripples through one module.

Three families of adapters:

- `character_to_falaw(project, name)` / `environment_to_falaw(...)`:
  build live `falaw.Character` / `falaw.Environment` instances from
  muvid’s persistent card.json + curated reference images.
- `word_timings_for_window(project, start_s, end_s)`: pull
  `(text, start, end)` tuples from the project’s lacing alignment
  store, in the shape `an.audio.WordTimingProvider` and
  `muvid.cost` callers expect. Times are absolute (in song-seconds),
  not slice-relative — see [`shifted_word_timings()`](#muvid.contracts.shifted_word_timings) if you need
  shot-slice-relative output.
- `shifted_word_timings(timings, *, offset_s)` / `progress_event_to_dict(event)`:
  pure-data transformations that don’t need a project handle.

These are deliberately thin: each function delegates to the canonical
home (`falaw.scene.Character`, `lacing.tracks.subtitle.SubtitleTrack`,
etc.) and just glues to muvid’s persistent state. None of them
introduces new types — they translate, they don’t redefine.

### Functions

| [`character_to_falaw`](#muvid.contracts.character_to_falaw)(project, name)              | Build a `falaw.Character` from this project's character card.     |
|-------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| [`environment_to_falaw`](#muvid.contracts.environment_to_falaw)(project, name)            | Build a `falaw.Environment` from this project's environment card. |
| [`progress_event_to_dict`](#muvid.contracts.progress_event_to_dict)(event)                  | Serialize a `falaw.ProgressEvent` into a JSON-safe dict.          |
| [`shifted_word_timings`](#muvid.contracts.shifted_word_timings)(timings, \*, offset_s)    | Shift each timing by `-offset_s` (clamping starts at 0).          |
| [`word_timings_for_window`](#muvid.contracts.word_timings_for_window)(project, start_s, ...) | Read `(text, start, end)` tuples from the alignment store.        |

### muvid.contracts.character_to_falaw(project, name)

Build a `falaw.Character` from this project’s character card.

Resolves `reference_image_url` from the curated anchor (the first
`selected/` image, falling back to the first `refs/` image) so
downstream falaw renders have a usable URL or local path. The
voice spec (if any) is mirrored 1:1.

### muvid.contracts.environment_to_falaw(project, name)

Build a `falaw.Environment` from this project’s environment card.

### muvid.contracts.progress_event_to_dict(event)

Serialize a `falaw.ProgressEvent` into a JSON-safe dict.

The shape matches the records [`muvid.events.log_fal_events_to()`](muvid.events.html.md#muvid.events.log_fal_events_to)
writes — pulled out as a public helper so other consumers (a UI
SSE stream, a remote telemetry sink) don’t have to rebuild it.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.contracts.shifted_word_timings(timings, , offset_s)

Shift each timing by `-offset_s` (clamping starts at 0).

Use when handing absolute song-time timings to a tool that wants
them relative to a shot’s audio slice (where t=0 is the slice’s
start). Mirrors `audio[shot.start_s:shot.end_s]` cropping.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float)]]

### muvid.contracts.word_timings_for_window(project, start_s, end_s, , asset_id=None)

Read `(text, start, end)` tuples from the alignment store.

Times are **absolute song-seconds**. Returns an empty list when
the alignment store doesn’t exist yet, or when `lacing` /
`lacing.tracks` aren’t installed (so callers can degrade
gracefully).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float)]]
