"""Choreo — event-driven visual music, muvid's second subgenre plugin.

Audio in, nothing else. Onsets in three frequency bands become objects that
appear ON the event and then live — Fischinger's igniting shapes, Gondry's
*Star Guitar* landscape whose spacing is the rhythm, McLaren's scratches on
black, or a swarm — and the song's sections change the arrangement.

    from muvid.choreo import tools
    tools.render_choreo('song.wav', 'out.mp4', archetype='star_guitar')

or from the command line::

    python -m muvid.choreo render song.wav out.mp4 --archetype star_guitar

The pipeline is four seams, each with a default that genuinely works:

``analysis``
    the event list, beat grid and sections — numpy only; ``mixing``'s
    librosa beat tracker when installed, a built-in estimate otherwise.
``spec``
    the treatment: direction {palette, background, density} + scenes[]
    {applies_to, archetype, params}. Closed vocabularies; repair, not reject.
``scene``
    every object's birth, death, shape, place and motion — computed in Python,
    deterministic given a seed.
``render``
    numpy frames piped into ffmpeg; a YouTube-spec mp4 with the song muxed.

This module is a PEP 562 lazy facade, like ``muvid/__init__.py``: importing it
pulls nothing heavy, and each attribute is resolved on first use.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Analysis",
    "Canvas",
    "ChoreoScene",
    "TreatmentSpec",
    "analysis",
    "analyze",
    "compile_scene",
    "manifest",
    "pipeline",
    "render",
    "render_choreo",
    "scene",
    "spec",
    "tools",
]

_LAZY = {
    "analysis": "muvid.choreo.analysis",
    "spec": "muvid.choreo.spec",
    "scene": "muvid.choreo.scene",
    "render": "muvid.choreo.render",
    "pipeline": "muvid.choreo.pipeline",
    "tools": "muvid.choreo.tools",
    "manifest": "muvid.choreo.manifest",
}

_LAZY_ATTRS = {
    "Analysis": ("muvid.choreo.analysis", "Analysis"),
    "analyze": ("muvid.choreo.analysis", "analyze"),
    "TreatmentSpec": ("muvid.choreo.spec", "TreatmentSpec"),
    "Canvas": ("muvid.choreo.scene", "Canvas"),
    "ChoreoScene": ("muvid.choreo.scene", "ChoreoScene"),
    "compile_scene": ("muvid.choreo.scene", "compile_scene"),
    "render_choreo": ("muvid.choreo.tools", "render_choreo"),
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
