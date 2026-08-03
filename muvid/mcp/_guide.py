"""The one SSOT for muvid's MCP model-facing instructions + the ``help`` text.

Kept tiny: the ``music-visualizer`` genre is a single deterministic render, so the
workflow is just create → render. A host that aggregates this genre (the unified reelee
connector) composes its OWN bridge using the prefixed tool names — it does NOT append
this verbatim (these name the tools unprefixed).
"""

from __future__ import annotations

INSTRUCTIONS = (
    "muvid music-visualizer — turn a song into a 16:9, YouTube-ready visualizer video "
    "(+ thumbnail), deterministically and for free (ffmpeg only, no AI, no keys).\n\n"
    "Workflow:\n"
    "1. Create a project: create_project(genre='music-visualizer', template='<look>'). "
    "See list_visuals() for the looks (still / cqt / bars / spectrum / waves / scope) or "
    "use 'auto'.\n"
    "2. Render: render_visualizer(project_id, audio='<https URL to the song>', "
    "cover='<https URL to the cover>', visual='<look>'). Audio/cover are direct media "
    "URLs, fetched server-side; the 'still' look needs a cover.\n"
    "3. Inspect: project_status(project_id) lists your renders (paths + the platform-"
    "check verdict).\n\n"
    "All tools are free. Renders are stored server-side in your project bucket."
)
