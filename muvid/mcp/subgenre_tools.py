"""Generic MCP tools over the subgenre plugin surface.

Two free tools that make ANY installed subgenre reachable from a host
connector without muvid knowing it exists:

* :func:`list_subgenres` — the catalogue, import-free.
* :func:`render_subgenre` — render one, with every file input fetched through
  the SSRF-guarded, size-bounded, workspace-scoped ``_resolve_input`` and the
  request validated against the manifest's own schema before any plugin code
  is imported.

A third-party subgenre gets this transport for free; muvid's own
``lyric-video`` keeps its richer dedicated tools alongside.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from muvid.mcp.tools import _download_claim, _resolve_input, _tool_error, _workspace

__all__ = ["list_subgenres", "render_subgenre"]

#: Inputs whose values are file references (fetched) rather than primitives
#: are identified by the manifest: a property with ``"format": "path"`` OR a
#: name in this set is fetched through _resolve_input. Everything else is
#: passed through as-is after schema validation.
_FILE_INPUT_NAMES = frozenset({"audio", "lyrics", "subtitles", "cover", "image",
                               "reference_image", "clip", "video"})

#: A plugin may declare list-valued file inputs (photos[], clips[]); cap them.
MAX_FILE_INPUTS = int(os.environ.get("MUVID_SUBGENRE_MAX_FILE_INPUTS", "64"))


def list_subgenres() -> dict:
    """List every installed subgenre with its input/param schemas. Free.

    Imports no rendering code — a broken plugin appears under ``load_errors``
    rather than breaking the list.
    """
    from muvid.subgenres import subgenre_catalog

    return subgenre_catalog()


def _is_file_input(name: str, schema: dict) -> bool:
    prop = (schema.get("properties") or {}).get(name) or {}
    if prop.get("format") == "path":
        return True
    if prop.get("type") == "array" and (prop.get("items") or {}).get("format") == "path":
        return True
    return name in _FILE_INPUT_NAMES


def render_subgenre(
    project_id: str, *, subgenre: str, inputs: dict, params: dict | None = None,
) -> dict:
    """Render one installed subgenre into the caller's project. Free.

    ``inputs`` are the manifest's declared inputs; any file-valued one is an
    http(s) URL fetched into the caller's workspace (never a host path).
    ``params`` are validated against the manifest's ``params_schema``.
    """
    from muvid.subgenres import get_subgenre
    from muvid.subgenres import render_subgenre as _render
    from muvid.subgenres._schema import SchemaError

    if not isinstance(inputs, dict):
        raise _tool_error("inputs must be a JSON object")
    try:
        manifest = get_subgenre(subgenre)
    except KeyError as exc:
        raise _tool_error(str(exc)) from exc
    if manifest.cost_profile is not None:
        # The generic tool is FREE by declaration; a costed subgenre needs its
        # own metered tool, because a host meters by tool name.
        raise _tool_error(
            f"subgenre {subgenre!r} declares a cost profile and cannot be rendered "
            "through the free generic tool"
        )

    proj = _workspace().open_project(project_id)
    import uuid

    render_id = uuid.uuid4().hex[:12]
    render_dir = proj.new_render_dir(render_id)
    fetch_dir = render_dir / "inputs"
    fetch_dir.mkdir(parents=True, exist_ok=True)

    resolved: dict[str, Any] = {}
    n_files = 0
    for name, value in inputs.items():
        if _is_file_input(name, manifest.inputs or {}):
            values = value if isinstance(value, list) else [value]
            fetched = []
            for i, url in enumerate(values):
                n_files += 1
                if n_files > MAX_FILE_INPUTS:
                    raise _tool_error(f"more than {MAX_FILE_INPUTS} file inputs")
                fetched.append(str(_resolve_input(url, fetch_dir / f"{name}-{i}", label=name)))
            resolved[name] = fetched if isinstance(value, list) else fetched[0]
        else:
            resolved[name] = value

    ext = {"video/mp4": "mp4", "image/png": "png", "image/jpeg": "jpg",
           "image/gif": "gif", "audio/mpeg": "mp3"}.get(manifest.produces, "bin")
    out = render_dir / f"video.{ext}"
    try:
        result = _render(
            subgenre, inputs=resolved, params=params or {},
            workdir=render_dir / "work", output=out,
        )
    except SchemaError as exc:
        raise _tool_error("request does not satisfy the subgenre's schema: "
                          + "; ".join(exc.errors)) from exc
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise _tool_error(f"render refused: {exc}") from exc

    meta = {
        "render_id": render_id,
        "subgenre": subgenre,
        "video": str(result.output),
        "duration": result.duration_s,
        "artifacts": {k: str(v) for k, v in result.artifacts.items()},
        "meta": dict(result.meta),
        "download": _download_claim(project_id, render_id),
        "note": (
            f"Call `reelee_get_download_url(genre='muvid', project_id='{project_id}', "
            f"artifact_id='{render_id}')` for a link to watch and download this."
        ),
    }
    proj.write_render_meta(render_id, meta)
    return meta
