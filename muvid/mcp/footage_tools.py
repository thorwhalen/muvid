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
Lifecycle around it (muvid#22): ``list_music_video_projects`` finds a project whose id
was lost, and ``remove_footage`` takes a clip back out — which invalidates the
alignment, exactly as ``set_song`` does.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from muvid.footage.align import MIN_CONFIDENCE, MIN_MARGIN, MIN_SUPPORT
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
#: Re-exported, not re-declared: the threshold and the verdict it feeds now live with
#: the aligner (``muvid.footage.align``), because the same number has to decide what
#: this tool REPORTS and what ``validate_edl`` REFUSES, and two copies of a calibrated
#: constant is how those two answers drift apart (muvid#59).
_MIN_CONFIDENCE = MIN_CONFIDENCE
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

    Returns each clip's offset, a confidence in [0,1], its ``support`` (the fraction of
    the clip that agrees on that offset, ``null`` when the aligner took a single
    whole-clip measurement), and its coverage of the song, plus two lists:

    - ``low_confidence`` — clips that matched weakly, for reporting;
    - ``unreliable`` — clips whose offset the aligner will NOT vouch for. These stay in
      the project and stay addressable, and the auto path simply prefers other clips
      over them: a span another clip covers goes to that clip, and a span only an
      unreliable clip covers is left as a gap and reported in ``coverage.excluded``
      (muvid#88). ``assemble_music_video`` still REFUSES an explicit ``edl`` that cuts
      to one, and still refuses an auto edit when NO clip is trustworthy, unless called
      with ``allow_unreliable=true`` — because a wrong offset does not fail, it renders
      a video out of sync with the song (muvid#59). Re-align, accept the smaller edit,
      or opt in deliberately;
    - ``no_consensus`` — clips too short to be put to a vote at all (under about 4.5 s).
      **A clip in this list can be marked reliable and still be wrong**, and no other
      field will say so: its offset rests on one measurement, judged by a confidence
      score that does not rank correctness in this band — measured on the muvid#59
      shoot, the WRONG offset scored highest of three (0.834 against 0.566 and 0.621),
      and on a repeating fixture a 4.4 s clip landing 8 s out is vouched at 0.381.
      Nothing is refused on this basis, because refusing would take the correct short
      clips with it. So if a short clip looks out of sync in the render, this list is
      the first place to look — and muvid#91 is where that trade-off is being decided.

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
        # The list that has TEETH, kept separate from `low_confidence` because they
        # answer different questions: one is a number to look at, the other is what
        # the render will refuse. They coincide today for a whole-clip estimator and
        # deliberately will not once support is measured (muvid#59).
        "unreliable": [
            {
                "clip_id": a.clip_id,
                "confidence": round(a.confidence, 3),
                "support": _round_support(a.support),
                # The separator, and the one to read first: negative means the clip's
                # own evidence prefers a DIFFERENT offset, which is why it was refused.
                "margin": _round_support(a.margin),
                "window_s": _round_support(a.window_s),
            }
            for a in aligns
            if not a.reliable
        ],
        # REPORTED, never enforced — the same posture as `offset_consensus` below.
        # `support: null` means the estimator could not hold a vote at all: it fits its
        # window to the clip down to a 3 s floor, so this is only a clip shorter than
        # `window_floor + hop` = 4.5 s (measured: 4.4 s unvoted, 4.5 s the first with a
        # number). The offset then rests on a single measurement and the verdict falls
        # back to the confidence coefficient.
        #
        # SAY THE HAZARD, because this list is the only place it is visible: a clip in
        # here can be `reliable: true` AND WRONG. Measured on a repeating fixture, a
        # 4.4 s clip landing 7.99 s out is vouched at confidence 0.381, while the same
        # material at 4.5 s — one vote away — is refused (margin -0.252). The
        # coefficient does not rank correctness in this band (on the muvid#59 shoot the
        # WRONG offset scored highest of three, 0.834 against 0.566 and 0.621), so
        # nothing else flags it. Refusing the whole band would take the correct short
        # clips with it and re-break the compatibility read muvid#87 fixed, so the band
        # is named rather than gated — muvid#91 owns that decision.
        "no_consensus": [a.clip_id for a in aligns if a.support is None],
        "confidence_metric": "onset-envelope correlation at the waveform's lag",
        "confidence_threshold": _MIN_CONFIDENCE,
        # Support must EXCEED this, and the strictness is the meaning: at exactly this
        # value every window had the offset on its ballot and none found it unaided.
        # A FLOOR on how much evidence reached the offset...
        "support_threshold": MIN_SUPPORT,
        "support_threshold_is_exclusive": True,
        # ...and the SEPARATOR, which is what actually does the work: measured on the
        # muvid#59 material, margin>0 passes 24/24 correct and 0/6 noise where a support
        # threshold alone passed 18/24. Negative margin = the evidence prefers elsewhere.
        "margin_threshold": MIN_MARGIN,
        "margin_threshold_is_exclusive": True,
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


#: Every optional :class:`~muvid.footage.edl.EdlEntry` field :func:`_edl_json`
#: carries, and how to render it. **The list is the round trip.** ``_as_entry``
#: reads all of these back by name, so a field missing from here is a direction
#: the caller gave, the renderer honoured, and the returned/persisted edit does
#: not contain — which is worse than a field that was never accepted, because the
#: tool's own contract says this list "must feed straight back as the edl= argument
#: and reproduce the same render". ``crop``/``crop_end`` (muvid#60) and ``look``
#: (the looks seam) were each dropped this way; ``transition`` (muvid#34) was
#: hand-written and survived, which is exactly why the three are now one table
#: rather than three ``if``s the next field forgets to join.
#:
#: Each row is ``(field, render, absent)``, where ``absent`` is the value that
#: means "omit this key". It is a column rather than a hardcoded ``None`` because
#: ``look_time_varying`` (muvid#73) is a **boolean** whose absent value is
#: ``False``, and ``False is not None`` — an ``is not None`` test would have
#: emitted it on every entry ever written, changing every existing
#: ``renders/*/meta.json`` and every DECISION body for a field almost none of
#: them use. Same omit-when-absent contract, one generalisation.
_EDL_OPTIONAL_FIELDS = (
    ("transition", lambda v: v.to_dict(), None),
    ("crop", lambda v: v.to_dict(), None),
    ("crop_end", lambda v: v.to_dict(), None),
    ("look", str, None),
    ("look_time_varying", bool, False),
)


def _edl_json(e) -> dict:
    """One EDL entry as JSON — full precision (it must feed back verbatim), gaps as null.

    Optional fields are emitted ONLY when set (:data:`_EDL_OPTIONAL_FIELDS`). That
    omit-when-absent rule is what keeps every existing ``renders/*/meta.json``
    byte-identical — the compatibility surface named in ``.claude/CLAUDE.md``, since
    these bodies have no schema version to bump — and keeps the render -> edit ->
    re-render round trip (muvid#21 item 3) exact for an edit that uses none of them.
    Values are plain dicts, not the frozen dataclasses: ``write_render_meta`` runs
    ``json.dumps`` over this, which has no encoder for a dataclass.

    ``bool`` as ``look_time_varying``'s renderer is doing real work, not
    decoration: the field's value is a ``LookFragment``-derived flag and a plain
    ``bool()`` is what guarantees the JSON carries ``true``/``false`` rather than
    something ``json.dumps`` would reject or a subclass would smuggle through.
    """
    out = {
        "song_start": e.song_start,
        "song_end": e.song_end,
        "clip_id": e.clip_id or None,
    }
    for field, render, absent in _EDL_OPTIONAL_FIELDS:
        v = getattr(e, field, absent)
        if v != absent:
            out[field] = render(v)
    return out


def _round_support(support: float | None) -> float | None:
    """``None`` stays ``None`` over the wire — "not measured" is not "measured zero"."""
    return None if support is None else round(float(support), 3)


def _coverage_report(entries, aligns, song_dur: float, *, excluded=()) -> dict:
    """What the song's timeline looks like under ``entries`` — covered, weak, and MISSING.

    Answers the three questions a person actually has about a proposed edit, in the form the
    directive requires: an aggregate percentage is not enough, so uncovered audio is named
    with explicit start and end times, and a span whose only footage is weakly aligned is
    listed separately with the confidence that makes it weak.

    Pass only FOOTAGE entries: a gap entry renders fill, and filled is not covered — the
    user still has no footage there, which is precisely what this report exists to say.

    ``excluded`` (muvid#88) is the fourth question, and it is a different one from
    ``uncovered``: those spans DID have footage and the edit gave them up because the only
    clip covering them is one the aligner will not vouch for. They appear in ``uncovered``
    as well — the user has no usable footage there either way — and here with the clip and
    the three numbers the verdict rests on, which is what makes it actionable (re-align
    that clip, shoot again, or re-run with ``allow_unreliable``). ``weak_segments`` and
    ``excluded`` are therefore mutually exclusive on any one span: a weak span made the cut,
    an excluded one did not.
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
    # `reliable`, not a bare confidence comparison: the verdict is the aligner's
    # (muvid.footage.align.vouches_for), so this report says exactly what
    # validate_edl will refuse rather than a second opinion that can drift from it.
    weak = [
        {
            "song_start": round(e.song_start, 2),
            "song_end": round(e.song_end, 2),
            "clip_id": e.clip_id,
            "confidence": round(by_id[e.clip_id].confidence, 3),
            "support": _round_support(by_id[e.clip_id].support),
            "margin": _round_support(by_id[e.clip_id].margin),
        }
        for e in entries
        if e.clip_id in by_id and not by_id[e.clip_id].reliable
    ]
    covered_s = sum(hi - lo for lo, hi in covered)
    return {
        "song_duration": round(song_dur, 2),
        "covered_seconds": round(covered_s, 2),
        "coverage_fraction": round(covered_s / song_dur, 4) if song_dur else 0.0,
        "uncovered": gaps,
        "weak_segments": weak,
        "excluded": [x.to_dict() for x in excluded],
        "confidence_threshold": _MIN_CONFIDENCE,
    }


def _exclusion_note(x) -> str:
    """One ``warnings`` line per span the recovery gave up (muvid#88).

    The reply's own contract says ``warnings`` is the whole of a caller's ability to know
    what the render PLAN found, and a hole in the video is the largest such finding there
    is. Nesting it only under ``coverage.excluded`` meant an agent reading ``ok`` and
    ``warnings`` — which is what the docstring tells it to read — could ship a black
    stretch without ever seeing why.
    """
    from muvid.footage.edl import UNVOUCHED_SELECTION

    why = (
        "a clip the aligner vouches for covers that span, but the selection did not use "
        "it — try another strategy or selection config"
        if x.reason == UNVOUCHED_SELECTION
        else "no clip the aligner vouches for covers that span — re-align or re-shoot"
    )
    numbers = f"confidence {x.confidence:.3f}"
    if x.support is not None:
        numbers += f", support {x.support:.2f}"
    if x.margin is not None:
        numbers += f", margin {x.margin:+.2f}"
    return (
        f"set aside {x.song_end - x.song_start:.1f} s of clip {x.clip_id!r} at "
        f"{x.song_start:.1f}-{x.song_end:.1f} s ({numbers}): {why}. It renders as a gap."
    )


def _auto_edl(aligns, song_dur: float, *, strategy, context, recover: bool):
    """The auto path, in one place: select -> set aside -> gap-fill (muvid#88).

    Both tools that build an edit from a strategy go through this, so ``propose_edit``
    cannot propose an edit ``assemble_music_video`` would not produce — the two are
    documented as the same edit and the recovery is the kind of step that drifts between
    two call sites otherwise.

    ``recover=False`` (i.e. the caller passed ``allow_unreliable``) skips the exclusion
    entirely: someone who has said they want the unvouched footage rendered means the
    footage, not a gap where it would have been.

    Returns ``(entries, excluded)``; the caller still passes ``entries`` through
    ``validate_edl``, which stays the ONE gate.
    """
    from muvid.footage.edl import exclude_unvouched, fill_gaps
    from muvid.footage.strategy import select_edl

    selected = select_edl(strategy, aligns, song_dur, context=context)
    excluded = []
    if recover:
        selected, excluded = exclude_unvouched(selected, aligns)
    return fill_gaps(selected, song_dur), excluded


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
    ``assemble_music_video``'s auto path, and the same edit it would build — including the
    ``coverage.excluded`` recovery described there.
    """
    from muvid.footage.edl import validate_edl
    from muvid.footage.strategy import DEFAULT_STRATEGY

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
        proposal, excluded = _auto_edl(
            aligns, song_dur, strategy=strat, context=context, recover=True
        )
        entries = validate_edl(
            proposal,
            aligns,
            song_dur,
            canvas=proj.canvas(),
            # This tool renders nothing; refusing here would deny the caller the very
            # diagnosis they came for, since `coverage.weak_segments` names each
            # unvouched span and the time it covers. The refusal belongs where the
            # encode does — `assemble_music_video` (muvid#59). After muvid#88 that
            # difference is narrow: the recovery above has already set aside every
            # span an assemble would have refused, EXCEPT in the one case it declines
            # to act on — a shoot with no trustworthy clip at all, where this returns
            # the unvouched edit with `weak_segments` naming every span of it and
            # `excluded` empty, and the assemble refuses.
            allow_unreliable=True,
        )
    except (ValueError, KeyError) as e:
        raise _tool_error(f"could not build a valid edit: {e}") from e
    return {
        "project_id": project_id,
        "strategy": strat,
        "edl": [_edl_json(e) for e in entries],
        "assemble_refusal": _assemble_refusal(entries, aligns, song_dur, proj.canvas()),
        "coverage": _coverage_report(
            [e for e in entries if not e.is_gap],
            aligns,
            song_dur,
            excluded=excluded,
        ),
        "warnings": [_exclusion_note(x) for x in excluded],
    }


def _assemble_refusal(entries, aligns, song_dur: float, canvas) -> "dict | None":
    """``None`` if assembling this proposal would render; the refusal if it would not.

    ``propose_edit`` validates with ``allow_unreliable=True`` so it can diagnose rather
    than refuse — which left one case indistinguishable from success: a shoot where NO
    clip is trustworthy comes back as a full unvouched edit with ``excluded`` empty, and
    nothing in the reply said the render would be refused. So the question is put to the
    GATE rather than answered by re-implementing its predicate here; two gates that can
    disagree is the thing muvid#88 exists to avoid.
    """
    from muvid.footage.edl import UnreliableAlignmentError, validate_edl

    try:
        validate_edl(entries, aligns, song_dur, canvas=canvas)
    except UnreliableAlignmentError as e:
        return {
            "error": "unreliable_alignment",
            "clip_ids": e.clip_ids,
            "message": str(e),
        }
    return None


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
    allow_unreliable: bool = False,
) -> dict:
    """Assemble the music video — auto (a selection ``strategy``) or an explicit ``edl``. Free.

    - ``edl``: an explicit edit — a list of ``{song_start, song_end, clip_id}`` spans. Must
      be in order, non-overlapping, and each within its clip's coverage. A span with no
      footage is an explicit gap entry (``clip_id: null``); spans of the song your entries
      do not reach (head, tail, interior holes) are gap-filled automatically and named in
      the ``coverage`` report.
    - each ``edl`` entry may also carry ``transition``:
      ``{"duration_s": 0.4, "curve": "fade"}`` — blend IN from the previous entry instead
      of hard-cutting. The blend is CENTRED on the boundary, so a beat-snapped cut stays
      on the beat: each side supplies ``duration_s/2`` of source beyond its own span, and
      both must actually have it (a gap side always does — it fades from black). Rejected,
      not ignored, if it is on the FIRST entry (nothing to blend from), names a curve
      outside ``fade``/``fadeblack``/``fadewhite``/``dissolve``/``wipe*``/``slide*``/
      ``smooth*``/``circleopen``/``circleclose``, is under 0.04 s, or does not fit. Omit
      it for a hard cut — the default, and what every entry without the key means.
    - each ``edl`` entry may also carry ``crop`` / ``crop_end``
      (``{"x":0,"y":0.25,"w":1,"h":0.5}``, fractions of the SOURCE frame — the
      framing decision; ``crop_end`` pans that window and must be the same size)
      and ``look`` (ONE linear ffmpeg filter chain — the grade, LUT, posterise or
      in-shot punch-in, applied to the delivery CANVAS after scaling).
      ``look`` is an **allowlist**: only the filters named by
      ``muvid.footage.edl.LOOK_FILTERS`` are accepted, and a chain naming
      anything else — a
      second container (``movie=``), a filter that writes a file
      (``metadata=…:file=``, ``deshake=filename=``), a pad label, a graph
      separator, or an unclosed quote — is refused by name at ``validate_edl``,
      not discovered as an ffmpeg side effect. Its output frame is also bounded:
      a ``scale``/``pad``/``zoompan`` size must be a plain pixel count (not
      ``iw*80``, not ``-1``, not ``hd720``) and no more than
      ``muvid.footage.edl.MAX_LOOK_SCALE`` times the render canvas — frame size
      is memory, and ``scale=8000:8000`` costs 328 MB against 19 MB for a look
      that stays at canvas size. On those four filters only options muvid has
      measured may be set at all — ``scale`` w/h/s/size + ``flags``, ``pad``
      w/h/x/y + ``color``, ``crop`` w/h/x/y, all of ``zoompan`` — because
      ``pad=aspect`` and ``scale=force_original_aspect_ratio`` move the frame
      while declaring no size a bound can read (590 MB and 941 MB measured on a
      1920x1080 canvas). Compile one with ``muvid.footage.look``
      (``punch_in`` / ``motion`` / ``stylize``) rather than hand-writing it.
    - an entry carrying a MOVING look (a punch-in, a pan) should also set
      ``look_time_varying: true``. It changes no pixels; it puts a line in the
      reply's ``warnings`` when a blended boundary restarts the move's ramp,
      which it does because the blend is a separate seek (muvid#73). Leave it
      off — the default — for a grade, a LUT or a posterise, which never read the
      clock. All five fields survive verbatim in the returned ``edl``.
    - ``strategy='weighted'`` (score-driven): the beat-snapped Viterbi selector reads the
      persisted score tracks (run ``score_footage`` first) and the selection config —
      ``preset`` ("energetic"/"contemplative") and/or ``weights`` (per-metric) and/or
      ``config`` (``lambda_switch``/``l_min_s``/``l_max_s``/``boundary_mode``). Re-weighting
      is cheap: it re-selects from the SAME scores without re-scoring.
    - otherwise **full-auto**: a registered alignment-only ``strategy`` (see
      ``list_strategies``; default ``best_confidence``) builds the edit from the alignments.
    - ``canvas``: render-time override ("landscape"/"portrait"/"square") — the same edit
      re-rendered in another shape, no new project needed. Default: the project's canvas.
    - **A clip the aligner will not vouch for costs its own spans, not the whole edit**
      (muvid#88). On the auto path the strategy prefers a vouched clip wherever one
      covers the span, so an untrustworthy clip is simply not chosen while any other
      clip covers that stretch of the song. Where it was the ONLY footage, the span is
      set aside rather than cut to: it renders as a gap and is named in
      ``coverage.excluded`` with the clip, the span, and the confidence/support/margin
      the verdict rests on. So a five-clip shoot with one bad clip still assembles.
      The refusal remains for the two cases it was built for: an explicit ``edl`` naming
      an unvouched clip (you chose it), and an auto edit where NO clip is trustworthy —
      a black video reported as success is the failure this exists to prevent, so that
      still comes back as an error naming every clip.
    - ``allow_unreliable``: render even on clips whose alignment the aligner will not
      vouch for. **Off by default and it should stay off**: a wrong offset does not
      fail, it delivers a video out of sync with the song, so the refusal is the only
      thing standing between a bad measurement and a bad render (muvid#59). Set it when
      you have checked the offsets yourself, or when a slightly-off cut beats no cut at
      all — it also turns OFF the set-aside above, since someone who opted in wants that
      footage rendered rather than gapped. ``align_footage`` names the clips this
      applies to in its ``unreliable`` list.

    The video is EXACTLY the song's duration: each cut is trimmed at its aligned in-point,
    scaled onto the canvas (padded, never stretched), gaps render black, and the CLEAN
    song audio runs under it all. Returns the render + the same coverage report
    ``propose_edit`` gives.

    **Read the returned ``warnings`` list.** It is always present and usually
    empty, and it is what the render PLAN found: a transition that rounded to
    zero frames at this fps, or a moving look on a blended boundary whose ramp
    therefore restarts (muvid#73). Neither fails the render — ``ok`` stays true —
    so this list is the only place either one is ever said. It exists because
    these findings used to be Python warnings on the server's stderr, which a
    remote caller has no access to.
    """
    from muvid.footage.assemble import assemble_music_video as _assemble
    from muvid.footage.edl import (
        UnreliableAlignmentError,
        derive_cuts,
        fill_gaps,
        validate_edl,
    )
    from muvid.footage.strategy import DEFAULT_STRATEGY
    from muvid.visualize import failures, report, verify_video

    proj = _open(project_id)
    if not proj.has_song():
        raise _tool_error("no song set — call set_song first")
    aligns = proj.load_alignments()
    if not aligns:
        raise _tool_error("no alignment — call align_footage first")
    song_dur = proj.song_duration()
    # Resolved BEFORE validation, not after: a caller-supplied `look` is bounded
    # against the canvas it will be rendered onto (muvid#75), and the canvas is
    # also what a `canvas=` override changes. Resolving it afterwards — where this
    # line used to sit — would have bounded a portrait render against the
    # project's landscape canvas, i.e. the gate and the renderer disagreeing about
    # the one number the bound is relative to.
    canvas_wh = _resolve_canvas(proj, canvas)

    has_selection_config = bool(preset or weights or config)
    try:
        if edl is not None:
            if has_selection_config:
                raise ValueError(
                    "selection config (preset/weights/config) can't accompany an explicit edl"
                )
            entries = validate_edl(
                fill_gaps(edl, song_dur),
                aligns,
                song_dur,
                canvas=canvas_wh,
                allow_unreliable=allow_unreliable,
            )
            used_strategy = None
            # The caller named these clips; nothing is set aside on their behalf
            # (muvid#88 is about the AUTO path, where nobody chose the bad clip).
            excluded = []
        else:
            strat = strategy or (
                "weighted" if has_selection_config else DEFAULT_STRATEGY
            )
            if has_selection_config and strat != "weighted":
                raise ValueError(
                    f"selection config only applies to strategy='weighted' (got {strat!r})"
                )
            context = _selection_context(proj, strat, preset, weights, config)
            proposal, excluded = _auto_edl(
                aligns,
                song_dur,
                strategy=strat,
                context=context,
                # A caller who opted in wants the footage rendered, not set aside.
                recover=not allow_unreliable,
            )
            entries = validate_edl(
                proposal,
                aligns,
                song_dur,
                canvas=canvas_wh,
                allow_unreliable=allow_unreliable,
            )
            used_strategy = strat
    except UnreliableAlignmentError as e:
        # Named separately from the generic "could not build a valid edit" because the
        # remedy is different in kind: nothing about the EDL is malformed, and re-writing
        # it will not help — the OFFSETS are not trustworthy, and the caller has to
        # re-align, drop those clips, or say they want them anyway.
        raise _tool_error(f"{e} (clips: {', '.join(e.clip_ids)})") from e
    except (ValueError, KeyError) as e:
        raise _tool_error(f"could not build a valid edit: {e}") from e

    cuts = derive_cuts(entries, aligns, proj.clip_paths())
    render_id = uuid.uuid4().hex[:12]
    # The reference a human can actually say. Assigned here, at creation, so it
    # never renumbers under them (see MusicVideoFootageProject.ensure_render_refs).
    ref_n = proj.next_render_ref()
    render_dir = proj.new_render_dir(render_id)
    # The render-plan findings, on their way to the CALLER. `_part_plan` raises
    # them as `AssemblyWarning`s, which reach this process's stderr and stop
    # there — and on the deployed per-caller connector the caller has no stderr,
    # so a muvid#73 hitch came back as an `ok` render with nothing said about it.
    # A warning the caller cannot see is the silent no-op this repo refuses
    # everywhere else, so the sink is threaded in and its contents are returned.
    notes: list[str] = [_exclusion_note(x) for x in excluded]
    try:
        out = _assemble(
            cuts,
            str(proj.song_path()),
            str(render_dir / "final.mp4"),
            canvas=canvas_wh,
            on_note=notes.append,
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
        # `cut 4` — what the caller says when they want to talk about this
        # render. The integer is the durable fact; the wording is nw's.
        "ref_n": ref_n,
        "ref": _format_ref(ref_n),
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
            [e for e in entries if not e.is_gap], aligns, song_dur, excluded=excluded
        ),
        "ok": not failures(checks),
        "checks": report(checks),
        # What the render PLAN found: a transition that rounded to zero frames at
        # this fps, a time-varying look on a blended boundary (muvid#73). Neither
        # fails the render — `ok` stays true — so this list is the whole of the
        # caller's ability to know. ALWAYS present, empty when clean: an absent
        # key makes "old build" and "nothing to report" the same reading, which
        # is the ambiguity the omit-when-absent rule exists to avoid for EDL
        # fields whose absent value is meaningful. Here the meaningful value is
        # "nothing", and a caller has to be able to rely on being told it.
        "warnings": notes,
        # The retrieval claim: what a host's generic download route (reelee#252) turns
        # into a signed short-lived URL. `video` stays a server-side path — useful to an
        # operator, unreadable to a remote caller; the claim is the caller's handle.
        "download": _download_claim(project_id, render_id),
        # What the caller should DO next, naming the tool that does it. The
        # previous wording ("ask the host to sign the `download` claim") named
        # no tool, and no tool could sign a muvid claim anyway — so a user who
        # followed it exactly still ended with nothing (thorwhalen/reelee#322).
        "note": (
            f"Call `reelee_get_download_url(genre='muvid', project_id='{project_id}', "
            f"artifact_id='{render_id}')` for a link to watch and download this. "
            f"You can refer to it as \u201c{_format_ref(ref_n)}\u201d from now on."
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


def _format_ref(n: int) -> str:
    from nw.delivery import format_ref

    return format_ref(n)


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


# -- the master beat grid, on its own (thorwhalen/muvid#18 item 5) ------------

#: Where the song's beat grid is cached: beside the score tensor it feeds, under the
#: project's ``scores/`` dir, so it shares that dir's lifecycle (``set_song`` and a
#: re-align that changed the offsets both rmtree it — the second is more than the grid
#: needs, since it depends on the song alone, but a recompute is seconds and a second
#: invalidation policy is a second thing to get wrong). Keyed on ``song_hash`` on top
#: of that, so a record is never served for a song it was not measured on.
_BEAT_GRID_CACHE_NAME = "beat_grid.json"
#: Rounded exactly as ``grid.save_scores`` rounds a scoring run's manifest, so the two
#: records of the same song's grid agree to the digit.
_BEAT_TIME_DECIMALS = 4
_TEMPO_DECIMALS = 3
#: The ``source`` vocabulary of a ``beat_grid`` reply — how the grid was obtained, in
#: the order the tool tries them (cheapest first; the estimator runs last, once).
_BEAT_GRID_FROM_CACHE = "cache"
_BEAT_GRID_FROM_SCORES = "scores"
_BEAT_GRID_COMPUTED = "computed"


def beat_grid(project_id: str) -> dict:
    """The song's beat grid — tempo and beat instants on the song timeline — WITHOUT
    running the scoring job. Free.

    ``score_footage`` computes this very grid as its first stage and then decodes every
    clip through cv2 in the background, which is far too much to pay when all a caller
    wants is where the beats are (thorwhalen/muvid#18 item 5) — a fixed-stride cut grid,
    a check that the tempo came out right, a cut plan of its own. This is one call to
    ``mixing.audio.beat_grid`` on the song alone (the "compute once on the master"
    invariant: clips map to it through their offsets, never the other way round),
    cached under the project as ``scores/beat_grid.json`` keyed on ``song_hash`` so the
    second call is a file read; a project that has already been scored is served from
    that run's manifest instead. ``source`` says which (``computed`` | ``cache`` |
    ``scores``).

    Needs the ``scoring`` extra (``mixing[beats]``, i.e. librosa); without it the reply
    is a clean error naming the install, never a traceback. Needs a song
    (``set_song``); no alignment is required.

    Returns ``tempo_bpm``, ``beats`` (seconds, ascending), ``n_beats``,
    ``song_duration`` (so a caller can close the last bar) and ``source``.
    ``downbeats`` is present only when the estimator measured any — the librosa
    backend has no downbeat tracker, and an empty list would read as "this song has
    no downbeats", a measurement nobody made (gate, don't zero).
    """
    proj = _open(project_id)
    if not proj.has_song():
        raise _tool_error("no song set — call set_song first")
    source, record = _beat_grid_record(proj, song_hash=proj.song_hash())
    return _beat_grid_reply(
        project_id, record, source=source, song_duration=proj.song_duration()
    )


def _beat_grid_record(proj, *, song_hash: str) -> tuple[str, dict]:
    """``(source, record)`` from the cheapest source that can answer for THIS song.

    Each source either answers with a record measured on ``song_hash`` or declines with
    ``None``; the estimator is last and never declines (it computes, caches, or raises).
    Adding a source is a row here, not a branch in the tool.
    """
    cache_path = _beat_grid_cache_path(proj)

    def compute():
        return _compute_beat_grid(proj, cache_path=cache_path, song_hash=song_hash)

    sources = (
        (_BEAT_GRID_FROM_CACHE, lambda: _read_beat_grid_cache(cache_path, song_hash)),
        (_BEAT_GRID_FROM_SCORES, lambda: _beat_grid_from_scores(proj, song_hash)),
        (_BEAT_GRID_COMPUTED, compute),
    )
    for source, load in sources:
        record = load()
        if record is not None:
            return source, record
    raise AssertionError("the computed source never declines")  # pragma: no cover


def _beat_grid_cache_path(proj) -> Path:
    from muvid.footage.scoring.grid import scores_dir  # the dir name's SSOT

    return scores_dir(proj.root) / _BEAT_GRID_CACHE_NAME


def _read_beat_grid_cache(path: Path, song_hash: str) -> dict | None:
    """The cached record if it was measured on THIS song, else ``None``.

    Absent, torn (a concurrent writer) or unreadable is a miss — recomputed, never
    served — and so is a record carrying another song's hash (the ``scores/`` dir
    outlives a hand-edited manifest or a copied project tree; the key does not).
    """
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("song_hash") != song_hash:
        return None
    return record


def _beat_grid_from_scores(proj, song_hash: str) -> dict | None:
    """Lift the grid out of a scoring run's manifest when one exists for THIS song.

    ``score_project`` computes exactly this grid as its first stage and persists it in
    ``scores/manifest.json``, so a scored project has already paid for it. Only the
    song hash has to match: that manifest goes stale for SCORES the moment the
    alignment changes (``manifest_is_current`` checks the fingerprint too), but the
    beat grid depends on the song alone.
    """
    from muvid.footage.scoring.grid import load_manifest

    manifest = load_manifest(proj.root)
    if not manifest or manifest.get("song_hash") != song_hash:
        return None
    beats = manifest.get("beats")
    if not isinstance(beats, dict) or not isinstance(beats.get("beat_times"), list):
        return None
    return {
        "song_hash": song_hash,
        "tempo_bpm": manifest.get("tempo_bpm"),
        "beat_times": beats["beat_times"],
        "downbeat_times": beats.get("downbeat_times") or [],
    }


def _compute_beat_grid(proj, *, cache_path: Path, song_hash: str) -> dict:
    """Run ``mixing.audio.beat_grid`` on the song and write the record to the cache.

    Imported HERE, not at module top: ``mixing.audio`` is core but the estimator's
    librosa is the ``scoring`` extra, and this module is on the connector's import-safe
    path. The ONLY exception translated is ``ImportError`` — the way ``mixing`` reports
    a missing optional package — so a caller gets the install hint instead of a
    traceback; whatever else the estimator raises is a ``mixing`` regression and
    propagates untouched (never a broad except around a sibling's call).
    """
    try:
        from mixing.audio import beat_grid as estimate

        grid = estimate(str(proj.song_path()))
    except ImportError as e:
        raise _tool_error(
            "the beat grid needs librosa, which the 'scoring' extra provides — "
            f"pip install 'muvid[scoring]' ({e})"
        ) from e
    record = _as_beat_grid_record(grid, song_hash=song_hash)
    _write_beat_grid_cache(cache_path, record)
    return record


def _as_beat_grid_record(grid, *, song_hash: str) -> dict:
    """The cache record: the three ``BeatGrid`` fields muvid reads (the same three the
    scoring orchestrator reads), rounded like the scores manifest, plus the key."""
    import math

    tempo = float(grid.tempo_bpm)
    return {
        "song_hash": song_hash,
        "tempo_bpm": round(tempo, _TEMPO_DECIMALS) if math.isfinite(tempo) else None,
        "beat_times": [round(float(t), _BEAT_TIME_DECIMALS) for t in grid.beat_times],
        "downbeat_times": [
            round(float(t), _BEAT_TIME_DECIMALS) for t in grid.downbeat_times
        ],
        "computed_at": time.time(),
    }


def _write_beat_grid_cache(path: Path, record: dict) -> None:
    """tmp + ``os.replace`` (the same crash-consistency as the score files), so a
    concurrent call never reads a torn record. The reply is already correct by the
    time this runs: a cache that cannot be written (a scoring job's rmtree racing this
    call, a read-only tree) costs the NEXT call a recompute, nothing more."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(record, indent=2))
        os.replace(tmp, path)
    except OSError:
        pass


def _beat_grid_reply(
    project_id: str, record: dict, *, source: str, song_duration: float
) -> dict:
    beats = list(record.get("beat_times") or [])
    reply = {
        "project_id": project_id,
        "tempo_bpm": record.get("tempo_bpm"),
        "beats": beats,
        "n_beats": len(beats),
        "song_duration": song_duration,
        "source": source,
    }
    downbeats = list(record.get("downbeat_times") or [])
    if downbeats:  # measured → reported; unmeasured → absent (never an empty list)
        reply["downbeats"] = downbeats
    return reply


def _alignment_covers_clips(proj) -> bool:
    """Whether a persisted alignment exists AND has a record for every current clip.

    "Aligned" is a claim about the clip SET, not about a file: a clip added after
    ``align_footage`` has no offset, the auto edit would silently pass it over, and the
    file on disk would still say "aligned". Reading it this way makes the listing's next
    action honest — false means "run align_footage", whichever way it became false.
    """
    clip_ids = {c["clip_id"] for c in proj.list_clips()}
    aligned_ids = {a.clip_id for a in proj.load_alignments()}
    return bool(clip_ids) and clip_ids <= aligned_ids


def list_music_video_projects() -> dict:
    """List YOUR music_video (footage) projects, newest-modified first. Free.

    The way back to a lost ``project_id`` (muvid#22): ``list_projects`` spans both
    muvid genres but says nothing about a footage project's progress, and every other
    footage tool needs the id first. Each row carries where the project is in the
    workflow — ``has_song``, ``n_clips``, ``aligned``, ``n_renders`` — so the next call
    reads off the listing without a ``footage_status`` per project:

    - ``aligned`` is true only when the persisted alignment covers every current clip.
      An ``add_footage`` or ``remove_footage`` after ``align_footage`` makes it false
      again until you re-align.
    - ``n_renders`` counts what ``footage_status`` lists under ``renders``; ``0`` is a
      positive answer ("no cut yet"), never an omitted project.

    ``[]`` means you have no footage projects; a visualizer project is not one and
    shows up in ``list_projects`` instead.
    """
    ws = _workspace()
    rows = []
    for r in ws.list_projects():
        try:
            proj = ws.open_project(r["project_id"])
        except (FileNotFoundError, ValueError):
            continue  # vanished mid-scan — genuinely absent
        rows.append(
            {
                "project_id": r["project_id"],
                "title": r.get("title") or r["project_id"],
                "has_song": proj.has_song(),
                "n_clips": len(proj.list_clips()),
                "aligned": _alignment_covers_clips(proj),
                "n_renders": len(proj.list_renders()),
                "created": r.get("created"),
                "modified": r.get("modified"),
            }
        )
    return {"projects": rows}


def remove_footage(project_id: str, *, clip_id: str) -> dict:
    """Remove one footage clip from the project — its stored file and its entry. Free.

    Irreversible for the clip (re-add it from its URL if it was a mistake); existing
    renders are untouched. Removal INVALIDATES the alignment and every persisted score
    track, exactly as ``set_song`` does (muvid#22) — the alignment describes the clip set
    it was measured on, and a removed clip's offset must not outlive the clip. So run
    ``align_footage`` again before ``propose_edit`` / ``assemble_music_video``; until
    then ``footage_status`` reports the project unaligned and this project's
    ``aligned`` flag in ``list_music_video_projects`` is false.

    An unknown ``clip_id`` is refused, naming the clip ids the project holds, and a
    refusal changes nothing on disk. Returns what was removed, the clips that remain,
    and whether an alignment / score tracks were actually dropped.
    """
    proj = _open(project_id)
    known = proj.list_clips()
    if clip_id not in {c["clip_id"] for c in known}:
        raise _tool_error(_unknown_clip_message(clip_id, known))
    removed = proj.remove_clip(clip_id)
    return {
        "project_id": project_id,
        "removed": {"clip_id": removed["clip_id"], "name": removed["name"]},
        "clips": proj.list_clips(),
        "alignment_invalidated": removed["alignment_invalidated"],
        "scores_invalidated": removed["scores_invalidated"],
    }


def _unknown_clip_message(clip_id: str, known: list[dict]) -> str:
    if not known:
        return f"unknown clip_id {clip_id!r} — this project has no clips (add_footage first)"
    listed = ", ".join(
        (
            c["clip_id"]
            if c.get("name", c["clip_id"]) == c["clip_id"]
            else f"{c['clip_id']} ({c['name']})"
        )
        for c in known
    )
    return f"unknown clip_id {clip_id!r} — this project's clips are: {listed}"


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
    or ``propose_edit`` — a faithful read, not a re-selection. Annotations referencing a
    song other than this project's are refused, not read (muvid#35), so a clipboard from
    another project fails saying so instead of splicing in the wrong spans.
    """
    try:
        from muvid.footage.lacing_bridge import edl_from_annotations
        from lacing.model import Annotation
    except ImportError as e:
        raise _tool_error(
            "the lacing-native editor bridge needs the 'editor' extra "
            "(pip install 'muvid[editor]')"
        ) from e

    proj = _open(project_id)  # authorizes the caller against this project
    try:
        parsed = [Annotation.model_validate(a) for a in annotations]
    except Exception as e:  # noqa: BLE001 — surface a clean ToolError, not a raw pydantic one
        raise _tool_error(f"could not read annotations: {e}") from e
    try:
        edl = edl_from_annotations(
            parsed,
            # No song set yet — nothing to cross-check against, so stay permissive
            # (song_hash() would raise FileNotFoundError on a songless project).
            expected_song_asset_id=proj.song_hash() if proj.has_song() else None,
        )
    except ValueError as e:
        raise _tool_error(str(e)) from e
    return {"project_id": project_id, "edl": edl}
