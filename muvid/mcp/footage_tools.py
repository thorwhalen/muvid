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
#: Total-bytes cap for a folder archive (env ``MUVID_FOOTAGE_FOLDER_MAX_BYTES``). A shoot
#: is several clips, so this is necessarily larger than the per-clip cap.
_FOLDER_MAX_BYTES = int(
    os.environ.get("MUVID_FOOTAGE_FOLDER_MAX_BYTES", str(3 * 1024 * 1024 * 1024))
)


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


#: Media extensions an archive member must carry to be treated as footage.
_VIDEO_EXTENSIONS = ("mp4", "mov", "m4v", "webm", "avi", "mkv", "mpg", "mpeg", "3gp")


def _resolve_media_url(url: str, *, what: str) -> str:
    """Normalise a pasted share link to a direct-download URL, refusing folder links.

    A user pastes what their cloud drive gave them, which is a *page*, not a download. This
    is where that becomes a fetchable URL — and where a folder link is turned away with the
    name of the tool that does handle it, rather than being fetched and failing later as an
    unreadable 'media' file.
    """
    from muvid.mcp._fetch import FetchError, resolve_share_link

    try:
        direct, kind = resolve_share_link(url)
    except FetchError as e:
        raise _tool_error(f"could not resolve the {what} link: {e}") from e
    if kind in ("archive", "folder"):
        raise _tool_error(
            f"that is a FOLDER link, not a single {what} — one such link holds many files. "
            f"Use add_footage_folder(project_id, url=...) to add every clip in it, or share "
            f"the individual file and pass its link here."
        )
    return direct


def set_song(project_id: str, *, url: str) -> dict:
    """Set the project's fixed clean song from an http(s) URL. Free.

    Accepts a **share link** (Google Drive / Dropbox / OneDrive) as well as a direct media
    URL — the link is normalised before fetching, and the downloaded bytes are checked to be
    media, so a private-file sign-in page is refused with that diagnosis rather than stored.

    This is the reference every uploaded clip is aligned to and whose audio the final
    video uses. Replaces any previous song. Duration/size-capped.
    """
    from muvid.mcp._fetch import FetchError, fetch_to_file_streaming

    proj = _open(project_id)
    import tempfile

    direct = _resolve_media_url(url, what="song")
    with tempfile.TemporaryDirectory() as tmp:
        ext = _url_ext(url, "") or _url_ext(direct, "mp3")
        tmp_song = Path(tmp) / f"song.{ext}"
        try:
            fetch_to_file_streaming(
                direct, tmp_song, max_bytes=_SONG_MAX_BYTES, expect_kind="audio"
            )
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

    Accepts a **share link** as well as a direct media URL. Fetched server-side (streamed to
    disk; SSRF-guarded, size/duration-capped) and asserted to be media before anything is
    stored. Returns the assigned ``clip_id``. Re-run ``align_footage`` after adding clips.

    For a whole shoot in one folder, use :func:`add_footage_folder` — a folder link holds
    many files and is refused here by name.
    """
    from muvid.mcp._fetch import FetchError, fetch_to_file_streaming

    import tempfile

    proj = _open(project_id)
    if len(proj.list_clips()) >= _MAX_CLIPS:
        raise _tool_error(f"clip limit reached ({_MAX_CLIPS}); this is a bounded v1")
    direct = _resolve_media_url(url, what="clip")
    clip_id = uuid.uuid4().hex[:8]
    ext = _url_ext(url, "") or _url_ext(direct, "mp4")
    # Fetch into a tempdir, then hand off to add_clip (which copies into clips/ under the
    # sanitized name) — so the download path and the stored path are distinct + sanitized,
    # and a failed/oversized fetch leaves no orphan in the project.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_clip = Path(tmp) / f"clip.{ext}"
        try:
            fetch_to_file_streaming(
                direct, tmp_clip, max_bytes=_CLIP_MAX_BYTES, expect_kind="video"
            )
        except FetchError as e:
            raise _tool_error(f"could not fetch the clip: {e}") from e
        dur = _duration(tmp_clip)
        if dur > _CLIP_MAX_DURATION_S:
            raise _tool_error(
                f"clip is {dur:.0f}s; the {_CLIP_MAX_DURATION_S}s limit is exceeded"
            )
        proj.add_clip(clip_id, str(tmp_clip), ext=ext, name=name)
    return {"project_id": project_id, "clip_id": clip_id, "name": name or clip_id}


def add_footage_folder(project_id: str, *, url: str, name_prefix: str = "") -> dict:
    """Add EVERY clip in a shared folder (Drive / Dropbox / OneDrive) in one call. Free.

    A shoot is a folder, not a file — this is the natural unit for music-video footage. The
    folder link is normalised and downloaded as a single archive server-side, then expanded
    into one clip per media member.

    Members that are skipped — wrong type, over the per-clip size limit, or past the project
    clip cap — are NAMED in ``skipped`` with the reason. Nothing is silently truncated: a
    coverage decision made on quietly-shortened input is worse than one made on a short list.

    Returns the added clips and the skipped members. Run ``align_footage`` afterwards.
    """
    import tempfile

    from muvid.mcp._fetch import (
        FetchError,
        extract_media_members,
        fetch_to_file_streaming,
        resolve_share_link,
    )

    proj = _open(project_id)
    existing = len(proj.list_clips())
    room = _MAX_CLIPS - existing
    if room <= 0:
        raise _tool_error(f"clip limit reached ({_MAX_CLIPS}); this is a bounded v1")

    try:
        direct, kind = resolve_share_link(url)
    except FetchError as e:
        raise _tool_error(f"could not resolve the folder link: {e}") from e
    if kind == "folder":
        raise _tool_error(
            "this provider offers no downloadable URL for a folder — listing it needs an "
            "API credential. Share the files individually and add them with add_footage."
        )
    if kind != "archive":
        raise _tool_error(
            "that link points at a single file, not a folder — use add_footage for it."
        )

    added, skipped = [], []
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "folder.zip"
        try:
            fetch_to_file_streaming(
                direct, archive, max_bytes=_FOLDER_MAX_BYTES, expect_kind="archive"
            )
        except FetchError as e:
            raise _tool_error(f"could not fetch the folder: {e}") from e
        members, skipped = extract_media_members(
            archive,
            Path(tmp) / "members",
            extensions=_VIDEO_EXTENSIONS,
            max_members=room,
            max_member_bytes=_CLIP_MAX_BYTES,
        )
        for member in members:
            dur = _duration(member)
            if dur > _CLIP_MAX_DURATION_S:
                skipped.append(
                    {
                        "name": member.name,
                        "reason": f"{dur:.0f}s exceeds the {_CLIP_MAX_DURATION_S}s limit",
                    }
                )
                continue
            clip_id = uuid.uuid4().hex[:8]
            label = f"{name_prefix}{member.stem}" if name_prefix else member.stem
            proj.add_clip(
                clip_id, str(member), ext=member.suffix.lstrip(".").lower(), name=label
            )
            added.append(
                {"clip_id": clip_id, "name": label, "duration": round(dur, 2)}
            )
    return {
        "project_id": project_id,
        "added": added,
        "skipped": skipped,
        "clip_count": existing + len(added),
    }


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
    # A confidence is guidance, not a verdict — so say what produced it and what it is being
    # compared against. A bare list of rejected ids is undiagnosable: the caller cannot tell
    # a genuinely unrelated clip from a correctly-aligned one that the threshold happened to
    # miss (muvid#15).
    return {
        "project_id": project_id,
        "alignments": [a.to_dict() for a in aligns],
        "low_confidence": [
            {"clip_id": a.clip_id, "confidence": round(a.confidence, 3)}
            for a in aligns
            if a.confidence < _MIN_CONFIDENCE
        ],
        "confidence_metric": "onset-envelope correlation at the waveform's lag",
        "confidence_threshold": _MIN_CONFIDENCE,
        "offset_consensus": _offset_consensus(aligns),
        "dropped_no_overlap": dropped,
    }


def _offset_consensus(aligns) -> dict:
    """How well the clips AGREE on their offsets — evidence a per-clip score cannot give.

    Several devices recording one performance land at nearly the same offset, so a clip far
    from the cluster is the suspect one regardless of its confidence. This separates real
    footage from unrelated material where the per-clip number does not: on a real shoot,
    five clips clustered within 1.6 s while the outlier sat 79 s away — yet one of the five
    scored *below* the confidence threshold and one of them scored barely above it.

    Reported, never enforced: it is a signal for the caller, not another silent gate.
    """
    if len(aligns) < 2:
        return {"median_offset": None, "outliers": []}
    offsets = sorted(a.offset_s for a in aligns)
    median = offsets[len(offsets) // 2]
    spread = [abs(a.offset_s - median) for a in aligns]
    # Anything more than 5 s from the median is not the same take.
    return {
        "median_offset": round(median, 3),
        "outliers": [
            {"clip_id": a.clip_id, "offset_s": round(a.offset_s, 2), "from_median": round(d, 2)}
            for a, d in zip(aligns, spread)
            if d > 5.0
        ],
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


def _coverage_report(entries, aligns, song_dur: float) -> dict:
    """What the song's timeline looks like under ``entries`` — covered, weak, and MISSING.

    Answers the three questions a person actually has about a proposed edit, in the form the
    directive requires: an aggregate percentage is not enough, so uncovered audio is named
    with explicit start and end times, and a span whose only footage is weakly aligned is
    listed separately with the confidence that makes it weak.
    """
    by_id = {a.clip_id: a for a in aligns}
    covered = sorted((e.song_start, e.song_end) for e in entries)
    gaps, cursor = [], 0.0
    for lo, hi in covered:
        if lo - cursor > 1e-3:
            gaps.append({"song_start": round(cursor, 2), "song_end": round(lo, 2)})
        cursor = max(cursor, hi)
    if song_dur - cursor > 1e-3:
        gaps.append({"song_start": round(cursor, 2), "song_end": round(song_dur, 2)})
    weak = [
        {
            "song_start": round(e.song_start, 2),
            "song_end": round(e.song_end, 2),
            "clip_id": e.clip_id,
            "confidence": round(by_id[e.clip_id].confidence, 3),
        }
        for e in entries
        if e.clip_id in by_id and by_id[e.clip_id].confidence < _MIN_CONFIDENCE
    ]
    covered_s = sum(hi - lo for lo, hi in covered)
    return {
        "song_duration": round(song_dur, 2),
        "covered_seconds": round(covered_s, 2),
        "coverage_fraction": round(covered_s / song_dur, 4) if song_dur else 0.0,
        "uncovered": gaps,
        "weak_segments": weak,
        "confidence_threshold": _MIN_CONFIDENCE,
    }


def propose_edit(
    project_id: str,
    *,
    strategy: str = "",
    preset: str = "",
    weights: dict | None = None,
    config: dict | None = None,
) -> dict:
    """Propose an EDL **without rendering it** — the cheap half of assembly. Free, seconds.

    Selection and rendering are separate concerns, and only one of them costs an encode.
    This returns the edit an ``assemble_music_video`` call *would* have produced, so a
    caller can compare several strategies/weightings, read the coverage report, edit the
    list by hand, and only then pay for a render — passing the chosen EDL straight back to
    ``assemble_music_video(project_id, edl=...)``.

    Returns the ``edl`` (ready to feed back verbatim), the ``strategy`` actually used, and a
    ``coverage`` report naming every uncovered span of the song and every segment that made
    the cut despite weak alignment. Same arguments as ``assemble_music_video``'s auto path.
    """
    from muvid.footage.edl import validate_edl
    from muvid.footage.strategy import DEFAULT_STRATEGY, select_edl

    proj = _open(project_id)
    if not proj.has_song():
        raise _tool_error("no song set — call set_song first")
    aligns = proj.load_alignments()
    if not aligns:
        raise _tool_error("no alignment — call align_footage first")
    song_dur = proj.song_duration()
    has_selection_config = bool(preset or weights or config)
    strat = strategy or ("weighted" if has_selection_config else DEFAULT_STRATEGY)
    try:
        context = _selection_context(proj, strat, preset, weights, config)
        entries = validate_edl(
            select_edl(strat, aligns, song_dur, context=context), aligns, song_dur
        )
    except (ValueError, KeyError) as e:
        raise _tool_error(f"could not build a valid edit: {e}") from e
    return {
        "project_id": project_id,
        "strategy": strat,
        "edl": [
            {
                "song_start": e.song_start,
                "song_end": e.song_end,
                "clip_id": e.clip_id,
            }
            for e in entries
        ],
        "coverage": _coverage_report(entries, aligns, song_dur),
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
                raise ValueError(
                    "selection config (preset/weights/config) can't accompany an explicit edl"
                )
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
