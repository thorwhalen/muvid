"""muvid — tools to make music videos.

Two independent halves:

- **Narrative pipeline** (needs the AI extras: ``falaw``, ``lacing``,
  ``lookbook``): transcribe a song, align lyrics, define characters and
  environments, write a shot script, render and compose. The verbs below are
  also the CLI. Project model: :class:`MusicVideoProject` and the schema
  dataclasses.
- **Visualizer** (:mod:`muvid.visualize`, needs only ``ffmpeg`` + ``mixing``):
  turn a song and a cover into a still / Ken Burns / audio-reactive music video,
  plus a thumbnail. Deterministic, no AI, no network.

    >>> from muvid.visualize import render_audio_video
    >>> render_audio_video("song.wav", image="cover.png")            # doctest: +SKIP

The narrative-pipeline names are imported **lazily** so that ``import muvid`` (and
hence ``import muvid.visualize``) does not require the heavy AI extras — the
import of a given name only pulls its dependencies when you actually use it.
"""

from __future__ import annotations

import importlib

#: Lazily-loaded public names → the submodule they live in. Kept lazy so the
#: lightweight :mod:`muvid.visualize` path never drags in the AI extras.
_LAZY = {
    # high-level facade
    "add_character": "muvid.facade",
    "add_character_images": "muvid.facade",
    "add_environment": "muvid.facade",
    "align_lyrics": "muvid.facade",
    "compose": "muvid.facade",
    "curate_character": "muvid.facade",
    "generate_character_images": "muvid.facade",
    "init_project": "muvid.facade",
    "parse_script": "muvid.facade",
    "render": "muvid.facade",
    "render_environment": "muvid.facade",
    "render_shot": "muvid.facade",
    "status": "muvid.facade",
    "transcribe_song": "muvid.facade",
    "write_script": "muvid.facade",
    # data model
    "MusicVideoProject": "muvid.project",
    "CharacterRef": "muvid.schema",
    "EnvironmentRef": "muvid.schema",
    "ProjectSpec": "muvid.schema",
    "SectionSpec": "muvid.schema",
    "ShotSpec": "muvid.schema",
    "SongInfo": "muvid.schema",
    # visualizer entry point (the rest of the surface is under muvid.visualize)
    "render_audio_video": "muvid.visualize",
}

__all__ = [*_LAZY, "visualize"]


def __getattr__(name: str):
    """Lazily import a public name on first access (PEP 562)."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'muvid' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__():
    return sorted(__all__)
