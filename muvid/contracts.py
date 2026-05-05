"""Adapters between muvid's SSOT and sibling-package shapes.

muvid's source of truth is ``project.json`` + a folder of cards. The
sibling packages (``falaw``, ``an``, ``lacing``) have their own typed
shapes for related concepts — ``falaw.Character`` carries inline URLs,
``an.audio.WordTiming`` is a tuple, etc. Each package having its own
abstraction is intentional (different concerns, different lifecycles),
but **translation** between them belongs in one place — here — so the
seams are inspectable and a single change in any sibling's type only
ripples through one module.

Three families of adapters:

- ``character_to_falaw(project, name)`` / ``environment_to_falaw(...)``:
  build live ``falaw.Character`` / ``falaw.Environment`` instances from
  muvid's persistent card.json + curated reference images.
- ``word_timings_for_window(project, start_s, end_s)``: pull
  ``(text, start, end)`` tuples from the project's lacing alignment
  store, in the shape ``an.audio.WordTimingProvider`` and
  ``muvid.cost`` callers expect. Times are absolute (in song-seconds),
  not slice-relative — see :func:`shifted_word_timings` if you need
  shot-slice-relative output.
- ``shifted_word_timings(timings, *, offset_s)`` / ``progress_event_to_dict(event)``:
  pure-data transformations that don't need a project handle.

These are deliberately thin: each function delegates to the canonical
home (``falaw.scene.Character``, ``lacing.tracks.subtitle.SubtitleTrack``,
etc.) and just glues to muvid's persistent state. None of them
introduces new types — they translate, they don't redefine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from muvid.project import MusicVideoProject


__all__ = [
    "character_to_falaw",
    "environment_to_falaw",
    "progress_event_to_dict",
    "shifted_word_timings",
    "word_timings_for_window",
]


# --- character / environment ---------------------------------------------


def character_to_falaw(project: MusicVideoProject, name: str):
    """Build a ``falaw.Character`` from this project's character card.

    Resolves ``reference_image_url`` from the curated anchor (the first
    ``selected/`` image, falling back to the first ``refs/`` image) so
    downstream falaw renders have a usable URL or local path. The
    voice spec (if any) is mirrored 1:1.
    """
    from falaw.scene import Character, Voice
    from muvid.characters import get_character_anchor_image

    card = project.read_character_card(name)
    voice = None
    voice_card = card.get("voice")
    if voice_card:
        voice = Voice(
            name=voice_card.get("name", name),
            voice_id=voice_card.get("voice_id", ""),
            reference_audio_url=voice_card.get("reference_audio_url", ""),
            model_id=voice_card.get("model_id", ""),
            style_notes=voice_card.get("style_notes", ""),
        )

    try:
        anchor = get_character_anchor_image(project, name)
        anchor_url = str(anchor)
    except FileNotFoundError:
        anchor_url = ""

    return Character(
        name=name,
        description=card.get("description", ""),
        reference_image_url=anchor_url,
        voice=voice,
        style_notes=card.get("style_notes", ""),
    )


def environment_to_falaw(project: MusicVideoProject, name: str):
    """Build a ``falaw.Environment`` from this project's environment card."""
    from falaw.scene import Environment
    from muvid.environments import get_environment_anchor_image

    card = project.read_environment_card(name)
    anchor = get_environment_anchor_image(project, name)
    return Environment(
        name=name,
        description=card.get("description", ""),
        reference_image_url=str(anchor) if anchor else "",
        time_of_day=card.get("time_of_day", ""),
        lighting=card.get("lighting", ""),
    )


# --- word timings -------------------------------------------------------


WordTimingTuple = tuple[str, float, float]


def word_timings_for_window(
    project: MusicVideoProject,
    start_s: float,
    end_s: float,
    *,
    asset_id: str | None = None,
) -> list[WordTimingTuple]:
    """Read ``(text, start, end)`` tuples from the alignment store.

    Times are **absolute song-seconds**. Returns an empty list when
    the alignment store doesn't exist yet, or when ``lacing`` /
    ``lacing.tracks`` aren't installed (so callers can degrade
    gracefully).
    """
    align_path = project.root / "lyrics" / "alignment.annot"
    if not align_path.exists():
        return []
    try:
        from lacing import SqliteStore
        from lacing.tracks.subtitle import SubtitleTrack
    except ImportError:
        return []

    store = SqliteStore(str(align_path))
    try:
        track = SubtitleTrack(store, asset_id=asset_id)
        out: list[WordTimingTuple] = []
        for ann in track.words_in(start_s, end_s):
            text = ann.body.get("text", "")
            if not text:
                continue
            ws = ann.reference.interval.start.to_seconds()
            we = ann.reference.interval.end.to_seconds()
            we = max(ws, we)
            out.append((text, ws, we))
        return out
    finally:
        store.close()


def shifted_word_timings(
    timings: Sequence[WordTimingTuple], *, offset_s: float
) -> list[WordTimingTuple]:
    """Shift each timing by ``-offset_s`` (clamping starts at 0).

    Use when handing absolute song-time timings to a tool that wants
    them relative to a shot's audio slice (where t=0 is the slice's
    start). Mirrors ``audio[shot.start_s:shot.end_s]`` cropping.
    """
    out: list[WordTimingTuple] = []
    for text, ws, we in timings:
        ws_shifted = max(0.0, ws - offset_s)
        we_shifted = max(ws_shifted, we - offset_s)
        out.append((text, ws_shifted, we_shifted))
    return out


# --- progress events ----------------------------------------------------


def progress_event_to_dict(event) -> dict[str, Any]:
    """Serialize a ``falaw.ProgressEvent`` into a JSON-safe dict.

    The shape matches the records :func:`muvid.events.log_fal_events_to`
    writes — pulled out as a public helper so other consumers (a UI
    SSE stream, a remote telemetry sink) don't have to rebuild it.
    """
    return {
        "kind": event.kind,
        "application": event.application,
        "call_id": event.call_id,
        "message": event.message or "",
        "pct": event.pct,
        "elapsed_s": event.elapsed_s,
    }
