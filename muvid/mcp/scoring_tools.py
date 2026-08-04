"""MCP tools for the footage SCORING layer (thorwhalen/muvid#13).

A background scoring job (via ``nw.jobs`` — the federation's durable/cancellable async
facade, reused rather than a second system) computes per-clip score tracks; the editor +
``assemble_music_video(strategy='weighted')`` read them. All FREE (no AI/keys).

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
import math
import os
import time

from muvid.mcp.identity import current_email

_SCORE_KIND = "footage.score"
#: Cap on the long-poll wait (safely under the connector's HTTP request timeout).
_MAX_WAIT_S = int(os.environ.get("MUVID_SCORING_MAX_WAIT_S", "25"))
#: Default cap on points-per-(clip,metric) returned over the wire.
_MAX_POINTS = int(os.environ.get("MUVID_SCORING_MAX_POINTS_WIRE", "1500"))


def _tool_error(msg: str):
    from fastmcp.exceptions import ToolError

    return ToolError(msg)


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
        from muvid.footage.scoring import score_project

        return score_project(
            project,
            metrics=params.get("metrics"),
            hop_s=params.get("hop_s", 0.1),
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
    if not proj.has_song():
        raise _tool_error("no song set — call set_song first")
    aligns = proj.load_alignments()
    if not aligns:
        raise _tool_error("no alignment — call align_footage first")

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
    from muvid.footage.scoring.grid import (
        align_fingerprint,
        load_manifest,
        load_tensor,
        manifest_is_current,
    )

    proj = _open(project_id)
    manifest = load_manifest(proj.root)
    if manifest is None:
        raise _tool_error("no scores yet — call score_footage first")
    # Staleness guard: a re-align/new song that raced the score job leaves stale scores.
    if not manifest_is_current(
        manifest,
        song_hash=proj.song_hash(),
        align_fingerprint=align_fingerprint(proj.load_alignments()),
    ):
        raise _tool_error(
            "scores are stale (song or alignment changed) — re-run score_footage"
        )
    tensor = load_tensor(proj.root)
    if tensor is None:
        raise _tool_error("scores are present but unreadable — re-run score_footage")

    grid = {
        "t0": manifest["t0"],
        "hop_s": manifest["hop_s"],
        "n": manifest["n"],
        "song_duration": round(manifest["n"] * manifest["hop_s"], 3),
    }
    if not clip_id:
        return _scores_summary(proj, tensor, manifest, grid, max_points)
    return _clip_scores(tensor, manifest, grid, clip_id, metrics, max_points)


def _scores_summary(proj, tensor, manifest, grid, max_points) -> dict:
    from muvid.footage.select_score import selection_margin

    aligns = proj.load_alignments()
    margin = selection_margin(aligns, tensor)
    coverage = manifest.get("coverage", {})
    beats = manifest.get("beats", {}).get("beat_times", [])
    return {
        "project_id": proj.project_id,
        "metrics": tensor.metrics,
        "clips": [
            {
                "clip_id": cid,
                "coverage": round(
                    float(tensor.M[tensor.clip_index(cid)].any(axis=1).mean()), 4
                ),
            }
            for cid in tensor.clip_ids
        ],
        "metric_coverage": coverage,
        "beats": {"count": len(beats), "times": _decimate_list(beats, max_points)},
        "tempo_bpm": manifest.get("tempo_bpm"),
        "selection_margin": _decimate_values(margin, max_points, pool="min"),
        "grid": grid,
        "lipsync_enabled": manifest.get("lipsync_enabled", False),
    }


def _clip_scores(tensor, manifest, grid, clip_id, metrics, max_points) -> dict:
    if clip_id not in tensor.clip_ids:
        raise _tool_error(f"unknown clip {clip_id!r}; scored: {tensor.clip_ids}")
    ci = tensor.clip_index(clip_id)
    want = [m for m in tensor.metrics if not metrics or m in set(metrics)]
    out_metrics = {}
    for m in want:
        mi = tensor.metric_index(m)
        vals = tensor.S[ci, :, mi]
        mask = tensor.M[ci, :, mi]
        out_metrics[m] = {
            "values": _decimate_values(vals, max_points),  # null where masked
            "mask": _decimate_bool(mask, max_points),
            "direction": manifest.get("directions", {}).get(m, "higher_better"),
            "norm": manifest.get("norms", {}).get(m),
        }
    return {"clip_id": clip_id, "metrics": out_metrics, "grid": grid}


# -- bounded, NaN-safe serialization -----------------------------------------


def _stride(n: int, max_points: int) -> int:
    return max(1, math.ceil(n / max(1, max_points)))


def _decimate_values(arr, max_points: int, *, pool: str = "stride") -> list:
    """Decimate a float array to ≤max_points; NaN → ``null`` (never a NaN JSON token)."""
    import numpy as np

    a = np.asarray(arr, dtype=float)
    s = _stride(len(a), max_points)
    if s == 1:
        picked = a
    elif pool == "min":  # preserve toss-up minima (selection_margin)
        picked = np.array(
            [
                np.nanmin(a[i : i + s]) if np.isfinite(a[i : i + s]).any() else np.nan
                for i in range(0, len(a), s)
            ]
        )
    else:
        picked = a[::s]
    return [None if not math.isfinite(x) else round(float(x), 4) for x in picked]


def _decimate_bool(arr, max_points: int) -> list:
    import numpy as np

    a = np.asarray(arr, dtype=bool)
    s = _stride(len(a), max_points)
    return [bool(x) for x in a[::s]]


def _decimate_list(xs, max_points: int) -> list:
    s = _stride(len(xs), max_points)
    return [round(float(x), 4) for x in xs[::s]]
