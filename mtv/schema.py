"""Schema for an mtv project — the SSOT data shapes.

A music video project is a folder. ``project.json`` at the root holds the
plan: song metadata, named characters/environments, song sections, and
shots with start/end times and a chosen render strategy. Every other
file in the project (lyrics, alignment, character cards, storyboards,
shot videos) is a derived artifact that points back to entries here.

Schemas are dataclasses (frozen=True) so equality/hash are structural
and edits create new instances. ``to_dict`` / ``from_dict`` are
mechanical (asdict / kwargs); ``schema_version`` lets us migrate later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, Mapping


SCHEMA_VERSION = 1

#: Render strategies a single shot can use. The ``render`` subpackage
#: dispatches on this string.
RenderStrategy = Literal[
    "lipsync",
    "image_to_video",
    "text_to_video",
    "animation",
    "still",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class SongInfo:
    """Metadata for the master audio file."""

    audio_path: str  # relative to project root
    duration_s: float
    sample_rate: int = 0
    bitrate: int = 0
    bpm: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionSpec:
    """A non-overlapping span of the song with a label.

    ``label`` is free-form ("intro", "verse", "chorus", "bridge",
    "outro") so users can use whatever taxonomy fits their song.
    """

    id: str
    start_s: float
    end_s: float
    label: str = ""
    energy: str = ""  # "low" | "medium" | "high" | free-form
    mood: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotSpec:
    """A timeline-locked visual unit of the music video.

    ``[start_s, end_s)`` is half-open. Shots within a project are
    sorted by ``start_s`` and should be non-overlapping (the validator
    warns otherwise — overlap can be intentional for transitions but
    isn't supported by the basic compositor).
    """

    id: str
    start_s: float
    end_s: float
    section_id: str = ""
    render_strategy: RenderStrategy = "image_to_video"
    environment: str = ""  # name of an EnvironmentRef
    characters: tuple[str, ...] = ()  # names of CharacterRefs
    description: str = ""  # the prose direction for the shot
    camera: str = ""  # "static" | "slow push-in" | ...
    framing: str = "medium"
    notes: str = ""

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


@dataclass(frozen=True, slots=True, kw_only=True)
class CharacterRef:
    """Pointer to a character folder under ``characters/<name>/``.

    The folder contains the canonical card.json + curated reference
    images. We only carry the name + a quick description here so the
    project SSOT stays small.
    """

    name: str
    description: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentRef:
    """Pointer to an environment folder under ``environments/<name>/``."""

    name: str
    description: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectSpec:
    """The top-level project SSOT, persisted as ``project.json``."""

    schema_version: int = SCHEMA_VERSION
    title: str = ""
    song: SongInfo | None = None
    characters: tuple[CharacterRef, ...] = ()
    environments: tuple[EnvironmentRef, ...] = ()
    sections: tuple[SectionSpec, ...] = ()
    shots: tuple[ShotSpec, ...] = ()
    global_style: str = ""
    notes: str = ""

    # --- helpers ---

    def with_(self, **changes: Any) -> "ProjectSpec":
        return replace(self, **changes)

    def section(self, section_id: str) -> SectionSpec:
        for s in self.sections:
            if s.id == section_id:
                return s
        raise KeyError(f"No section named {section_id!r}")

    def shot(self, shot_id: str) -> ShotSpec:
        for s in self.shots:
            if s.id == shot_id:
                return s
        raise KeyError(f"No shot named {shot_id!r}")

    def character(self, name: str) -> CharacterRef:
        for c in self.characters:
            if c.name == name:
                return c
        raise KeyError(f"No character named {name!r}")

    def environment(self, name: str) -> EnvironmentRef:
        for e in self.environments:
            if e.name == name:
                return e
        raise KeyError(f"No environment named {name!r}")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convert tuples to lists for JSON friendliness.
        for k in ("characters", "environments", "sections", "shots"):
            d[k] = list(d[k])
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProjectSpec":
        return _project_from_dict(data)


# --- (de)serialization helpers --------------------------------------------


def _project_from_dict(data: Mapping[str, Any]) -> ProjectSpec:
    """Build a ProjectSpec from a (json-loaded) dict.

    Forward-compatible: unknown keys at any level are silently dropped.
    Missing keys fall back to the dataclass default.
    """
    song = data.get("song")
    return ProjectSpec(
        schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        title=data.get("title", ""),
        song=SongInfo(**_only_known(SongInfo, song)) if song else None,
        characters=tuple(
            CharacterRef(**_only_known(CharacterRef, c))
            for c in data.get("characters", ())
        ),
        environments=tuple(
            EnvironmentRef(**_only_known(EnvironmentRef, e))
            for e in data.get("environments", ())
        ),
        sections=tuple(
            SectionSpec(**_only_known(SectionSpec, s))
            for s in data.get("sections", ())
        ),
        shots=tuple(
            ShotSpec(**_only_known(ShotSpec, s)) for s in data.get("shots", ())
        ),
        global_style=data.get("global_style", ""),
        notes=data.get("notes", ""),
    )


def _only_known(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strip unknown keys before passing to a dataclass constructor."""
    if payload is None:
        return {}
    fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return {k: v for k, v in payload.items() if k in fields}
