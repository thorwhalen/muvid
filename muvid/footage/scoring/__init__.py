"""Footage scoring — per-clip score tracks on the shared song-time grid (thorwhalen/muvid#13).

Resolve every clip's every metric onto ONE fixed-rate song-time grid → a tensor
``S[clip, frame, metric]`` that BOTH the auto composer (the ``weighted`` Viterbi selector in
:mod:`muvid.footage.select_score`) and the Phase-2 multichannel editor read. This subpackage
holds the grid/normalization data model (:mod:`~muvid.footage.scoring.grid`), the per-metric
extractors, and the orchestrator that runs them.

**Import-safe.** This ``__init__`` imports nothing heavy — cv2/mediapipe/librosa/torch live
behind the ``muvid[scoring]`` (and ``muvid[scoring-lipsync]``) extras and are lazy-imported
inside function bodies. The orchestrator :func:`score_project` is exposed lazily so
``import muvid.footage.scoring`` stays light.

See ``misc/docs/footage_scoring_design.md`` (LOCKED decisions) for the architecture: the
torch-free core (quality + motion-beat + segment + the selector) is the default, prod-safe
tier; the lip-sync tier (Demucs + SyncNet) is opt-in and off by default (CC-BY-NC weights +
OOM risk on the memory-fragile prod box).
"""

from __future__ import annotations

__all__ = ["score_project", "list_available_extractors", "DEFAULT_METRICS"]


def __getattr__(name):
    # Lazy attribute access keeps `import muvid.footage.scoring` free of numpy/cv2.
    if name in ("score_project", "list_available_extractors", "DEFAULT_METRICS"):
        from muvid.footage.scoring.orchestrator import (  # noqa: F401
            DEFAULT_METRICS,
            list_available_extractors,
            score_project,
        )

        return {
            "score_project": score_project,
            "list_available_extractors": list_available_extractors,
            "DEFAULT_METRICS": DEFAULT_METRICS,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
