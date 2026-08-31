"""The ``music-visualizer`` MCP tool surface (thorwhalen/muvid#3).

Module-level tool functions a host FastMCP server registers via
:func:`muvid.mcp.register_tools` (referenced as ``muvid.mcp.tools:<name>``). Every tool
is **free** — the visualizer spends no money (ffmpeg only, no AI, no API keys) — so none
is metered; a host still records them, and (when it sets ``metered_tools``) they bypass
the credit gate so a capped user can always render.

The caller is resolved from the OAuth token (:func:`muvid.mcp.identity.current_email`),
and all work lands in that caller's own :class:`~muvid.mcp.workspace.VisualizerProject`
bucket, addressed by ``project_id``. Audio/cover inputs are given as **http(s) URLs**,
fetched server-side through the SSRF-guarded, size/time-bounded
:mod:`muvid.mcp._fetch`; the render is additionally bounded by an input-duration cap and
(via ``$MUVID_FFMPEG_TIMEOUT_S``) an ffmpeg wall-clock timeout, because the connector
renders synchronously over HTTP.

Rendered artifacts are stored **server-side** in the caller's bucket (like braidio's
audio renders). A downloadable URL depends on the storage backend minting one (an S3
migration is a shared follow-up); a local store returns paths, retrievable via
:func:`project_status`.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from muvid.genre import EXPOSED_VISUALS, _VISUAL_INFO
from muvid.mcp.identity import current_email
from muvid.mcp.workspace import VisualizerWorkspace

#: Reject inputs longer than this many seconds — a long song is cheap to download but
#: expensive to render synchronously (env ``MUVID_MAX_DURATION_S``; default 15 min).
MAX_DURATION_S = int(os.environ.get("MUVID_MAX_DURATION_S", str(15 * 60)))

#: The connector encoder preset — faster than muvid's default ``medium`` to keep a
#: synchronous render short (env ``MUVID_RENDER_PRESET``).
RENDER_PRESET = os.environ.get("MUVID_RENDER_PRESET", "veryfast")

#: Valid tool inputs for ``visual`` — the exposed looks plus the ``auto`` selector.
_VALID_VISUALS = frozenset(EXPOSED_VISUALS) | {"auto"}


def _workspace() -> VisualizerWorkspace:
    return VisualizerWorkspace.for_email(current_email())


def _tool_error(msg: str):
    from fastmcp.exceptions import ToolError

    return ToolError(msg)


def list_visuals() -> dict:
    """List the available visualizer looks (the genre's Templates). Free.

    Each look is a ``visual`` you can pass to ``render_visualizer``. ``needs_cover``
    marks the ones that require a cover image. ``auto`` (the default) picks a still
    cover when you give an image, else an audio-reactive CQT.
    """
    return {
        "visuals": [
            {
                "name": v,
                "title": _VISUAL_INFO[v]["title"],
                "description": _VISUAL_INFO[v]["description"],
                "needs_cover": _VISUAL_INFO[v]["needs_cover"],
            }
            for v in EXPOSED_VISUALS
        ],
        "default": "auto",
    }


def list_projects() -> dict:
    """List ALL your muvid projects — music-video (footage) AND visualizer. Free.

    Rows carry ``muvid_genre`` ("footage" or "visualizer") and ``n_renders``
    (0 means the project exists but has no finished render yet — it is still
    listed). This tool used to read only the visualizer drawer, so a caller
    whose footage renders the connector had just SERVED was told they had no
    projects at all — a false statement, not a limitation (field finding,
    2026-08-30).
    """
    from muvid.downloads import list_projects as _list_both_drawers

    return {
        "projects": [
            {
                "project_id": p.project_id,
                "title": p.title,
                "muvid_genre": p.meta.get("muvid_genre"),
                "n_renders": p.deliverable_count,
                "created": p.created_at,
                "modified": p.modified_at,
            }
            for p in _list_both_drawers(current_email())
        ]
    }


def project_status(project_id: str) -> dict:
    """Your project's details and its renders (newest-first), whichever muvid
    genre it belongs to. Free.

    Spans both drawers, exactly as downloads do: asserting "no project X" about
    a footage project because only the visualizer drawer was checked is a false
    statement about the caller's own work (muvid#23's shape, still live on this
    tool until now).
    """
    from muvid.downloads import _open

    try:
        kind, pid, proj = _open(current_email(), project_id)
    except KeyError as e:
        raise _tool_error(str(e)) from None
    return {
        "project_id": pid,
        "muvid_genre": kind,
        "title": proj.manifest().get("title", pid),
        "renders": proj.list_renders(),
    }


def _resolve_input(url: str, dest: Path, *, label: str) -> Path:
    """Fetch a caller-supplied media URL to ``dest`` (SSRF-guarded, size/time-bounded)."""
    from muvid.mcp._fetch import FetchError, fetch_to_file

    if not isinstance(url, str) or not url.strip():
        raise _tool_error(f"{label} must be an http(s) URL")
    try:
        return fetch_to_file(url.strip(), dest)
    except FetchError as e:
        raise _tool_error(f"could not fetch {label}: {e}") from e


def _download_claim(project_id: str, render_id: str) -> dict:
    from muvid.downloads import claim

    return claim(project_id, render_id)


def render_visualizer(
    project_id: str,
    *,
    audio: str,
    cover: str | None = None,
    visual: str = "auto",
    title: str | None = None,
    normalize: bool = False,
) -> dict:
    """Render a song into a 16:9 YouTube-ready visualizer video (+ thumbnail). Free.

    Args:
        project_id: Your project (create one with ``create_project(genre=
            'music-visualizer')``).
        audio: An http(s) URL to the song (a direct media link, e.g. an ``.mp3``/
            ``.wav``). Fetched server-side; SSRF-guarded and size/duration-capped.
        cover: An http(s) URL to the cover image. Required for the ``still`` look;
            optional (and composed onto the frame) for the audio-reactive looks; a
            thumbnail is derived from it when present.
        visual: One of ``list_visuals()`` (``still``/``cqt``/``bars``/``spectrum``/
            ``waves``/``scope``) or ``auto`` (still with a cover, else cqt).
        title: An optional title burned into the frame.
        normalize: Loudness-normalize the audio to a YouTube-appropriate target.

    Returns:
        ``{render_id, video, thumbnail, visual, duration, size, ok, checks, note}`` —
        ``video``/``thumbnail`` are server-side paths (retrievable via
        ``project_status``); ``visual`` is the concrete look rendered (``auto`` resolved);
        ``ok`` is the platform-check verdict.
    """
    from muvid.visualize import (
        failures,
        media_duration,
        render_audio_video,
        report,
        thumbnail_image,
        verify_video,
    )

    proj = _workspace().open_project(project_id)

    if visual not in _VALID_VISUALS:
        raise _tool_error(
            f"unknown visual {visual!r}. Available: {', '.join(sorted(_VALID_VISUALS))}"
        )
    if _VISUAL_INFO.get(visual, {}).get("needs_cover") and not cover:
        raise _tool_error(f"the {visual!r} look needs a cover image (pass `cover`)")

    # Resolve ``auto`` to the concrete look muvid will pick (still with a cover, else cqt),
    # so the stored/returned ``visual`` is what was actually rendered — not the literal
    # "auto" (render_audio_video echoes the input string verbatim).
    resolved_visual = visual if visual != "auto" else ("still" if cover else "cqt")

    render_id = uuid.uuid4().hex[:12]
    render_dir = proj.new_render_dir(render_id)
    # All-or-nothing: on ANY failure after the dir exists (fetch, duration cap, a
    # timed-out/failed ffmpeg render) remove the render dir so a partial mp4 is not
    # orphaned on the shared prod box (list_renders would never reap a meta-less dir).
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpd = Path(tmp)
            audio_path = _resolve_input(audio, tmpd / "audio", label="audio")

            duration = media_duration(audio_path)
            if duration > MAX_DURATION_S:
                raise _tool_error(
                    f"audio is {duration:.0f}s; the {MAX_DURATION_S}s render limit "
                    "is exceeded"
                )

            cover_path = (
                _resolve_input(cover, tmpd / "cover", label="cover") if cover else None
            )

            video_out = render_dir / "video.mp4"
            result = render_audio_video(
                audio_path,
                image=cover_path,
                visual=visual,
                saveas=video_out,
                preset=RENDER_PRESET,
                normalize=normalize,
                title=title,
            )

            thumb_out: Path | None = None
            if cover_path is not None:
                thumb_out = render_dir / "thumbnail.jpg"
                thumbnail_image(cover_path, saveas=thumb_out, title=title)

            checks = verify_video(video_out, audio=audio_path, thumbnail=thumb_out)
    except Exception:
        import shutil

        shutil.rmtree(render_dir, ignore_errors=True)
        raise

    ok = not failures(checks)
    meta = {
        "render_id": render_id,
        "video": str(video_out),
        "thumbnail": str(thumb_out) if thumb_out else None,
        "visual": resolved_visual,
        "duration": result.duration,
        "size": list(result.size),
        "ok": ok,
        "checks": report(checks),
        # The retrieval claim, same shape the footage genre returns. `video` and
        # `thumbnail` stay as server-side paths — useful to an operator, unreadable
        # to a remote caller; the claim is the caller's handle.
        "download": _download_claim(project_id, render_id),
        # The old note said "a downloadable URL awaits the storage-backend
        # migration". It never did: the connector's signed route resolves a path
        # and streams it, and never calls dol.content_url. A tool that advertises
        # its own output as unreachable, and is wrong about why, is worse than one
        # that says nothing (muvid#8).
        "note": (
            f"Call `reelee_get_download_url(genre='muvid', project_id='{project_id}', "
            f"artifact_id='{render_id}')` for a link to watch and download this"
            + (
                f", or artifact_id='{render_id}.thumbnail' for the poster image."
                if thumb_out
                else "."
            )
        ),
    }
    proj.write_render_meta(render_id, meta)
    return meta
