"""muvid — tools to make music videos.

Public surface (also the CLI verbs):

    init_project, transcribe_song, align_lyrics,
    add_character, add_character_images, generate_character_images,
    curate_character,
    add_environment, render_environment,
    write_script, parse_script,
    render_shot, render, compose, status,

Project model:

    MusicVideoProject(root)  — folder-backed project facade
    ProjectSpec, SongInfo, SectionSpec, ShotSpec,
    CharacterRef, EnvironmentRef
"""

from __future__ import annotations

from muvid.facade import (
    add_character,
    add_character_images,
    add_environment,
    align_lyrics,
    compose,
    curate_character,
    generate_character_images,
    init_project,
    parse_script,
    render,
    render_environment,
    render_shot,
    status,
    transcribe_song,
    write_script,
)
from muvid.project import MusicVideoProject
from muvid.schema import (
    CharacterRef,
    EnvironmentRef,
    ProjectSpec,
    SectionSpec,
    ShotSpec,
    SongInfo,
)

__all__ = [
    # high-level facade
    "add_character",
    "add_character_images",
    "add_environment",
    "align_lyrics",
    "compose",
    "curate_character",
    "generate_character_images",
    "init_project",
    "parse_script",
    "render",
    "render_environment",
    "render_shot",
    "status",
    "transcribe_song",
    "write_script",
    # data model
    "CharacterRef",
    "EnvironmentRef",
    "MusicVideoProject",
    "ProjectSpec",
    "SectionSpec",
    "ShotSpec",
    "SongInfo",
]
