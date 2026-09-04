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
class CropWindow:
    """A rectangle to take from the source frame, as fractions of its width/height.

    Normalised rather than pixels so one window is valid for every clip in a
    multi-device edit regardless of its resolution, and so an EDL survives a source
    being re-encoded at a different size. ``(0, 0, 1, 1)`` is the whole frame.

    The convention is ``burns.Rect``'s, deliberately — top-left origin, window
    fraction — so a crop authored here and a Ken Burns path computed there
    interoperate with no rename table.

    This is the spatial half the EDL lacked: without it every source is letterboxed
    onto the canvas, so a portrait clip in a landscape edit is ~68% black bars and a
    caller has no way to say which two-thirds of the frame to keep. That choice is
    editorial — on a real 478x850 clip of dancers a whole body does not fit in a
    full-width 16:9 window at all (315-380px of subject into 269px), so "heads or
    feet" is a decision per cut, not a default.
    """

    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, d: dict) -> "CropWindow":
        return cls(x=float(d["x"]), y=float(d["y"]), w=float(d["w"]), h=float(d["h"]))


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
    #: Take only this rectangle of the source frame. ``None`` keeps the whole frame
    #: letterboxed onto the canvas, which is what every EDL written before this
    #: field existed means — additive in both directions, like ``transition``.
    crop: "CropWindow | None" = None
    #: With ``crop``, makes the window MOVE linearly from ``crop`` to ``crop_end``
    #: across the cut — a pan. Same size as ``crop`` (see :func:`validate_edl`): a
    #: window that changes size mid-cut resizes the filter's output every frame,
    #: which is a different and much less robust thing than a pan. A push-in is
    #: expressed as a *different* fixed window on the *next* cut, or — since the
    #: ``looks`` seam below — as a ``look`` carrying a ``zoompan`` ramp, which is
    #: the one filter that CAN resize its window mid-cut (muvid#66).
    crop_end: "CropWindow | None" = None
    #: **The ``looks`` seam.** A compiled ffmpeg filter-chain fragment applied to
    #: this cut's picture once it has been normalised onto the canvas. ``None``
    #: (the default) emits nothing at all, so an EDL written before this field
    #: existed renders byte-identically — additive in both directions, like
    #: ``transition`` and ``crop``.
    #:
    #: muvid does not author this string: :mod:`muvid.footage.look` compiles it
    #: from a :class:`looks.Look` or from a punch-in request. That split is the
    #: whole point of the seam — ``looks`` decides what a pixel becomes, muvid
    #: keeps ``-c:v`` and the process shape.
    #:
    #: **A caller may still hand one over, so this field is a trust boundary.**
    #: ``assemble_music_video`` is a live per-caller MCP tool taking free-form
    #: ``edl`` dicts, and this string becomes ffmpeg the renderer runs. So
    #: :func:`_validate_look` gates it against the ALLOWLIST
    #: :data:`LOOK_FILTERS` — not against a list of refusals — and also refuses a
    #: fragment that names a container input, that is more than ONE linear chain,
    #: or that is not lexically closed. The first two of those would break the
    #: bounded-memory invariant the assembler rests on; the allowlist is what
    #: keeps a look from writing this machine's disk.
    look: "str | None" = None

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
    #: Carried through from the EDL entry, unchanged — the assembler compiles these
    #: to a ``crop`` filter, because normalised fractions only become pixels once
    #: you know the source dimensions, which only ffmpeg knows.
    crop: "CropWindow | None" = None
    crop_end: "CropWindow | None" = None
    #: Carried through from the EDL entry, unchanged and already validated — the
    #: ``looks`` seam. The assembler splices it into the ONE filter template both
    #: of its render sites share, so a look lands identically on a solo cut and on
    #: each side of a blended boundary. See :attr:`EdlEntry.look`.
    look: "str | None" = None

    @property
    def duration(self) -> float:
        return self.song_end - self.song_start


def _as_crop(raw, field: str) -> "CropWindow | None":
    """Parse one crop field. RAISES on anything malformed, like the transition read.

    Same reasoning: ``_as_entry`` serves the caller's explicit ``edl=`` argument, and
    dropping a requested framing silently is the bug — a caller who asked for the
    bottom third and got the whole letterboxed frame has no way to tell.
    """
    if raw is None:
        return None
    if isinstance(raw, CropWindow):
        return raw
    try:
        return CropWindow.from_dict(raw)
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError(
            f"EDL entry {field} is malformed ({raw!r}): it must be an object with "
            "numeric 'x', 'y', 'w' and 'h', as fractions of the source frame."
        ) from exc


def _as_look(raw) -> "str | None":
    """Parse the look field. RAISES on a non-string, like the crop read.

    Same posture and the same reason: ``_as_entry`` serves the caller's explicit
    ``edl=`` argument, so dropping a requested look silently is the bug. The
    *content* of the string is :func:`_validate_look`'s business — this only
    settles the type, because ``validate_edl`` is the ONE gate and a second one
    here would be a second place to keep in agreement.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(
            f"EDL entry look is malformed ({raw!r}): it must be a string — one "
            "compiled ffmpeg filter chain, as muvid.footage.look emits."
        )
    return raw


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
        crop=_as_crop(e.get("crop"), "crop"),
        crop_end=_as_crop(e.get("crop_end"), "crop_end"),
        look=_as_look(e.get("look")),
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
    - a ``look`` (the ``looks`` seam) names only filters in :data:`LOOK_FILTERS`,
      is ONE lexically-closed linear filter chain, names no container input, and is
      not on a gap — see :func:`_validate_look`, which is the trust boundary for a
      caller-supplied filter string;
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
        if e.crop is not None or e.crop_end is not None:
            _validate_crop(i, e)
        if e.look is not None:
            _validate_look(i, e)
        prev_end = e.song_end
        prev = e
    return entries


def _span(e: EdlEntry) -> float:
    return e.song_end - e.song_start


def _validate_crop(i, e) -> None:
    """The four things a crop has to satisfy. Raises ``ValueError``.

    Split out only for length; it is part of :func:`validate_edl`, which remains
    the ONE gate. Nothing else may check these.
    """
    if e.crop is None:
        raise ValueError(
            f"EDL entry {i} has crop_end but no crop. crop_end is where the window "
            "MOVES TO; without a starting window there is nothing to move."
        )
    for name, c in (("crop", e.crop), ("crop_end", e.crop_end)):
        if c is None:
            continue
        if c.w <= 0 or c.h <= 0:
            raise ValueError(
                f"EDL entry {i}: {name} has a non-positive size ({c.w}x{c.h}). "
                "A crop window is a fraction of the source frame, so w and h must "
                "be > 0."
            )
        if c.x < -_EPS or c.y < -_EPS or c.x + c.w > 1 + _EPS or c.y + c.h > 1 + _EPS:
            raise ValueError(
                f"EDL entry {i}: {name} ({c.x:.3f}, {c.y:.3f}, {c.w:.3f}, {c.h:.3f}) "
                "falls outside the source frame. These are FRACTIONS: x, y >= 0 and "
                "x+w, y+h <= 1."
            )
    if e.is_gap:
        raise ValueError(
            f"EDL entry {i} is a gap but carries a crop. A gap has no source frame "
            "to take a rectangle out of — the assembler fills it synthetically."
        )
    if e.crop_end is not None and (
        abs(e.crop_end.w - e.crop.w) > _EPS or abs(e.crop_end.h - e.crop.h) > _EPS
    ):
        # `crop` cannot vary its output size at all: `w` and `h` are evaluated ONCE,
        # at configure time, when `t` is still NAN. So a window that CHANGES SIZE
        # across the cut either refuses to configure or — the quiet, dangerous case —
        # exits 0 and renders every frame at one wrong size. (It does not re-init per
        # frame; an earlier version of this comment said so, and also blamed the same
        # non-existent behaviour on `zoompan`, which can in fact resize.)
        # A push-in is expressed as a different fixed window on the next cut.
        raise ValueError(
            f"EDL entry {i}: crop_end must be the same SIZE as crop "
            f"({e.crop.w:.3f}x{e.crop.h:.3f}, got {e.crop_end.w:.3f}x{e.crop_end.h:.3f}). "
            "crop_end pans the window; it does not resize it. A window that "
            "GROWS is a punch-in: express it as a `look` (muvid.footage.look."
            "punch_in), which compiles to `zoompan` — the one filter that can."
        )


#: Characters a look fragment may not carry as SYNTAX. Each turns the fragment
#: from a filter *chain* into a filter *graph*, and the assembler splices it into
#: a larger chain with commas — so ``a,b;c,d`` spliced into ``X,<frag>,Y`` becomes
#: a different graph than either side wrote. ``[`` and ``]`` are how a graph names
#: a pad, which is also how it would reach a second decoder (``[1:v]``) and take
#: the bounded-memory invariant with it. ``looks`` escapes all three with a single
#: backslash (verified against ``looks.escape_filter_value``), so an escaped one
#: inside a path stays legal and only a bare one is refused.
_LOOK_FORBIDDEN = "[];"

#: The filters a ``look`` may name. **An allowlist, because a look is executable
#: ffmpeg arriving from a remote caller.**
#:
#: ``assemble_music_video`` is a live MCP tool on the per-caller reelee AV
#: connector and its ``edl`` argument is free-form dicts, so this string is
#: attacker-supplied input to a process that can write the host's filesystem.
#: Measured, on this branch before the allowlist existed: a look of
#: ``metadata=mode=print:file=<any path the renderer can write>`` passed the gate,
#: rendered normally, returned a success payload, and truncated the named file to
#: zero bytes. ``deshake=filename=`` is a second, structurally different write
#: primitive; ``movie=``/``amovie=`` open an unaccounted container; ``sendcmd``,
#: ``signature``, ``ssim`` and ``psnr`` each name a file of their own.
#:
#: A blocklist cannot close that — there are ~481 filters and the dangerous ones
#: have nothing lexical in common. So the rule is the one this module already uses
#: for :data:`TRANSITION_CURVES` and that ``an``'s camera table uses for moves:
#: **a curated vocabulary we own, refused at the gate rather than discovered as an
#: ffmpeg side effect three stages later.**
#:
#: Two groups, and the split is the maintenance rule:
#:
#: - **Compiled** — every filter the two compilers on this seam can emit:
#:   :mod:`muvid.footage.look` (``zoompan``/``crop``/``scale``/``setpts``, via
#:   ``looks.compile_motion``) and ``looks``' registered ffmpeg implementations
#:   (their declared ``ImplRef.requires_filters``). This set is *pinned against
#:   ``looks`` by a test*, deliberately rather than derived from it at import
#:   time: deriving would let a new ``looks`` effect widen muvid's remote-input
#:   surface silently, where the test makes it a decision someone records here.
#: - **Hand-authored** — ``hue``, the one filter this repo's own docstrings reach
#:   for and nothing compiles. It is LGPL, takes no path, and is what a person
#:   writes when they want "desaturate that shot".
#:
#: **What earns a place here**, and it is checked by
#: ``tests/test_edl_look.py::test_no_allowlisted_filter_can_name_a_file``: the
#: filter must declare no filesystem-path option at all. ``lut3d``'s ``file`` is
#: the single recorded exception (:data:`_LOOK_FILE_OPTIONS`) — it *loads* a
#: ``.cube``, which is how ``looks``' flagship grade reaches its LUT, and it reads
#: rather than writes. Nothing else may name a path, so adding a filter here is a
#: two-place edit and the second place is a measurement of the real binary.
#:
#: **What this does NOT bound**, stated because a partial claim is worse than
#: none. An allowlisted filter can still be given absurd PARAMETERS, and the two
#: that matter are measured rather than guessed at:
#:
#: - **Frame size is memory, and THREE options set it**, not one. Measured on
#:   ffmpeg 9.0.1, three frames from a 64x48 source, peak RSS:
#:
#:   =====================================  =========
#:   look                                   peak RSS
#:   =====================================  =========
#:   ``scale=64:48``                        10 MB
#:   ``scale=64:48,scale=8000:8000``        300 MB
#:   ``zoompan=d=1:s=8000x8000:fps=25``     289 MB
#:   ``scale=w='iw*80':h='ih*80'``          112 MB
#:   =====================================  =========
#:
#:   All four are ACCEPTED by this gate. An earlier version of this note named
#:   only ``scale``, which under-enumerated the surface it exists to describe:
#:   ``zoompan``'s ``s`` is a second lever of the same magnitude, and it is
#:   allowlisted precisely because ``muvid.footage.look.punch_in`` needs it.
#:   Not closed here because a correct bound is RELATIVE TO THE CANVAS — which
#:   this function is not given — and because ``iw*80`` is a legal width, so a
#:   literal cap is not the whole answer. Tracked as muvid#76.
#: - ``lut3d=file=`` will *attempt* to open any path the renderer can read. It
#:   cannot write, and a non-``.cube`` file fails to parse.
#:
#: Both are failures inside the caller's OWN render. Writing to someone else's
#: disk is the class this closes.
LOOK_FILTERS = frozenset(
    {
        # -- compiled: muvid.footage.look, via looks.compile_motion --
        "zoompan",
        "crop",
        "scale",
        "setpts",
        # -- compiled: looks' registered ffmpeg implementations --
        "bilateral",
        "boxblur",
        "colorchannelmixer",
        "colorlevels",
        "eq",
        "gblur",
        "lut3d",
        "lutrgb",
        "lutyuv",
        "unsharp",
        # `null` and `pad` are emitted by ``looks``' geometry effects and declared
        # by NONE of them: ``fill``/``fit``/``stretch`` each declare
        # ``requires_filters=("scale",)`` and then compile to `null` when the
        # target already IS the clip size, and to `scale,pad=…` when it
        # letterboxes. Found by COMPILING every effect, not by reading the
        # registry — which is why the drift test is a floor on this set and not a
        # definition of it, and why looks' declaration is worth fixing upstream.
        "null",
        "pad",
        # -- hand-authored --
        "hue",
    }
)

#: The only ``(filter, option)`` pair on :data:`LOOK_FILTERS` allowed to name a
#: path, with the reason it is allowed: ``lut3d`` LOADS a ``.cube``, which is the
#: whole of ``looks``' ``lut3d``/``gradient_map`` effect, and loading is a read.
_LOOK_FILE_OPTIONS = {("lut3d", "file")}


class _LookSyntaxError(ValueError):
    """A look fragment that is not lexically CLOSED. Carries the open character."""

    def __init__(self, char: str):
        self.char = char
        super().__init__(char)


def _significant(s: str):
    r"""Yield ``(index, char)`` for each character ffmpeg reads as SYNTAX.

    Models both of ffmpeg's escape mechanisms, because modelling only one is a
    silent hole: ``av_get_token`` treats ``\`` as escaping the next character
    **and** copies everything between single quotes literally — inside quotes a
    backslash is an ordinary character and a terminator is swallowed. So
    ``crop=x='min(a,b)'`` is ONE filter, not two, and a check that walks only
    backslashes splits it in the middle.

    Raises:
        _LookSyntaxError: if the fragment ends inside a quote, or on a dangling
            backslash. Neither is a character to refuse — it is the fragment
            failing to be CLOSED, which is exactly what "one chain spliceable by
            comma" requires: an open quote swallows the ``,format=yuv420p[a];[1:``
            the transition site appends, and a trailing backslash escapes the
            splice's own comma. Both render fine alone and restructure the graph
            beside a transition (measured: ffmpeg exits 234 with
            ``No option name near 'v]scale=...'``).

    Yields every character whose syntactic role ffmpeg will honour — i.e. all of
    them except the ones an escape or a quoted run hides:

    >>> "".join(c for _, c in _significant("scale=2,hue=s=0"))
    'scale=2,hue=s=0'
    >>> "".join(c for _, c in _significant(r"lut3d=file=a\[b\].cube"))
    'lut3d=file=ab.cube'
    >>> "".join(c for _, c in _significant("crop=x='min(a,b)'"))
    'crop=x='
    """
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            if i + 1 >= len(s):
                raise _LookSyntaxError("\\")
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise _LookSyntaxError("'")
            i = j + 1
            continue
        yield i, c
        i += 1


def _unquote(s: str) -> str:
    r"""``s`` as ffmpeg's tokenizer resolves it — escapes applied, quotes removed.

    Needed for the *name* half of :func:`_look_filter_names`, and its role is to
    **widen** what the allowlist accepts, not to secure it. An earlier version of
    this docstring had that backwards — it claimed a raw-text compare "would be
    bypassed" by ``'metadata'`` or ``\m\e\t\a\d\a\t\a``. Measured, it is not:
    against an ALLOWLIST a raw compare fails **closed**, because neither spelling
    is a member either. What a raw compare loses is the legitimate direction —
    ``h\ue=s=0`` resolves to the allowed ``hue`` and would be refused.

    ======================================  =============  ================
    spelling                                raw compare    with ``_unquote``
    ======================================  =============  ================
    ``metadata=mode=print:file=…``          refused        refused
    ``\m\e\t\a\d\a\t\a=…``                   refused        refused
    ``h\ue=s=0``  (legitimate)              **refused**    accepted
    ======================================  =============  ================

    The security comes from :data:`LOOK_FILTERS` being a closed set. Stating that
    correctly matters: a reader who believes this function is the guard could
    "simplify" by dropping the allowlist and keeping the normalisation, which is
    the one edit that would reopen the hole.

    >>> _unquote(r"h\ue")
    'hue'
    >>> _unquote("'hue'")
    'hue'
    """
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            if i + 1 >= len(s):
                raise _LookSyntaxError("\\")
            out.append(s[i + 1])
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise _LookSyntaxError("'")
            out.append(s[i + 1 : j])
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _first_unescaped(s: str, chars: str) -> "str | None":
    r"""The first character of ``chars`` in ``s`` that ffmpeg would read as syntax.

    >>> _first_unescaped("scale=2,hue=s=0", "[];") is None
    True
    >>> _first_unescaped("[0:v]scale=2", "[];")
    '['
    >>> _first_unescaped(r"lut3d=file=a\[b\].cube", "[];") is None
    True
    >>> _first_unescaped("lut3d=file='a[b].cube'", "[];") is None
    True
    """
    for _, c in _significant(s):
        if c in chars:
            return c
    return None


def _strip_unescaped(s: str) -> str:
    r"""Trim whitespace from the ends — but only whitespace ffmpeg would trim.

    A plain ``.strip()`` here removed an ESCAPED space and left the backslash
    that escaped it, so ``hue\ =s=0`` became ``hue\`` and the lexer raised
    ``_LookSyntaxError("\\")`` from outside the caller's try/except: a lexically
    closed look refused with a message that was a single backslash, reaching an
    MCP caller as the whole explanation.

    >>> _strip_unescaped("  hue  ")
    'hue'
    >>> _strip_unescaped(r"hue\ ")
    'hue\\ '
    """
    significant = {i for i, _ in _significant(s)}
    lo, hi = 0, len(s)
    while lo < hi and s[lo].isspace() and lo in significant:
        lo += 1
    while hi > lo and s[hi - 1].isspace() and hi - 1 in significant:
        hi -= 1
    return s[lo:hi]


def _look_filter_names(look: str) -> "list[str]":
    r"""The filter each link of the chain names, as ffmpeg will resolve it.

    Splits on the commas ffmpeg reads as separators (not on the ones inside a
    quoted expression), then takes each link's name token — everything before its
    first significant ``=``, minus a ``@instance`` label — and resolves it through
    :func:`_unquote`.

    Only **unescaped** whitespace is trimmed, because that is the only kind
    ffmpeg trims: ``hue =s=0`` works (rc=0), while ``\ hue=s=0`` names the
    filter *space-h-u-e* and fails with "No such filter: ' hue'" (rc=8). Both
    measured. A plain ``.strip()`` here resolved the second to ``hue`` and
    accepted it — harmless, since ffmpeg then refused it, but it also meant the
    gate and the binary disagreed about what a fragment says, which is the one
    thing a gate must not do.

    >>> _look_filter_names("scale=2,hue=s=0")
    ['scale', 'hue']
    >>> _look_filter_names("crop=x='min(a,b)':y=0,zoompan=d=1")
    ['crop', 'zoompan']
    >>> _look_filter_names(r"\m\e\t\a\d\a\t\a=mode=print")
    ['metadata']
    >>> _look_filter_names("hue@grade=s=0")
    ['hue']
    """
    breaks = [i for i, c in _significant(look) if c == ","]
    spans, start = [], 0
    for b in breaks + [len(look)]:
        spans.append(look[start:b])
        start = b + 1
    names = []
    for link in spans:
        eq = next((i for i, c in _significant(link) if c == "="), len(link))
        raw = _strip_unescaped(link[:eq])
        at = next((i for i, c in _significant(raw) if c == "@"), len(raw))
        # No `.strip()` AFTER unescaping. `\ ` is an escaped space, so stripping
        # the unescaped result removed the space and left the backslash that
        # escaped it — `_unquote` then raised `_LookSyntaxError("\\")` from
        # OUTSIDE `_validate_look`'s try/except, so a lexically CLOSED look was
        # refused with a message that was a single backslash. Through the MCP
        # tool that reached the caller as the whole explanation.
        names.append(_unquote(raw[:at]))
    return names


def _validate_look(i, e) -> None:
    """What a look fragment has to satisfy. Raises ``ValueError``.

    Split out only for length; it is part of :func:`validate_edl`, which remains
    the ONE gate. Nothing else may check these.

    A look is **executable ffmpeg supplied by a caller**, and
    ``assemble_music_video`` is a live per-caller MCP tool, so this is the
    trust boundary for the whole seam. Four rules:

    - **Only filters muvid names.** :data:`LOOK_FILTERS` is an allowlist, and the
      constant carries why a blocklist cannot work here.
    - **One linear chain.** The fragment is concatenated with commas into a chain
      the assembler already builds, so a graph separator or a pad label makes the
      result mean something neither side wrote. ``[1:v]`` is also how a filter
      reaches a second ``-i``, and a constant number of decoders per invocation is
      the whole of the bounded-memory guarantee muvid#21/#24 bought.
    - **Lexically closed.** An unterminated quote or a trailing backslash is not a
      forbidden character — it is a fragment that means one thing alone and
      another thing spliced, which is the same defect one level down.
    - **Not on a gap.** A gap has no footage; the assembler builds its black fill
      from a synthetic source with its own chain, so a look there would need a
      third splice site and would be styling nothing.

    A look wanting a second SOURCE has nowhere to go, and saying so is the point:
    ``movie=`` — which an earlier version of this message advised — is refused,
    and would not have worked if it were not. It is a zero-input source filter, so
    at the solo site it leaves the preceding chain unconsumed and ffmpeg refuses
    the whole simple filtergraph (*"had 1 input(s) and 2 output(s)"*, measured),
    and at the transition site it opens a second container from inside the
    fragment — the accounting muvid#21/#24 exists to keep. Compositing needs a
    second splice site the assembler does not have.
    """
    look = e.look
    if not look.strip():
        raise ValueError(
            f"EDL entry {i} has an empty look. Omit the field (or pass null) to "
            "render without one — an empty string is a request that cannot be "
            "honoured, and honouring nothing quietly is how a direction gets lost."
        )
    if look.strip() != look:
        raise ValueError(
            f"EDL entry {i}: look has leading or trailing whitespace ({look!r}). "
            "It is spliced verbatim into a filter chain, so trimming it here would "
            "be this module quietly editing an ffmpeg expression."
        )
    if look.startswith(",") or look.endswith(","):
        raise ValueError(
            f"EDL entry {i}: look starts or ends with a comma ({look!r}). The "
            "assembler supplies the separators; a stray one emits an empty filter."
        )
    try:
        bad = _first_unescaped(look, _LOOK_FORBIDDEN)
    except _LookSyntaxError as exc:
        raise ValueError(
            f"EDL entry {i}: look is not lexically closed — it ends "
            + (
                "inside a single-quoted run"
                if exc.char == "'"
                else "on a dangling backslash"
            )
            + f" ({look!r}). ffmpeg's tokenizer would swallow whatever the "
            "assembler splices after it, so the fragment means one thing on a "
            "solo cut and something else on a blended boundary, where it eats "
            "the `,format=yuv420p[a];[1:` that follows and builds a different "
            "graph. Close it."
        ) from None
    if bad is not None:
        raise ValueError(
            f"EDL entry {i}: look contains an unescaped {bad!r} ({look!r}). A look "
            "must be ONE linear filter chain naming no container input: it is "
            "spliced into the per-cut chain, where a pad label or a graph separator "
            "silently builds a different graph, and a second input would add a "
            "decoder per cut — the shape muvid#21/#24 was OOM-killed for. Escape a "
            f"literal {bad!r} inside a path with a backslash "
            "(looks.escape_filter_value does)."
        )
    for name in _look_filter_names(look):
        if name in LOOK_FILTERS:
            continue
        raise ValueError(
            f"EDL entry {i}: look names the filter {name!r}, which muvid does not "
            f"offer ({look!r}). A look is executable ffmpeg reaching a renderer "
            "that can write this machine's disk, and it arrives over a per-caller "
            "tool surface, so the filters are an ALLOWLIST rather than a set of "
            "refusals: muvid offers "
            f"{sorted(LOOK_FILTERS)}. Compile the look with muvid.footage.look "
            "(punch_in / motion / stylize) instead of hand-writing one, and if a "
            "filter genuinely belongs on this seam add it to "
            "muvid.footage.edl.LOOK_FILTERS deliberately — it must name no file."
        )
    if e.is_gap:
        raise ValueError(
            f"EDL entry {i} is a gap but carries a look. A gap has no footage to "
            "style — the assembler fills it synthetically from its own source."
        )


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
                    crop=e.crop,
                    crop_end=e.crop_end,
                    look=e.look,
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
                crop=e.crop,
                crop_end=e.crop_end,
                look=e.look,
            )
        )
    return cuts
