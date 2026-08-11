"""EDL data types + the ``validate_edl`` single-source-of-truth gate.

An **EDL** (edit decision list) says which clip covers which span of the SONG timeline:
an ordered list of :class:`EdlEntry` ``{song_start, song_end, clip_id}``, where an empty/
null ``clip_id`` is an explicit **gap** (no footage — rendered as fill). A strategy or a
caller produces one; :func:`fill_gaps` pads it to the full song (head/tail/interior holes
become gap entries); :func:`validate_edl` is the ONE gate every path (explicit and
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
#: Cap on EDL entries (env-tunable), on BOTH the auto and the explicit-``edl`` paths. The
#: assembler runs one bounded ffmpeg per cut (memory O(1) in cut count), so this now caps
#: total WORK (N encoder invocations on a shared box), not a single command's inputs.
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
    """One cut: show ``clip_id`` over the song span ``[song_start, song_end]``.

    ``clip_id == ""`` is a **gap entry** — no footage covers this span, and the renderer
    fills it (black in v1). Gaps are explicit entries rather than absences so that an EDL
    is always contiguous over its span, every span of the song is accounted for by
    exactly one entry, and "no clip here" survives a JSON round trip (``clip_id: null``).
    """

    song_start: float
    song_end: float
    clip_id: str

    @property
    def is_gap(self) -> bool:
        return not self.clip_id


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
    clip_id = e["clip_id"]
    return EdlEntry(
        song_start=float(e["song_start"]),
        song_end=float(e["song_end"]),
        # JSON callers write a gap as clip_id: null; internally it is "".
        clip_id="" if clip_id is None else str(clip_id),
    )


def fill_gaps(entries: Sequence, song_duration: float) -> list[EdlEntry]:
    """Make an edit span the WHOLE song by inserting explicit gap entries.

    Three holes become gap entries (muvid#21 items 1+2, one mechanism): the head
    (``[0, first.song_start]`` — without this, footage starting at t=5 s silently loses
    the intro), interior holes between consecutive entries, and the tail
    (``[last.song_end, song_duration]``). Entries are sorted by start; overlap and
    containment stay :func:`validate_edl`'s business — call this BEFORE it, never after.

    An empty selection stays empty (an all-black video is not a first cut worth
    rendering silently — the caller decides what "no usable footage" means).
    """
    ordered = sorted((_as_entry(e) for e in entries), key=lambda e: e.song_start)
    if not ordered:
        return []
    # Range-check the CALLER's entries before inserting anything: a bogus span (say
    # [32, 35] of a 30 s song) would otherwise make validate_edl report the failure on
    # the phantom gap inserted to reach it — an entry the caller never wrote. Note the
    # entries are also SORTED here: an out-of-order list is normalised, not rejected.
    for e in ordered:
        if e.song_start < -_EPS or e.song_end > song_duration + _EPS:
            raise ValueError(
                f"EDL entry [{e.song_start:.3f}, {e.song_end:.3f}] is outside the "
                f"song [0, {song_duration:.3f}]"
            )
    out: list[EdlEntry] = []
    cursor = 0.0
    for e in ordered:
        if e.song_start - cursor > _EPS:
            out.append(EdlEntry(cursor, e.song_start, ""))
        out.append(e)
        cursor = max(cursor, e.song_end)
    if song_duration - cursor > _EPS:
        out.append(EdlEntry(cursor, song_duration, ""))
    return out


def validate_edl(
    edl: Sequence,
    alignments: Sequence[FootageAlignment],
    song_duration: float,
) -> list[EdlEntry]:
    """Validate an EDL (from a strategy OR a caller) — the ONE gate before any cutting.

    Enforces, raising ``ValueError`` with a specific message otherwise:

    - non-empty; every non-gap ``clip_id`` is a known alignment;
    - each span is positive and lies within ``[0, song_duration]``;
    - spans are in ascending order and **non-overlapping**;
    - spans are **contiguous** (gapless) — a hole must be an explicit gap entry
      (``clip_id`` empty/null, rendered as fill), which :func:`fill_gaps` inserts; an
      *implicit* hole is still an error, so nothing goes missing silently;
    - each non-gap span lies within its clip's aligned coverage, AND the derived
      ``clip_in = song_start - offset`` satisfies ``0 <= clip_in`` and
      ``clip_in + span_duration <= clip_duration`` (the clip actually contains that span).

    Returns the normalized list of :class:`EdlEntry`.
    """
    entries = [_as_entry(e) for e in edl]
    if not entries:
        raise ValueError("EDL is empty — nothing to assemble.")
    # Count FOOTAGE cuts only: fill_gaps can as much as double the entry count with gap
    # entries the caller never wrote, and being told "502 entries" after submitting 251
    # is undiagnosable. Gaps are structurally bounded by footage cuts + 1, so the total
    # work stays bounded by ~2x the cap either way.
    n_cuts = sum(1 for e in entries if not e.is_gap)
    if n_cuts > MAX_EDL_ENTRIES:
        raise ValueError(
            f"EDL has {n_cuts} footage cuts; the {MAX_EDL_ENTRIES}-cut limit is exceeded"
        )
    by_id = {a.clip_id: a for a in alignments}

    prev_end = None
    for i, e in enumerate(entries):
        if not e.is_gap and e.clip_id not in by_id:
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
                    "is covered by no entry. A span with no footage must be an explicit "
                    "gap entry (clip_id null) — fill_gaps() inserts them."
                )
        if not e.is_gap:
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
        if e.is_gap:
            # A gap has no source: the assembler renders fill (black) for the span.
            cuts.append(
                AssemblyCut(
                    song_start=e.song_start,
                    song_end=e.song_end,
                    clip_id="",
                    clip_in=0.0,
                    clip_path="",
                )
            )
            continue
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
