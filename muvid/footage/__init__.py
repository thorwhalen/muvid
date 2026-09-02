"""Footage-aligned music video — align several device recordings of one song, assemble.

The engine behind muvid's ``music_video`` genre (thorwhalen/reelee#229): a user uploads
several video clips, each a *different-device* recording of the SAME fixed song (so the
captured audio is time-shifted, noisy, drifting — never a perfect match). Each clip is
aligned to the clean song's timeline by audio cross-correlation (``mixing.audio``), and a
chosen or auto-selected edit is assembled into a music video over the clean song audio.

Modules:

- :mod:`muvid.footage.align` — align a clip set to the song (thin over ``mixing.audio``).
- :mod:`muvid.footage.strategy` — the pluggable ``SelectionStrategy`` registry that turns
  alignments into an EDL (which clip covers which span of the song).
- :mod:`muvid.footage.edl` — the ``validate_edl`` SSOT + EDL/alignment data types,
  including the per-cut ``CropWindow`` (the EDL's spatial half, muvid#60).
- :mod:`muvid.footage.assemble` — the bounded single-ffmpeg-pass assembler.
- :mod:`muvid.footage.workspace` — the per-user stateful project (song + clips + manifest).
"""

from muvid.footage.edl import (
    FootageAlignment,
    EdlEntry,
    CropWindow,
    validate_edl,
    derive_cuts,
)
from muvid.footage.strategy import (
    SelectionStrategy,
    register_selection_strategy,
    list_strategies,
    resolve_strategy,
    select_edl,
)

__all__ = [
    "FootageAlignment",
    "EdlEntry",
    "CropWindow",
    "validate_edl",
    "derive_cuts",
    "SelectionStrategy",
    "register_selection_strategy",
    "list_strategies",
    "resolve_strategy",
    "select_edl",
]
