"""EDL data types + the ``validate_edl`` single-source-of-truth gate.

An **EDL** (edit decision list) says which clip covers which span of the SONG timeline:
an ordered list of :class:`EdlEntry` ``{song_start, song_end, clip_id}``. A strategy or a
caller produces one; :func:`validate_edl` is the ONE gate every path (explicit and
auto/strategy) passes before any cutting, and :func:`derive_cuts` centralizes the sign
convention (``clip_in = song_start - offset_s``) so no third-party strategy can desync the
result. Times are seconds (float).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

#: Spans shorter than this (seconds) are treated as coincident / zero — guards float noise.
_EPS = 1e-3
#: Cap on EDL entries (env-tunable). Bounds the assembler's per-cut ffmpeg ``-i`` inputs +
#: filtergraph size on BOTH the auto and the explicit-``edl`` paths, so a caller can't
#: submit hundreds of thousands of 1 ms spans and exhaust fds / ARG_MAX on the shared box.
MAX_EDL_ENTRIES = int(os.environ.get("MUVID_FOOTAGE_MAX_EDL_ENTRIES", "500"))


@dataclass(frozen=True)
class FootageAlignment:
    """Where one uploaded clip sits on the song timeline (muvid's per-clip record).

    Mirrors ``mixing.audio.ClipAlignment`` but keyed by the caller-facing ``clip_id`` and
    JSON-round-trippable (persisted in the project manifest).
    """

    clip_id: str
    offset_s: float
    confidence: float
    duration_s: float
    coverage: tuple[float, float]  # clamped to [0, song_duration]
    #: Whether the clip intersects the song timeline at all. A clip that does NOT is
    #: still recorded — a source must never leave the addressable set as a side effect
    #: of being measured. Selection filters on this; reporting shows it with a reason.
    overlaps: bool = True

    def to_dict(self) -> dict:
        return {
            "clip_id": self.clip_id,
            "offset_s": self.offset_s,
            "confidence": self.confidence,
            "duration_s": self.duration_s,
            "coverage": list(self.coverage),
            "overlaps": self.overlaps,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FootageAlignment":
        cov = d["coverage"]
        return cls(
            clip_id=d["clip_id"],
            offset_s=float(d["offset_s"]),
            confidence=float(d["confidence"]),
            duration_s=float(d["duration_s"]),
            coverage=(float(cov[0]), float(cov[1])),
            # Records written before `overlaps` existed were only ever persisted when
            # they overlapped, so absent means True.
            overlaps=bool(d.get("overlaps", True)),
        )


@dataclass(frozen=True)
class EdlEntry:
    """One cut: show ``clip_id`` over the song span ``[song_start, song_end]``."""

    song_start: float
    song_end: float
    clip_id: str


@dataclass(frozen=True)
class AssemblyCut:
    """A validated cut ready to render: the EDL span + the derived in-point + clip path."""

    song_start: float
    song_end: float
    clip_id: str
    clip_in: (
        float  # seconds into the clip where this span begins (= song_start - offset)
    )
    clip_path: str

    @property
    def duration(self) -> float:
        return self.song_end - self.song_start


def _as_entry(e) -> EdlEntry:
    if isinstance(e, EdlEntry):
        return e
    return EdlEntry(
        song_start=float(e["song_start"]),
        song_end=float(e["song_end"]),
        clip_id=str(e["clip_id"]),
    )


def validate_edl(
    edl: Sequence,
    alignments: Sequence[FootageAlignment],
    song_duration: float,
) -> list[EdlEntry]:
    """Validate an EDL (from a strategy OR a caller) — the ONE gate before any cutting.

    Enforces, raising ``ValueError`` with a specific message otherwise:

    - non-empty; every ``clip_id`` is a known alignment;
    - each span is positive and lies within ``[0, song_duration]``;
    - spans are in ascending order and **non-overlapping**;
    - spans are **contiguous** (gapless) — consecutive spans meet (v1 requires a
      continuous edit; visualizer/black gap-fill is a deliberate follow-up);
    - each span lies within its clip's aligned coverage, AND the derived
      ``clip_in = song_start - offset`` satisfies ``0 <= clip_in`` and
      ``clip_in + span_duration <= clip_duration`` (the clip actually contains that span).

    Returns the normalized list of :class:`EdlEntry`.
    """
    entries = [_as_entry(e) for e in edl]
    if not entries:
        raise ValueError("EDL is empty — nothing to assemble.")
    if len(entries) > MAX_EDL_ENTRIES:
        raise ValueError(
            f"EDL has {len(entries)} entries; the {MAX_EDL_ENTRIES}-cut limit is exceeded"
        )
    by_id = {a.clip_id: a for a in alignments}

    prev_end = None
    for i, e in enumerate(entries):
        if e.clip_id not in by_id:
            raise ValueError(
                f"EDL entry {i} references unknown clip {e.clip_id!r}; "
                f"known: {sorted(by_id)}"
            )
        if e.song_end - e.song_start <= _EPS:
            raise ValueError(
                f"EDL entry {i} has a non-positive span [{e.song_start}, {e.song_end}]"
            )
        if e.song_start < -_EPS or e.song_end > song_duration + _EPS:
            raise ValueError(
                f"EDL entry {i} span [{e.song_start:.3f}, {e.song_end:.3f}] is outside "
                f"the song [0, {song_duration:.3f}]"
            )
        if prev_end is not None:
            if e.song_start < prev_end - _EPS:
                raise ValueError(
                    f"EDL entry {i} overlaps the previous span (starts {e.song_start:.3f} "
                    f"< {prev_end:.3f})"
                )
            if e.song_start > prev_end + _EPS:
                raise ValueError(
                    f"EDL has a gap before entry {i}: [{prev_end:.3f}, {e.song_start:.3f}] "
                    "is covered by no clip. v1 needs a continuous edit — add footage for "
                    "that span or pass an explicit edl."
                )
        a = by_id[e.clip_id]
        clip_in = e.song_start - a.offset_s
        if (
            clip_in < -_EPS
            or clip_in + (e.song_end - e.song_start) > a.duration_s + _EPS
        ):
            raise ValueError(
                f"EDL entry {i}: clip {e.clip_id!r} does not contain song span "
                f"[{e.song_start:.3f}, {e.song_end:.3f}] (its coverage is "
                f"[{a.coverage[0]:.3f}, {a.coverage[1]:.3f}])."
            )
        prev_end = e.song_end
    return entries


def derive_cuts(
    edl: Sequence[EdlEntry],
    alignments: Sequence[FootageAlignment],
    clip_paths: dict,
) -> list[AssemblyCut]:
    """Turn a *validated* EDL into render-ready cuts — the ONE place ``clip_in`` is derived.

    Strategies emit only ``{song_start, song_end, clip_id}``; the sign convention
    ``clip_in = song_start - offset`` lives here (SSOT), so no strategy can desync the cut.
    """
    by_id = {a.clip_id: a for a in alignments}
    cuts = []
    for e in edl:
        a = by_id[e.clip_id]
        if e.clip_id not in clip_paths:
            raise ValueError(f"no stored file for clip {e.clip_id!r}")
        cuts.append(
            AssemblyCut(
                song_start=e.song_start,
                song_end=e.song_end,
                clip_id=e.clip_id,
                clip_in=max(0.0, e.song_start - a.offset_s),
                clip_path=str(clip_paths[e.clip_id]),
            )
        )
    return cuts
