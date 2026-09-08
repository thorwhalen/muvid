"""Montage — a beat-cut video from a pool of stills and clips (subgenre plugin).

The STANDARD kind of music video for material that has no timeline of its
own: photos and short clips cut to the song's beat grid and section
structure — what CapCut's "photo beat sync" templates, Animoto and Rotor sell.

    from muvid.montage import tools
    tools.render_montage('song.wav', 'out.mp4', photos=['a.jpg', 'b.jpg', 'c.jpg'])

or from the command line::

    python -m muvid.montage render song.wav out.mp4 --photos a.jpg b.jpg c.jpg

Four seams, each with a default that genuinely works:

``analysis``
    the beat grid (``mixing.audio.beat_grid`` where librosa is installed, a
    numpy estimator otherwise — and the plan says which), downbeats, and
    coarse sections from energy when none are supplied.
``spec``
    the treatment: a direction (accent, grade, cut feel, reuse policy) and
    scenes naming an archetype from a closed set per section.
``plan``
    the PLANNER — the core of the subgenre. Pool + grid + sections -> an edit
    list, with an explicit reuse policy so twelve photos carry a three-minute
    song. Pure and deterministic; written out as ``plan.json`` beside every render.
``render``
    ffmpeg in bounded stages: per-slot ``zoompan``/trim parts, ``xfade``
    blends, ``xstack`` grids, stream-copy concat, one YouTube-spec mux.

This module is a PEP 562 lazy facade, like ``muvid/__init__.py``: importing it
pulls nothing heavy, and each attribute is resolved on first use.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Analysis",
    "Canvas",
    "Plan",
    "TreatmentSpec",
    "analysis",
    "analyze_song",
    "manifest",
    "pipeline",
    "plan",
    "plan_montage",
    "render",
    "render_montage",
    "spec",
    "tools",
]

_LAZY = {
    "analysis": "muvid.montage.analysis",
    "manifest": "muvid.montage.manifest",
    "pipeline": "muvid.montage.pipeline",
    "plan": "muvid.montage.plan",
    "render": "muvid.montage.render",
    "spec": "muvid.montage.spec",
    "tools": "muvid.montage.tools",
}

_LAZY_ATTRS = {
    "Analysis": ("muvid.montage.analysis", "Analysis"),
    "Canvas": ("muvid.montage.render", "Canvas"),
    "Plan": ("muvid.montage.plan", "Plan"),
    "TreatmentSpec": ("muvid.montage.spec", "TreatmentSpec"),
    "analyze_song": ("muvid.montage.tools", "analyze_song"),
    "plan_montage": ("muvid.montage.tools", "plan_montage"),
    "render_montage": ("muvid.montage.tools", "render_montage"),
}


def __getattr__(name: str) -> Any:
    from importlib import import_module

    if name in _LAZY:
        return import_module(_LAZY[name])
    if name in _LAZY_ATTRS:
        module, attr = _LAZY_ATTRS[name]
        return getattr(import_module(module), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
