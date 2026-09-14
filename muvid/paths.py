"""The ONE place muvid decides where its data lives.

Every muvid part that persists anything answers *where* from here: the footage genre's
stateful projects (``muvid/footage/workspace.py``), the visualizer's per-user render
buckets (``muvid/mcp/workspace.py``), and — new with this module — the local, single-user
project folders the part-3 pipeline and the part-4 subgenres work in.

Default root ``~/.local/share/muvid``, overridable with ``$MUVID_DATA_HOME``. Per the
app-data-lifecycle rule this is the user-data dir and **never** the app/deploy tree: a
deploy's ``rsync --delete`` treats anything it did not build as drift and erases it.

**Why this module exists at all, rather than a third copy of a four-line function.**
``data_home`` and :func:`safe_component` were duplicated verbatim in the two workspace
modules, and the part-3/part-4 surfaces had *no* copy — ``root`` is a required positional
on every ``muvid`` CLI verb and on :class:`muvid.project.MusicVideoProject`, so those
surfaces never computed a default and the only written-down answer lived in prose, in
``.claude/skills/muvid/SKILL.md``. That prose said ``~/muvid/<song-stem>`` and was
followed literally, which put 36 MB of a real project — two Suno takes, three rendered
videos, the poem sources — in ``~/muvid/il-pleut``: an app-named directory under ``$HOME``
that no deploy, backup or tool owns. A default stated in prose drifts from the code and
cannot be tested; one computed by a function can be both. So the fix is not a better
sentence, it is :func:`project_root` plus a ``muvid project-root`` verb the skill *calls*
instead of a path the skill *states*.

A person choosing to keep their own working files in ``~/Downloads`` is a different thing
and not this module's business — the rule is about where a *tool* writes by default.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Env var overriding the muvid data root. ONE knob for the root, never one per kind —
#: N env vars where one belongs is the anti-pattern the app-data-lifecycle rule names.
DATA_HOME_ENV_VAR = "MUVID_DATA_HOME"

#: The ``{kind}`` subfolder local (single-user, non-connector) project folders live in.
#: Data never goes at the root itself: leaving the root open is what let the footage
#: genre add ``music_video/`` and the visualizer ``visualizer/`` without a migration.
PROJECTS_KIND = "projects"


def data_home() -> Path:
    """The muvid data root: ``$MUVID_DATA_HOME`` or ``~/.local/share/muvid``."""
    override = os.environ.get(DATA_HOME_ENV_VAR)
    return Path(override) if override else Path.home() / ".local" / "share" / "muvid"


def safe_component(value: str, *, label: str) -> str:
    """A single, traversal-safe path component (no ``/``, ``\\``, ``..``, or empties).

    Every caller-supplied name reaching the data root goes through this. On the MCP
    surface the names arrive from a remote OAuth caller, so this is a trust boundary,
    not a tidiness check.
    """
    v = (value or "").strip()
    if not v or v in (".", "..") or "/" in v or "\\" in v or "\x00" in v:
        raise ValueError(f"invalid {label}: {value!r}")
    return v


def project_root(name: str, *, kind: str = PROJECTS_KIND) -> Path:
    """Where a local muvid project named ``name`` belongs: ``{data_home}/{kind}/{name}``.

    Returns the path without creating it — ``muvid init`` (or a subgenre's own renderer)
    owns creation, and a resolver that made directories could not be used to *ask* where
    a project would go.

    ``kind`` is the seam for a surface that wants its own subtree rather than sharing
    ``projects/``; the connector-facing genres already use their own (``music_video/``,
    ``visualizer/``) through the workspace classes that own those layouts.
    """
    return (
        data_home()
        / safe_component(kind, label="kind")
        / safe_component(name, label="project name")
    )
