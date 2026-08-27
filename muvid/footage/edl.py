"""EDL data types + the ``validate_edl`` single-source-of-truth gate.

An **EDL** (edit decision list) says which clip covers which span of the SONG timeline:
an ordered list of :class:`EdlEntry` ``{song_start, song_end, clip_id}``, where an empty/
null ``clip_id`` is an explicit **gap** (no footage — rendered as fill). A strategy or a
caller produces one; :func:`fill_gaps` pads it to the full song (head/tail/interior holes
become gap entries); :func:`validate_edl` is the ONE gate every path (explicit and
auto/strategy) passes before any cutting, and :func:`derive_cuts` centralizes the sign
convention (``clip_in = song_start - offset_s``) so no third-party strategy can desync the
result. Times are seconds (float).

An entry may carry an optional :class:`Transition` — a blend IN from its predecessor
instead of a hard cut (muvid#34). It is an annotation on a boundary that already exists
implicitly, so spans stay one-per-song-span and nothing about reading an EDL changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

#: Spans shorter than this (seconds) are treated as coincident / zero — guards float noise.
_EPS = 1e-3
#: Cap on EDL entries (env-tunable), on BOTH the auto and the explicit-``edl`` paths. The
#: assembler runs one bounded ffmpeg per PART (memory O(1) in cut count), so this caps
#: total WORK — N encoder invocations on a shared box — not a single command's inputs.
#: It counts footage CUTS, and parts are no longer one-per-cut: a transitioned boundary
#: adds a part, so a fully-transitioned edit approaches ``2 * cuts`` invocations. The cap
#: stays on cuts deliberately — it is the number the caller wrote and can act on — but
#: read it as bounding work only to within that factor.
MAX_EDL_ENTRIES = int(os.environ.get("MUVID_FOOTAGE_MAX_EDL_ENTRIES", "500"))

#: Where a transition sits relative to the cut it is on: the fraction taken from
#: BEFORE the boundary. ``0.5`` is centred — each side supplies ``duration/2`` of
#: extra source, and the perceptual midpoint of the blend lands exactly on the
#: authored boundary.
#:
#: Not a tuning knob. A trailing transition (``1.0``) would need spare coverage on
#: only one side and is therefore satisfiable at more boundaries — but it puts the
#: perceived cut ``duration/2`` LATE on every transition, which is precisely what
#: the beat-snapped Viterbi selector in :mod:`muvid.footage.select_score` exists to
#: prevent. Centring is also the NLE convention ("centered on cut").
TRANSITION_SPLIT = 0.5

#: Shortest transition that is not a lie. Below roughly one frame the xfade emits
#: no blended frames at all and the "transition" is a hard cut wearing a label —
#: the same class of silent no-op as muvid#44's ``camera: {move: static}``, which
#: `an` refused precisely so it could not happen quietly. The renderer ALSO warns
#: if a transition rounds to zero frames at the actual render fps, which this
#: song-time floor cannot know.
MIN_TRANSITION_S = float(os.environ.get("MUVID_FOOTAGE_MIN_TRANSITION_S", "0.04"))

#: The transition curves muvid offers. A curated subset of ffmpeg's 58 ``xfade``
#: transitions, not all of them: this is a vocabulary we own and must keep working
#: across ffmpeg builds, and an unrecognised name is refused at
#: :func:`validate_edl` rather than discovered as an ffmpeg error three stages
#: later. Same posture as the ``an`` camera-move table — translate at the boundary,
#: never pass a name through and hope.
TRANSITION_CURVES = frozenset(
    {
        "fade",
        "fadeblack",
        "fadewhite",
        "dissolve",
        "wipeleft",
        "wiperight",
        "wipeup",
        "wipedown",
        "slideleft",
        "slideright",
        "slideup",
        "slidedown",
        "smoothleft",
        "smoothright",
        "circleopen",
        "circleclose",
    }
)


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
class Transition:
    """How this entry blends IN from its predecessor.

    A small frozen record rather than a bare float, so ``curve`` (and anything
    after it) is an additive field rather than another shape change on a wire
    record.

    The transition belongs to the entry it is *on* — an annotation of that entry's
    **entrance**. That is what keeps the EDL's defining invariant intact: spans
    stay one-per-song-span, so the thing you can read is still the thing that
    renders. (The two rejected shapes both break it: silently widening a span
    makes the EDL stop meaning what it says, and a synthetic third entry between
    two real ones makes authoring one transition a three-entry edit — the class of
    mistake muvid#35 was filed about — while doubling the wire record count of a
    heavily-cut edit for a purely presentational reason.)

    A transition on the FIRST entry is rejected, not ignored: there is no
    predecessor to blend from, so it is a request that cannot be honoured, and
    honouring nothing quietly is how a direction gets lost.
    """

    duration_s: float
    curve: str = "fade"

    def to_dict(self) -> dict:
        return {"duration_s": self.duration_s, "curve": self.curve}

    @classmethod
    def from_dict(cls, d: dict) -> "Transition":
        return cls(duration_s=float(d["duration_s"]), curve=str(d.get("curve", "fade")))


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
    #: Blend in from the predecessor rather than hard-cutting. ``None`` (the
    #: default) is a hard cut, so an EDL written before this field existed is a
    #: valid EDL now, and one written with it, read by older code, renders hard
    #: cuts — degraded, never wrong, in both directions.
    transition: "Transition | None" = None

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
    #: Carried through from the EDL entry, unchanged. `derive_cuts` gains no
    #: transition arithmetic: the extra source material a blend needs is measured
    #: in FRAMES at the render fps, which only the assembler knows.
    transition: "Transition | None" = None

    @property
    def duration(self) -> float:
        return self.song_end - self.song_start


def _as_entry(e) -> EdlEntry:
    if isinstance(e, EdlEntry):
        return e
    clip_id = e["clip_id"]
    raw = e.get("transition")
    if raw is None:
        transition = None
    else:
        # RAISES on anything malformed, deliberately. `_as_entry` serves the
        # explicit `edl=` argument — a caller's request — where dropping a
        # direction silently is the bug. The lacing bridge's read is the opposite
        # posture (skip, never crash) because that is a browser's output, not a
        # request; the two are not inconsistent, they have different authors.
        try:
            transition = Transition.from_dict(raw)
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError(
                f"EDL entry transition is malformed ({raw!r}): it must be an "
                "object with a numeric 'duration_s' and an optional 'curve'."
            ) from exc
    return EdlEntry(
        song_start=float(e["song_start"]),
        song_end=float(e["song_end"]),
        # JSON callers write a gap as clip_id: null; internally it is "".
        clip_id="" if clip_id is None else str(clip_id),
        transition=transition,
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

    **A transition blends in from whatever precedes it on the timeline, which after
    this function may be an inserted gap** — i.e. a fade from black. That is a
    consequence worth stating rather than a bug to guard: a transition annotates its
    entry's ENTRANCE, and the entrance is wherever the entry actually starts once the
    edit spans the whole song. So a transition on the caller's first entry is
    REJECTED when footage starts at t=0 (nothing precedes it) and becomes a fade-in
    from black when it does not (the head gap precedes it). Both follow from the same
    rule; the tests pin both so the coherence stays deliberate.
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
      ``clip_in + span_duration <= clip_duration`` (the clip actually contains that span);
    - a :class:`Transition` is on an entry that HAS a predecessor, names a known curve,
      is at least :data:`MIN_TRANSITION_S` long, fits in song time counting BOTH
      transitions an entry can carry, and fits in each side's aligned coverage — see
      :func:`_validate_transition`.

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
    prev: EdlEntry | None = None
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
        if e.transition is not None:
            _validate_transition(i, e, prev, by_id)
        prev_end = e.song_end
        prev = e
    return entries


def _span(e: EdlEntry) -> float:
    return e.song_end - e.song_start


def _validate_transition(i, e, prev, by_id) -> None:
    """The four things a transition has to satisfy. Raises ``ValueError``.

    Split out only for length; it is part of :func:`validate_edl`, which remains
    the ONE gate. Nothing else may check these.
    """
    t = e.transition
    if prev is None:
        raise ValueError(
            f"EDL entry {i} carries a transition but is the first entry — there is "
            "nothing to blend in FROM. A transition annotates an entry's entrance, "
            "so the first entry of the edit cannot have one. (An entry preceded by "
            "a gap CAN: it blends in from black.)"
        )
    if t.curve not in TRANSITION_CURVES:
        raise ValueError(
            f"EDL entry {i}: unknown transition curve {t.curve!r}; "
            f"muvid offers {sorted(TRANSITION_CURVES)}."
        )
    if t.duration_s < MIN_TRANSITION_S:
        raise ValueError(
            f"EDL entry {i}: transition duration {t.duration_s:.3f}s is below the "
            f"{MIN_TRANSITION_S:.3f}s floor. A transition shorter than about a frame "
            "renders as a hard cut, which is a direction that silently did nothing."
        )

    # (1) It must FIT IN SONG TIME. An entry can be claimed from BOTH ends — its own
    # incoming transition eats the head of its span, its successor's eats the tail —
    # and checking each transition against a whole span in isolation lets two
    # legal-looking transitions together consume more than the span between them.
    #
    # Two checks, each looking ONE way, and that is exactly enough. A forward term
    # for the successor's claim would be dead: entry i+1's backward check evaluates
    # the identical inequality, so it could never be the sole cause of a rejection.
    # (Mutation testing is how that was established — the term was written first and
    # deleting it left the suite green.)
    lead, trail = t.duration_s * TRANSITION_SPLIT, t.duration_s * (1 - TRANSITION_SPLIT)
    prev_own_trail = (
        prev.transition.duration_s * (1 - TRANSITION_SPLIT) if prev.transition else 0.0
    )
    if prev_own_trail + lead > _span(prev) + _EPS:
        raise ValueError(
            f"EDL entry {i}: its transition needs {lead:.3f}s from the END of entry "
            f"{i - 1}, whose {_span(prev):.3f}s span"
            + (
                f" already gives {prev_own_trail:.3f}s to its own transition. "
                "Together they exceed it."
                if prev_own_trail
                else " is shorter than that."
            )
        )
    if trail > _span(e) + _EPS:
        raise ValueError(
            f"EDL entry {i}: its transition needs {trail:.3f}s from the START of its "
            f"own {_span(e):.3f}s span, which is shorter than that."
        )

    # (2) It must fit in each side's aligned COVERAGE, evaluated PER SIDE. A blend
    # reads past its own span into the neighbour's source material — legitimately,
    # which is why this is not the overlap rule above. A gap side is skipped: its
    # black source is synthetic and re-parameterizable, so it can always supply the
    # window.
    if not prev.is_gap:
        a = by_id[prev.clip_id]
        # UNCLAMPED, deliberately: `derive_cuts` clamps `clip_in` at 0 for the
        # renderer, and reading a clamped value here would let a short clip pass
        # by silently pretending it starts earlier than it does.
        end_in_clip = (prev.song_start - a.offset_s) + _span(prev)
        if end_in_clip + trail > a.duration_s + _EPS:
            raise ValueError(
                f"EDL entry {i}: the transition needs {trail:.3f}s of clip "
                f"{prev.clip_id!r} PAST the end of entry {i - 1}'s span, but the clip "
                f"ends {a.duration_s - end_in_clip:.3f}s after it."
            )
    if not e.is_gap:
        a = by_id[e.clip_id]
        start_in_clip = e.song_start - a.offset_s
        if start_in_clip - lead < -_EPS:
            raise ValueError(
                f"EDL entry {i}: the transition needs {lead:.3f}s of clip "
                f"{e.clip_id!r} BEFORE its span starts, but the span begins only "
                f"{start_in_clip:.3f}s into the clip."
            )


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
                    transition=e.transition,
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
                transition=e.transition,
            )
        )
    return cuts
