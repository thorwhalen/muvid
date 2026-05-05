"""Surface fal progress events into the muvid project.

Whenever muvid invokes fal (during ``render``, ``render_environment``,
``transcribe`` for transcripts that arrive via fal, etc.), the
``falaw.events`` stream is written to ``.muvid/fal_events.jsonl`` so:

- ``muvid status`` can show "currently running ..." and recent
  per-shot timings;
- the local UI can stream the same events over SSE without setting
  up its own subscriber;
- post-mortem of long renders is just ``cat .muvid/fal_events.jsonl``.

The integration is opt-in via :func:`log_fal_events_to`, which yields a
context manager that subscribes / unsubscribes around the ``with``
block. ``muvid.facade.render`` wraps every render call in one.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Iterator


@contextmanager
def log_fal_events_to(path: str | Path) -> Iterator[None]:
    """Subscribe a JSONL writer to falaw's event bus for the duration.

    No-op (silent) if ``falaw`` isn't installed.
    """
    try:
        from falaw import subscribe, unsubscribe
    except Exception:
        yield
        return

    from muvid.contracts import progress_event_to_dict

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    def _write(event):
        with target.open("a") as f:
            f.write(json.dumps(progress_event_to_dict(event)) + "\n")

    subscribe(_write)
    try:
        yield
    finally:
        unsubscribe(_write)


def read_recent_fal_events(
    path: str | Path, *, limit: int = 50
) -> list[dict]:
    """Read the tail of the JSONL log. Returns empty list if absent."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        with p.open() as f:
            lines = f.readlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
