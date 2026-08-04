"""MCP tools for the footage-aligned ``music_video`` genre (thorwhalen/reelee#229).

Module-level tool functions (referenced ``muvid.mcp.footage_tools:<name>``) a host
aggregates via :func:`muvid.mcp.register_tools`. All FREE (ffmpeg + numpy only, no AI/keys).
The caller is resolved from the OAuth token; all work lands in that caller's stateful
:class:`~muvid.footage.workspace.FootageWorkspace` project. Media URLs are fetched
server-side through the SSRF-guarded, size/time-bounded fetch (video streams straight to
disk); alignment + the single-ffmpeg-pass assembly are bounded by hard resource caps and
``$MUVID_FFMPEG_TIMEOUT_S`` — the connector renders synchronously over HTTP.

Workflow: ``create_project(genre='music_video')`` → ``set_song`` → ``add_footage`` ×N →
``align_footage`` → (``footage_timeline`` to inspect) → ``assemble_music_video``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

from muvid.mcp.identity import current_email

# -- resource caps (env-tunable) --------------------------------------------
_MAX_CLIPS = int(os.environ.get("MUVID_FOOTAGE_MAX_CLIPS", "8"))
_CLIP_MAX_BYTES = int(os.environ.get("MUVID_FOOTAGE_MAX_BYTES", str(400 * 1024 * 1024)))
_CLIP_MAX_DURATION_S = int(
    os.environ.get("MUVID_FOOTAGE_MAX_CLIP_DURATION_S", str(12 * 60))
)
_SONG_MAX_BYTES = int(
    os.environ.get("MUVID_FOOTAGE_SONG_MAX_BYTES", str(100 * 1024 * 1024))
)
_SONG_MAX_DURATION_S = int(
    os.environ.get("MUVID_FOOTAGE_MAX_SONG_DURATION_S", str(12 * 60))
)
_MIN_CONFIDENCE = float(os.environ.get("MUVID_FOOTAGE_MIN_CONFIDENCE", "0.3"))


def _tool_error(msg: str):
    from fastmcp.exceptions import ToolError

    return ToolError(msg)


def _workspace():
    from muvid.footage.workspace import FootageWorkspace

    return FootageWorkspace.for_email(current_email())


def _open(project_id: str):
    """Open the caller's project, surfacing a missing project as a clean ToolError."""
    try:
        return _workspace().open_project(project_id)
    except FileNotFoundError as e:
        raise _tool_error(f"no such project {project_id!r}") from e


def _url_ext(url: str, default: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lstrip(".").lower()
    return suffix or default


def _duration(path) -> float:
    from muvid.visualize.ffmpeg import media_duration

    return float(media_duration(path))


def set_song(project_id: str, *, url: str) -> dict:
    """Set the project's fixed clean song from an http(s) URL (a direct media link). Free.

    This is the reference every uploaded clip is aligned to and whose audio the final
    video uses. Replaces any previous song. Duration/size-capped.
    """
    from muvid.mcp._fetch import FetchError, fetch_to_file_streaming

    proj = _open(project_id)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ext = _url_ext(url, "mp3")
        tmp_song = Path(tmp) / f"song.{ext}"
        try:
            fetch_to_file_streaming(url, tmp_song, max_bytes=_SONG_MAX_BYTES)
        except FetchError as e:
            raise _tool_error(f"could not fetch the song: {e}") from e
        dur = _duration(tmp_song)
        if dur > _SONG_MAX_DURATION_S:
            raise _tool_error(
                f"song is {dur:.0f}s; the {_SONG_MAX_DURATION_S}s limit is exceeded"
            )
        proj.set_song(str(tmp_song), ext=ext)
    return {"project_id": project_id, "song_duration": round(dur, 2)}


def add_footage(project_id: str, *, url: str, name: str = "") -> dict:
    """Add a footage video clip from an http(s) URL (a recording of the song). Free.

    Fetched server-side (streamed to disk; SSRF-guarded, size/duration-capped). Returns
    the assigned ``clip_id``. Re-run ``align_footage`` after adding clips.
    """
    from muvid.mcp._fetch import FetchError, fetch_to_file_streaming

    import tempfile

    proj = _open(project_id)
    if len(proj.list_clips()) >= _MAX_CLIPS:
        raise _tool_error(f"clip limit reached ({_MAX_CLIPS}); this is a bounded v1")
    clip_id = uuid.uuid4().hex[:8]
    ext = _url_ext(url, "mp4")
    # Fetch into a tempdir, then hand off to add_clip (which copies into clips/ under the
    # sanitized name) — so the download path and the stored path are distinct + sanitized,
    # and a failed/oversized fetch leaves no orphan in the project.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_clip = Path(tmp) / f"clip.{ext}"
        try:
            fetch_to_file_streaming(url, tmp_clip, max_bytes=_CLIP_MAX_BYTES)
        except FetchError as e:
            raise _tool_error(f"could not fetch the clip: {e}") from e
        dur = _duration(tmp_clip)
        if dur > _CLIP_MAX_DURATION_S:
            raise _tool_error(
                f"clip is {dur:.0f}s; the {_CLIP_MAX_DURATION_S}s limit is exceeded"
            )
        proj.add_clip(clip_id, str(tmp_clip), ext=ext, name=name)
    return {"project_id": project_id, "clip_id": clip_id, "name": name or clip_id}


def align_footage(project_id: str) -> dict:
    """Align every uploaded clip to the song by audio, and persist the result. Free.

    Returns each clip's offset, a confidence in [0,1], and its coverage of the song, plus a
    ``low_confidence`` list (clips that matched weakly — likely wrong; re-shoot or drop).
    Run this after adding/removing clips and before assembling.
    """
    from muvid.footage.align import align_footage as _align

    proj = _open(project_id)
    if not proj.has_song():
        raise _tool_error("no song set — call set_song first")
    clips = list(proj.clip_paths().items())
    if not clips:
        raise _tool_error("no footage added — call add_footage first")
    aligns = _align(str(proj.song_path()), clips, song_duration=proj.song_duration())
    proj.save_alignments(aligns)
    # New offsets invalidate every persisted score track (the song-time grid mapping moved).
    proj.invalidate_scores()
    aligned_ids = {a.clip_id for a in aligns}
    dropped = [cid for cid, _ in clips if cid not in aligned_ids]
    return {
        "project_id": project_id,
        "alignments": [a.to_dict() for a in aligns],
        "low_confidence": [a.clip_id for a in aligns if a.confidence < _MIN_CONFIDENCE],
        "dropped_no_overlap": dropped,
    }


def footage_timeline(project_id: str) -> dict:
    """The coverage map: which clips cover which spans of the song (overlaps shown). Free.

    The surface for choosing which parts to use before ``assemble_music_video``. Built from
    the persisted alignment (run ``align_footage`` first).
    """
    proj = _open(project_id)
    aligns = proj.load_alignments()
    if not aligns:
        raise _tool_error("no alignment yet — call align_footage first")
    song_dur = proj.song_duration()
    boundaries = sorted({0.0, song_dur} | {b for a in aligns for b in a.coverage})
    spans = []
    for lo, hi in zip(boundaries, boundaries[1:]):
        if hi - lo <= 1e-3:
            continue
        mid = (lo + hi) / 2
        covering = [
            {"clip_id": a.clip_id, "confidence": round(a.confidence, 3)}
            for a in aligns
            if a.coverage[0] - 1e-6 <= mid < a.coverage[1] + 1e-6
        ]
        spans.append(
            {
                "song_start": round(lo, 2),
                "song_end": round(hi, 2),
                "covered_by": covering,
            }
        )
    return {
        "project_id": project_id,
        "song_duration": round(song_dur, 2),
        "spans": spans,
        "uncovered": [s for s in spans if not s["covered_by"]],
    }


def assemble_music_video(
    project_id: str,
    *,
    strategy: str = "",
    edl: list | None = None,
    preset: str = "",
    weights: dict | None = None,
    config: dict | None = None,
) -> dict:
    """Assemble the music video — auto (a selection ``strategy``) or an explicit ``edl``. Free.

    - ``edl``: an explicit edit — a list of ``{song_start, song_end, clip_id}`` spans. Must
      be in order, non-overlapping, contiguous, and each within its clip's coverage.
    - ``strategy='weighted'`` (score-driven): the beat-snapped Viterbi selector reads the
      persisted score tracks (run ``score_footage`` first) and the selection config —
      ``preset`` ("energetic"/"contemplative") and/or ``weights`` (per-metric) and/or
      ``config`` (``lambda_switch``/``l_min_s``/``l_max_s``/``boundary_mode``). Re-weighting
      is cheap: it re-selects from the SAME scores without re-scoring.
    - otherwise **full-auto**: a registered alignment-only ``strategy`` (see
      ``list_strategies``; default ``best_confidence``) builds the edit from the alignments.

    Each cut is trimmed at its aligned in-point, scaled onto the project canvas, and
    concatenated; the CLEAN song audio for the covered span is used. Returns the render.
    """
    from muvid.footage.assemble import assemble_music_video as _assemble
    from muvid.footage.edl import derive_cuts, validate_edl
    from muvid.footage.strategy import DEFAULT_STRATEGY, select_edl
    from muvid.visualize import failures, report, verify_video

    proj = _open(project_id)
    if not proj.has_song():
        raise _tool_error("no song set — call set_song first")
    aligns = proj.load_alignments()
    if not aligns:
        raise _tool_error("no alignment — call align_footage first")
    song_dur = proj.song_duration()

    has_selection_config = bool(preset or weights or config)
    try:
        if edl is not None:
            if has_selection_config:
                raise ValueError("selection config (preset/weights/config) can't accompany an explicit edl")
            entries = validate_edl(edl, aligns, song_dur)
            used_strategy = None
        else:
            strat = strategy or (
                "weighted" if has_selection_config else DEFAULT_STRATEGY
            )
            if has_selection_config and strat != "weighted":
                raise ValueError(
                    f"selection config only applies to strategy='weighted' (got {strat!r})"
                )
            context = _selection_context(proj, strat, preset, weights, config)
            entries = validate_edl(
                select_edl(strat, aligns, song_dur, context=context), aligns, song_dur
            )
            used_strategy = strat
    except (ValueError, KeyError) as e:
        raise _tool_error(f"could not build a valid edit: {e}") from e

    cuts = derive_cuts(entries, aligns, proj.clip_paths())
    render_id = uuid.uuid4().hex[:12]
    render_dir = proj.new_render_dir(render_id)
    try:
        out = _assemble(
            cuts,
            str(proj.song_path()),
            str(render_dir / "final.mp4"),
            canvas=proj.canvas(),
        )
        checks = verify_video(out)
    except Exception:
        import shutil

        shutil.rmtree(render_dir, ignore_errors=True)
        raise

    meta = {
        "render_id": render_id,
        "video": str(out),
        "strategy": used_strategy,
        "canvas": list(proj.canvas()),
        "covered_span": [
            round(entries[0].song_start, 2),
            round(entries[-1].song_end, 2),
        ],
        "edl": [
            {
                "song_start": round(e.song_start, 2),
                "song_end": round(e.song_end, 2),
                "clip_id": e.clip_id,
            }
            for e in entries
        ],
        "ok": not failures(checks),
        "checks": report(checks),
        "note": (
            "Artifact stored server-side in your project; retrieve via footage_status. "
            "A downloadable URL awaits the storage-backend migration."
        ),
    }
    proj.write_render_meta(render_id, meta)
    return meta


def _selection_context(proj, strat, preset, weights, config):
    """Build a ``SelectionContext`` from persisted scores for the ``weighted`` strategy.

    Returns ``None`` for alignment-only strategies (they ignore context). When scores are
    absent, the tensor is ``None`` and ``weighted_selection`` raises a clear "run scoring
    first" the caller surfaces — so no scores gives a helpful error, not a silent bad edit.
    """
    if strat != "weighted":
        return None
    from muvid.footage.scoring.grid import (
        align_fingerprint,
        load_manifest,
        load_tensor,
        manifest_is_current,
    )
    from muvid.footage.select_score import SelectionContext, resolve_config

    manifest = load_manifest(proj.root) or {}
    beats = manifest.get("beats", {})
    # Stale scores (a re-align since scoring) must NOT drive a weighted edit — treat the tensor
    # as absent so weighted_selection raises the clear "run scoring first" the caller surfaces.
    fresh = manifest_is_current(
        manifest,
        song_hash=proj.song_hash() if proj.has_song() else "",
        align_fingerprint=align_fingerprint(proj.load_alignments()),
    )
    return SelectionContext(
        tensor=load_tensor(proj.root) if fresh else None,
        beat_times=beats.get("beat_times", []),
        downbeat_times=beats.get("downbeat_times", []),
        shot_boundaries=manifest.get("shot_boundaries"),
        config=resolve_config(preset=preset or None, weights=weights, config=config),
    )


def footage_status(project_id: str) -> dict:
    """Your project's song, clips, alignment summary, and renders. Free."""
    proj = _open(project_id)
    m = proj.manifest()
    return {
        "project_id": project_id,
        "title": m.get("title", project_id),
        "canvas": m.get("canvas"),
        "has_song": proj.has_song(),
        "song_duration": round(proj.song_duration(), 2) if proj.has_song() else None,
        "clips": proj.list_clips(),
        "aligned": [a.clip_id for a in proj.load_alignments()],
        "renders": proj.list_renders(),
    }


def list_strategies() -> dict:
    """The selection strategies available for full-auto assembly. Free."""
    from muvid.footage.strategy import DEFAULT_STRATEGY, list_strategies as _ls

    return {"strategies": _ls(), "default": DEFAULT_STRATEGY}
