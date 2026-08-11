"""MCP tools for the footage-aligned ``music_video`` genre (thorwhalen/reelee#229).

Module-level tool functions (referenced ``muvid.mcp.footage_tools:<name>``) a host
aggregates via :func:`muvid.mcp.register_tools`. All FREE (ffmpeg + numpy only, no AI/keys).
The caller is resolved from the OAuth token; all work lands in that caller's stateful
:class:`~muvid.footage.workspace.FootageWorkspace` project. Media URLs are fetched
server-side through the SSRF-guarded, size/time-bounded fetch (video streams straight to
disk); alignment + assembly are bounded by hard resource caps and
``$MUVID_FFMPEG_TIMEOUT_S`` (assembly runs one bounded single-input ffmpeg per cut, so
memory does not grow with cut count) — the connector renders synchronously over HTTP.

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
#: Below this, an alignment is REPORTED as low-confidence (never dropped — see
#: align_footage). Calibrated for the clean-master-vs-phone regime the connector actually
#: sees, on the onset-envelope feature (muvid#15): measured against a studio master, four
#: provably-correct clips scored 0.173–0.603 while the one genuinely unrelated clip scored
#: 0.021 — so 0.1 separates them ~2x/5x, where the old 0.3 (a defensible number for the
#: EASIER clip-to-clip regime) flagged five of six correct alignments as suspect.
_MIN_CONFIDENCE = float(os.environ.get("MUVID_FOOTAGE_MIN_CONFIDENCE", "0.1"))
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
            added.append({"clip_id": clip_id, "name": label, "duration": round(dur, 2)})
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
    from muvid.footage.scoring.grid import align_fingerprint

    proj = _open(project_id)
    if not proj.has_song():
        raise _tool_error("no song set — call set_song first")
    clips = list(proj.clip_paths().items())
    if not clips:
        raise _tool_error("no footage added — call add_footage first")
    old_fingerprint = align_fingerprint(proj.load_alignments())
    aligns = _align(str(proj.song_path()), clips, song_duration=proj.song_duration())
    proj.save_alignments(aligns)
    # Scores are keyed to the offsets they were computed under (align_fingerprint is that
    # key's SSOT), so a re-align that reproduces the same offsets must not throw away the
    # most expensive artifact in the pipeline (muvid#24 B4). Correctness does not depend on
    # deleting here — the read path refuses stale scores via manifest_is_current — this
    # only reclaims storage the moment the scores are known to be stale.
    if align_fingerprint(aligns) != old_fingerprint:
        proj.invalidate_scores()
    # Every clip has a record now, so "did not overlap" is a REPORTED PROPERTY of a clip
    # that is still there, not an inference from something missing. A clip is never removed
    # from the project or from the alignment artifact by anything but an explicit request:
    # choosing what goes into an edit is a matter of referencing sources and intervals, and
    # a source must stay referenceable whatever its measurements say.
    aligned_ids = {a.clip_id for a in aligns}
    no_overlap = [a.clip_id for a in aligns if not a.overlaps]
    missing = [cid for cid, _ in clips if cid not in aligned_ids]
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
        # Usable-for-an-edit, not present-in-the-project: these clips are still here, still
        # listed, still addressable — they just cover no part of the song.
        "no_overlap_with_song": no_overlap,
        # Should always be empty. Non-empty means a clip lost its record somewhere upstream,
        # which is a bug, not a verdict about the footage.
        "unrecorded": missing,
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
            {
                "clip_id": a.clip_id,
                "offset_s": round(a.offset_s, 2),
                "from_median": round(d, 2),
            }
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


def _edl_json(e) -> dict:
    """One EDL entry as JSON — full precision (it must feed back verbatim), gaps as null."""
    return {
        "song_start": e.song_start,
        "song_end": e.song_end,
        "clip_id": e.clip_id or None,
    }


def _coverage_report(entries, aligns, song_dur: float) -> dict:
    """What the song's timeline looks like under ``entries`` — covered, weak, and MISSING.

    Answers the three questions a person actually has about a proposed edit, in the form the
    directive requires: an aggregate percentage is not enough, so uncovered audio is named
    with explicit start and end times, and a span whose only footage is weakly aligned is
    listed separately with the confidence that makes it weak.

    Pass only FOOTAGE entries: a gap entry renders fill, and filled is not covered — the
    user still has no footage there, which is precisely what this report exists to say.
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

    Returns the ``edl`` (ready to feed back verbatim — spans the WHOLE song, with spans no
    footage covers as explicit gap entries, ``clip_id: null``, rendered as black), the
    ``strategy`` actually used, and a ``coverage`` report naming every uncovered span of
    the song and every segment that made the cut despite weak alignment. Same arguments as
    ``assemble_music_video``'s auto path.
    """
    from muvid.footage.edl import fill_gaps, validate_edl
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
            fill_gaps(select_edl(strat, aligns, song_dur, context=context), song_dur),
            aligns,
            song_dur,
        )
    except (ValueError, KeyError) as e:
        raise _tool_error(f"could not build a valid edit: {e}") from e
    return {
        "project_id": project_id,
        "strategy": strat,
        "edl": [_edl_json(e) for e in entries],
        "coverage": _coverage_report(
            [e for e in entries if not e.is_gap], aligns, song_dur
        ),
    }


def _resolve_canvas(proj, canvas: str) -> tuple[int, int]:
    """The render canvas: an explicit per-render override, else the project's.

    The project canvas is fixed at create time, but re-rendering the same edit as portrait
    must not require a new project and a re-upload of every asset (muvid#21 item 7) —
    rendering is cheap and repeatable BY DESIGN, so the render call owns this knob.
    """
    from muvid.footage.workspace import CANVASES

    if not canvas:
        return proj.canvas()
    if canvas not in CANVASES:
        raise _tool_error(
            f"unknown canvas {canvas!r}; choose one of {sorted(CANVASES)}"
        )
    return CANVASES[canvas]


def assemble_music_video(
    project_id: str,
    *,
    strategy: str = "",
    edl: list | None = None,
    preset: str = "",
    weights: dict | None = None,
    config: dict | None = None,
    canvas: str = "",
) -> dict:
    """Assemble the music video — auto (a selection ``strategy``) or an explicit ``edl``. Free.

    - ``edl``: an explicit edit — a list of ``{song_start, song_end, clip_id}`` spans. Must
      be in order, non-overlapping, and each within its clip's coverage. A span with no
      footage is an explicit gap entry (``clip_id: null``); spans of the song your entries
      do not reach (head, tail, interior holes) are gap-filled automatically and named in
      the ``coverage`` report.
    - ``strategy='weighted'`` (score-driven): the beat-snapped Viterbi selector reads the
      persisted score tracks (run ``score_footage`` first) and the selection config —
      ``preset`` ("energetic"/"contemplative") and/or ``weights`` (per-metric) and/or
      ``config`` (``lambda_switch``/``l_min_s``/``l_max_s``/``boundary_mode``). Re-weighting
      is cheap: it re-selects from the SAME scores without re-scoring.
    - otherwise **full-auto**: a registered alignment-only ``strategy`` (see
      ``list_strategies``; default ``best_confidence``) builds the edit from the alignments.
    - ``canvas``: render-time override ("landscape"/"portrait"/"square") — the same edit
      re-rendered in another shape, no new project needed. Default: the project's canvas.

    The video is EXACTLY the song's duration: each cut is trimmed at its aligned in-point,
    scaled onto the canvas (padded, never stretched), gaps render black, and the CLEAN
    song audio runs under it all. Returns the render + the same coverage report
    ``propose_edit`` gives.
    """
    from muvid.footage.assemble import assemble_music_video as _assemble
    from muvid.footage.edl import derive_cuts, fill_gaps, validate_edl
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
            entries = validate_edl(fill_gaps(edl, song_dur), aligns, song_dur)
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
                fill_gaps(
                    select_edl(strat, aligns, song_dur, context=context), song_dur
                ),
                aligns,
                song_dur,
            )
            used_strategy = strat
    except (ValueError, KeyError) as e:
        raise _tool_error(f"could not build a valid edit: {e}") from e

    canvas_wh = _resolve_canvas(proj, canvas)
    cuts = derive_cuts(entries, aligns, proj.clip_paths())
    render_id = uuid.uuid4().hex[:12]
    render_dir = proj.new_render_dir(render_id)
    try:
        out = _assemble(
            cuts,
            str(proj.song_path()),
            str(render_dir / "final.mp4"),
            canvas=canvas_wh,
        )
        # audio= arms the duration-match check — the one that catches a mis-built
        # filtergraph (muvid#24 B3); correct now BECAUSE every EDL is gap-filled to the
        # full song. expected_canvas keeps the aspect/resolution checks honest for a
        # deliberate portrait/square render — without it, verify hard-fails every
        # non-16:9 canvas the canvas= override exists to produce.
        checks = verify_video(
            out, audio=str(proj.song_path()), expected_canvas=canvas_wh
        )
    except Exception:
        import shutil

        shutil.rmtree(render_dir, ignore_errors=True)
        raise

    meta = {
        "render_id": render_id,
        "video": str(out),
        "strategy": used_strategy,
        "canvas": list(canvas_wh),
        # "rendered", not "covered": after gap-filling this is always the whole song —
        # where the user actually HAS footage is the coverage report's business.
        "rendered_span": [
            round(entries[0].song_start, 2),
            round(entries[-1].song_end, 2),
        ],
        # Full precision, NOT rounded: this list must feed straight back as the edl=
        # argument and reproduce the same render. round(x, 2) moved boundaries by up to
        # 5 ms — past validate_edl's 1 ms tolerance, so the render → edit → re-render loop
        # could fail outright, and even when it validated it re-rendered a different video
        # (muvid#21 item 3). propose_edit already returns full precision; same contract.
        "edl": [_edl_json(e) for e in entries],
        "coverage": _coverage_report(
            [e for e in entries if not e.is_gap], aligns, song_dur
        ),
        "ok": not failures(checks),
        "checks": report(checks),
        # The retrieval claim: what a host's generic download route (reelee#252) turns
        # into a signed short-lived URL. `video` stays a server-side path — useful to an
        # operator, unreadable to a remote caller; the claim is the caller's handle.
        "download": _download_claim(project_id, render_id),
        "note": (
            "Artifact stored server-side in your project. Ask the host to sign the "
            "`download` claim for a fetchable URL; a hosted connector exposes this as "
            "its download route/tool."
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


def _download_claim(project_id: str, render_id: str) -> dict:
    from muvid.downloads import claim

    return claim(project_id, render_id)


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


def footage_editor_document(project_id: str) -> dict:
    """The project as lacing-native standoff annotations, for a multitrack editor. Free.

    One tier per clip (its ``clip-alignment/v1`` +, once scored, its
    ``clip-score-track/v1`` curves) plus a ``DECISION`` tier holding the current default
    proposal as ``music-video-edl/v1`` entries — everything referenced to the song by
    content hash, on one shared song-time axis (thorwhalen/reelee-web#203). Needs the
    ``editor`` extra (``lacing``); requires alignment (run ``align_footage`` first).

    After a human edits the DECISION tier, feed its annotations back to
    ``assemble_music_video`` via ``footage_edl_from_annotations``.
    """
    try:
        from muvid.footage.lacing_bridge import editor_document
    except ImportError as e:
        raise _tool_error(
            "the lacing-native editor bridge needs the 'editor' extra "
            "(pip install 'muvid[editor]')"
        ) from e

    proj = _open(project_id)
    if not proj.load_alignments():
        raise _tool_error("no alignment yet — call align_footage first")
    try:
        return editor_document(proj)
    except ValueError as e:
        raise _tool_error(str(e)) from e


def footage_edl_from_annotations(project_id: str, *, annotations: list[dict]) -> dict:
    """The DECISION tier's annotations, turned back into an ``edl=`` argument. Free.

    The timeline-to-EDL half: pass ``footage_editor_document``'s ``DECISION`` tier
    (after whatever an editor did to it) and get back plain
    ``{song_start, song_end, clip_id}`` dicts, ready for ``assemble_music_video(edl=...)``
    or ``propose_edit`` — a faithful read, not a re-selection.
    """
    try:
        from muvid.footage.lacing_bridge import edl_from_annotations
        from lacing.model import Annotation
    except ImportError as e:
        raise _tool_error(
            "the lacing-native editor bridge needs the 'editor' extra "
            "(pip install 'muvid[editor]')"
        ) from e

    _open(project_id)  # authorizes the caller against this project
    try:
        parsed = [Annotation.model_validate(a) for a in annotations]
    except Exception as e:  # noqa: BLE001 — surface a clean ToolError, not a raw pydantic one
        raise _tool_error(f"could not read annotations: {e}") from e
    return {"project_id": project_id, "edl": edl_from_annotations(parsed)}
