"""MCP tools for the footage-aligned ``music_video`` genre (thorwhalen/reelee#229).

Module-level tool functions (referenced ``muvid.mcp.footage_tools:<name>``) a host
aggregates via :func:`muvid.mcp.register_tools`. All ffmpeg + numpy only, no AI/keys.

**A transport, not an implementation.** The operations live in
:mod:`muvid.footage.service`; each tool here resolves ``project_id`` to the caller's
stateful :class:`~muvid.footage.workspace.FootageWorkspace` project (the caller comes
from the OAuth token), calls the operation, and turns its
:class:`~muvid.footage.errors.FootageError` into a fastmcp ``ToolError``. What stays here
is what only this surface does: fetching a URL (SSRF-guarded, size/time-bounded, streamed
to disk — the service takes a local file), expanding a shared folder, the per-caller
project listing, and the download claim on a render.

The tool list is derived from the operations catalogue (:mod:`muvid.mcp._footage_ops`):
operations without a hand-written tool here get a GENERATED one, ``footage_<op>``, built
at import from the operation's own signature and docstring (the named-edit operations:
``footage_set_offset``, ``footage_save_edit``, ``footage_set_cut`` …).

Workflow: ``create_project(genre='music_video')`` → ``set_song`` → ``add_footage`` ×N →
``align_footage`` → (``footage_timeline`` to inspect) → ``propose_edit(save=true)`` →
``footage_set_cut`` … → ``footage_render`` (or ``assemble_music_video`` in one call).
Lifecycle around it (muvid#22): ``list_music_video_projects`` finds a project whose id
was lost, and ``remove_footage`` takes a clip back out — which invalidates the
alignment, exactly as ``set_song`` does.
"""

from __future__ import annotations

import inspect
import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from muvid.footage import service
from muvid.footage.errors import FootageError
from muvid.footage.service import EDL_OPTIONAL_FIELDS as _EDL_OPTIONAL_FIELDS  # noqa: F401
from muvid.footage.service import edl_json as _edl_json  # noqa: F401
import re

from muvid.mcp._footage_ops import GENERATED_OPS, op_tool_name
from muvid.mcp.identity import current_email

# -- resource caps: the service's, read here for the fetch path -----------------
#: Re-exported from :mod:`muvid.footage.service` (the one place they are declared). The
#: fetch path checks them BEFORE a download lands in the project; the service checks the
#: same numbers again for every other way a file arrives.
_MAX_CLIPS = service.MAX_CLIPS
_CLIP_MAX_BYTES = service.CLIP_MAX_BYTES
_CLIP_MAX_DURATION_S = service.CLIP_MAX_DURATION_S
_SONG_MAX_BYTES = service.SONG_MAX_BYTES
_SONG_MAX_DURATION_S = service.SONG_MAX_DURATION_S
_MIN_CONFIDENCE = service.MIN_CONFIDENCE
#: Total-bytes cap for a folder archive (env ``MUVID_FOOTAGE_FOLDER_MAX_BYTES``). A shoot
#: is several clips, so this is necessarily larger than the per-clip cap.
_FOLDER_MAX_BYTES = int(
    os.environ.get("MUVID_FOOTAGE_FOLDER_MAX_BYTES", str(3 * 1024 * 1024 * 1024))
)
#: Media extensions an archive member must carry to be treated as footage.
_VIDEO_EXTENSIONS = ("mp4", "mov", "m4v", "webm", "avi", "mkv", "mpg", "mpeg", "3gp")


def _tool_error(msg: str):
    from fastmcp.exceptions import ToolError

    return ToolError(msg)


def _renamings() -> list:
    """``(pattern, tool)`` pairs rewriting an operation's name to the tool serving it.

    The service speaks in OPERATION names (``set_cut``, ``align``) because the studio
    does; a connector caller only has TOOLS (``footage_set_cut``, ``align_footage``). A
    name in double backticks is always an op reference; a bare one is rewritten only when
    it contains ``_`` (``set_offset``), since bare ``render`` or ``align`` is plain English.
    """
    rows = []
    for spec in service.FOOTAGE_OP_SPECS:
        tool = op_tool_name(spec.name)
        if tool == spec.name:
            continue
        rows.append((re.compile(rf"``{spec.name}``"), f"``{tool}``"))
        if "_" in spec.name:
            rows.append((re.compile(rf"(?<![\w]){spec.name}(?![\w])"), tool))
    return rows


_RENAMINGS = _renamings()


def _as_tool_names(text: str) -> str:
    """``text`` with operation names replaced by the tool names a caller can call."""
    for pattern, tool in _RENAMINGS:
        text = pattern.sub(tool, text)
    return text


@contextmanager
def _refusals():
    """A service refusal becomes a clean ``ToolError`` carrying the original cause —
    phrased in TOOL names, the only names a connector caller can act on."""
    try:
        yield
    except FootageError as e:
        # The ROOT cause rides along (an ImportError naming the missing extra, the
        # validator's error), so the translation adds a type and loses nothing.
        raise _tool_error(_as_tool_names(str(e))) from (e.__cause__ or e)


def _workspace():
    from muvid.footage.workspace import FootageWorkspace

    return FootageWorkspace.for_email(current_email())


def _open(project_id: str):
    """Open the caller's project, surfacing a missing project as a clean ToolError."""
    try:
        return _workspace().open_project(project_id)
    except FileNotFoundError as e:
        raise _tool_error(f"no such project {project_id!r}") from e


def _call(project_id: str, op, **params) -> dict:
    """Open the caller's project, run ``op`` on it, answer ``{project_id, **result}``."""
    proj = _open(project_id)
    with _refusals():
        out = op(proj, **params)
    return {"project_id": project_id, **out}


def _url_ext(url: str, default: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lstrip(".").lower()
    return suffix or default


def _duration(path) -> float:
    from muvid.visualize.ffmpeg import media_duration

    return float(media_duration(path))


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
    import tempfile

    from muvid.mcp._fetch import FetchError, fetch_to_file_streaming

    proj = _open(project_id)
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
        with _refusals():
            out = service.set_song(proj, path=str(tmp_song), ext=ext, duration_s=dur)
    return {"project_id": project_id, **out}


def add_footage(project_id: str, *, url: str, name: str = "") -> dict:
    """Add a footage video clip from an http(s) URL (a recording of the song). Free.

    Accepts a **share link** as well as a direct media URL. Fetched server-side (streamed to
    disk; SSRF-guarded, size/duration-capped) and asserted to be media before anything is
    stored. Returns the assigned ``clip_id``. Re-run ``align_footage`` after adding clips.

    For a whole shoot in one folder, use :func:`add_footage_folder` — a folder link holds
    many files and is refused here by name.
    """
    import tempfile

    from muvid.mcp._fetch import FetchError, fetch_to_file_streaming

    proj = _open(project_id)
    if len(proj.list_clips()) >= _MAX_CLIPS:
        raise _tool_error(f"clip limit reached ({_MAX_CLIPS}); this is a bounded v1")
    direct = _resolve_media_url(url, what="clip")
    ext = _url_ext(url, "") or _url_ext(direct, "mp4")
    # Fetch into a tempdir, then hand off to the service (which copies into clips/ under
    # the sanitized name) — so a failed/oversized fetch leaves no orphan in the project.
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
        with _refusals():
            out = service.add_clip(
                proj, path=str(tmp_clip), ext=ext, name=name, duration_s=dur
            )
    return {"project_id": project_id, **out}


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
            label = f"{name_prefix}{member.stem}" if name_prefix else member.stem
            try:
                out = service.add_clip(
                    proj,
                    path=str(member),
                    ext=member.suffix.lstrip(".").lower(),
                    name=label,
                    duration_s=dur,
                )
            except FootageError as e:
                # One refused member must not lose the ones already added: named, and on.
                skipped.append({"name": member.name, "reason": _as_tool_names(str(e))})
                continue
            added.append(
                {"clip_id": out["clip_id"], "name": label, "duration": round(dur, 2)}
            )
    return {
        "project_id": project_id,
        "added": added,
        "skipped": skipped,
        "clip_count": existing + len(added),
    }


def align_footage(project_id: str, *, keep_declared: bool = True) -> dict:
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

    A clip placed by hand (``footage_set_offset``) is left as placed and named in
    ``kept_declared``; ``keep_declared=false`` forgets those first
    (``footage_clear_offset``) and measures every clip.
    """
    proj = _open(project_id)
    with _refusals():
        if not keep_declared:  # the connector's opt-in: forget them, then measure
            for a in proj.load_alignments():
                if a.source == "declared":
                    service.clear_offset(proj, clip_id=a.clip_id)
        out = service.align(proj)
    return {"project_id": project_id, **out}


def footage_timeline(project_id: str) -> dict:
    """The coverage map: which clips cover which spans of the song (overlaps shown). Free.

    The surface for choosing which parts to use before ``assemble_music_video``. Built from
    the persisted alignment (run ``align_footage`` first).
    """
    return _call(project_id, service.timeline)


def propose_edit(
    project_id: str,
    *,
    strategy: str = "",
    preset: str = "",
    weights: dict | None = None,
    config: dict | None = None,
    save: bool = False,
    name: str = "",
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

    ``save=true`` also keeps it as a named edit (``name``, default "Edit N") and returns
    its ``edit_id`` — change it cut by cut with ``footage_set_cut`` /
    ``footage_split_cut`` / ``footage_merge_cut`` and render it with ``footage_render``.
    """
    return _call(
        project_id,
        service.propose_edit,
        strategy=strategy,
        preset=preset,
        weights=weights,
        config=config,
        save=save,
        name=name,
    )


def _render_claims(project_id: str):
    """The keys only this surface can put on a render record: the retrieval claim a
    host's download route signs (reelee#252), and the next action naming the tool."""

    def annotate(render_id: str, ref_n: int) -> dict:
        from muvid.downloads import claim
        from nw.delivery import format_ref

        return {
            # `video` stays a server-side path — useful to an operator, unreadable to a
            # remote caller; the claim is the caller's handle.
            "download": claim(project_id, render_id),
            # What the caller should DO next, naming the tool that does it
            # (thorwhalen/reelee#322).
            "note": (
                f"Call `reelee_get_download_url(genre='muvid', project_id='{project_id}', "
                f"artifact_id='{render_id}')` for a link to watch and download this. "
                f"You can refer to it as “{format_ref(ref_n)}” from now on."
            ),
        }

    return annotate


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
    span: list | None = None,
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
    - ``span``: ``[start_s, end_s]`` — render only that stretch of the song (a trimmed
      edit): the ``edl`` is gap-filled within it, the video is that long, and the song is
      cut to it and faded out at the end when it stops before the song does. A render
      of a trimmed edit records its ``span``; pass it back with its ``edl`` to reproduce
      it. Default: the whole song.
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
    proj = _open(project_id)
    with _refusals():
        return service.assemble(
            proj,
            strategy=strategy,
            edl=edl,
            preset=preset,
            weights=weights,
            config=config,
            canvas=canvas,
            allow_unreliable=allow_unreliable,
            annotate=_render_claims(project_id),
            span=span,
        )


def footage_render(
    project_id: str, *, edit_id: str, canvas: str = "", allow_unreliable: bool = False
) -> dict:
    """Render a SAVED edit (``propose_edit(save=true)`` / ``footage_save_edit``) into a
    music video. Free, minutes.

    Same render, same refusal and same reply as ``assemble_music_video`` with that edit's
    cut list as ``edl`` — plus ``edit_id``, so the video says which edit it came from.
    ``canvas`` re-renders the same edit as "landscape"/"portrait"/"square". Refused when
    the edit cuts to a clip whose offset the aligner will not vouch for, unless
    ``allow_unreliable`` (see ``assemble_music_video``). Read the returned ``warnings``.
    """
    proj = _open(project_id)
    with _refusals():
        return service.render(
            proj,
            edit_id=edit_id,
            canvas=canvas,
            allow_unreliable=allow_unreliable,
            annotate=_render_claims(project_id),
        )


def footage_status(project_id: str) -> dict:
    """Your project's song, clips, alignment summary, and renders. Free.

    Also: each clip's offset and whether it was measured or declared (``alignments``),
    the saved ``edits``, and ``next_step`` — the operation that moves the project on.
    """
    out = _call(project_id, service.status)
    step = out.get("next_step")
    if step:
        out["next_step"] = {
            **step,
            "op": op_tool_name(step["op"]),
            "why": _as_tool_names(step["why"]),
        }
    return out


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
    return _call(project_id, service.beat_grid)


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
    return _call(project_id, service.remove_clip, clip_id=clip_id)


def list_strategies() -> dict:
    """The selection strategies available for full-auto assembly. Free."""
    return service.strategies()


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
    proj = _open(project_id)
    with _refusals():
        return service.editor_document(proj)


def footage_edl_from_annotations(project_id: str, *, annotations: list[dict]) -> dict:
    """The DECISION tier's annotations, turned back into an ``edl=`` argument. Free.

    The timeline-to-EDL half: pass ``footage_editor_document``'s ``DECISION`` tier
    (after whatever an editor did to it) and get back plain
    ``{song_start, song_end, clip_id}`` dicts, ready for ``assemble_music_video(edl=...)``
    or ``propose_edit`` — a faithful read, not a re-selection. Annotations referencing a
    song other than this project's are refused, not read (muvid#35), so a clipboard from
    another project fails saying so instead of splicing in the wrong spans.
    """
    return _call(project_id, service.edl_from_annotations, annotations=annotations)


# -- generated tools: one per catalogue operation with no hand-written tool ----------


def _op_tool(op_name: str):
    """A tool ``footage_<op>(project_id, **params)`` over :mod:`muvid.footage.service`.

    Its signature is the operation's own minus the project (and minus what the catalogue
    hides), its docstring the operation's — so the tool cannot drift from the operation
    it serves.
    """
    spec = next(s for s in service.FOOTAGE_OP_SPECS if s.name == op_name)
    op = getattr(service, op_name)
    sig = inspect.signature(op, eval_str=True)
    params = [p for p in list(sig.parameters.values())[1:] if p.name not in spec.hide]
    project_param = inspect.Parameter(
        "project_id", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str
    )

    def tool(project_id: str, **kwargs) -> dict:
        return _call(project_id, op, **kwargs)

    tool.__name__ = tool.__qualname__ = op_tool_name(op_name)
    tool.__module__ = __name__
    tool.__doc__ = _as_tool_names(op.__doc__ or "")
    tool.__signature__ = sig.replace(
        parameters=[project_param, *params], return_annotation=dict
    )
    tool.__annotations__ = {
        "project_id": str,
        **{p.name: p.annotation for p in params},
        "return": dict,
    }
    return tool


for _op_name in GENERATED_OPS:
    globals()[op_tool_name(_op_name)] = _op_tool(_op_name)
del _op_name
