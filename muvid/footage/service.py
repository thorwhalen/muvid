"""The footage operations — one function per thing you can do to a music-video project.

**This module is the single source of truth for the ``music_video`` genre's operations.**
Every surface reaches them from here and lists them from :data:`FOOTAGE_OP_SPECS`, never
again by hand:

- the MCP connector (:mod:`muvid.mcp.footage_tools`, :mod:`muvid.mcp.scoring_tools`)
  resolves ``project_id`` to the caller's workspace project, calls the function, and
  turns a :class:`~muvid.footage.errors.FootageError` into a ``ToolError``;
- a host that serves the genre (reelee's studio) reads the catalogue nw holds —
  ``nw.genre_ops("music_video")``, registered from :data:`FOOTAGE_OP_SPECS` by
  :mod:`muvid.genre_music_video` — and calls the same functions on a host-placed
  :class:`muvid.Project`'s ``footage``.

Contract of every operation here:

- the first argument is a :class:`~muvid.footage.workspace.MusicVideoFootageProject`
  (``fp``), everything else is keyword-only and JSON-able;
- the result is a JSON-able ``dict`` (no ``project_id`` — the transport knows which
  project it opened);
- a refusal raises :class:`FootageError` with a message naming the next action — never a
  transport's error type, never a raw traceback for a caller mistake;
- the docstring is **model-facing**: it is what an assistant reads to decide whether and
  how to call the operation, so it says what the operation changes and what to read in
  its reply.

Media arrives as a LOCAL file (``set_song(fp, path=...)``, ``add_clip(fp, path=...)``):
fetching a URL is the MCP connector's business, streaming an upload the host's. Either
way the file is COPIED into the project, so the caller may delete it afterwards.

**Import-light by design** (stdlib + :mod:`muvid.footage.edl` at module top): a host
imports the genre module, which imports this one to read the operations' signatures, and
must not pay for numpy/ffmpeg/fastmcp to build a catalogue.

The named-edit operations (``save_edit``, ``set_cut``, ``split_cut``, ``merge_cut``,
``replace_edit``, ``render(edit_id=...)``) are what make "change cut 7 and render again"
possible: before them, an EDL was persisted only inside a render's ``meta.json``. An edit
lives at ``footage/edits/<edit_id>.json`` and every change to it goes through
``validate_edl`` — structurally, with ``allow_unreliable=True`` (an edit is a plan; the
trust refusal belongs where the encode does, in :func:`render`).
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Optional, Union

from muvid.footage.edl import (
    DECLARED,
    MEASURED,
    MIN_CONFIDENCE,
    MIN_MARGIN,
    MIN_SUPPORT,
    FootageAlignment,
)
from muvid.footage.errors import FootageCancelled, FootageError

# -- resource caps (env-tunable) ----------------------------------------------
#: Most clips one project may hold (env ``MUVID_FOOTAGE_MAX_CLIPS``).
MAX_CLIPS = int(os.environ.get("MUVID_FOOTAGE_MAX_CLIPS", "8"))
#: Most photos one project may hold (env ``MUVID_FOOTAGE_MAX_STILLS``) — separate from
#: :data:`MAX_CLIPS`: a photo costs no decode at cut time and a day out is dozens of them.
MAX_STILLS = int(os.environ.get("MUVID_FOOTAGE_MAX_STILLS", "40"))
#: The file types taken as a photo rather than a video (lower-case, with the dot).
STILL_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif")
#: How much of a song listening must have heard in a video — its windowed-vote support —
#: for an UNPLACEABLE video to count as "has the song, place it by hand" rather than "has
#: no song in it, cut it to the music" (env ``MUVID_HEARD_SUPPORT``). Below
#: :data:`~muvid.footage.edl.MIN_SUPPORT` (which places a video); measured: b-roll with
#: no song in it scored at most 0.33 with a positive margin (eight phone clips of a day
#: out; a 6 s one reached 0.5 at margin 0.0 — hence the margin condition too), while an
#: off-by-a-bar alias of a real recording scored 0.487 at margin +0.115 (muvid#59). A
#: first cut, not a calibration — the independent second opinion is thorwhalen/mixing#55.
HEARD_SUPPORT = float(os.environ.get("MUVID_HEARD_SUPPORT", "0.4"))
#: A stored photo's longest side, pixels: enough for a 4K canvas with a Ken Burns zoom,
#: small enough that a 48-megapixel phone photo does not cost every cut a big decode.
STILL_MAX_SIDE = 3840
#: What a clip's ``has_song`` may say: ``auto`` (the default — believe the listening),
#: ``yes`` (it is a recording of the song: sync it, never cut it to the music) or ``no``
#: (it is not: always cut it to the music).
HAS_SONG_CHOICES = ("auto", "yes", "no")
#: Largest clip file accepted, bytes (env ``MUVID_FOOTAGE_MAX_BYTES``).
CLIP_MAX_BYTES = int(os.environ.get("MUVID_FOOTAGE_MAX_BYTES", str(400 * 1024 * 1024)))
#: Longest clip accepted, seconds (env ``MUVID_FOOTAGE_MAX_CLIP_DURATION_S``).
CLIP_MAX_DURATION_S = int(
    os.environ.get("MUVID_FOOTAGE_MAX_CLIP_DURATION_S", str(12 * 60))
)
#: Largest song file accepted, bytes (env ``MUVID_FOOTAGE_SONG_MAX_BYTES``).
SONG_MAX_BYTES = int(
    os.environ.get("MUVID_FOOTAGE_SONG_MAX_BYTES", str(100 * 1024 * 1024))
)
#: Longest song accepted, seconds (env ``MUVID_FOOTAGE_MAX_SONG_DURATION_S``).
SONG_MAX_DURATION_S = int(
    os.environ.get("MUVID_FOOTAGE_MAX_SONG_DURATION_S", str(12 * 60))
)
#: Default cap on points-per-(clip, metric) a ``scores`` reply carries.
MAX_WIRE_POINTS = int(os.environ.get("MUVID_SCORING_MAX_POINTS_WIRE", "1500"))
#: The scoring grid step the connector and the studio default to (10 Hz).
DEFAULT_SCORE_HOP_S = 0.1

#: How far from the median offset a clip must sit to be named an outlier: several
#: devices recording one performance land within a second or two of each other.
OUTLIER_FROM_MEDIAN_S = 5.0
#: Spans shorter than this (seconds) are float noise, as in :mod:`muvid.footage.edl`.
_EPS = 1e-3
#: How long the song fades out at the end of a render that stops before the song does
#: (a trimmed edit's ``span``) — long enough not to cut the music off mid-note, short
#: enough not to eat the last bar the edit chose to end on.
TAIL_FADE_S = 1.5
#: Where in its source the cover frame is taken, as a fraction of the duration — past
#: the opening (often black or a count-in), well before the end.
COVER_AT_FRACTION = 0.25
#: Width of the cover frame, pixels (height follows the aspect).
COVER_WIDTH = 640
#: Lengths of the generated ids (hex characters).
_CLIP_ID_HEX, _EDIT_ID_HEX, _RENDER_ID_HEX = 8, 8, 12
#: Re-exported: the calibrated constants live with the aligner so that what the tools
#: REPORT and what ``validate_edl`` REFUSES cannot drift apart (muvid#59).
_MIN_CONFIDENCE = MIN_CONFIDENCE


# =============================================================================
# media: the song and the clips
# =============================================================================


def set_song(
    fp,
    *,
    path: str,
    ext: str = "",
    filename: str = "",
    duration_s: Optional[float] = None,
) -> dict:
    """Set the project's song — the clean master every video is aligned to and whose
    audio the finished video uses. Replaces any previous song.

    The file arrives from the host (an upload): ``path`` is where the host put it and
    ``filename`` its original name, so the song keeps its name and extension; it is
    copied into the project, so the host may delete its copy afterwards.
    Replacing the song THROWS AWAY every clip's offset and every footage score, because
    they were measured against the old song — re-run ``align`` afterwards. Size- and
    duration-capped.

    Returns ``song_duration`` (seconds), the stored ``song`` (name, and the
    ``artifact_id`` to play it by when the project is hosted) and whether an alignment
    was dropped.
    """
    src = _existing_file(path, what="song")
    _refuse_oversize(src, cap=SONG_MAX_BYTES, what="song")
    dur = float(duration_s) if duration_s is not None else _probe_duration(src)
    if dur > SONG_MAX_DURATION_S:
        raise FootageError(
            f"song is {dur:.0f}s; the {SONG_MAX_DURATION_S}s limit is exceeded"
        )
    had_alignment = bool(fp.load_alignments())
    fp.set_song(
        str(src),
        ext=_ext_of(ext, filename, src),
        name=Path(filename).name if filename else "",
        duration_s=dur,
    )
    _warm(song_analysis, fp)  # the beat and sections, while the person waits anyway
    return {
        "song_duration": round(dur, 2),
        "song": fp.song_info(),
        "alignment_invalidated": had_alignment,
    }


def add_clip(
    fp,
    *,
    path: str,
    name: str = "",
    filename: str = "",
    clip_id: str = "",
    ext: str = "",
    duration_s: Optional[float] = None,
) -> dict:
    """Add one video or photo to the project.

    A video that is a recording of the song (a concert, a dance) is synced to it once
    ``align`` has listened to it; any other video, and every photo, is cut to the music
    instead (``propose_edit``). A photo (a file ending in one of
    :data:`STILL_SUFFIXES`) is stored upright and at most :data:`STILL_MAX_SIDE` pixels
    on its longest side, and moves on screen with a pan or zoom.

    The file arrives from the host (an upload): ``path`` is where the host put it and
    ``filename`` its original name; it is copied into the project. ``name`` is what the
    video is called on screen (default: the original file name without its extension). ``clip_id`` fixes the id (default: a fresh one); an id already in the
    project is refused. Size-, duration- and count-capped.

    Run ``align`` afterwards: listening is how a video with the song in it gets its
    place on the song. Returns the ``clip_id``, its ``name`` and ``duration`` (and
    ``artifact_id`` when hosted); a photo also ``kind: "still"``, ``width``, ``height``.
    """
    rows = fp.list_clips()
    known = {c["clip_id"] for c in rows}
    still = _is_still_name(filename or str(path), ext)
    n_stills = sum(1 for c in rows if c.get("kind") == STILL_KIND)
    if still and n_stills >= MAX_STILLS:
        raise FootageError(f"photo limit reached ({MAX_STILLS}); this is a bounded v1")
    if not still and len(known) - n_stills >= MAX_CLIPS:
        raise FootageError(f"video limit reached ({MAX_CLIPS}); this is a bounded v1")
    clip_id = _normalised_id(clip_id, label="clip_id") if clip_id else ""
    if clip_id and clip_id in known:
        raise FootageError(
            f"clip {clip_id!r} is already in the project — remove it first, or add this "
            "one under another id"
        )
    src = _existing_file(path, what="clip")
    _refuse_oversize(src, cap=CLIP_MAX_BYTES, what="clip")
    if still:
        return _add_still(fp, src, clip_id=clip_id, name=name, filename=filename)
    dur = float(duration_s) if duration_s is not None else _probe_duration(src)
    if dur > CLIP_MAX_DURATION_S:
        raise FootageError(
            f"clip is {dur:.0f}s; the {CLIP_MAX_DURATION_S}s limit is exceeded"
        )
    cid = clip_id or uuid.uuid4().hex[:_CLIP_ID_HEX]
    label = name or (Path(filename).stem if filename else "") or cid
    fp.add_clip(cid, str(src), ext=_ext_of(ext, filename, src), name=label)
    _record_clip_duration(fp, cid, dur)
    _record_clip_hash(fp, cid)
    _warm(_warm_activity, fp, cid)
    _ensure_cover(fp)
    _prepare_filmstrip(fp, cid)
    out = {"clip_id": cid, "name": label, "duration": round(dur, 2)}
    row = next((c for c in fp.list_clips() if c["clip_id"] == cid), {})
    if row.get("artifact_id"):
        out["artifact_id"] = row["artifact_id"]
    return out


STILL_KIND = "still"


def _is_still_name(name: str, ext: str = "") -> bool:
    suffix = ("." + ext.lstrip(".")) if ext else Path(name or "").suffix
    return suffix.lower() in STILL_SUFFIXES


def _add_still(fp, src: Path, *, clip_id: str, name: str, filename: str) -> dict:
    """Store a photo upright (its EXIF orientation applied — a phone portrait is stored
    sideways otherwise, and ffmpeg does not turn it), RGB, as JPEG, bounded in size."""
    import tempfile

    try:
        from PIL import Image, ImageOps
    except ImportError as e:  # pragma: no cover - pillow ships with the scoring extra
        raise FootageError(f"adding photos needs Pillow ({e})") from e
    if src.suffix.lower() in (".heic", ".heif"):
        try:
            from pillow_heif import register_heif_opener
        except ImportError as e:
            raise FootageError(
                f"{src.name!r} is an iPhone HEIC photo, which this server cannot read yet "
                "— export it as JPEG (on the iPhone: Settings > Camera > Formats > Most "
                "Compatible) and add it again"
            ) from e
        register_heif_opener()
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((STILL_MAX_SIDE, STILL_MAX_SIDE))
            with tempfile.TemporaryDirectory() as tmp:
                normalised = Path(tmp) / "still.jpg"
                im.save(normalised, "JPEG", quality=92)
                width, height = im.size
                cid = clip_id or uuid.uuid4().hex[:_CLIP_ID_HEX]
                label = name or (Path(filename).stem if filename else "") or cid
                fp.add_clip(cid, str(normalised), ext="jpg", name=label, kind=STILL_KIND)
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        # PIL's "cannot identify image file" is an OSError; a huge photo is a bomb error
        raise FootageError(f"could not read {src.name!r} as a photo: {e}") from e
    _ensure_cover(fp)
    out = {"clip_id": cid, "name": label, "kind": STILL_KIND, "duration": None,
           "width": width, "height": height}
    row = next((c for c in fp.list_clips() if c["clip_id"] == cid), {})
    if row.get("artifact_id"):
        out["artifact_id"] = row["artifact_id"]
    return out


def set_has_song(
    fp, *, clip_id: str, has_song: Literal["auto", "yes", "no"]
) -> dict:
    """Say whether a video has the song in its own sound — overriding what listening found.

    ``"yes"``: it is a recording of the song (a concert, a dance); it is synced to the
    song and never cut freely to the music. ``"no"``: it is not (a day out, b-roll); it
    is always cut to the music. ``"auto"`` (the default) believes the listening: a
    video ``align`` placed confidently is synced, any other is cut to the music. A
    photo is always cut to the music. Like a hand-placed offset, the choice survives
    listening again. Takes effect at the next ``propose_edit``.
    """
    cid = _normalised_id(clip_id, label="clip_id")
    if has_song not in HAS_SONG_CHOICES:
        raise FootageError(
            f"has_song must be one of {list(HAS_SONG_CHOICES)}, got {has_song!r}"
        )
    m = fp.manifest()
    row = next((c for c in m.get("clips", []) if c.get("clip_id") == cid), None)
    if row is None:
        raise FootageError(_unknown_clip_message(cid, fp.list_clips()))
    if row.get("kind") == STILL_KIND and has_song != "auto":
        raise FootageError("a photo has no sound — it is always cut to the music")
    if has_song == "auto":
        row.pop("has_song", None)
    else:
        row["has_song"] = has_song
    fp._write_manifest(m)
    placements = {a.clip_id: a for a in fp.load_alignments()}
    return {"clip_id": cid, "has_song": has_song,
            "role": _role_of(row, placements.get(cid))}


#: A clip's role in the edit: SYNCED to the song where it was filmed, cut FREEly to the
#: music (:mod:`muvid.footage.music_cut`), or NOT YET LISTENED to (a video ``align`` has
#: not heard, which nothing may guess about: a concert cut to the music is a desynced
#: concert, the muvid#59 failure by another road).
SYNCED, FREE_ROLE, UNHEARD = "synced", "to_the_music", "not_listened"


def heard_the_song(alignment) -> bool:
    """Did listening hear the song in this video, placed or not? ``True`` when the
    aligner vouches for its place, and ALSO when it could not place it but enough of the
    clip agreed on some offset (:data:`HEARD_SUPPORT`) — the repetitive-song case of
    muvid#59, where the song is plainly there and its place is ambiguous. ``False`` is
    "couldn't hear the song in it": the video is cut to the music."""
    if alignment.reliable:
        return True
    # Both, because support alone is coarse on a short clip (a 6 s clip votes with two
    # or three windows: 0.33, 0.5) — measured, a 6 s b-roll clip at support 0.5 had a
    # margin of 0.0, while the muvid#59 alias was 0.487 with a margin of +0.115.
    return (
        alignment.support is not None
        and alignment.support >= HEARD_SUPPORT
        and alignment.margin is not None
        and alignment.margin > 0.0
    )


def _role_of(row: dict, alignment) -> str:
    """The one rule that decides how a clip is used: a photo is cut to the music; what a
    person said (``set_has_song``) holds; a video nobody has listened to is UNHEARD; a
    video listening heard the song in is synced (placed, or set aside until placed by
    hand when its place is unsure); any other is cut to the music."""
    if row.get("kind") == STILL_KIND:
        return FREE_ROLE
    has = row.get("has_song", "auto")
    if has == "yes":
        return SYNCED
    if has == "no":
        return FREE_ROLE
    if alignment is None:
        return UNHEARD
    return SYNCED if heard_the_song(alignment) else FREE_ROLE


def _placements(fp) -> list:
    """A placement for EVERY clip: its alignment where it has one, else an UNPLACED
    record (never persisted) carrying its duration — what a free cut needs to be
    validated and rendered. A photo's duration is the bound
    :data:`~muvid.footage.edl.STILL_DURATION_S`."""
    from muvid.footage.edl import STILL_DURATION_S, unplaced

    aligns = fp.load_alignments()
    have = {a.clip_id for a in aligns}
    out = list(aligns)
    for c in fp.manifest().get("clips", []):
        cid = c["clip_id"]
        if cid in have:
            continue
        if c.get("kind") == STILL_KIND:
            out.append(unplaced(cid, STILL_DURATION_S))
        else:
            out.append(unplaced(cid, _clip_duration(fp, cid)))
    return out


def footage_roles(fp) -> dict:
    """``{clip_id: "synced" | "to_the_music"}`` — how ``propose_edit`` will use each clip."""
    by_id = {a.clip_id: a for a in fp.load_alignments()}
    return {
        c["clip_id"]: _role_of(c, by_id.get(c["clip_id"]))
        for c in fp.manifest().get("clips", [])
    }


def _free_sources(fp, placements) -> list:
    """The clips and photos to cut to the music, as :class:`music_cut.FreeSource`."""
    from muvid.footage.music_cut import STILL, VIDEO, FreeSource

    by_id = {a.clip_id: a for a in fp.load_alignments()}
    paths = fp.clip_paths()
    out = []
    for c in fp.manifest().get("clips", []):
        cid = c["clip_id"]
        if _role_of(c, by_id.get(cid)) != FREE_ROLE:
            continue
        still = c.get("kind") == STILL_KIND
        out.append(
            FreeSource(
                clip_id=cid,
                path=paths[cid],
                kind=STILL if still else VIDEO,
                duration_s=None if still else _clip_duration(fp, cid),
            )
        )
    return out


def _activity_envelope(fp, source):
    """A video's picture-change envelope, measured once and kept (``beats/``)."""
    from muvid.footage import beats as bs
    from muvid.footage.music_cut import Envelope

    path = Path(source.path)
    try:
        record = bs.cached_signals(
            fp.root,
            _recorded_clip_hash(fp, source.clip_id, path),
            bs.ACTIVITY,
            lambda: bs.activity_signal(path),
        )
    except Exception:  # noqa: BLE001 — cv2.error, a decoder's own: unmeasured, never fatal
        return None  # the planner's blind stride
    return Envelope.from_signals(record.get("signals") or {})


def song_analysis(fp):
    """The song's beats, bars and sections for cutting to the music
    (:func:`muvid.montage.analysis.analyze`), measured once per song and kept beside the
    other per-media measurements (``beats/``), keyed on the song's hash."""
    from muvid.footage import music_cut
    from muvid.footage.media_views import read_json, write_json
    from muvid.montage.analysis import Analysis

    path = Path(fp.root) / "beats" / f"{fp.song_hash()[:16]}-montage-analysis.json"
    cached = read_json(path)
    if cached is not None:
        try:
            return Analysis.from_dict(cached)
        except (KeyError, TypeError, ValueError):
            pass  # an older shape: measure again
    analysis = music_cut._default_analysis(str(fp.song_path()))
    write_json(path, analysis.to_dict())
    return analysis


def _warm(fn, *args) -> None:
    """Measure now what "Cut it for me" would otherwise measure on the spot — at upload,
    where a person already waits. Best-effort: a failure here is measured (and reported)
    again when it is needed."""
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        pass


def _set_aside_unvouched(entries, alignments):
    """Turn every anchored cut to an unvouched clip into a gap, reported as
    :class:`~muvid.footage.edl.ExcludedSpan` — the muvid#88 recovery, without its
    "keep the edit non-empty" clause, for when the music can fill what it leaves."""
    from muvid.footage.edl import ExcludedSpan, _exclusion_reason

    by_id = {a.clip_id: a for a in alignments}
    kept, excluded = [], []
    for e in entries:
        a = None if e.is_gap or e.is_free else by_id.get(e.clip_id)
        if a is None or a.reliable:
            kept.append(e)
            continue
        excluded.append(
            ExcludedSpan(
                clip_id=e.clip_id,
                song_start=e.song_start,
                song_end=e.song_end,
                reason=_exclusion_reason(e, alignments),
                confidence=a.confidence,
                support=a.support,
                margin=a.margin,
            )
        )
    return [e for e in kept if not e.is_gap], excluded


def _music_fill(fp, entries, sources, song_dur, *, pace: str = "", canvas=None):
    """Replace the gap entries of ``entries`` with free cuts of ``sources`` cut to the
    music; ``(entries, report)``. The synced cuts are untouched."""
    from muvid.footage.assemble import DEFAULT_FPS
    from muvid.footage.edl import fill_gaps
    from muvid.footage.music_cut import fill_spans

    entries = fill_gaps(entries, song_dur) if entries else []
    gaps = (
        [(e.song_start, e.song_end) for e in entries if e.is_gap]
        if entries
        else [(0.0, song_dur)]
    )
    if not gaps or not sources:
        return entries, None
    cut = fill_spans(
        str(fp.song_path()),
        sources,
        analysis=song_analysis(fp),
        spans=gaps,
        song_duration=song_dur,
        cut_feel=pace,
        envelope_of=lambda s: _activity_envelope(fp, s),
        canvas=canvas or fp.canvas(),
        fps=DEFAULT_FPS,
    )
    kept = [e for e in entries if not e.is_gap]
    return fill_gaps(sorted(kept + cut.entries, key=lambda e: e.song_start), song_dur), cut.report


def remove_clip(fp, *, clip_id: str) -> dict:
    """Remove one footage video from the project — its stored file and its entry.

    Irreversible for the clip (add it again if it was a mistake); existing renders are
    untouched. Removal INVALIDATES every measured offset and every footage score, as
    changing the song does — the alignment describes the clip set it was measured on —
    so run ``align`` again before cutting. Offsets a person DECLARED for the remaining
    clips are kept. An unknown ``clip_id`` is refused, naming the project's clips.
    """
    known = fp.list_clips()
    if clip_id not in {c["clip_id"] for c in known}:
        raise FootageError(_unknown_clip_message(clip_id, known))
    removed = fp.remove_clip(clip_id)
    return {
        "removed": {"clip_id": removed["clip_id"], "name": removed["name"]},
        "clips": fp.list_clips(),
        "alignment_invalidated": removed["alignment_invalidated"],
        "scores_invalidated": removed["scores_invalidated"],
    }


def _normalised_id(value: str, *, label: str) -> str:
    """A caller's id, stripped and checked (letters, digits, ``_``, ``-``; ≤64)."""
    from muvid.footage.workspace import normalise_id

    try:
        return normalise_id(value, label=label)
    except ValueError as e:
        raise FootageError(str(e)) from e


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


# =============================================================================
# the state of the project
# =============================================================================


def status(fp) -> dict:
    """Where the music video stands: the song, the videos and where each sits on the
    song, the saved edits, the finished videos, and the next useful step.

    ``aligned`` lists the clips that have an offset; ``alignments`` says for each one
    whether the offset was ``measured`` (by ``align``) or ``declared`` (``set_offset``)
    and whether it is trusted for rendering (``reliable``). ``renders`` is newest first
    (the same rows ``renders`` gives — no server paths).
    ``next_step`` names the operation that moves the project forward and why.
    """
    m = fp.manifest()
    aligns = fp.load_alignments()
    render_rows = renders(fp)["renders"]
    edit_rows = [_edit_summary(fp, rec) for rec in fp.list_edit_records()]
    out = {
        "title": m.get("title", fp.project_id),
        "canvas": m.get("canvas"),
        "has_song": fp.has_song(),
        "song_duration": round(fp.song_duration(), 2) if fp.has_song() else None,
        "clips": fp.list_clips(),
        "aligned": [a.clip_id for a in aligns],
        "renders": render_rows,
        "song": fp.song_info(),
        "alignments": [_alignment_row(a) for a in aligns],
        # how "Cut it for me" will use each clip: synced, or cut to the music
        "roles": footage_roles(fp),
        "edits": edit_rows,
        "next_step": _next_step(fp, aligns, edit_rows, render_rows),
    }
    cover = fp.cover_info()
    if cover and cover.get("artifact_id"):
        out["cover_artifact_id"] = cover["artifact_id"]
    return out


def _alignment_row(a: FootageAlignment) -> dict:
    return {
        "clip_id": a.clip_id,
        "offset_s": a.offset_s,
        "source": a.source,
        "reliable": a.reliable,
        "overlaps": a.overlaps,
        "coverage": list(a.coverage),
        "duration_s": a.duration_s,
    }


def _next_step(fp, aligns, edit_rows, render_rows) -> dict:
    """The one operation that moves the project forward, and why — in workflow order."""
    rows = fp.list_clips()
    clip_ids = {c["clip_id"] for c in rows}
    video_ids = {c["clip_id"] for c in rows if c.get("kind") != STILL_KIND}
    said_yes = {c["clip_id"] for c in rows if c.get("has_song") == "yes"}
    aligned = {a.clip_id for a in aligns}
    steps = (
        (not fp.has_song(), "set_song", "there is no song yet"),
        (not clip_ids, "add_clip", "there are no videos or photos yet"),
        (
            not video_ids <= aligned,
            "align",
            "some videos have not been listened to yet — listening is how a video "
            "with the song in it gets synced (the others are cut to the music)",
        ),
        (
            any(not a.reliable and a.clip_id in said_yes for a in aligns),
            "set_offset",
            "a video you said has the song in it could not be placed by listening — "
            "place it by hand",
        ),
        (not edit_rows, "propose_edit", "there is no edit yet"),
        (not render_rows, "render", "no edit has been made into a video yet"),
    )
    for condition, op, why in steps:
        if condition:
            return {"op": op, "why": why}
    return {
        "op": "set_cut",
        "why": "the video is made — watch it, change a cut, and make it again",
    }


# =============================================================================
# where each clip sits on the song
# =============================================================================


def align(fp) -> dict:
    """Find where each video sits on the song by listening to its own audio, and save it.

    Returns each clip's offset, a confidence in [0,1], its ``support`` (the fraction of
    the clip that agrees on that offset, ``null`` when the aligner took a single
    whole-clip measurement), and its coverage of the song, plus these lists:

    - ``low_confidence`` — clips that matched weakly, for reporting;
    - ``unreliable`` — clips whose offset the aligner will NOT vouch for: most often a
      video that simply does not have the song in it (a day out, b-roll). These stay
      in the project, and ``propose_edit`` cuts them TO the music instead of syncing
      them (``footage_roles``) — unless listening heard the song in them without being
      able to place it (``heard_the_song``), or a person said they have the song
      (``set_has_song yes``). Those stay synced: the auto path prefers other clips over
      them and sets aside a span only they cover (``coverage.excluded``, muvid#88),
      which is then filled from the footage cut to the music, if there is any. Rendering still REFUSES an explicit edit that cuts to one, and still
      refuses an auto edit when NO clip is trustworthy, unless called with
      ``allow_unreliable=true`` — because a wrong offset does not fail, it renders a
      video out of sync with the song (muvid#59). Re-align, place the clip by hand
      (``set_offset``), accept the smaller edit, or opt in deliberately;
    - ``no_consensus`` — clips too short to be put to a vote at all (under about 4.5 s).
      **A clip in this list can be marked reliable and still be wrong**, and no other
      field will say so: its offset rests on one measurement, judged by a confidence
      score that does not rank correctness in this band — measured on the muvid#59
      shoot, the WRONG offset scored highest of three (0.834 against 0.566 and 0.621),
      and on a repeating fixture a 4.4 s clip landing 8 s out is vouched at 0.381.
      Nothing is refused on this basis, because refusing would take the correct short
      clips with it. So if a short clip looks out of sync in the render, this list is
      the first place to look — and muvid#91 is where that trade-off is being decided.

    A clip whose offset a person DECLARED (``set_offset``) is ALWAYS left alone — a
    person placed it, usually because the aligner got it wrong (muvid#59) — and is named
    in ``kept_declared``. To have one measured again, ``clear_offset`` it first. Every
    measured record says ``source: "measured"``.

    Run this after adding/removing clips and before cutting.
    """
    from muvid.footage.align import align_footage as _align
    from muvid.footage.scoring.grid import align_fingerprint

    if not fp.has_song():
        raise FootageError("no song set — call set_song first")
    stills = {c["clip_id"] for c in fp.list_clips() if c.get("kind") == STILL_KIND}
    clips = [(cid, p) for cid, p in fp.clip_paths().items() if cid not in stills]
    if not clips:
        raise FootageError(
            "no videos to listen to — add a video (photos have no sound to place them by)"
        )
    previous = fp.load_alignments()
    declared = {a.clip_id: a for a in previous if a.source == DECLARED}
    to_measure = [(cid, p) for cid, p in clips if cid not in declared]
    measured = (
        _align(str(fp.song_path()), to_measure, song_duration=fp.song_duration())
        if to_measure
        else []
    )
    by_id = {a.clip_id: a for a in measured} | declared
    aligns = [by_id[cid] for cid, _ in clips if cid in by_id]
    fp.save_alignments(aligns)
    # Scores are keyed to the offsets they were computed under (align_fingerprint is that
    # key's SSOT), so a re-align that reproduces the same offsets must not throw away the
    # most expensive artifact in the pipeline (muvid#24 B4). Correctness does not depend on
    # deleting here — the read path refuses stale scores via manifest_is_current — this
    # only reclaims storage the moment the scores are known to be stale.
    if align_fingerprint(aligns) != align_fingerprint(previous):
        fp.invalidate_scores()
    # Every clip has a record now, so "did not overlap" is a REPORTED PROPERTY of a clip
    # that is still there, not an inference from something missing. A clip is never removed
    # from the project or from the alignment artifact by anything but an explicit request.
    aligned_ids = {a.clip_id for a in aligns}
    return {
        "alignments": [a.to_dict() for a in aligns],
        "low_confidence": [
            {"clip_id": a.clip_id, "confidence": round(a.confidence, 3)}
            for a in aligns
            if a.confidence < _MIN_CONFIDENCE
        ],
        # The list that has TEETH, kept separate from `low_confidence` because they
        # answer different questions: one is a number to look at, the other is what
        # the render will refuse (muvid#59).
        "unreliable": [
            {
                "clip_id": a.clip_id,
                "confidence": round(a.confidence, 3),
                "support": _round_opt(a.support),
                # The separator, and the one to read first: negative means the clip's
                # own evidence prefers a DIFFERENT offset, which is why it was refused.
                "margin": _round_opt(a.margin),
                "window_s": _round_opt(a.window_s),
            }
            for a in aligns
            if not a.reliable
        ],
        # REPORTED, never enforced. `support: null` means the estimator could not hold a
        # vote at all (a clip shorter than ~4.5 s): the offset rests on one measurement
        # and the verdict falls back to the confidence coefficient, which does not rank
        # correctness in this band — so a clip in here can be `reliable: true` AND WRONG
        # (muvid#91 owns that decision). Declared records are not in it: nobody voted.
        "no_consensus": [
            a.clip_id for a in aligns if a.support is None and a.source == MEASURED
        ],
        "confidence_metric": "onset-envelope correlation at the waveform's lag",
        "confidence_threshold": _MIN_CONFIDENCE,
        # Support must EXCEED this: at exactly this value every window had the offset on
        # its ballot and none found it unaided. A FLOOR on how much evidence reached it...
        "support_threshold": MIN_SUPPORT,
        "support_threshold_is_exclusive": True,
        # ...and the SEPARATOR, which does the work: negative margin = the evidence
        # prefers elsewhere (muvid#59: margin>0 passes 24/24 correct and 0/6 noise).
        "margin_threshold": MIN_MARGIN,
        "margin_threshold_is_exclusive": True,
        "offset_consensus": _offset_consensus(aligns),
        # Usable-for-an-edit, not present-in-the-project: these clips are still here.
        "no_overlap_with_song": [a.clip_id for a in aligns if not a.overlaps],
        # Should always be empty; non-empty is a bug upstream, not a verdict on footage.
        "unrecorded": [cid for cid, _ in clips if cid not in aligned_ids],
        "kept_declared": sorted(declared),
    }


def set_offset(fp, *, clip_id: str, offset_s: float) -> dict:
    """Place one video on the song BY HAND: the song time at which the video's own
    first frame plays (negative = the video starts before the song does).

    Use it when ``align`` gets a clip wrong — on long, repetitive songs it can land a
    whole chorus away (muvid#59) — or when you already know the offset. The offset is
    recorded as ``source: "declared"`` and trusted for rendering (a person vouched for
    it); how much of the song the clip covers is computed from the two durations.
    ``align`` keeps it unless told otherwise. Changing an offset makes the footage
    scores stale, so they are dropped.
    """
    from muvid.footage.scoring.grid import align_fingerprint

    known = fp.list_clips()
    if clip_id not in {c["clip_id"] for c in known}:
        raise FootageError(_unknown_clip_message(clip_id, known))
    if not fp.has_song():
        raise FootageError("no song set — call set_song first")
    offset = float(offset_s)
    if not math.isfinite(offset):
        raise FootageError(
            f"offset_s must be a finite number of seconds, got {offset_s}"
        )
    record = declared_alignment(
        clip_id,
        offset,
        clip_duration=_clip_duration(fp, clip_id),
        song_duration=fp.song_duration(),
    )
    previous = fp.load_alignments()
    order = [c["clip_id"] for c in known]
    by_id = {a.clip_id: a for a in previous} | {clip_id: record}
    aligns = [by_id[cid] for cid in order if cid in by_id]
    fp.save_alignments(aligns)
    stale = align_fingerprint(aligns) != align_fingerprint(previous)
    if stale:
        fp.invalidate_scores()
    return {"alignment": record.to_dict(), "scores_invalidated": stale}


def clear_offset(fp, *, clip_id: str) -> dict:
    """Forget where I placed this video: remove a hand-declared offset, so the next
    ``align`` measures the clip by its audio instead.

    Only a DECLARED offset can be forgotten (a measured one is replaced by aligning
    again); an unknown clip, or one with no declared offset, is refused. Until ``align``
    runs again the clip has no place on the song, and footage scores made with the old
    offset are dropped.
    """
    from muvid.footage.scoring.grid import align_fingerprint

    known = fp.list_clips()
    if clip_id not in {c["clip_id"] for c in known}:
        raise FootageError(_unknown_clip_message(clip_id, known))
    previous = fp.load_alignments()
    record = next((a for a in previous if a.clip_id == clip_id), None)
    if record is None or record.source != DECLARED:
        raise FootageError(
            f"clip {clip_id!r} has no offset placed by hand — nothing to forget"
        )
    aligns = [a for a in previous if a.clip_id != clip_id]
    fp.save_alignments(aligns)
    stale = align_fingerprint(aligns) != align_fingerprint(previous)
    if stale:
        fp.invalidate_scores()
    return {
        "cleared": clip_id,
        "offset_s": record.offset_s,
        "scores_invalidated": stale,
    }


def declared_alignment(
    clip_id: str, offset_s: float, *, clip_duration: float, song_duration: float
) -> FootageAlignment:
    """The alignment record of a DECLARED offset — coverage computed, nothing measured.

    ``confidence`` is 1.0 and ``reliable`` True because a person vouched for the offset;
    ``support``/``margin`` stay ``None`` because no vote was held. ``source`` says so.

    >>> a = declared_alignment("c1", -8.5, clip_duration=260.0, song_duration=249.6)
    >>> a.coverage, a.overlaps, a.source
    ((0.0, 249.6), True, 'declared')
    """
    lo = max(0.0, offset_s)
    hi = min(song_duration, offset_s + clip_duration)
    overlaps = hi - lo > _EPS
    return FootageAlignment(
        clip_id=clip_id,
        offset_s=offset_s,
        confidence=1.0,
        duration_s=clip_duration,
        coverage=(lo, hi) if overlaps else (lo, lo),
        overlaps=overlaps,
        support=None,
        reliable=True,
        source=DECLARED,
    )


def _offset_consensus(aligns) -> dict:
    """How well the clips AGREE on their offsets — evidence a per-clip score cannot give.

    Several devices recording one performance land at nearly the same offset, so a clip
    far from the cluster is the suspect one regardless of its confidence. Reported, never
    enforced: a signal for the caller, not another silent gate.
    """
    if len(aligns) < 2:
        return {"median_offset": None, "outliers": []}
    offsets = sorted(a.offset_s for a in aligns)
    median = offsets[len(offsets) // 2]
    return {
        "median_offset": round(median, 3),
        "outliers": [
            {
                "clip_id": a.clip_id,
                "offset_s": round(a.offset_s, 2),
                "from_median": round(abs(a.offset_s - median), 2),
            }
            for a in aligns
            if abs(a.offset_s - median) > OUTLIER_FROM_MEDIAN_S
        ],
    }


def timeline(fp) -> dict:
    """Which videos cover which spans of the song (overlaps shown), from the saved
    alignment — the map for choosing what to cut to. Run ``align`` first."""
    aligns = fp.load_alignments()
    if not aligns:
        raise FootageError("no alignment yet — call align_footage first")
    song_dur = fp.song_duration()
    boundaries = sorted({0.0, song_dur} | {b for a in aligns for b in a.coverage})
    spans = []
    for lo, hi in zip(boundaries, boundaries[1:]):
        if hi - lo <= _EPS:
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
        "song_duration": round(song_dur, 2),
        "spans": spans,
        "uncovered": [s for s in spans if not s["covered_by"]],
    }


# =============================================================================
# the song's beat grid (muvid#18 item 5)
# =============================================================================

#: Where the song's beat grid is cached: beside the score tensor it feeds, under the
#: project's ``scores/`` dir, so it shares that dir's lifecycle. Keyed on ``song_hash``
#: on top of that, so a record is never served for a song it was not measured on.
_BEAT_GRID_CACHE_NAME = "beat_grid.json"
#: Rounded exactly as ``grid.save_scores`` rounds a scoring run's manifest.
_BEAT_TIME_DECIMALS = 4
_TEMPO_DECIMALS = 3
#: The ``source`` vocabulary of a ``beat_grid`` reply, cheapest first.
_BEAT_GRID_FROM_CACHE = "cache"
_BEAT_GRID_FROM_SCORES = "scores"
_BEAT_GRID_COMPUTED = "computed"


def beat_grid(fp) -> dict:
    """The song's beat grid — tempo and beat instants on the song timeline — without
    looking at the footage.

    Computed once on the song (never per clip — clips map to it through their offsets)
    and cached under the project keyed on the song's content hash, so the second call is
    a file read; a project that has been scored is served from that run instead.
    ``source`` says which (``computed`` | ``cache`` | ``scores``).

    Needs the ``scoring`` extra (librosa); without it the refusal names the install.
    Needs a song; no alignment is required.

    Returns ``tempo_bpm``, ``beats`` (seconds, ascending), ``n_beats``,
    ``song_duration`` and ``source``, and for numbering bars: ``downbeats`` (the beats
    that start a bar), ``downbeats_source`` — ``measured`` (the estimator found them),
    ``derived`` (the beat phase carrying the most onset energy, ``beats_per_bar`` beats
    to a bar — muvid.montage's rule) or ``first_beat`` (no onset energy to vote with, so
    bars start on the first beat) — ``beats_per_bar`` and ``bar_of_beat`` (each beat's
    bar number, 1 for the first bar, 0 for a pickup before it).
    """
    if not fp.has_song():
        raise FootageError("no song set — call set_song first")
    song_hash = fp.song_hash()
    source, record = _beat_grid_record(fp, song_hash=song_hash)
    beats = list(record.get("beat_times") or [])
    from muvid.footage.beats import fitted_tempo

    reply = {
        # Fitted to the beats, not the estimator's own figure (which can be biased by
        # a couple of percent — see `fitted_tempo`); the estimator's when too few.
        "tempo_bpm": fitted_tempo(beats) or record.get("tempo_bpm"),
        "beats": beats,
        "n_beats": len(beats),
        "song_duration": fp.song_duration(),
        "source": source,
    }
    reply.update(_bars(beats, record))
    return reply


#: Beats to a bar when the estimator does not say (4/4, as muvid.montage assumes).
BEATS_PER_BAR = 4


def _bars(beats: list, record: dict) -> dict:
    """The bar numbering of a grid: measured downbeats when there are any, else the
    onset-energy vote over the beat phases, else the first beat (said which)."""
    import bisect

    measured = list(record.get("downbeat_times") or [])
    if measured:
        downbeats, source = measured, "measured"
    else:
        from muvid.montage.analysis import downbeats_from_beats

        energy = list(record.get("onset_at_beats") or [])
        downbeats = list(downbeats_from_beats(beats, energy, BEATS_PER_BAR))
        source = "derived" if energy else "first_beat"
    return {
        "downbeats": downbeats,
        "downbeats_source": source,
        "beats_per_bar": BEATS_PER_BAR,
        "bar_of_beat": [bisect.bisect_right(downbeats, t + 1e-9) for t in beats],
    }


def _beat_grid_record(fp, *, song_hash: str) -> tuple[str, dict]:
    """``(source, record)`` from the cheapest source that can answer for THIS song."""
    from muvid.footage.scoring.grid import scores_dir

    cache_path = scores_dir(fp.root) / _BEAT_GRID_CACHE_NAME
    sources = (
        (_BEAT_GRID_FROM_CACHE, lambda: _read_beat_grid_cache(cache_path, song_hash)),
        (_BEAT_GRID_FROM_SCORES, lambda: _beat_grid_from_scores(fp, song_hash)),
        (
            _BEAT_GRID_COMPUTED,
            lambda: _compute_beat_grid(fp, cache_path=cache_path, song_hash=song_hash),
        ),
    )
    for source, load in sources:
        record = load()
        if record is not None:
            return source, record
    raise AssertionError("the computed source never declines")  # pragma: no cover


def _read_beat_grid_cache(path: Path, song_hash: str) -> Optional[dict]:
    """The cached record if it was measured on THIS song, else ``None`` (a miss)."""
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("song_hash") != song_hash:
        return None
    return record


def _beat_grid_from_scores(fp, song_hash: str) -> Optional[dict]:
    """Lift the grid out of a scoring run's manifest when one exists for THIS song."""
    from muvid.footage.scoring.grid import load_manifest

    manifest = load_manifest(fp.root)
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


def _compute_beat_grid(fp, *, cache_path: Path, song_hash: str) -> dict:
    """Run ``mixing.audio.beat_grid`` on the song and cache the record.

    The ONLY exception translated is ``ImportError`` (a missing optional package);
    whatever else the estimator raises is a ``mixing`` regression and propagates.
    """
    try:
        from mixing.audio import beat_grid as estimate

        grid = estimate(str(fp.song_path()))
    except ImportError as e:
        raise FootageError(
            "the beat grid needs librosa, which the 'scoring' extra provides — "
            f"pip install 'muvid[scoring]' ({e})"
        ) from e
    tempo = float(grid.tempo_bpm)
    record = {
        "song_hash": song_hash,
        "tempo_bpm": round(tempo, _TEMPO_DECIMALS) if math.isfinite(tempo) else None,
        "beat_times": [round(float(t), _BEAT_TIME_DECIMALS) for t in grid.beat_times],
        "downbeat_times": [
            round(float(t), _BEAT_TIME_DECIMALS) for t in grid.downbeat_times
        ],
        # the onset energy AT each beat — what the downbeat vote reads (bar numbers)
        "onset_at_beats": _onset_at_beats(grid),
        "computed_at": time.time(),
    }
    _write_json_quietly(cache_path, record)
    return record


def _onset_at_beats(grid) -> list:
    """The estimator's onset envelope sampled at each beat (``[]`` when it has none)."""
    env = getattr(grid, "onset_env", None)
    hop = getattr(grid, "onset_hop_s", None)
    if env is None or not hop or not len(env):
        return []
    last = len(env) - 1
    return [
        round(float(env[min(last, max(0, round(float(t) / hop)))]), 4)
        for t in grid.beat_times
    ]


def _write_json_quietly(path: Path, record: dict) -> None:
    """tmp + ``os.replace``; a cache that cannot be written costs the NEXT call a
    recompute, nothing more — the reply is already correct."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(record, indent=2))
        os.replace(tmp, path)
    except OSError:
        pass


# =============================================================================
# scoring the footage (muvid#13)
# =============================================================================


def require_scorable(fp) -> list:
    """The alignments a scoring run would use; refuses without a song or an alignment."""
    if not fp.has_song():
        raise FootageError("no song set — call set_song first")
    aligns = fp.load_alignments()
    if not aligns:
        raise FootageError("no alignment — call align_footage first")
    return aligns


def run_scoring(
    fp,
    *,
    hop_s: float = DEFAULT_SCORE_HOP_S,
    metrics: Optional[list] = None,
    enable_lipsync: Optional[bool] = None,
    progress_cb=None,
    should_cancel=None,
) -> dict:
    """Score every aligned clip and persist the tensor (the engine behind :func:`score`,
    with the job hooks a background runner passes)."""
    from muvid.footage.scoring import score_project

    return score_project(
        fp,
        metrics=metrics,
        hop_s=hop_s,
        enable_lipsync=enable_lipsync,
        progress_cb=progress_cb,
        should_cancel=should_cancel,
    )


def score(
    fp,
    *,
    hop_s: float = DEFAULT_SCORE_HOP_S,
    metrics: Optional[list[str]] = None,
    should_cancel=None,
) -> dict:
    """Look at the footage: score every placed video, on the song's own timeline —
    picture quality and how its movement sits on the beat — and save the curves.

    Slow (it decodes every clip), so hosts run it in the background. Needs a song and an
    alignment. Every core metric is computed; weighting happens later, when cutting, so
    re-weighting never re-scores. The lip-sync tier is off unless the operator enabled
    it. Returns what was scored and what was skipped (and why).
    """
    require_scorable(fp)
    out = run_scoring(fp, hop_s=hop_s, metrics=metrics, should_cancel=should_cancel)
    if out.get("status") == "cancelled":  # score_project polls between clips/stages
        raise FootageCancelled(f"scoring stopped at {out.get('stage')}")
    return out


def scores(
    fp,
    *,
    clip_id: str = "",
    metrics: Optional[list[str]] = None,
    max_points: int = MAX_WIRE_POINTS,
) -> dict:
    """The saved footage curves — for the lanes under each video, and for inspection.

    - no ``clip_id`` → a SUMMARY (metrics, per-clip coverage, beats, tempo, the decimated
      ``selection_margin``, grid geometry) — bounded, safe as the default;
    - ``clip_id`` → that clip's curves (values as ``null``-masked arrays, decimated to
      ``max_points`` per metric).
    """
    from muvid.footage.scoring.grid import (
        align_fingerprint,
        load_manifest,
        load_tensor,
        manifest_is_current,
    )

    manifest = load_manifest(fp.root)
    if manifest is None:
        raise FootageError("no scores yet — call score_footage first")
    if not manifest_is_current(
        manifest,
        song_hash=fp.song_hash(),
        align_fingerprint=align_fingerprint(fp.load_alignments()),
    ):
        raise FootageError(
            "scores are stale (song or alignment changed) — re-run score_footage"
        )
    tensor = load_tensor(fp.root)
    if tensor is None:
        raise FootageError("scores are present but unreadable — re-run score_footage")
    grid = {
        "t0": manifest["t0"],
        "hop_s": manifest["hop_s"],
        "n": manifest["n"],
        "song_duration": round(manifest["n"] * manifest["hop_s"], 3),
    }
    if not clip_id:
        return _scores_summary(fp, tensor, manifest, grid, max_points)
    return _clip_scores(tensor, manifest, grid, clip_id, metrics, max_points)


def _scores_summary(fp, tensor, manifest, grid, max_points) -> dict:
    from muvid.footage.select_score import selection_margin

    margin = selection_margin(fp.load_alignments(), tensor)
    beats = manifest.get("beats", {}).get("beat_times", [])
    return {
        "project_id": fp.project_id,
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
        "metric_coverage": manifest.get("coverage", {}),
        "beats": {"count": len(beats), "times": _decimate_list(beats, max_points)},
        "tempo_bpm": manifest.get("tempo_bpm"),
        "selection_margin": _decimate_values(margin, max_points, pool="min"),
        "grid": grid,
        "lipsync_enabled": manifest.get("lipsync_enabled", False),
    }


def _clip_scores(tensor, manifest, grid, clip_id, metrics, max_points) -> dict:
    if clip_id not in tensor.clip_ids:
        raise FootageError(f"unknown clip {clip_id!r}; scored: {tensor.clip_ids}")
    ci = tensor.clip_index(clip_id)
    want = [m for m in tensor.metrics if not metrics or m in set(metrics)]
    out_metrics = {}
    for m in want:
        mi = tensor.metric_index(m)
        out_metrics[m] = {
            "values": _decimate_values(tensor.S[ci, :, mi], max_points),
            "mask": _decimate_bool(tensor.M[ci, :, mi], max_points),
            "direction": manifest.get("directions", {}).get(m, "higher_better"),
            "norm": manifest.get("norms", {}).get(m),
        }
    return {"clip_id": clip_id, "metrics": out_metrics, "grid": grid}


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
    return [bool(x) for x in a[:: _stride(len(a), max_points)]]


def _decimate_list(xs, max_points: int) -> list:
    return [round(float(x), 4) for x in xs[:: _stride(len(xs), max_points)]]


# =============================================================================
# making an edit
# =============================================================================


def strategies(fp=None) -> dict:
    """The ways to cut on offer — the selection strategies ``propose_edit`` accepts
    (``weighted`` reads the footage scores; the rest use only the alignment)."""
    from muvid.footage.strategy import DEFAULT_STRATEGY, list_strategies

    return {"strategies": list_strategies(), "default": DEFAULT_STRATEGY}


#: Every optional :class:`~muvid.footage.edl.EdlEntry` field :func:`edl_json` carries,
#: and how to render it. **The list is the round trip.** ``_as_entry`` reads all of
#: these back by name, so a field missing from here is a direction the caller gave, the
#: renderer honoured, and the returned/persisted edit does not contain. Each row is
#: ``(field, render, absent)``, where ``absent`` is the value that means "omit this
#: key" — a column rather than a hardcoded ``None`` because ``look_time_varying``
#: (muvid#73) is a boolean whose absent value is ``False``.
EDL_OPTIONAL_FIELDS = (
    ("transition", lambda v: v.to_dict(), None),
    ("crop", lambda v: v.to_dict(), None),
    ("crop_end", lambda v: v.to_dict(), None),
    ("look", str, None),
    ("look_time_varying", bool, False),
    ("look_spec", dict, None),
    ("slip_s", float, 0.0),
    ("rate", float, 1.0),
    ("source_in", float, None),
)


def edl_json(e) -> dict:
    """One EDL entry as JSON — full precision (it must feed back verbatim), gaps as null.

    Optional fields are emitted ONLY when set (:data:`EDL_OPTIONAL_FIELDS`), which keeps
    every existing ``renders/*/meta.json`` byte-identical and the render -> edit ->
    re-render round trip (muvid#21 item 3) exact.
    """
    out = {
        "song_start": e.song_start,
        "song_end": e.song_end,
        "clip_id": e.clip_id or None,
    }
    for field_name, render_value, absent in EDL_OPTIONAL_FIELDS:
        v = getattr(e, field_name, absent)
        if v != absent:
            out[field_name] = render_value(v)
    return out


def _round_opt(x: Optional[float]) -> Optional[float]:
    """``None`` stays ``None`` over the wire — "not measured" is not "measured zero"."""
    return None if x is None else round(float(x), 3)


def coverage_report(
    entries, aligns, song_dur: float, *, excluded=(), span=None
) -> dict:
    """What the song's timeline looks like under ``entries`` — covered, weak, MISSING.

    Pass only FOOTAGE entries: a gap renders fill, and filled is not covered. Uncovered
    audio is named with explicit start/end times; a span whose only footage is weakly
    aligned is listed with the numbers that make it weak; ``excluded`` (muvid#88) names
    spans the auto path gave up because only an unvouched clip covered them.
    ``span`` (a trimmed edit's ``(start, end)``) bounds what counts as uncovered.
    """
    by_id = {a.clip_id: a for a in aligns}
    lo_bound, hi_bound = span if span is not None else (0.0, song_dur)
    covered = sorted((e.song_start, e.song_end) for e in entries)
    gaps, cursor = [], float(lo_bound)
    for lo, hi in covered:
        if lo - cursor > _EPS:
            gaps.append({"song_start": round(cursor, 2), "song_end": round(lo, 2)})
        cursor = max(cursor, hi)
    if hi_bound - cursor > _EPS:
        gaps.append({"song_start": round(cursor, 2), "song_end": round(hi_bound, 2)})
    # `reliable`, not a bare confidence comparison: the verdict is the aligner's, so this
    # report says exactly what validate_edl will refuse.
    weak = [
        {
            "song_start": round(e.song_start, 2),
            "song_end": round(e.song_end, 2),
            "clip_id": e.clip_id,
            "confidence": round(by_id[e.clip_id].confidence, 3),
            "support": _round_opt(by_id[e.clip_id].support),
            "margin": _round_opt(by_id[e.clip_id].margin),
        }
        for e in entries
        # a free cut claims no sync, so its clip's placement cannot make it weak
        if e.clip_id in by_id and not by_id[e.clip_id].reliable and not e.is_free
    ]
    covered_s = sum(hi - lo for lo, hi in covered)
    length = hi_bound - lo_bound
    report = {
        "song_duration": round(song_dur, 2),
        "covered_seconds": round(covered_s, 2),
        "coverage_fraction": round(covered_s / length, 4) if length else 0.0,
        "uncovered": gaps,
        "weak_segments": weak,
        "excluded": [x.to_dict() for x in excluded],
        "confidence_threshold": _MIN_CONFIDENCE,
    }
    if span is not None:
        report["span"] = [lo_bound, hi_bound]
    return report


def exclusion_note(x) -> str:
    """One ``warnings`` line per span the auto path set aside (muvid#88)."""
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

    Both paths that build an edit from a strategy go through this, so ``propose_edit``
    cannot propose an edit :func:`assemble` would not produce. ``recover=False`` (the
    caller passed ``allow_unreliable``) skips the exclusion entirely. Returns
    ``(entries, excluded)``; ``validate_edl`` stays the ONE gate.
    """
    from muvid.footage.edl import exclude_unvouched, fill_gaps
    from muvid.footage.strategy import select_edl

    selected = select_edl(strategy, aligns, song_dur, context=context)
    excluded = []
    if recover:
        selected, excluded = exclude_unvouched(selected, aligns)
    return fill_gaps(selected, song_dur), excluded


def _require_song_and_alignment(fp) -> list:
    """The song is set and there is footage; returns a placement for EVERY clip
    (:func:`_placements`) — an edit may cut a clip to the music without its ever having
    been placed on the song."""
    if not fp.has_song():
        raise FootageError("no song set — call set_song first")
    if not fp.list_clips():
        raise FootageError("no footage added — call add_clip first")
    return _placements(fp)


def _auto_edit(fp, placements, song_dur, *, strategy, context, recover, pace="",
               canvas=None):
    """The auto path, synced half then music half: the strategy cuts the clips that
    carry the song (:func:`_role_of`); every span it leaves empty is cut to the music
    from the rest and the photos (:func:`_music_fill`). ``(entries, excluded, music)``
    where ``music`` is the music-cut report (``None`` when nothing was cut to it)."""
    roles = footage_roles(fp)
    unheard = [cid for cid, r in roles.items() if r == UNHEARD]
    if unheard:
        raise FootageError(
            f"{len(unheard)} video(s) have not been listened to yet ({', '.join(unheard)}) — "
            "run align first: listening is how a video with the song in it gets synced "
            "and one without it gets cut to the music. Or say which it is with "
            "set_has_song."
        )
    synced = [a for a in placements if roles.get(a.clip_id) == SYNCED]
    # A score-driven strategy is asked even with nothing to sync, so a missing scoring
    # run is still reported as one rather than quietly bypassed.
    if synced or context is not None:
        entries, excluded = _auto_edl(
            synced, song_dur, strategy=strategy, context=context, recover=recover
        )
    else:
        entries, excluded = [], []
    free = _free_sources(fp, placements)
    if free and recover:
        # `exclude_unvouched` keeps an unvouched cut rather than empty the edit, so the
        # refusal stays alive when nothing else could fill it. Here something can: set
        # those spans aside too, and let the music fill them.
        entries, more = _set_aside_unvouched(entries, synced)
        excluded = list(excluded) + more
    try:
        entries, music = _music_fill(fp, entries, free, song_dur, pace=pace, canvas=canvas)
    except (ImportError, OSError) as e:
        # The music half needs the montage analysis (librosa, ffmpeg). Without it the
        # synced half still stands; the spans it leaves stay gaps, and the reply says why.
        from muvid.footage.edl import fill_gaps

        entries = fill_gaps(entries, song_dur) if entries else []
        music = {"error": f"could not cut to the music: {e}", "n_cuts": 0}
    if not entries:
        raise FootageError(
            "nothing to cut: no video has a confident place on the song, and there are "
            "no other videos or photos to cut to the music"
        )
    return entries, excluded, music


def _check_span(fp, span) -> Optional[tuple[float, float]]:
    """``span`` as ``(start, end)`` inside the song, or ``None`` for the whole song.

    A span equal to the whole song is normalised to ``None``, so "the whole song" has
    one spelling on disk and follows the song if its length is ever re-probed.
    """
    if span is None:
        return None
    try:
        start, end = (float(x) for x in span)
    except (TypeError, ValueError) as e:
        raise FootageError(f"span must be [start_s, end_s], got {span!r}") from e
    song_dur = fp.song_duration()
    if not (0.0 - _EPS <= start < end <= song_dur + _EPS) or end - start <= _EPS:
        raise FootageError(
            f"span [{start:g}, {end:g}] is not a stretch of the song [0, {song_dur:.3f}]"
        )
    start, end = max(0.0, start), min(song_dur, end)
    if start <= _EPS and song_dur - end <= _EPS:
        return None
    return start, end


def _span_of(record: dict) -> Optional[tuple[float, float]]:
    span = record.get("span")
    return (float(span[0]), float(span[1])) if span else None


def _windowed(entries, span) -> list:
    """What of a whole-song edit renders inside ``span``: its cuts clipped to the window
    and gap-filled across it. The edit itself is never changed by this — a span is a
    render-time WINDOW, so trimming to it loses nothing and widening it restores
    exactly what was there."""
    from muvid.footage.edl import fill_gaps

    if span is None:
        return list(entries)
    kept = _trim_to_span([e for e in entries if not e.is_gap], span)
    return fill_gaps(kept, span[1], start=span[0])


def _trim_to_span(entries, span) -> list:
    """Entries clipped to ``span``: whole entries outside it dropped, straddling ones
    cut at its boundaries (a pan re-derived where it was at the cut), and the blend of an
    entry that now opens the span dropped — nothing precedes it to blend from."""
    from muvid.footage.edl import with_start

    if span is None:
        return list(entries)
    start, end = span
    out = []
    for e in entries:
        if e.song_end <= start + _EPS or e.song_start >= end - _EPS:
            continue
        lo, hi = max(e.song_start, start), min(e.song_end, end)
        changes = {"song_end": hi}
        if e.crop_end is not None:
            changes.update(crop=_crop_at(e, lo), crop_end=_crop_at(e, hi))
        if lo <= start + _EPS:
            changes["transition"] = None
        # A window, never stored: the trimmed head keeps the cut's footage map even
        # where that needs a slip no stored cut may carry.
        out.append(replace(with_start(e, lo, bounded=False), **changes))
    return out


def _selection_context(fp, strat, preset, weights, config):
    """A ``SelectionContext`` from persisted scores, for the ``weighted`` strategy only.

    When scores are absent or stale the tensor is ``None`` and ``weighted_selection``
    raises a clear "run scoring first" — no scores gives a helpful error, not a silent
    bad edit.
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

    manifest = load_manifest(fp.root) or {}
    beats = manifest.get("beats", {})
    fresh = manifest_is_current(
        manifest,
        song_hash=fp.song_hash() if fp.has_song() else "",
        align_fingerprint=align_fingerprint(fp.load_alignments()),
    )
    return SelectionContext(
        tensor=load_tensor(fp.root) if fresh else None,
        beat_times=beats.get("beat_times", []),
        downbeat_times=beats.get("downbeat_times", []),
        shot_boundaries=manifest.get("shot_boundaries"),
        config=resolve_config(preset=preset or None, weights=weights, config=config),
    )


def assemble_refusal(entries, aligns, song_dur: float, canvas) -> Optional[dict]:
    """``None`` if rendering this edit would go ahead; the refusal if it would not —
    put to the GATE rather than re-implemented here."""
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


def propose_edit(
    fp,
    *,
    strategy: str = "",
    preset: str = "",
    weights: Optional[dict[str, float]] = None,
    config: Optional[dict] = None,
    save: bool = True,
    name: str = "",
    span: Optional[tuple[float, float]] = None,
    pace: Literal["", "slow", "steady", "driving", "frantic"] = "",
) -> dict:
    """Cut it for me: build an edit of the whole song — or of ``span`` (``[start_s,
    end_s]``, the part of the song the video covers) — from the placed videos, and (by
    default) save it as a named edit, without rendering anything.

    ``strategy`` picks how (see ``strategies``; default ``best_confidence``). Giving a
    ``preset`` ("energetic"/"contemplative"), per-metric ``weights`` or a ``config``
    (``lambda_switch``/``l_min_s``/``l_max_s``/``boundary_mode``) selects the
    score-driven ``weighted`` strategy, which needs ``score`` first.

    Returns the ``edl`` (spans the WHOLE song; spans no footage covers are explicit gap
    entries, ``clip_id: null``, rendered as black), the ``strategy`` used, a
    ``coverage`` report naming every uncovered span and every weakly-aligned segment,
    ``warnings``, and ``assemble_refusal`` (non-null when rendering it would be refused
    because no clip is trustworthy). With ``save`` it also returns the ``edit_id`` to
    change it (``set_cut`` …) and render it (``render``).

    **Footage that does not contain the song is cut to the music.** The strategy cuts
    only the videos that are recordings of the song (placed confidently by ``align``,
    or marked ``set_has_song yes``); every span they leave is filled from the other
    videos and the photos, cut on the song's beats and bars, each video shown at the
    stretch whose picture changes land on the beat (``music`` in the reply says how
    that went). With no recording of the song at all, the whole song is cut to the
    music — no ``align`` needed. ``pace`` (``slow``/``steady``/``driving``/``frantic``)
    sets how often those cuts come.
    """
    from muvid.footage.edl import validate_edl
    from muvid.footage.strategy import DEFAULT_STRATEGY

    aligns = _require_song_and_alignment(fp)
    song_dur = fp.song_duration()
    span = _check_span(fp, span)
    has_selection_config = bool(preset or weights or config)
    strat = strategy or ("weighted" if has_selection_config else DEFAULT_STRATEGY)
    try:
        context = _selection_context(fp, strat, preset, weights, config)
        proposal, excluded, music = _auto_edit(
            fp, aligns, song_dur, strategy=strat, context=context, recover=True,
            pace=pace,
        )
        if span is not None:
            excluded = [
                x for x in excluded if x.song_end > span[0] and x.song_start < span[1]
            ]
        # This renders nothing; refusing here would deny the diagnosis the caller came
        # for — the refusal belongs where the encode does (muvid#59).
        entries = validate_edl(
            proposal, aligns, song_dur, canvas=fp.canvas(), allow_unreliable=True
        )
        # The edit is the whole song; the span is the window that renders.
        window = validate_edl(
            _windowed(entries, span),
            aligns,
            song_dur,
            canvas=fp.canvas(),
            allow_unreliable=True,
        )
    except (ValueError, KeyError) as e:
        raise FootageError(f"could not build a valid edit: {e}") from e
    out = {
        "strategy": strat,
        "music": music,
        "roles": footage_roles(fp),
        "edl": [edl_json(e) for e in entries],
        "assemble_refusal": assemble_refusal(window, aligns, song_dur, fp.canvas()),
        "coverage": coverage_report(
            [e for e in window if not e.is_gap],
            aligns,
            song_dur,
            excluded=excluded,
            span=span,
        ),
        "warnings": [exclusion_note(x) for x in excluded],
    }
    if span is not None:
        out["span"] = list(span)
    if save:
        selection = {"strategy": strat}
        for key, value in (
            ("preset", preset),
            ("weights", weights),
            ("config", config),
            ("pace", pace),
        ):
            if value:
                selection[key] = value
        how = f"cut automatically ({strat}{', ' + preset if preset else ''}"
        how += (", the rest cut to the music)" if music else ")")
        record = _new_edit_record(
            fp, out["edl"], name=name or _default_edit_name(fp), how_made=how, span=span
        )
        record["selection"] = selection
        lock = getattr(fp, "edits_lock", None)
        if lock is None:
            fp.write_edit(record["edit_id"], record)
        else:
            with lock():
                fp.write_edit(record["edit_id"], record)
        out["edit_id"] = record["edit_id"]
        out["name"] = record["name"]
    return out


# -- named edits -------------------------------------------------------------


def _edits_locked(fn):
    """Run an edit mutation under the project's edits lock (read-modify-write of
    ``edits/<id>.json`` is serialised; each write is atomic besides)."""
    import functools

    @functools.wraps(fn)
    def locked(fp, **params):
        lock = getattr(fp, "edits_lock", None)
        if lock is None:
            return fn(fp, **params)
        with lock():
            return fn(fp, **params)

    return locked


@_edits_locked
def save_edit(
    fp,
    *,
    edl: list[dict],
    name: str = "",
    how_made: str = "by hand",
    edit_id: str = "",
    span: Optional[tuple[float, float]] = None,
) -> dict:
    """Save a cut list as a new named edit.

    ``edl`` is a list of ``{song_start, song_end, clip_id}`` spans (plus optional
    ``transition``/``crop``/``crop_end``/``look``/``look_time_varying``), in the same
    form ``get_edit`` returns and ``propose_edit`` produces. Holes are filled with gap
    entries; the list is checked (order, overlap, every span inside its clip's coverage)
    and refused with the reason if it does not hold. ``edit_id`` fixes the id (an
    existing one is refused — use ``replace_edit``). ``span`` (``[start_s, end_s]``)
    makes the edit cover only that part of the song — its render is that long, the song
    cut to it; default the whole song. Returns the saved edit.
    """
    span = _check_span(fp, span)
    entries = _validated_entries(fp, edl)
    edit_id = _normalised_id(edit_id, label="edit_id") if edit_id else ""
    if edit_id and fp.has_edit(edit_id):
        raise FootageError(
            f"edit {edit_id!r} already exists — use replace_edit to change it"
        )
    record = _new_edit_record(
        fp,
        [edl_json(e) for e in entries],
        name=name or _default_edit_name(fp),
        how_made=how_made,
        edit_id=edit_id,
        span=span,
    )
    fp.write_edit(record["edit_id"], record)
    return _edit_reply(fp, record)


def edits(fp) -> dict:
    """The saved edits, oldest first: each one's ``edit_id``, ``name``, how it was made,
    how many cuts it has, and ``problem`` — why it would not validate against the
    current alignment (``null`` when it does). ``unreliable`` names clips it cuts to
    whose offsets rendering would refuse."""
    return {"edits": [_edit_summary(fp, rec) for rec in fp.list_edit_records()]}


def get_edit(fp, *, edit_id: str) -> dict:
    """One saved edit: its cut list (``edl``, every span of the song, gaps as
    ``clip_id: null``), its name and history, and a ``coverage`` report. Cut indexes in
    ``set_cut``/``merge_cut`` refer to positions in this ``edl``."""
    return _edit_reply(fp, _read_edit(fp, edit_id))


@_edits_locked
def replace_edit(fp, *, edit_id: str, edl: list[dict]) -> dict:
    """Replace a saved edit's whole cut list — the power tool for rewriting an edit at
    once. The new list is checked exactly as ``save_edit`` checks one; on refusal the
    edit is left as it was. The previous list is not kept."""
    record = _read_edit(fp, edit_id)
    entries = _validated_entries(fp, edl)
    return _store_changed(fp, record, entries)


@_edits_locked
def set_cut(
    fp,
    *,
    edit_id: str,
    index: int,
    clip_id: Optional[str] = None,
    song_start: Optional[float] = None,
    song_end: Optional[float] = None,
    look: Optional[Union[str, dict]] = None,
    look_time_varying: Optional[bool] = None,
    slip_s: Optional[float] = None,
    rate: Optional[float] = None,
) -> dict:
    """Change one cut of a saved edit (``index`` is its position in ``get_edit``'s edl).

    - ``clip_id``: show another video over this span (``""`` makes it a gap). The new
      video must cover the span. Its framing (``crop``) is dropped, since it was chosen
      for the old video's frame; its ``look`` is kept. A video cut to the music (or a
      photo) is shown from its start; a video with the song in it, where it was filmed.
    - ``song_start`` / ``song_end``: move the cut's boundaries. The neighbouring cut's
      boundary moves with it, so the edit stays one continuous timeline; a move that
      would swallow a neighbour whole is refused (join them with ``merge_cut``).
    - ``look``: a NAMED look from ``looks`` — ``{"name": "slow_push", "zoom": 1.08}``,
      compiled for this cut's length and the project's canvas and kept on the cut as
      ``look_spec`` (with every parameter's value) so it can be shown and changed —
      or, for power users,
      one raw ffmpeg filter chain (allowlisted; set ``look_time_varying`` for one that
      moves). ``""`` removes it.
    - ``slip_s``: show a slightly different moment of the same video over the same
      span — ``0.1`` reads the footage 0.1 s later — to put a dancer's moves on the
      beat where the clip's alignment is right overall but a little off here. At
      most one beat either way (``SLIP_MAX_S``); ``0`` removes it. A new video
      (``clip_id``) starts unslipped.
    - ``rate``: play this cut's video a little faster or slower — ``1.03`` shows 3 %
      more footage over the same span, so the moves run 3 % faster (the song is never
      touched). Within ``1 +- RATE_MAX_DEV``; ``1`` removes it. The video must hold the
      footage the cut then reads. A new video starts at speed 1.

    Parameters left out are unchanged. The changed edit is checked and saved; returns it.
    """
    from muvid.footage.edl import EdlEntry

    record, entries = _edit_entries(fp, edit_id)
    i = _check_index(entries, index)
    e: EdlEntry = entries[i]
    changes: dict = {}
    if clip_id is not None and (clip_id or "") != e.clip_id:
        changes.update(
            clip_id=clip_id or "",
            crop=None,
            crop_end=None,
            slip_s=0.0,
            rate=1.0,
            source_in=_swap_in_point(fp, clip_id or "", e),
        )
        if not clip_id:  # a gap carries no picture, so no look either
            changes.update(
                look=None, look_time_varying=False, look_spec=None, transition=None
            )
    if isinstance(look, dict):
        spec, fragment = _named_look(fp, look, duration_s=e.song_end - e.song_start)
        changes["look"] = str(fragment)
        changes["look_time_varying"] = bool(fragment.time_varying)
        changes["look_spec"] = spec  # so a screen can show and re-edit the choice
    elif look is not None:
        changes["look"] = look or None
        changes["look_spec"] = None  # a hand-written filter names no look
        if not look:
            changes["look_time_varying"] = False
    if look_time_varying is not None:
        changes["look_time_varying"] = bool(look_time_varying)
    if slip_s is not None:
        from muvid.footage.edl import _as_slip

        try:
            changes["slip_s"] = _as_slip(slip_s)
        except ValueError as err:
            raise FootageError(str(err)) from err
    if rate is not None:
        from muvid.footage.edl import _as_rate

        try:
            changes["rate"] = _as_rate(rate)
        except ValueError as err:
            raise FootageError(str(err)) from err
    if song_start is not None:
        if i > 0:
            prev = entries[i - 1]
            if float(song_start) <= prev.song_start + _EPS:
                raise FootageError(
                    f"moving cut {i}'s start to {song_start:.3f}s would swallow cut "
                    f"{i - 1} entirely — join them with merge_cut instead"
                )
            entries[i - 1] = replace(prev, song_end=float(song_start))
    if song_end is not None:
        changes["song_end"] = float(song_end)
        if i + 1 < len(entries):
            nxt = entries[i + 1]
            if float(song_end) >= nxt.song_end - _EPS:
                raise FootageError(
                    f"moving cut {i}'s end to {song_end:.3f}s would swallow cut "
                    f"{i + 1} entirely — join them with merge_cut instead"
                )
            entries[i + 1] = _moved_start(nxt, float(song_end))
    e = replace(e, **changes)
    if song_start is not None:
        # The cut keeps showing the footage it showed where it still plays: at a speed
        # other than 1, moving its start moves its slip too.
        e = _moved_start(e, float(song_start))
    entries[i] = e
    return _store_changed(fp, record, entries, changed=i)


def _swap_in_point(fp, clip_id: str, e) -> Optional[float]:
    """Where a cut swapped onto ``clip_id`` starts in it: ``None`` (synced, follows the
    clip's place) for a clip with the song in it; for a clip cut to the music, the start
    of the clip — just past a thumb on the button, and far enough in for the cut's blend
    — so a swap never inherits another clip's in-point (47 s into a 10 s clip)."""
    from muvid.footage.music_cut import EDGE_S

    if not clip_id:
        return None
    row = next((c for c in fp.manifest().get("clips", []) if c.get("clip_id") == clip_id), None)
    if row is None:
        return None  # unknown: the edit gate names it
    aligns = {a.clip_id: a for a in fp.load_alignments()}
    if _role_of(row, aligns.get(clip_id)) == SYNCED:
        return None
    lead = e.transition.duration_s if e.transition is not None else 0.0
    if row.get("kind") == STILL_KIND:
        return round(lead, 4)
    return round(min(EDGE_S + lead, max(0.0, _clip_duration(fp, clip_id) - (e.song_end - e.song_start))), 4)


def _moved_start(e, start: float):
    """``e`` starting at ``start``, its footage map kept (``edl.with_start``) — the
    one way this module moves a cut's start."""
    from muvid.footage.edl import with_start

    try:
        return with_start(e, start)
    except ValueError as err:
        raise FootageError(str(err)) from err


def _named_look(fp, spec: dict, *, duration_s: float):
    from muvid.footage.assemble import DEFAULT_FPS
    from muvid.footage.look import LookError
    from muvid.footage.named_looks import (
        NamedLookError,
        compile_named_look,
        resolve_named_look,
    )

    try:
        resolved = resolve_named_look(spec)
        fragment = compile_named_look(
            resolved, canvas=fp.canvas(), fps=DEFAULT_FPS, duration_s=duration_s
        )
    except (NamedLookError, LookError) as e:
        raise FootageError(str(e)) from e
    return resolved, fragment


def filmstrips(fp) -> dict:
    """Every video's filmstrip — thumbnails to draw each camera's lane.

    Per clip: sprite sheets of ``frame_w`` x ``frame_h`` frames (``cols`` x ``rows`` to
    a sheet, left to right then down), sampled at ``fps`` frames per second of the
    CLIP's own time — frame ``i`` is the clip at ``i / fps`` s, which sits at song time
    ``offset + i / fps``. Each sheet is an ``artifact_id`` (when the project is hosted),
    with its ``first_frame`` and ``n_frames``. Made once per clip and kept; a clip that
    has none yet takes a few seconds the first time.

    Returns ``{fps, clips: {clip_id: {duration_s, n_frames, frame_w, frame_h, sheets:
    [{artifact_id, cols, rows, first_frame, n_frames}]}}}``.
    """
    from muvid.footage.media_views import FILMSTRIP_FPS

    return {
        "fps": FILMSTRIP_FPS,
        "clips": {c["clip_id"]: _filmstrip(fp, c["clip_id"]) for c in fp.list_clips()},
    }


def filmstrip(fp, *, clip_id: str) -> dict:
    """One video's filmstrip (the same record ``filmstrips`` gives per clip, with its
    ``clip_id`` and ``fps``)."""
    from muvid.footage.media_views import FILMSTRIP_FPS

    known = fp.list_clips()
    if clip_id not in {c["clip_id"] for c in known}:
        raise FootageError(_unknown_clip_message(clip_id, known))
    return {"clip_id": clip_id, "fps": FILMSTRIP_FPS, **_filmstrip(fp, clip_id)}


def _filmstrip(fp, clip_id: str) -> dict:
    from muvid.footage.media_views import clip_filmstrip
    from muvid.visualize.ffmpeg import FfmpegError

    try:
        return clip_filmstrip(fp, clip_id)
    except (FfmpegError, ValueError) as e:
        raise FootageError(
            f"could not read video {clip_id!r} for its filmstrip: {e}"
        ) from e


def peaks(fp, *, n: int = 2000) -> dict:
    """The song's waveform, to draw under the timeline: ``n`` equal slices of the song,
    each the loudest moment in it (mono), scaled so the loudest slice is 1.0.

    Returns ``{duration_s, n, peaks: [0..1, ...]}``; slice ``i`` covers song time
    ``i * duration_s / n`` to ``(i + 1) * duration_s / n``. Kept per song and ``n``.
    """
    from muvid.footage.media_views import PEAKS_MAX_N, PEAKS_MIN_N, song_peaks
    from muvid.visualize.ffmpeg import FfmpegError

    if not fp.has_song():
        raise FootageError("no song set — call set_song first")
    if not PEAKS_MIN_N <= int(n) <= PEAKS_MAX_N:
        raise FootageError(
            f"n must be between {PEAKS_MIN_N} and {PEAKS_MAX_N}, got {n}"
        )
    try:
        return song_peaks(fp, n=int(n))
    except FfmpegError as e:
        raise FootageError(f"could not read the song for its waveform: {e}") from e


#: The ``source`` that names the song in :func:`beat_signals` (anything else is a clip id).
SONG_SOURCE = "song"


#: ``beat_signals``' default pooling: enough to see a beat across a whole song, small
#: enough for a caller that reads the numbers (the co-director). The editor asks for 0.
BEAT_SIGNALS_DEFAULT_POINTS = 1000


def beat_signals(
    fp, *, source: str = SONG_SOURCE, max_points: int = BEAT_SIGNALS_DEFAULT_POINTS
) -> dict:
    """Where the beat is in the song or in one video — CONTINUOUS signals, to look at,
    threshold and bend, not only beat instants.

    ``source`` is ``"song"`` or a clip id. The song gets its sound (``audio_onset``: the
    onset envelope the beat grid is estimated from). A video gets its own soundtrack's
    ``audio_onset`` when it has one, and three visual signals: ``motion`` (how much the
    people in the picture move, the camera's own move taken out), ``motion_stops`` (moves
    stopping dead and turning — the movement accents) and ``motion_stops_local`` (the same,
    place by place).

    Each signal is in the media's OWN time: sample ``i`` is at ``t0 + i * hop_s`` s of the
    song, or of the clip (song time ``offset + t``). Values are unnormalised, with
    ``min``, ``max`` and ``p99`` beside them; ``None`` is a sample that was not measured.
    ``max_points`` pools each signal to at most that many samples by their maximum, so
    a peak survives (0 = every sample; an editor drawing it wants that).

    Measured once per media and kept (a video's first call reads every frame and takes
    tens of seconds; a second call for the same video waits for the first rather than
    measuring again). Needs the ``scoring`` extra.

    Returns ``{source, kind: audio|video, duration_s, tempo_bpm, beats, signals:
    {name: {name, label, domain, t0, hop_s, n, min, max, p99, values}}}`` — ``beats``
    and ``tempo_bpm`` are the soundtrack's (``[]`` / ``None`` without one).
    """
    from muvid.footage import beats as bs

    if max_points and int(max_points) < 2:
        raise FootageError(
            f"max_points must be 0 (all) or at least 2, got {max_points}"
        )
    if source == SONG_SOURCE:
        if not fp.has_song():
            raise FootageError("no song set — call set_song first")
        path, media_hash, kind = fp.song_path(), fp.song_hash(), "audio"
        duration, what = fp.song_duration(), "the song"
    else:
        known = fp.list_clips()
        if source not in {c["clip_id"] for c in known}:
            raise FootageError(_unknown_clip_message(source, known))
        path = Path(fp.clip_paths()[source])
        media_hash, kind = _recorded_clip_hash(fp, source, path), "video"
        duration, what = _clip_duration(fp, source), f"video {source!r}"

    def measured(which: str, compute) -> dict:
        def checked() -> dict:
            record = compute()
            if not bs.has_signal(record):
                raise FootageError(
                    f"could not measure the beat of {what}: nothing in it could be read"
                )
            return record

        try:
            return bs.cached_signals(fp.root, media_hash, which, checked)
        except FootageError:
            raise  # already a refusal (and a ValueError — keep it off the clause below)
        except ImportError as e:
            raise FootageError(
                "measuring the beat needs librosa and opencv, which the 'scoring' extra "
                f"provides — pip install 'muvid[scoring]' ({e})"
            ) from e
        except (ValueError, OSError, EOFError) as e:
            # An unreadable or truncated file (a decoder's ValueError / EOFError, an
            # OSError from the file itself) is the media's fault, said as a refusal.
            raise FootageError(f"could not read {what} to measure its beat: {e}") from e

    sound = (
        measured("audio", lambda: _audio_signals_of(bs, path))
        if kind == "audio" or bs.has_audio(path)
        else {"signals": {}, "beats": [], "tempo_bpm": None}
    )
    signals = dict(sound["signals"])
    if kind == "audio":
        # Section changes are a second opinion on the song, not the answer to this
        # request: if they cannot be measured, the beat still is — the signal is
        # simply absent (a screen says "not measured"), never a refusal of the song.
        try:
            signals.update(
                measured("structure", lambda: bs.novelty_signal(path))["signals"]
            )
        except FootageError:
            pass
    if kind == "video":
        signals.update(measured("video", lambda: bs.visual_signals(path))["signals"])
    return {
        "source": source,
        "kind": kind,
        "duration_s": round(float(duration), 3),
        "tempo_bpm": sound.get("tempo_bpm"),
        "beats": sound.get("beats") or [],
        "signals": {
            name: bs.decimated(rec, int(max_points or 0))
            for name, rec in signals.items()
        },
    }


def _audio_signals_of(bs, path) -> dict:
    """``beats.audio_signals``, with a decoder's own "cannot decode" said as a
    ``ValueError`` (pydub's ``CouldntDecodeError`` is not one)."""
    try:
        from pydub.exceptions import CouldntDecodeError
    except ImportError:  # pragma: no cover — pydub comes with mixing
        CouldntDecodeError = ()  # noqa: N806
    try:
        return bs.audio_signals(path)
    except CouldntDecodeError as e:  # type: ignore[misc]
        raise ValueError(str(e)) from e


def _recorded_clip_hash(fp, clip_id: str, path: Path) -> str:
    """A clip's content hash WITHOUT writing the manifest: the recorded one when it is
    for this file, else hashed now. A read op must not read-modify-write the manifest —
    several of these run at once (one per video) beside ``add_clip`` / ``remove_clip``,
    and an unlocked write-back of a stale copy would drop a clip or revive a removed
    one."""
    from muvid.catalog import hash_file

    for c in fp.manifest().get("clips", []):
        if c.get("clip_id") == clip_id and c.get("hash") and c.get("file") == path.name:
            return c["hash"]
    return hash_file(path)


def looks(fp=None) -> dict:
    """The looks a cut can take — camera moves (punch in, slow push, slow pull, pans)
    and grades (vivid, black and white, posterize, cartoon) — each with its
    ``params_schema``. Give one to ``set_cut`` as ``look={"name": ..., **params}``."""
    from muvid.footage.named_looks import named_look_catalogue

    return {"looks": named_look_catalogue()}


#: The confidence a fitted cut must reach to be applied (a z against the null of
#: ``beat_fit``): 2.5 is about 1 in 160 by chance, per cut.
FIT_MIN_Z = 2.5
#: How much better than the cut's current timing a fit must score to replace it.
FIT_MIN_GAIN = 0.02


@_edits_locked
def fit_to_beat(
    fp,
    *,
    edit_id: str,
    indices: Optional[list[int]] = None,
    min_z: float = FIT_MIN_Z,
    apply: bool = True,
) -> dict:
    """Fit the moves to the beat: for each cut that shows a video in the part of the song
    the edit covers (its span) — or the cuts at ``indices`` — find the slip and speed
    that put its video's movement accents on the song's beat, and apply it where the
    evidence is strong.

    Per cut, every slip within +-0.25 s (a frame at 60 fps apart) and every speed from
    x0.92 to x1.08 (1 % apart) is tried; the best is scored against a null of the same
    search on the video's accents with their relation to the beat destroyed
    (``muvid.footage.beat_fit``), giving a ``z``. A cut changes only when ``z >= min_z``
    AND the fit is better than its current timing — and the video still holds the
    footage it then reads. Cuts shorter than three beats, gaps and videos with nothing
    measurable are left alone, each with its reason. ``apply=False`` reports without
    changing anything. All the changes are ONE step of the edit's history, so one
    ``undo_edit`` takes them all back.

    **How much to expect.** On three phone videos of a crowd dancing, the accents locked
    to the beat on one of them only; each video's own SOUND locked on all three. So on
    footage like that most cuts will be left as they are, which is the right answer when
    the picture does not show the beat. It works best on a clearly visible dancer.

    Needs the ``scoring`` extra: the first call measures each video's movement (tens of
    seconds a video; kept for next time). Returns the edit (``get_edit``'s shape) plus
    ``fit``: ``cuts`` (``{index, slip_s, rate, z, applied, reason}`` per cut looked at),
    ``fitted``, ``kept`` and ``min_z``.
    """
    import numpy as np

    from muvid.footage import beats as bs
    from muvid.footage.beat_fit import FIT_MIN_BEATS, BeatGrid, fit_cut
    from muvid.footage.edl import _clip_contains, placed_as

    record, entries = _edit_entries(fp, edit_id)
    grid = BeatGrid.fitted(beat_grid(fp)["beats"])
    if grid is None:
        raise FootageError(
            "the song has no steady beat to fit to (too few beats found, or the tempo "
            "changes) — set the cuts' timing by hand with set_cut's slip_s and rate"
        )
    span = _span_of(record)
    wanted = (
        [
            i
            for i, e in enumerate(entries)
            if not e.is_gap
            and (
                span is None
                or (e.song_end > span[0] + _EPS and e.song_start < span[1] - _EPS)
            )
        ]
        if indices is None
        else sorted({_check_index(entries, int(i)) for i in indices})
    )
    aligns = {a.clip_id: a for a in _placements(fp)}
    accents: dict = {}

    def accents_of(clip_id: str):
        if clip_id not in accents:
            try:
                sig = beat_signals(fp, source=clip_id, max_points=0)["signals"]
                rec = sig.get(bs.MOTION_STOPS)
            except FootageError:
                rec = None
            if not rec or not rec.get("n"):
                accents[clip_id] = None
            else:
                v = np.array(
                    [np.nan if x is None else x for x in rec["values"]], dtype=float
                )
                accents[clip_id] = (rec["t0"] + rec["hop_s"] * np.arange(v.size), v)
        return accents[clip_id]

    report, changed = [], {}
    for i in wanted:
        e = entries[i]
        row = {
            "index": i,
            "slip_s": e.slip_s,
            "rate": e.rate,
            "z": None,
            "applied": False,
        }
        if e.is_gap:
            report.append(row | {"reason": "a gap — no video to fit"})
            continue
        if e.song_end - e.song_start < FIT_MIN_BEATS * grid.period - _EPS:
            report.append(
                row | {"reason": "shorter than three beats — too short to judge by"}
            )
            continue
        a = aligns.get(e.clip_id)
        # a free cut is fitted through the placement its own in-point implies
        a = placed_as(e, a) if a is not None else None
        sig = accents_of(e.clip_id)
        if a is None or sig is None:
            report.append(
                row | {"reason": "this video's movement could not be measured"}
            )
            continue
        fit = fit_cut(
            sig[0],
            sig[1],
            song_start=e.song_start,
            song_end=e.song_end,
            offset=a.offset_s,
            current_slip=e.slip_s,
            current_rate=e.rate,
            grid=grid,
            feasible=lambda slip, rate, a=a, e=e: _clip_contains(
                a, e.song_start, e.song_end, slip, rate
            ),
        )
        if fit is None:
            report.append(row | {"reason": "no movement to go by in this stretch"})
            continue
        row["z"] = round(fit.z, 2)
        better = not np.isfinite(fit.current_score) or (
            fit.score > fit.current_score + FIT_MIN_GAIN
        )
        same = abs(fit.slip_s - e.slip_s) < 1e-6 and abs(fit.rate - e.rate) < 1e-6
        if fit.z < min_z:
            report.append(
                row
                | {
                    "reason": "the movement does not follow the beat clearly enough here"
                }
            )
        elif same or not better:
            report.append(row | {"reason": "already on the beat"})
        else:
            row.update(slip_s=round(fit.slip_s, 4), rate=round(fit.rate, 3))
            row.update(applied=bool(apply), reason="fitted" if apply else "would fit")
            report.append(row)
            if apply:
                changed[i] = replace(e, slip_s=row["slip_s"], rate=row["rate"])
    if changed:
        for i, e in changed.items():
            entries[i] = e
        reply = _store_changed(fp, record, entries)
    else:
        reply = _edit_reply(fp, record)
    fitted = sum(1 for r in report if r["applied"])
    reply["fit"] = {
        "cuts": report,
        "fitted": fitted,
        "kept": len(report) - fitted,
        "min_z": float(min_z),
    }
    return reply


@_edits_locked
def split_cut(fp, *, edit_id: str, at_s: float) -> dict:
    """Split the cut playing at song time ``at_s`` into two cuts of the same video.

    The two halves keep the cut's video, framing and look; a moving framing (a pan) is
    divided where it was at ``at_s``. Refused on a boundary (nothing to split). Returns
    the changed edit; ``changed`` is the index of the second half.
    """
    record, entries = _edit_entries(fp, edit_id)
    at = float(at_s)
    i = next(
        (
            k
            for k, e in enumerate(entries)
            if e.song_start + _EPS < at < e.song_end - _EPS
        ),
        None,
    )
    if i is None:
        raise FootageError(
            f"no cut plays across {at:.3f}s with room to split (it is on a boundary or "
            "outside the edit)"
        )
    e = entries[i]
    mid = _crop_at(e, at)
    first = replace(e, song_end=at, crop_end=mid if e.crop_end else None)
    second = replace(
        _moved_start(e, at), transition=None, crop=mid if e.crop_end else e.crop
    )
    entries[i : i + 1] = [first, second]
    reply = _store_changed(fp, record, entries, changed=i + 1)
    if e.look_time_varying:
        reply.setdefault("warnings", []).append(
            f"cut {i}'s look moves over time; the second half starts its move again"
        )
    return reply


@_edits_locked
def merge_cut(
    fp,
    *,
    edit_id: str,
    index: int,
    into: Literal["previous", "next"] = "previous",
) -> dict:
    """Join cut ``index`` to its neighbour: the neighbour (``into`` "previous" or
    "next") takes over its span, so the neighbour's video must cover it. The joined
    cut keeps the neighbour's video, framing and look. Returns the changed edit."""
    record, entries = _edit_entries(fp, edit_id)
    i = _check_index(entries, index)
    e = entries[i]
    if into == "previous":
        if i == 0:
            raise FootageError("cut 0 has no previous cut to join — use into='next'")
        entries[i - 1 : i + 1] = [replace(entries[i - 1], song_end=e.song_end)]
        changed = i - 1
    elif into == "next":
        if i + 1 >= len(entries):
            raise FootageError(
                f"cut {i} is the last cut and has no next cut to join — use "
                "into='previous'"
            )
        nxt = entries[i + 1]
        # The joined cut's entrance is now this cut's entrance, so its blend comes too.
        entries[i : i + 2] = [
            replace(_moved_start(nxt, e.song_start), transition=e.transition)
        ]
        changed = i
    else:
        raise FootageError(f"into must be 'previous' or 'next', got {into!r}")
    return _store_changed(fp, record, entries, changed=changed)


@_edits_locked
def delete_edit(fp, *, edit_id: str) -> dict:
    """Delete a saved edit. Videos already rendered from it are kept (they still name
    the edit they came from). An unknown ``edit_id`` is refused, naming the edits."""
    _read_edit(fp, edit_id)  # refuses with the known ids
    fp.delete_edit(edit_id)
    return {"deleted": edit_id, "edits": [r["edit_id"] for r in fp.list_edit_records()]}


@_edits_locked
def set_span(fp, *, edit_id: str, start_s: float, end_s: float) -> dict:
    """Choose which part of the song the video covers — where it starts and ends.

    **Trimming loses nothing.** The span is a window on the edit, not a cut of it: every
    cut is kept whole, and only what is RENDERED is limited to ``start_s``..``end_s``
    (the song cut to match, faded out at the end when it stops before the song does;
    cuts across an edge are shortened in the render only). Widening the span again —
    ``start_s=0`` and ``end_s`` = the song's length is the whole song — brings back
    exactly what was there. Returns the edit, with its ``span``.
    """
    record, _entries = _edit_entries(fp, edit_id)
    span = _check_span(fp, (start_s, end_s))
    previous = record
    record = dict(record, modified=time.time())
    _set_or_pop(record, "span", list(span) if span else None)
    _commit(fp, previous, record)
    return _edit_reply(fp, record)


@_edits_locked
def rename_edit(fp, *, edit_id: str, name: str) -> dict:
    """Give an edit a new name — what the edit picker and the renders made from it show.

    Only the name changes; the cuts, the span and the edit's id stay as they are, and
    the rename can be undone like any other change. Returns the edit.
    """
    record, _entries = _edit_entries(fp, edit_id)
    name = (name or "").strip()
    if not name:
        raise FootageError("an edit's name cannot be empty")
    if len(name) > EDIT_NAME_MAX_LEN:
        raise FootageError(
            f"an edit's name can be at most {EDIT_NAME_MAX_LEN} characters, "
            f"got {len(name)}"
        )
    previous = record
    record = dict(record, name=name, modified=time.time())
    _commit(fp, previous, record)
    return _edit_reply(fp, record)


#: The longest name an edit may carry — long enough for a description, short enough
#: to fit a picker.
EDIT_NAME_MAX_LEN = 120


#: How many earlier versions of one edit are kept for ``undo_edit`` (env-tunable).
EDIT_HISTORY_LIMIT = int(os.environ.get("MUVID_EDIT_HISTORY_LIMIT", "100"))


def _commit(fp, previous: dict, record: dict) -> None:
    """Write ``record`` over ``previous``, keeping ``previous`` for undo (and clearing
    redo — a new change forks the history). Callers hold the edits lock."""
    history = fp.read_edit_history(record["edit_id"])
    history["undo"] = (history["undo"] + [previous])[-EDIT_HISTORY_LIMIT:]
    history["redo"] = []
    fp.write_edit_history(record["edit_id"], history)
    fp.write_edit(record["edit_id"], record)


def _step_history(fp, edit_id: str, *, back: bool) -> dict:
    current = _read_edit(fp, edit_id)
    history = fp.read_edit_history(edit_id)
    source, dest = ("undo", "redo") if back else ("redo", "undo")
    if not history[source]:
        raise FootageError(
            f"nothing to {'undo' if back else 'redo'} on edit {edit_id!r}"
        )
    restored = dict(history[source].pop(), edit_id=edit_id)
    history[dest] = (history[dest] + [current])[-EDIT_HISTORY_LIMIT:]
    fp.write_edit_history(edit_id, history)
    fp.write_edit(edit_id, restored)
    return _edit_reply(fp, restored)


@_edits_locked
def undo_edit(fp, *, edit_id: str) -> dict:
    """Undo the last change to a saved edit (a cut changed, split, joined, the span, a
    whole replacement — by a person or by the assistant). Returns the edit as it now
    is; ``redo_edit`` puts the change back. Up to 100 changes are kept per edit."""
    return _step_history(fp, edit_id, back=True)


@_edits_locked
def redo_edit(fp, *, edit_id: str) -> dict:
    """Redo the change ``undo_edit`` last took back. A new change after an undo
    discards what could be redone. Returns the edit as it now is."""
    return _step_history(fp, edit_id, back=False)


def _set_or_pop(d: dict, key: str, value) -> None:
    if value is None:
        d.pop(key, None)
    else:
        d[key] = value


def _new_edit_record(
    fp,
    edl: list[dict],
    *,
    name: str,
    how_made: str,
    edit_id: str = "",
    span: Optional[tuple[float, float]] = None,
) -> dict:
    now = time.time()
    eid = _normalised_id(edit_id, label="edit_id") if edit_id else _fresh_edit_id(fp)
    record = {
        "edit_id": eid,
        "name": name,
        "how_made": how_made,
        "created": now,
        "modified": now,
        "edl": edl,
    }
    # Absent = the whole song: one spelling on disk, and every edit saved before spans
    # existed reads as what it was.
    if span is not None:
        record["span"] = [span[0], span[1]]
    return record


def _fresh_edit_id(fp) -> str:
    while True:
        eid = uuid.uuid4().hex[:_EDIT_ID_HEX]
        if not fp.has_edit(eid):
            return eid


def _default_edit_name(fp) -> str:
    return f"Edit {len(fp.list_edit_records()) + 1}"


def _read_edit(fp, edit_id: str) -> dict:
    try:
        return fp.read_edit(edit_id)
    except (KeyError, ValueError):
        known = [r["edit_id"] for r in fp.list_edit_records()]
        raise FootageError(
            f"unknown edit {edit_id!r} — this project's edits are: {known or 'none yet'}"
        ) from None


def _edit_entries(fp, edit_id: str):
    """``(record, entries)`` of a saved edit, the entries as :class:`EdlEntry` records."""
    from muvid.footage.edl import _as_entry

    record = _read_edit(fp, edit_id)
    try:
        return record, [_as_entry(e) for e in record.get("edl") or []]
    except (ValueError, KeyError, TypeError) as e:
        raise FootageError(f"edit {edit_id!r} is unreadable: {e}") from e


def _check_index(entries, index: int) -> int:
    i = int(index)
    if not 0 <= i < len(entries):
        raise FootageError(
            f"cut index {index} is out of range — this edit has cuts 0..{len(entries) - 1}"
        )
    return i


def _validated_entries(fp, edl) -> list:
    """``edl`` gap-filled over the whole song and checked against the current alignment
    — structurally. (An edit's ``span`` never trims what is stored: it is a window.)

    ``allow_unreliable=True``: an edit is a plan, and the trust refusal belongs where the
    encode does (:func:`render`); ``edits`` reports which clips it would refuse.
    """
    from muvid.footage.edl import fill_gaps, validate_edl

    aligns = _require_song_and_alignment(fp)
    song_dur = fp.song_duration()
    try:
        return validate_edl(
            fill_gaps(edl, song_dur),
            aligns,
            song_dur,
            canvas=fp.canvas(),
            allow_unreliable=True,
        )
    except (ValueError, KeyError, TypeError) as e:
        raise FootageError(f"not a valid edit: {e}") from e


def _store_changed(fp, record: dict, entries, *, changed: Optional[int] = None) -> dict:
    validated = _validated_entries(fp, entries)
    previous = record
    record = dict(record, edl=[edl_json(e) for e in validated], modified=time.time())
    _commit(fp, previous, record)
    reply = _edit_reply(fp, record)
    if changed is not None:
        reply["changed"] = changed
    return reply


def _edit_check(fp, record: dict) -> tuple[Optional[str], list, list]:
    """``(problem, unreliable_clip_ids, entries)`` of a record against the alignment now."""
    from muvid.footage.edl import UnreliableAlignmentError, validate_edl

    if not fp.has_song():
        return "no song set", [], []
    try:
        aligns = _placements(fp)
    except FootageError as e:
        return str(e), [], []
    try:
        entries = validate_edl(
            record.get("edl") or [],
            aligns,
            fp.song_duration(),
            canvas=fp.canvas(),
            allow_unreliable=True,
        )
        # what renders: the window (the whole song unless the edit has a span)
        entries = validate_edl(
            _windowed(entries, _span_of(record)),
            aligns,
            fp.song_duration(),
            canvas=fp.canvas(),
            allow_unreliable=True,
        )
    except (ValueError, KeyError, TypeError) as e:
        return str(e), [], []
    try:
        validate_edl(entries, aligns, fp.song_duration(), canvas=fp.canvas())
    except UnreliableAlignmentError as e:
        return None, list(e.clip_ids), entries
    except (ValueError, KeyError):
        pass
    return None, [], entries


def _edit_summary(fp, record: dict) -> dict:
    problem, unreliable, _ = _edit_check(fp, record)
    edl = record.get("edl") or []
    return {
        "edit_id": record["edit_id"],
        "name": record.get("name") or record["edit_id"],
        "how_made": record.get("how_made"),
        "created": record.get("created"),
        "modified": record.get("modified"),
        "n_cuts": sum(1 for e in edl if e.get("clip_id")),
        "n_entries": len(edl),
        "span": _effective_span(fp, record),
        "problem": problem,
        "unreliable": unreliable,
        **_history_flags(fp, record["edit_id"]),
    }


def _history_flags(fp, edit_id: str) -> dict:
    read = getattr(fp, "read_edit_history", None)
    history = read(edit_id) if read is not None else {"undo": [], "redo": []}
    return {"can_undo": bool(history["undo"]), "can_redo": bool(history["redo"])}


def _effective_span(fp, record: dict) -> Optional[list]:
    """The part of the song an edit covers: its ``span``, else the whole song."""
    span = _span_of(record)
    if span is not None:
        return list(span)
    return [0.0, fp.song_duration()] if fp.has_song() else None


def _edit_reply(fp, record: dict) -> dict:
    problem, unreliable, entries = _edit_check(fp, record)
    reply = _edit_summary(fp, record) | {"edl": record.get("edl") or []}
    if entries:
        reply["coverage"] = coverage_report(
            [e for e in entries if not e.is_gap],
            _placements(fp),
            fp.song_duration(),
            span=_span_of(record),
        )
    return reply


def _crop_at(e, at_s: float):
    """The crop window of a PANNING cut at song time ``at_s`` (linear, as it renders)."""
    from muvid.footage.edl import CropWindow

    if e.crop is None or e.crop_end is None:
        return None
    f = (at_s - e.song_start) / (e.song_end - e.song_start)

    def lerp(a: float, b: float) -> float:
        return a + (b - a) * f

    return CropWindow(
        x=lerp(e.crop.x, e.crop_end.x),
        y=lerp(e.crop.y, e.crop_end.y),
        w=e.crop.w,
        h=e.crop.h,
    )


# =============================================================================
# rendering
# =============================================================================


def resolve_canvas(fp, canvas: str) -> tuple[int, int]:
    """The render canvas: an explicit per-render override, else the project's."""
    from muvid.footage.workspace import CANVASES

    if not canvas:
        return fp.canvas()
    if canvas not in CANVASES:
        raise FootageError(
            f"unknown canvas {canvas!r}; choose one of {sorted(CANVASES)}"
        )
    return CANVASES[canvas]


def assemble(
    fp,
    *,
    strategy: str = "",
    edl: Optional[list] = None,
    preset: str = "",
    weights: Optional[dict] = None,
    config: Optional[dict] = None,
    canvas: str = "",
    allow_unreliable: bool = False,
    edit_id: Optional[str] = None,
    label: str = "",
    annotate=None,
    span: Optional[tuple[float, float]] = None,
    should_cancel=None,
) -> dict:
    """Assemble and render a music video — auto (a ``strategy``) or an explicit ``edl``.

    The engine behind :func:`render` and the MCP ``assemble_music_video`` tool (whose
    docstring is the full caller-facing contract). Validation is the ONE gate
    (``validate_edl``), with the trust refusal ON unless ``allow_unreliable``. Writes
    ``renders/<render_id>/final.mp4`` + ``meta.json`` and returns the meta.

    ``edit_id`` / ``label`` are recorded in the meta; ``annotate(render_id, ref_n)`` is
    the transport's hook for keys only it can fill (the MCP download claim) — merged into
    the meta before it is written. ``span`` (an explicit ``edl``'s part of the song)
    renders only that stretch: the video AND the song cut to it, the song faded out over
    :data:`TAIL_FADE_S` when the span ends before the song does.
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

    aligns = _require_song_and_alignment(fp)
    song_dur = fp.song_duration()
    # Resolved BEFORE validation: a caller's `look` is bounded against the canvas it will
    # be rendered onto (muvid#75), which is also what a `canvas=` override changes.
    canvas_wh = resolve_canvas(fp, canvas)
    has_selection_config = bool(preset or weights or config)
    try:
        if edl is not None:
            if has_selection_config:
                raise ValueError(
                    "selection config (preset/weights/config) can't accompany an "
                    "explicit edl"
                )
            span = _check_span(fp, span)
            # The edit over the whole song (structure only), then the window that
            # renders — where the trust gate applies: a clip outside the span is not
            # rendered, so it is not refused.
            whole = validate_edl(
                fill_gaps(edl, song_dur),
                aligns,
                song_dur,
                canvas=canvas_wh,
                allow_unreliable=True,
            )
            entries = validate_edl(
                _windowed(whole, span),
                aligns,
                song_dur,
                canvas=canvas_wh,
                allow_unreliable=allow_unreliable,
            )
            used_strategy = None
            excluded = []  # the caller named these clips; nothing is set aside for them
        else:
            strat = strategy or (
                "weighted" if has_selection_config else DEFAULT_STRATEGY
            )
            if has_selection_config and strat != "weighted":
                raise ValueError(
                    f"selection config only applies to strategy='weighted' (got {strat!r})"
                )
            context = _selection_context(fp, strat, preset, weights, config)
            proposal, excluded, _music = _auto_edit(
                fp,
                aligns,
                song_dur,
                strategy=strat,
                context=context,
                recover=not allow_unreliable,
                canvas=canvas_wh,
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
        # A different remedy in kind from a malformed EDL: the OFFSETS are not
        # trustworthy — re-align, drop those clips, or say you want them anyway.
        raise FootageError(f"{e} (clips: {', '.join(e.clip_ids)})") from e
    except (ValueError, KeyError) as e:
        raise FootageError(f"could not build a valid edit: {e}") from e

    cuts = derive_cuts(entries, aligns, fp.clip_paths())
    render_id = uuid.uuid4().hex[:_RENDER_ID_HEX]
    # The reference a human can say, assigned at creation so it never renumbers.
    ref_n = fp.next_render_ref()
    render_dir = fp.new_render_dir(render_id)
    # The render-plan findings, on their way to the CALLER (who has no stderr).
    notes: list[str] = [exclusion_note(x) for x in excluded]
    # A render that covers only part of the song is that part's length, and its music
    # stops early — so the check is told the length, and the song fades out. Neither
    # argument is passed for a whole-song render, which is byte-for-byte what it was.
    rendered = entries[-1].song_end - entries[0].song_start
    partial = rendered < song_dur - _EPS
    tail = {"fade_out_s": TAIL_FADE_S} if entries[-1].song_end < song_dur - _EPS else {}
    if should_cancel is not None:
        # polled between cuts by the assembler, which raises FootageCancelled
        tail["should_cancel"] = should_cancel
    try:
        out = _assemble(
            cuts,
            str(fp.song_path()),
            str(render_dir / "final.mp4"),
            canvas=canvas_wh,
            on_note=notes.append,
            **tail,
        )
        # audio= arms the duration-match check (muvid#24 B3); expected_canvas keeps the
        # aspect checks honest for a deliberate portrait/square render.
        checks = verify_video(
            out,
            audio=str(fp.song_path()),
            expected_canvas=canvas_wh,
            **({"expected_duration": rendered} if partial else {}),
        )
    except Exception:
        import shutil

        shutil.rmtree(render_dir, ignore_errors=True)
        raise

    meta = {
        "render_id": render_id,
        "ref_n": ref_n,
        **_ref_label(ref_n),
        "video": str(out),
        "strategy": used_strategy,
        "canvas": list(canvas_wh),
        # "rendered", not "covered": after gap-filling this is the edit's whole span
        # (the whole song unless the edit was trimmed).
        "rendered_span": [
            round(entries[0].song_start, 2),
            round(entries[-1].song_end, 2),
        ],
        # Full precision, NOT rounded: this list must feed straight back as edl= and
        # reproduce the same render (muvid#21 item 3).
        "edl": [edl_json(e) for e in entries],
        "coverage": coverage_report(
            [e for e in entries if not e.is_gap],
            aligns,
            song_dur,
            excluded=excluded,
            span=span,
        ),
        "ok": not failures(checks),
        "checks": report(checks),
        # ALWAYS present, empty when clean: what the render PLAN found (muvid#73).
        "warnings": notes,
    }
    if annotate is not None:
        meta.update(annotate(render_id, ref_n))
    if edit_id:
        meta["edit_id"] = edit_id
    if label:
        meta["label"] = label
    if span is not None:
        # With the edl, what re-renders this exact video: `assemble(edl=, span=)`.
        meta["span"] = [span[0], span[1]]
    artifact_id = _register_media(fp, out, kind="video", duration_s=song_dur)
    if artifact_id:
        meta["artifact_id"] = artifact_id
    fp.write_render_meta(render_id, meta)
    if artifact_id:
        refresh_cover(fp)
    return meta


def render(
    fp,
    *,
    edit_id: str,
    canvas: str = "",
    allow_unreliable: bool = False,
    annotate=None,
    should_cancel=None,
) -> dict:
    """Make the video: render a saved edit onto the canvas, over the clean song.

    Slow (minutes of encoding for a full song), so hosts run it in the background. The
    video is exactly as long as the part of the song the edit covers (its ``span``,
    default the whole song; see ``set_span``), with the song cut to match and faded out
    at the end when it stops early; gaps render black. ``canvas`` ("landscape" /
    "portrait" / "square") re-renders the same edit in another shape; default: the
    project's.

    Refused when the edit cuts to a clip whose offset the aligner will not vouch for —
    a wrong offset renders a video out of sync with the song (muvid#59) — unless
    ``allow_unreliable``; fix it with ``set_offset`` or by changing those cuts. Returns
    the render record: ``render_id``, ``edit_id``, its ``coverage``, ``ok`` and the
    ``checks`` behind it, ``warnings`` (read them — they are what the render plan found),
    and ``artifact_id`` to play it by when the project is hosted.
    """
    record = _read_edit(fp, edit_id)
    meta = assemble(
        fp,
        edl=record.get("edl") or [],
        canvas=canvas,
        allow_unreliable=allow_unreliable,
        edit_id=edit_id,
        label=record.get("name") or "",
        annotate=annotate,
        span=_span_of(record),
        should_cancel=should_cancel,
    )
    return public_render(fp, meta)


def public_render(fp, meta: dict) -> dict:
    """A render record as a HOSTED surface may return it: ``video`` made relative to the
    project (``renders/<id>/final.mp4``) — never an absolute server path; play it by its
    ``artifact_id``."""
    out = dict(meta)
    video = out.get("video")
    if video:
        try:
            out["video"] = Path(video).relative_to(fp.root).as_posix()
        except ValueError:
            out.pop("video")
    return out


def renders(fp) -> dict:
    """The finished videos, newest first: each one's ``render_id``, speakable ``ref``,
    the ``edit_id`` it was made from, its ``label``, canvas, ``ok``, the number of
    ``warnings``, and ``artifact_id`` to play it by when the project is hosted."""
    rows = []
    for meta in fp.list_renders():
        row = {
            "render_id": meta.get("render_id"),
            "ref_n": meta.get("ref_n"),
            "ref": meta.get("ref") or _ref_label(meta.get("ref_n")).get("ref"),
            "edit_id": meta.get("edit_id"),
            "label": meta.get("label"),
            "strategy": meta.get("strategy"),
            "canvas": meta.get("canvas"),
            "rendered_span": meta.get("rendered_span"),
            "ok": meta.get("ok"),
            "n_warnings": len(meta.get("warnings") or []),
            "artifact_id": meta.get("artifact_id"),
        }
        rows.append(row)
    return {"renders": rows}


def import_render(
    fp, *, path: str, render_id: str, label: str = "", edit_id: str = ""
) -> dict:
    """Bring a video finished ELSEWHERE into the project as a render (the importer's).

    Copied to ``renders/<render_id>/final.mp4`` with a meta that says it was imported:
    ``edit_id`` names the saved edit it was cut from (when known), and there are no
    ``checks`` — muvid did not make it and does not claim to have verified it.
    Idempotent: the same bytes under the same id change nothing.
    """
    from muvid.catalog import hash_file
    from muvid.footage.workspace import replace_file

    src = _existing_file(path, what="render")
    render_dir = fp.new_render_dir(render_id)
    dest = render_dir / "final.mp4"
    if not (dest.exists() and hash_file(dest) == hash_file(src)):
        replace_file(src, dest)  # a new inode: the old bytes' catalog blob stays intact
    meta_path = render_dir / "meta.json"
    previous = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    ref_n = previous.get("ref_n") or fp.next_render_ref()
    duration = _probe_duration(dest)
    # Where on the song the video sits: its edit's span when it names one, else the
    # opening of the song (a video made elsewhere starts where its song starts).
    span = _span_of(fp.read_edit(edit_id)) if edit_id and fp.has_edit(edit_id) else None
    meta = {
        "render_id": render_id,
        "ref_n": ref_n,
        **_ref_label(ref_n),
        "video": str(dest),
        "strategy": None,
        "canvas": frame_size(dest),
        "rendered_span": (
            [round(span[0], 2), round(span[1], 2)]
            if span
            else [0.0, round(duration, 2)]
        ),
        "imported": True,
        "warnings": [],
    }
    if edit_id:
        meta["edit_id"] = edit_id
    if label:
        meta["label"] = label
    artifact_id = _register_media(fp, dest, kind="video", duration_s=duration)
    if artifact_id:
        meta["artifact_id"] = artifact_id
    fp.write_render_meta(render_id, meta)
    return public_render(fp, meta)


def frame_rate(video) -> Optional[float]:
    """A video's average frame rate (frames per second), or ``None`` if unreadable."""
    from muvid.visualize.ffmpeg import FfmpegError, probe

    try:
        info = probe(Path(video))
    except FfmpegError:
        return None
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            num, _, den = str(stream.get("avg_frame_rate") or "0/0").partition("/")
            try:
                rate = float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError):
                return None
            return rate or None
    return None


def frame_size(video) -> Optional[list]:
    """``[width, height]`` of a video as DISPLAYED (a ±90° rotation swaps them)."""
    from muvid.visualize.ffmpeg import FfmpegError, probe

    try:
        info = probe(Path(video))
    except FfmpegError:
        return None
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            w, h = int(stream["width"]), int(stream["height"])
            rotation = next(
                (
                    int(d.get("rotation", 0))
                    for d in stream.get("side_data_list") or []
                    if "rotation" in d
                ),
                0,
            )
            return [h, w] if abs(rotation) % 180 == 90 else [w, h]
    return None


def _ref_label(ref_n) -> dict:
    """``{"ref": "cut 4"}`` spelled by ``nw.delivery`` (the one place the word lives);
    ``{}`` when nw is absent — ``ref_n`` is the durable fact, core muvid does not need nw."""
    if not isinstance(ref_n, int):
        return {}
    try:
        from nw.delivery import format_ref
    except ImportError:
        return {}
    return {"ref": format_ref(ref_n)}


# =============================================================================
# the lacing-native editor bridge (muvid#31)
# =============================================================================

_EDITOR_EXTRA_HINT = "the lacing-native editor bridge needs the 'editor' extra (pip install 'muvid[editor]')"


def editor_document(fp) -> dict:
    """The project as lacing-native standoff annotations, for a multitrack editor.

    One tier per clip (its ``clip-alignment/v1`` and, once scored, its
    ``clip-score-track/v1`` curves) plus a ``DECISION`` tier holding the current default
    proposal as ``music-video-edl/v1`` entries — everything referenced to the song by
    content hash, on one shared song-time axis. Needs the ``editor`` extra (lacing) and
    an alignment.
    """
    try:
        from muvid.footage.lacing_bridge import editor_document as _document
    except ImportError as e:
        raise FootageError(_EDITOR_EXTRA_HINT) from e
    if not fp.load_alignments():
        raise FootageError("no alignment yet — call align_footage first")
    try:
        return _document(fp)
    except ValueError as e:
        raise FootageError(str(e)) from e


def edl_from_annotations(fp, *, annotations: list[dict]) -> dict:
    """The editor's DECISION-tier annotations turned back into a cut list (``edl``),
    ready for ``save_edit`` / ``replace_edit`` — a faithful read, not a re-selection.
    Annotations referencing another song are refused (muvid#35)."""
    try:
        from lacing.model import Annotation

        from muvid.footage.lacing_bridge import edl_from_annotations as _read
    except ImportError as e:
        raise FootageError(_EDITOR_EXTRA_HINT) from e
    try:
        parsed = [Annotation.model_validate(a) for a in annotations]
    except Exception as e:  # noqa: BLE001 — a clean refusal, not a raw pydantic trace
        raise FootageError(f"could not read annotations: {e}") from e
    try:
        edl = _read(
            parsed,
            # No song set yet — nothing to cross-check against, so stay permissive.
            expected_song_asset_id=fp.song_hash() if fp.has_song() else None,
        )
    except ValueError as e:
        raise FootageError(str(e)) from e
    return {"edl": edl}


# =============================================================================
# media helpers: files, durations, the host catalog, the cover
# =============================================================================


def _existing_file(path: str, *, what: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_file():
        raise FootageError(f"the {what} file {p.name!r} is not there to read")
    return p


def _refuse_oversize(path: Path, *, cap: int, what: str) -> None:
    size = path.stat().st_size
    if size > cap:
        mb = 1024 * 1024
        raise FootageError(
            f"{what} file is {size / mb:.0f} MB; the {cap / mb:.0f} MB limit is exceeded"
        )


def _ext_of(ext: str, filename: str, path: Path) -> str:
    """The stored file's extension: explicit, else the original name's, else the path's."""
    for candidate in (ext, Path(filename).suffix if filename else "", path.suffix):
        e = (candidate or "").strip().lstrip(".").lower()
        if e:
            return e
    return ""


def _probe_duration(path: Path) -> float:
    """Media duration via ffprobe; an unreadable file is a refusal, not a traceback."""
    from muvid.visualize.ffmpeg import FfmpegError, media_duration

    try:
        return float(media_duration(path))
    except FfmpegError as e:
        raise FootageError(f"could not read {path.name!r} as media: {e}") from e


def _clip_duration(fp, clip_id: str) -> float:
    """A clip's duration: recorded at add time, else measured (and then recorded)."""
    from muvid.footage.edl import STILL_DURATION_S

    for c in fp.manifest().get("clips", []):
        if c.get("clip_id") == clip_id and c.get("kind") == STILL_KIND:
            return STILL_DURATION_S  # a photo can be held for any span
        if c.get("clip_id") == clip_id and c.get("duration") is not None:
            return float(c["duration"])
    dur = _probe_duration(Path(fp.clip_paths()[clip_id]))
    _record_clip_duration(fp, clip_id, dur)
    return dur


def _record_clip_hash(fp, clip_id: str) -> None:
    """Record a just-stored clip's content hash, so the measurements keyed on it
    (``beats/``) never re-hash a 400 MB file per request (``_recorded_clip_hash``)."""
    from muvid.catalog import hash_file

    path = Path(fp.clip_paths()[clip_id])
    m = fp.manifest()
    for c in m.get("clips", []):
        if c.get("clip_id") == clip_id:
            c["hash"] = hash_file(path)
    fp._write_manifest(m)


def _warm_activity(fp, clip_id: str) -> None:
    from muvid.footage.music_cut import FreeSource

    _activity_envelope(
        fp, FreeSource(clip_id, fp.clip_paths()[clip_id], duration_s=_clip_duration(fp, clip_id))
    )


def _record_clip_duration(fp, clip_id: str, duration_s: float) -> None:
    m = fp.manifest()
    for c in m.get("clips", []):
        if c.get("clip_id") == clip_id:
            c["duration"] = duration_s
    fp._write_manifest(m)


def _register_media(fp, path, *, kind: str, **meta) -> Optional[str]:
    """Register ``path`` in the project's host catalog; ``None`` without one."""
    catalog = getattr(fp, "media_catalog", None)
    if catalog is None:
        return None
    return catalog.register(path, kind=kind, **meta)


def refresh_cover(fp) -> Optional[str]:
    """Take the project's cover frame again — from the newest render, else from the
    first clip — and register it. Only for hosted projects (``None`` otherwise)."""
    if getattr(fp, "media_catalog", None) is None:
        return None
    source = _cover_source(fp)
    if source is None:
        return None
    from muvid.footage.workspace import fresh_output

    path, taken_from = source
    with fresh_output(fp.root / ".cover.frame.jpg") as tmp:
        grab_cover_frame(path, tmp)
        # set_cover puts it at cover.jpg as a NEW file (the old cover's blob stays)
        return fp.set_cover(tmp, taken_from=taken_from)


def _prepare_filmstrip(fp, clip_id: str) -> None:
    """Make a hosted clip's filmstrip at ingest, so the Edit tab opens with it.

    Only where a host will draw it (a catalog), and a clip ffmpeg cannot sample is
    left without one — ``filmstrips`` retries and reports it then; failing an upload
    over thumbnails would lose the clip for the sake of its preview.
    """
    if getattr(fp, "media_catalog", None) is None:
        return
    from muvid.footage.media_views import clip_filmstrip
    from muvid.visualize.ffmpeg import FfmpegError

    try:
        clip_filmstrip(fp, clip_id)
    except (FfmpegError, ValueError):  # retried, and reported, by `filmstrips`
        pass


def _ensure_cover(fp) -> None:
    """A cover as soon as there is a picture to take it from, and not replaced after."""
    if getattr(fp, "media_catalog", None) is not None and fp.cover_info() is None:
        refresh_cover(fp)


def _cover_source(fp) -> Optional[tuple[Path, str]]:
    for meta in fp.list_renders():  # newest first
        video = Path(meta.get("video") or "")
        if video.is_file():
            return video, f"render:{meta.get('render_id')}"
    for cid, p in fp.clip_paths().items():
        if Path(p).is_file():
            return Path(p), f"clip:{cid}"
    return None


def grab_cover_frame(video, dest) -> None:
    """Write one JPEG frame of ``video`` to ``dest`` — :data:`COVER_AT_FRACTION` of the
    way in, :data:`COVER_WIDTH` wide. The cover of every hosted muvid production."""
    from muvid.visualize.ffmpeg import run_ffmpeg

    video, dest = Path(video), Path(dest)
    # a photo is its own cover: no duration to seek a fraction into
    at = 0.0 if _is_still_name(video.name) else max(
        0.0, _probe_duration(video) * COVER_AT_FRACTION
    )
    run_ffmpeg(
        [
            "-ss",
            f"{at:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale={COVER_WIDTH}:-2",
            str(dest),
        ]
    )


# =============================================================================
# the catalogue — one row per operation; every surface derives from this
# =============================================================================


@dataclass(frozen=True)
class OpSpec:
    """One operation's catalogue row: the function (by ``name`` in this module), a
    plain-language ``title`` (it becomes a button and a command title), what it does to
    the project (``effect``: read | write | render | destroy) and how a host runs it
    (``runs``: now | job). ``hide`` names parameters a transport fills that a caller
    must not (``duration_s`` — a probed fact, not a claim a caller makes; ``annotate`` —
    a transport's hook).

    Host-agnostic data: :mod:`muvid.genre_music_video` turns these into ``nw.GenreOp``
    rows, and :mod:`muvid.mcp` derives its footage tools from the same names.
    """

    name: str
    title: str
    effect: str
    runs: str = "now"
    hide: tuple[str, ...] = ()
    #: Parameters only the HOST supplies (``nw.GenreOp.host_params``): an upload's
    #: server-side ``path`` and original ``filename``. Never in a client's schema.
    host_params: tuple[str, ...] = ()
    #: The op's own ceiling for a host-streamed upload (``nw.GenreOp.max_upload_bytes``).
    max_upload_bytes: Optional[int] = None


FOOTAGE_OP_SPECS: tuple[OpSpec, ...] = (
    OpSpec("status", "Show where the video stands", "read"),
    OpSpec(
        "set_song",
        "Set the song",
        "destroy",
        hide=("duration_s", "ext"),
        host_params=("path", "filename"),
        max_upload_bytes=SONG_MAX_BYTES,
    ),
    OpSpec(
        "add_clip",
        "Add a video or photo",
        "write",
        hide=("duration_s", "ext"),
        host_params=("path", "filename"),
        max_upload_bytes=CLIP_MAX_BYTES,
    ),
    OpSpec("remove_clip", "Remove a video", "destroy"),
    OpSpec("align", "Listen to the videos", "write", runs="job"),
    OpSpec("set_offset", "Place a video on the song by hand", "write"),
    OpSpec("clear_offset", "Forget where I placed this video", "destroy"),
    OpSpec("set_has_song", "Say whether a video has the song in it", "write"),
    OpSpec("timeline", "Show which videos cover which parts of the song", "read"),
    OpSpec("beat_grid", "Find the beat", "read"),
    OpSpec("peaks", "Show the song's waveform", "read"),
    OpSpec("beat_signals", "Show where the beat is in the song or a video", "read"),
    OpSpec("filmstrips", "Show the videos' filmstrips", "read"),
    OpSpec("filmstrip", "Show one video's filmstrip", "read"),
    OpSpec(
        "score",
        "Look at the footage",
        "write",
        runs="job",
        host_params=("should_cancel",),
    ),
    OpSpec("scores", "Show how each video scores over the song", "read"),
    OpSpec("strategies", "List the ways to cut", "read"),
    OpSpec("propose_edit", "Cut it for me", "write"),
    OpSpec("save_edit", "Save a cut list as a new edit", "write"),
    OpSpec("edits", "List the edits", "read"),
    OpSpec("get_edit", "Open an edit", "read"),
    OpSpec("replace_edit", "Replace an edit's whole cut list", "destroy"),
    OpSpec("set_cut", "Change a cut", "write"),
    OpSpec("split_cut", "Split a cut here", "write"),
    OpSpec("merge_cut", "Join a cut to its neighbour", "write"),
    OpSpec("fit_to_beat", "Fit the moves to the beat", "write"),
    OpSpec("set_span", "Choose which part of the song the video covers", "write"),
    OpSpec("rename_edit", "Rename an edit", "write"),
    OpSpec("looks", "List the looks", "read"),
    OpSpec("undo_edit", "Undo", "write"),
    OpSpec("redo_edit", "Redo", "write"),
    OpSpec("delete_edit", "Delete an edit", "destroy"),
    OpSpec(
        "render",
        "Make the video",
        "render",
        runs="job",
        hide=("annotate",),
        host_params=("should_cancel",),
    ),
    OpSpec("renders", "List the finished videos", "read"),
    OpSpec("editor_document", "Open the project in the timeline editor", "read"),
)


__all__ = [spec.name for spec in FOOTAGE_OP_SPECS] + [
    "FOOTAGE_OP_SPECS",
    "OpSpec",
    "FootageError",
    "FootageCancelled",
    "assemble",
    "declared_alignment",
    "edl_from_annotations",
    "edl_json",
    "coverage_report",
    "exclusion_note",
    "assemble_refusal",
    "resolve_canvas",
    "refresh_cover",
    "grab_cover_frame",
    "import_render",
    "frame_size",
    "frame_rate",
    "public_render",
    "require_scorable",
    "run_scoring",
    "EDL_OPTIONAL_FIELDS",
]
