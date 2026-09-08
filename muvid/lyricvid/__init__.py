"""Lyric video (kinetic typography) — muvid's first subgenre plugin.

Turn a song into a typographic music video: the words appear in time with the
singing, laid out by an archetype chosen for the song.

    from muvid.lyricvid import tools
    tools.render_lyric_video('song.wav', 'out.mp4', lyrics='lyrics.md')

or from the command line::

    python -m muvid.lyricvid render song.wav out.mp4 --lyrics lyrics.md

The pipeline is four seams, each with a default that genuinely works:

``timed_text``
    measured word times — reuses muvid's own aligner and lacing store rather
    than growing a second transcription path.
``director``
    the treatment decision. Default is a heuristic that reads the lyrics and
    costs nothing; an LLM creative director is opt-in.
``scene``
    every position and time, computed in Python from measurement. No model ever
    emits a coordinate or a timestamp.
``renderer``
    ``ass`` by default (frame-exact, no browser, leaves an editable subtitle
    file); ``web`` for effects ASS cannot express.

This module is a PEP 562 lazy facade, like ``muvid/__init__.py``: importing it
pulls nothing heavy, and each attribute is resolved on first use.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Canvas",
    "Scene",
    "TimedText",
    "TreatmentSpec",
    "compile_scene",
    "manifest",
    "pipeline",
    "render_lyric_video",
    "spec",
    "timed_text",
    "tools",
]

_LAZY = {
    "spec": "muvid.lyricvid.spec",
    "timed_text": "muvid.lyricvid.timed_text",
    "scene": "muvid.lyricvid.scene",
    "pipeline": "muvid.lyricvid.pipeline",
    "tools": "muvid.lyricvid.tools",
    "manifest": "muvid.lyricvid.manifest",
    "director": "muvid.lyricvid.director",
}

_LAZY_ATTRS = {
    "TreatmentSpec": ("muvid.lyricvid.spec", "TreatmentSpec"),
    "TimedText": ("muvid.lyricvid.timed_text", "TimedText"),
    "Scene": ("muvid.lyricvid.scene", "Scene"),
    "Canvas": ("muvid.lyricvid.scene", "Canvas"),
    "compile_scene": ("muvid.lyricvid.scene", "compile_scene"),
    "render_lyric_video": ("muvid.lyricvid.tools", "render_lyric_video"),
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
