"""``muvid.events`` — bridge falaw progress events to a per-project JSONL log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("falaw")


def test_log_fal_events_to_writes_jsonl(tmp_path):
    from falaw.events import ProgressEvent, emit
    from muvid.events import log_fal_events_to, read_recent_fal_events

    log = tmp_path / ".muvid" / "fal_events.jsonl"
    with log_fal_events_to(log):
        emit(
            ProgressEvent(
                kind="queued", application="fal-ai/x",
                call_id="abc123", elapsed_s=0.0,
            )
        )
        emit(
            ProgressEvent(
                kind="done", application="fal-ai/x",
                call_id="abc123", elapsed_s=0.5,
            )
        )

    assert log.exists()
    rows = read_recent_fal_events(log)
    assert [r["kind"] for r in rows] == ["queued", "done"]
    assert all(r["application"] == "fal-ai/x" for r in rows)


def test_log_fal_events_unsubscribes_on_exit(tmp_path):
    """After leaving the context, no further events are written."""
    from falaw.events import ProgressEvent, emit
    from muvid.events import log_fal_events_to, read_recent_fal_events

    log = tmp_path / ".muvid" / "fal_events.jsonl"
    with log_fal_events_to(log):
        emit(
            ProgressEvent(kind="queued", application="x", call_id="1")
        )
    # Outside the context — should not append.
    emit(ProgressEvent(kind="done", application="x", call_id="1"))

    rows = read_recent_fal_events(log)
    assert [r["kind"] for r in rows] == ["queued"]


def test_log_fal_events_no_op_if_falaw_missing(tmp_path, monkeypatch):
    """The contextmanager doesn't crash if falaw cannot be imported."""
    import sys

    monkeypatch.setitem(sys.modules, "falaw", None)
    from muvid.events import log_fal_events_to

    log = tmp_path / "x.jsonl"
    with log_fal_events_to(log):
        pass
    # Without falaw, nothing should be written.
    assert not log.exists()


def test_status_includes_recent_fal_events(tmp_path):
    """``facade.status`` exposes the most recent fal events."""
    from falaw.events import ProgressEvent, emit
    from muvid import facade
    from muvid.events import log_fal_events_to

    facade.init_project(tmp_path / "p")
    log = tmp_path / "p" / ".muvid" / "fal_events.jsonl"
    with log_fal_events_to(log):
        emit(
            ProgressEvent(kind="queued", application="fal-ai/test", call_id="x")
        )
        emit(
            ProgressEvent(kind="done", application="fal-ai/test", call_id="x")
        )

    s = facade.status(tmp_path / "p")
    assert "recent_fal_events" in s
    assert [e["kind"] for e in s["recent_fal_events"]] == ["queued", "done"]
