"""MCP tools for the footage SCORING layer (thorwhalen/muvid#13).

A background scoring job (via ``nw.jobs`` — the federation's durable/cancellable async
facade, reused rather than a second system) computes per-clip score tracks; the editor +
``assemble_music_video(strategy='weighted')`` read them. All FREE (no AI/keys).

A transport over :mod:`muvid.footage.service` (``require_scorable``, ``run_scoring``,
``scores``): what stays here is the connector's own job — enqueueing on ``nw.jobs`` and the
bounded long-poll over it. A host runs the same ``score`` operation as its own job.

Key design decisions (LOCKED, see ``misc/docs/footage_scoring_design.md``):

- **Scoring is keyed on INPUTS ONLY** (``song_hash`` + an alignment fingerprint + the metric
  set + hop). Weights/preset are NOT here — they enter at ``assemble`` time, so ONE tensor is
  reused across every preset for free, and a re-align mid-flight yields a NEW job (not a dedup
  to the stale run).
- **Bounded long-poll** status so an agent needs ~1 poll, not ~15.
- **NaN never hits the wire** — masked entries serialize as ``null``; the ``mask`` array is
  authoritative.
"""

from __future__ import annotations

import hashlib
import os
import time

from muvid.footage import service
from muvid.mcp.footage_tools import _refusals, _tool_error
from muvid.mcp.identity import current_email

_SCORE_KIND = "footage.score"
#: Cap on the long-poll wait (safely under the connector's HTTP request timeout).
_MAX_WAIT_S = int(os.environ.get("MUVID_SCORING_MAX_WAIT_S", "25"))
#: Default cap on points-per-(clip,metric) returned over the wire (the service's).
_MAX_POINTS = service.MAX_WIRE_POINTS


def _open(project_id: str):
    from muvid.footage.workspace import FootageWorkspace

    try:
        return FootageWorkspace.for_email(current_email()).open_project(project_id)
    except FileNotFoundError as e:
        raise _tool_error(f"no such project {project_id!r}") from e


def _score_dispatch():
    """The nw.jobs dispatch callable for a scoring job (kept tiny + picklable-free)."""

    def _run_scoring(
        project, params, *, job_id=None, on_event=None, should_cancel=None
    ):
        return service.run_scoring(
            project,
            metrics=params.get("metrics"),
            hop_s=params.get("hop_s", service.DEFAULT_SCORE_HOP_S),
            enable_lipsync=params.get("enable_lipsync"),
            progress_cb=on_event,
            should_cancel=should_cancel,
        )

    return {_SCORE_KIND: _run_scoring}


def score_footage(
    project_id: str, *, hop_s: float = 0.1, metrics: list | None = None
) -> dict:
    """Kick a BACKGROUND job that scores every aligned clip (quality + motion-to-beat). Free.

    Returns immediately with a ``job_id``; poll ``footage_score_status``. Scoring extracts ALL
    core metrics (re-weight later at assemble time — no re-scoring needed). Requires a song +
    a run of ``align_footage`` first. The heavy lip-sync tier is OFF by default (opt-in,
    off-prod).
    """
    try:
        from nw import jobs as nw_jobs
    except Exception as e:  # muvid[mcp] pins nw; this only fails on a broken env
        raise _tool_error(f"scoring needs nw.jobs (muvid[mcp]): {e}") from e

    proj = _open(project_id)
    with _refusals():
        aligns = service.require_scorable(proj)

    # Input-only idempotency: song content + offsets + metric set + hop. A re-align changes
    # the fingerprint → a fresh job (never a dedup onto the stale offsets).
    from muvid.footage.scoring.grid import align_fingerprint

    key_basis = f"{proj.song_hash()}:{align_fingerprint(aligns)}:{sorted(metrics or [])}:{hop_s}"
    idem = hashlib.sha256(f"{proj.root}:{_SCORE_KIND}:{key_basis}".encode()).hexdigest()

    job = nw_jobs.enqueue(
        proj,
        _SCORE_KIND,
        {
            "hop_s": hop_s,
            "metrics": metrics,
            "estimated_usd": 0.0,
            "output_kind": "compute",
        },
        dispatch=_score_dispatch(),
        idempotency_key=idem,
        label="Score footage",
        on_event=None,
    )
    return {"project_id": project_id, "job_id": job.job_id, "status": job.status}


def footage_score_status(
    project_id: str, *, job_id: str = "", wait_s: float = 0
) -> dict:
    """The scoring job's status (bounded long-poll). Free.

    Pass the ``job_id`` from ``score_footage`` (or omit for the newest scoring job). With
    ``wait_s`` > 0 this blocks up to ~``wait_s`` seconds (capped), returning early on a
    terminal state — so an agent needs ~1 poll, not many.
    """
    try:
        from nw import jobs as nw_jobs
    except Exception as e:  # same clean ToolError as score_footage on a broken env
        raise _tool_error(f"scoring needs nw.jobs (muvid[mcp]): {e}") from e

    proj = _open(project_id)
    deadline = time.monotonic() + min(max(0.0, wait_s), _MAX_WAIT_S)

    def _current():
        if job_id:
            return nw_jobs.get_job(proj, job_id)
        jobs = [j for j in nw_jobs.list_jobs(proj) if j.kind == _SCORE_KIND]
        return jobs[0] if jobs else None

    job = _current()
    terminal = {"succeeded", "failed", "cancelled"}
    while (
        job is not None and job.status not in terminal and time.monotonic() < deadline
    ):
        time.sleep(0.5)
        job = _current()
    if job is None:
        raise _tool_error("no scoring job found — call score_footage first")
    return {
        "project_id": project_id,
        "job_id": job.job_id,
        "status": job.status,
        "pct": job.pct,
        "stage": _stage_label(job),
        "error": job.error,
        "result": job.result,
    }


def _stage_label(job) -> str | None:
    p = job.progress
    if p.current_transform is None:
        return None
    if p.stage_index is not None and p.stage_count:
        return f"{p.current_transform} ({p.stage_index + 1}/{p.stage_count})"
    return p.current_transform


def footage_scores(
    project_id: str,
    *,
    clip_id: str = "",
    metrics: list | None = None,
    max_points: int = _MAX_POINTS,
) -> dict:
    """The persisted score tracks — for the multichannel editor + inspection. Free.

    - No ``clip_id`` → a SUMMARY (metrics, per-clip coverage %, beats, tempo, the decimated
      ``selection_margin``, grid geometry) — bounded, safe as the default.
    - ``clip_id`` → that clip's tracks (values as ``null``-masked arrays, decimated to
      ``max_points`` per metric), for the editor's lanes.
    """
    proj = _open(project_id)
    with _refusals():
        return service.scores(
            proj, clip_id=clip_id, metrics=metrics, max_points=max_points
        )
