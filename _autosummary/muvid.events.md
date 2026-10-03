# muvid.events

Surface fal progress events into the muvid project.

Whenever muvid invokes fal (during `render`, `render_environment`,
`transcribe` for transcripts that arrive via fal, etc.), the
`falaw.events` stream is written to `.muvid/fal_events.jsonl` so:

- `muvid status` can show “currently running …” and recent
  per-shot timings;
- the local UI can stream the same events over SSE without setting
  up its own subscriber;
- post-mortem of long renders is just `cat .muvid/fal_events.jsonl`.

The integration is opt-in via [`log_fal_events_to()`](#muvid.events.log_fal_events_to), which yields a
context manager that subscribes / unsubscribes around the `with`
block. `muvid.facade.render` wraps every render call in one.

### Functions

| [`log_fal_events_to`](#muvid.events.log_fal_events_to)(path)                   | Subscribe a JSONL writer to falaw's event bus for the duration.   |
|--------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| [`read_recent_fal_events`](#muvid.events.read_recent_fal_events)(path, \*[, limit]) | Read the tail of the JSONL log.                                   |

### muvid.events.log_fal_events_to(path)

Subscribe a JSONL writer to falaw’s event bus for the duration.

No-op (silent) if `falaw` isn’t installed.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`None`](https://docs.python.org/3/builtins/constants.html#None)]

### muvid.events.read_recent_fal_events(path, , limit=50)

Read the tail of the JSONL log. Returns empty list if absent.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]
