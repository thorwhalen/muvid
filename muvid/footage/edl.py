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
import re
from dataclasses import dataclass
from typing import NamedTuple, Sequence

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

#: How many times the delivery canvas a ``look`` may ask for, PER DIMENSION
#: (env-tunable). So the area bound is ``MAX_LOOK_SCALE ** 2`` — 16x the canvas.
#:
#: **Four, chosen against measurements rather than taste.** Three of them:
#:
#: - ``2`` is the standard supersample and would be the tempting answer, but it
#:   REFUSES a look muvid itself compiles: ``stylize(fill, target="1080x1080")``
#:   on a 640x360 canvas emits ``scale=1920:1080,crop=1080:1080:420:0``, which is
#:   exactly 3x linear. A bound that refuses the seam it protects is not a bound,
#:   it is an outage.
#: - ``3`` accepts that one *exactly on the boundary*, which is one rounding away
#:   from the same outage.
#: - ``4`` caps the worst case at 4x muvid's largest canvas — 7680x4320 — which
#:   measures **184.8 MB** peak RSS, against **327.6 MB** for the
#:   ``scale=8000:8000`` this closes and the ~2 GB ``scale=20000:20000``
#:   extrapolates to. (ffmpeg 9.0.1, three frames from a 64x48 source,
#:   ``/usr/bin/time -l``; the same harness reads 18.8 MB for ``scale=64:48`` and
#:   32.8 MB for ``scale=1920:1080``.)
#:
#: Bigger than the canvas is never useful — the delivered frame IS the canvas, so
#: anything past it is resampled straight back down — which is why a *small*
#: multiple is the whole of the legitimate range.
MAX_LOOK_SCALE = int(os.environ.get("MUVID_FOOTAGE_MAX_LOOK_SCALE", "4"))

#: The canvas :func:`validate_edl` bounds a look against when its caller names
#: none: the element-wise maximum of ``workspace.CANVASES``, so the default is
#: the LOOSEST bound that is still a bound — never an absent one.
#:
#: A ``None`` default would have been the smaller diff and the wrong shape: it
#: makes "nobody threaded the canvas through" indistinguishable from "this look
#: is fine", which is the silent no-op this module refuses everywhere else. The
#: default only covers a direct caller of :func:`validate_edl`.
#:
#: **Every muvid path that can carry a caller's look passes the real canvas, and
#: that is asserted by an AST scan of the call sites** rather than by behaviour.
#: The distinction is load-bearing and an earlier version of this comment glossed
#: it: three of the five sites validate machine-generated entries from
#: ``select_edl``, which has no ``look``, so deleting ``canvas=`` from any of
#: them left the whole suite green — the claim was true of one site and prose
#: about the rest. The scan holds all five (it found the fifth, in
#: ``select_score``), and the one site that deliberately passes no canvas is
#: recorded with its reason plus a test of the premise that reason rests on. See
#: ``tests/test_edl_look_size_bound.py``. Pinned against
#: ``workspace.CANVASES`` by a test rather than imported from it, because
#: ``muvid.footage.edl`` is on the import-safe path and ``workspace`` is not on
#: it for free.
DFLT_LOOK_CANVAS = (1920, 1920)

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
    #: that is not lexically closed, that sets an option muvid has not classified
    #: on one of the four filters that can change the output geometry, or that
    #: asks for a frame more than :data:`MAX_LOOK_SCALE` times the delivery
    #: canvas. The first two of those would break the bounded-memory invariant
    #: the assembler rests on; the allowlist is what keeps a look from writing
    #: this machine's disk; and the last two are what keep an allowlisted filter
    #: from spending 900 MB of it (muvid#75).
    look: "str | None" = None
    #: Whether :attr:`look` READS THE FILTER CLOCK — a punch-in, a pan, anything
    #: whose expressions mention ``t`` / ``in_time`` / ``n``. ``False`` (the
    #: default) means a grade, a LUT, a posterise: a look that draws every frame
    #: the same way and is therefore unaffected by where the clock starts.
    #:
    #: **It exists because the string throws that answer away** (muvid#73). A
    #: transitioned boundary renders as a separate two-input invocation whose
    #: inputs are input-side-seeked to the blend window, and input-side ``-ss``
    #: rebases the filter timeline to 0 — so a moving look **restarts its ramp**
    #: for the length of the blend. Measured on a 3.0 s cut at 25 fps with a
    #: 0.4 s fade and ``punch_in(zoom=1.12)``: the solo part's last frame is
    #: drawn at zoom 1.109 (mean |diff| 28.1/255 against the same frame rendered
    #: with no look), the blend part's first frame at zoom 1.000 (0.7/255 —
    #: indistinguishable from no punch at all). muvid cannot rebase the fragment
    #: without rewriting an arbitrary ffmpeg expression, which is exactly what
    #: ``looks`` refuses to do for itself (its rule 27), so the assembler WARNS
    #: rather than silently rendering the hitch
    #: (:func:`muvid.footage.assemble._part_plan`) — the same "never a silent
    #: no-op" posture as the zero-frame-transition warning beside it.
    #:
    #: **A caller-supplied look defaults to ``False``, so an UNDECLARED moving
    #: look stays silent.** That is the known limit of the chosen shape, not an
    #: oversight: the alternative is muvid deciding by reading the fragment,
    #: which is the same rewriting-an-arbitrary-expression problem one step
    #: earlier. muvid's own compilers declare it for you —
    #: :func:`muvid.footage.look.punch_in` and
    #: :func:`~muvid.footage.look.motion` return a fragment that says ``True``,
    #: :func:`~muvid.footage.look.stylize` one that answers from the compiled
    #: plan, and :func:`~muvid.footage.look.punch_in_cuts` sets this field FROM
    #: the fragment rather than hardcoding it.
    look_time_varying: bool = False

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
    #: Carried through unchanged, and the assembler is its ONE consumer: only it
    #: knows which boundaries become a separate two-input invocation, which is
    #: where a moving look's ramp restarts (muvid#73). See
    #: :attr:`EdlEntry.look_time_varying` for the measurement.
    look_time_varying: bool = False

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


def _as_look_time_varying(raw) -> bool:
    """Parse the ``look_time_varying`` flag. RAISES on a non-bool.

    Same posture as the look and crop reads, and here the coercion it refuses is
    the dangerous one: ``bool("false")`` is ``True``, so accepting a string would
    turn a caller writing ``"false"`` into a warning they cannot switch off, and
    ``bool(0)``/``bool("")`` would silently disarm a declaration that was made.
    JSON has real booleans; a caller sending anything else has a bug worth
    hearing about.
    """
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise ValueError(
            f"EDL entry look_time_varying is malformed ({raw!r}): it must be a "
            "boolean. It says whether the look READS THE CLOCK (a punch-in, a "
            "pan) — true makes the assembler warn when the cut borders a "
            "transition, where the ramp restarts (muvid#73)."
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
        look_time_varying=_as_look_time_varying(e.get("look_time_varying")),
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
    *,
    canvas: "tuple[int, int]" = DFLT_LOOK_CANVAS,
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
      is ONE lexically-closed linear filter chain, names no container input, is
      not on a gap, sets only options :data:`_LOOK_GEOMETRY_FILTERS` classifies on
      the four filters that can change the output frame, and asks for a frame no
      more than :data:`MAX_LOOK_SCALE` times ``canvas`` — see
      :func:`_validate_look`, which is the trust boundary for a caller-supplied
      filter string;
    - ``look_time_varying`` is a boolean, and is not set on an entry with no
      ``look`` (a declaration about a look that is not there is a request that
      cannot be honoured);
    - a :class:`Transition` is on an entry that HAS a predecessor, names a known curve,
      is at least :data:`MIN_TRANSITION_S` long, fits in song time counting BOTH
      transitions an entry can carry, and fits in each side's aligned coverage — see
      :func:`_validate_transition`.

    ``canvas`` is the DELIVERY canvas the assembler will render onto — the only
    thing a look's output frame can honestly be bounded against, since the look is
    spliced after the assembler's own ``scale``/``pad`` onto it. Every muvid path
    passes the real one; :data:`DFLT_LOOK_CANVAS` covers a direct caller and is
    the loosest bound rather than an absent one.

    Returns the normalized list of :class:`EdlEntry`.
    """
    w, h = int(canvas[0]), int(canvas[1])
    if w <= 0 or h <= 0:
        raise ValueError(
            f"canvas must be positive, got {canvas[0]}x{canvas[1]}. It is the "
            "delivery size a look's output frame is bounded against."
        )
    canvas = (w, h)
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
            _validate_look(i, e, canvas)
        elif e.look_time_varying:
            raise ValueError(
                f"EDL entry {i} sets look_time_varying but carries no look. The "
                "flag says whether THE LOOK reads the clock; with no look there "
                "is nothing for it to describe, and the warning it arms would "
                "never fire — a direction that quietly does nothing."
            )
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
#: **The frame size an allowlisted filter may ask for is bounded separately**
#: (muvid#75) — see :data:`_LOOK_GEOMETRY_FILTERS` and :data:`MAX_LOOK_SCALE`.
#: The allowlist is a vocabulary; it says nothing about the PARAMETERS a member
#: is given, and one of those parameters is memory. Nor is a size bound a bound
#: on the OPTIONS that set a size: ``pad``'s ``aspect`` and ``scale``'s
#: ``force_original_aspect_ratio`` both move the frame while declaring no
#: dimension a bound can read, so the four filters that can change the output
#: geometry are allowlisted per OPTION as well as by name.
#:
#: **What this still does NOT bound**, stated because a partial claim is worse
#: than none: ``lut3d=file=`` will *attempt* to open any path the renderer can
#: read. It cannot write, and a non-``.cube`` file fails to parse. That is a
#: failure inside the caller's OWN render; writing to someone else's disk is the
#: class this closes.
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


class _FilterOptions(NamedTuple):
    """Which options a look may set on one geometry-capable filter."""

    #: option name -> the axis it sets: ``"width"``, ``"height"``, or ``"size"``
    #: (a single ``WxH``). Bounded against the canvas.
    sizes: "dict[str, str]"
    #: accepted and not bounded — measured not to move the output frame.
    free: frozenset
    #: the leading UNNAMED slots a look may fill, in libavfilter's order. Short
    #: on purpose: a positional past the end is REFUSED, never dropped.
    positional: tuple


#: Which options a look may set on a filter that can change the OUTPUT FRAME
#: GEOMETRY — an allowlist, the same shape and for the same reason
#: :data:`LOOK_FILTERS` is one, and the correction of the first pass at muvid#75.
#:
#: **The first pass listed the options that SET a size and read the rest as
#: nothing, which is a blocklist wearing an allowlist's clothes** — and it leaked
#: twice, both bigger than the ``scale=8000:8000`` it refused. Measured on a
#: 1920x1080 canvas with the exact ``-vf`` the assembler builds, 3 frames,
#: ``/usr/bin/time -l`` peak RSS:
#:
#: ==========================================================  ============  =========
#: fragment                                                    frame         peak RSS
#: ==========================================================  ============  =========
#: (look at canvas size)                                       1920x1080      110 MB
#: ``scale=7680:4320`` — the bound's own stated worst case      7680x4320      268 MB
#: ``scale=8000:8000`` — REFUSED, the case muvid#75 named       8000x8000      403 MB
#: ``pad=w=1920:h=1080:aspect=1/30``                           1920x57600      590 MB
#: ``crop=w=1920:h=200,scale=w=7680:h=4320:``
#: ``force_original_aspect_ratio=increase``                    41472x4320      941 MB
#: ==========================================================  ============  =========
#:
#: Both leaks declare nothing the old table could read: ``pad``'s ``aspect``
#: grows w *or* h to satisfy a ratio, so both dimensions can sit AT canvas size;
#: ``scale``'s ``force_original_aspect_ratio`` derives the frame from the input
#: aspect, which a preceding ``crop`` makes extreme, so both dimensions can sit
#: exactly ON the bound. A third, ``force_divisible_by``, only acts in
#: combination with the second — which is why a one-option-at-a-time sweep
#: cannot find it and the table is an allowlist instead.
#:
#: **How this list was arrived at**, because "we named the levers we thought of"
#: is what produced the first pass: for every option of every one of the 17
#: filters in :data:`LOOK_FILTERS`, read out of ``ffmpeg -h filter=<name>``, a
#: value of the option's declared type was rendered from a 64x48 source in three
#: contexts — alone, beside a size, and beside a size AND
#: ``force_original_aspect_ratio`` — and the produced frame compared to the
#: fragment's own baseline. On ffmpeg 9.0.1 and 6.1.6 alike the frame moves for
#: exactly these: ``crop``'s ``out_w``/``w``/``out_h``/``h``, ``pad``'s
#: ``width``/``w``/``height``/``h``/``aspect``, ``scale``'s
#: ``w``/``width``/``h``/``height``/``size``/``s``/
#: ``force_original_aspect_ratio``/``force_divisible_by``, and ``zoompan``'s
#: ``s``. Nothing on the other 13 filters moves it, which is why they are absent
#: here and their options are not checked at all.
#:
#: The sweep is an instrument, not the guard. **The guard is that anything not
#: named here is refused**, because the census the sweep reads is itself
#: incomplete: ``ffmpeg -h filter=scale`` on 6.1.6 does not print ``s``/``size``
#: and ``scale=s=320x240`` works there anyway (measured, 320x240). A table that
#: classified only what the help prints would have had a hole on that binary.
#:
#: Two entries that look like omissions and are not:
#:
#: - ``crop``'s ``w``/``h`` are ``free``, not sizes. ``crop`` cannot GROW a frame
#:   — ``crop=8000:8000`` and ``crop=w='iw*80':h='ih*80'`` are both refused by
#:   ffmpeg ("Invalid too big or non positive size") — so its output is bounded
#:   by its input, and bounding it would refuse ``looks``' constant-size
#:   ``motion`` (``crop=w='iw*0.5':h='ih*0.5'``), the one muvid-compiled fragment
#:   whose size options are expressions.
#: - the positional prefixes stop at what muvid's own compilers emit, and a
#:   positional past the end is refused rather than dropped. Dropping it was the
#:   second half of the ``pad`` leak: ``pad``'s ``aspect`` is its SEVENTH
#:   positional slot, so ``pad=1920:1080:0:0:black:init:1/30`` reaches it without
#:   naming it (measured, both binaries: 192x48 from a 64x48 source at
#:   ``aspect=4/1``, and ``pad=64:48:0:0:black:init:4/1`` likewise). Carrying the
#:   full order instead would not be safe either, and the reason is measurable:
#:   the two builds this fleet runs do not declare the same option list for
#:   ``scale``. On 9.0.1 the fifth slot is ``size`` (``scale=100:100:bicubic:0:X``
#:   answers "Size and width/height expressions cannot be set at the same time");
#:   6.1.6's ``-h filter=scale`` does not list ``size``/``s`` at all, so its fifth
#:   slot is something else. A hardcoded full order would therefore disagree with
#:   one of the two binaries about what a bare argument SAYS, which is the one
#:   thing a gate must never do. Stopping short and refusing is version-proof.
#:
#:   The two rules overlap on purpose, and the overlap is defence rather than
#:   redundancy: lengthening ``pad``'s prefix to all seven slots still refuses
#:   ``pad=1920:1080:0:0:black:init:1/30``, because ``eval`` and ``aspect`` are
#:   then read as unclassified OPTIONS instead of unclassified SLOTS (verified by
#:   mutation). What the short prefix adds is that muvid never has to be right
#:   about a slot it did not measure.
_LOOK_GEOMETRY_FILTERS = {
    "scale": _FilterOptions(
        sizes={
            "w": "width",
            "width": "width",
            "h": "height",
            "height": "height",
            "s": "size",
            "size": "size",
        },
        free=frozenset({"flags"}),
        positional=("w", "h"),
    ),
    "pad": _FilterOptions(
        sizes={"w": "width", "width": "width", "h": "height", "height": "height"},
        free=frozenset({"x", "y", "color"}),
        positional=("w", "h", "x", "y"),
    ),
    "crop": _FilterOptions(
        sizes={},
        free=frozenset({"w", "out_w", "h", "out_h", "x", "y"}),
        positional=("w", "h", "x", "y"),
    ),
    "zoompan": _FilterOptions(
        sizes={"s": "size"},
        free=frozenset({"zoom", "z", "x", "y", "d", "fps"}),
        positional=("zoom", "x", "y", "d", "s", "fps"),
    ),
}

#: The measured reason a particular geometry option is refused rather than
#: bounded, quoted into the refusal so the caller is told what it does and not
#: merely that it is not allowed. Every other unclassified option gets the
#: generic message; these four are the ones a bound could plausibly have been
#: written for, so the reason it was not is recorded where it is enforced.
#:
#: All four are refused rather than bounded for one reason: bounding them means
#: computing the frame this filter will produce from the frame the previous one
#: produced — libavfilter's geometry negotiation, reimplemented in Python and
#: kept in agreement with two binaries. That is the same refusal
#: :func:`_validate_look_size` already makes for an expression, for the same
#: reason, and it costs the seam nothing: **no fragment muvid compiles sets any
#: of them** — swept over ``punch_in`` (4 zooms x 4 canvases x 2 rates),
#: ``motion``, and all 14 ``looks`` effects at 6 targets on 4 canvases, whose
#: entire emitted vocabulary is ``scale`` w/h positional, ``pad`` w/h/x/y
#: positional plus ``color``, ``crop`` w/h/x/y positional, ``zoompan``
#: ``z``/``x``/``y``/``d``/``s``/``fps``, ``lut3d=file``, and the colour filters.
_LOOK_REFUSED_OPTIONS = {
    ("pad", "aspect"): (
        "it pads to fit an ASPECT rather than a resolution, so `w`/`h` can be "
        "left AT canvas size and the frame still explodes: measured on a "
        "1920x1080 canvas, `pad=w=1920:h=1080:aspect=1/30` renders a 1920x57600 "
        "frame at 590 MB peak RSS, against 110 MB for a look that stays at "
        "canvas size and 403 MB for the `scale=8000:8000` this bound refuses"
    ),
    ("scale", "force_original_aspect_ratio"): (
        "it derives the frame from the INPUT aspect, which a preceding `crop` "
        "can make extreme, so both declared sizes can sit exactly ON the bound "
        "and the frame still explodes: measured, "
        "`crop=w=1920:h=200,scale=w=7680:h=4320:force_original_aspect_ratio="
        "increase` renders 41472x4320 at 941 MB peak RSS"
    ),
    ("scale", "force_divisible_by"): (
        "it rounds the frame up, and only in combination with "
        "`force_original_aspect_ratio` — measured on a 64x48 input, "
        "`scale=w=100:h=100:force_original_aspect_ratio=increase` produces "
        "133x100 and adding `force_divisible_by=64` makes it 128x128. An option "
        "that does nothing alone is exactly the one a one-at-a-time sweep misses"
    ),
    ("scale", "eval"): (
        "`eval=frame` re-evaluates the size expressions per frame, and this "
        "gate reads a fragment once. A size muvid cannot read is refused rather "
        "than bounded (see the `not a plain pixel count` rule), and a size that "
        "can CHANGE after it was read is the same problem in time"
    ),
    ("pad", "eval"): (
        "`eval=frame` re-evaluates the size expressions per frame, and this "
        "gate reads a fragment once — the same reason `scale`'s is refused"
    ),
}

#: A size option muvid can bound: a plain pixel count, optionally quoted.
_LOOK_PIXELS = re.compile(r"^\d+$")
#: A ``WxH`` image size muvid can bound. Deliberately NOT ffmpeg's full
#: ``av_parse_video_size`` vocabulary: that also accepts abbreviations, and
#: ``zoompan=d=1:s=whuxga:fps=25`` really does produce a 7680x4800 frame at
#: 321.2 MB (measured). A name muvid would have to keep a second copy of the
#: table for is refused and the caller told to spell the size.
_LOOK_PIXEL_SIZE = re.compile(r"^(\d+)x(\d+)$")


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


def _split_significant(s: str, sep: str) -> "list[str]":
    r"""``s`` split on the ``sep`` characters ffmpeg reads as SEPARATORS.

    The one splitter for both of a filter chain's levels — ``,`` between links,
    ``:`` between one link's arguments — so a quoted expression survives both.
    Written once rather than twice because the second copy is where the two stop
    agreeing about what a fragment says.

    >>> _split_significant("scale=2,hue=s=0", ",")
    ['scale=2', 'hue=s=0']
    >>> _split_significant("w='min(iw,2)':h=100", ":")
    ["w='min(iw,2)'", 'h=100']
    """
    breaks = [i for i, c in _significant(s) if c == sep]
    out, start = [], 0
    for b in breaks + [len(s)]:
        out.append(s[start:b])
        start = b + 1
    return out


def _look_links(look: str) -> "list[tuple[str, str]]":
    r"""Each link of the chain as ``(filter name, argument string)``.

    >>> _look_links("scale=2,hue=s=0")
    [('scale', '2'), ('hue', 's=0')]
    >>> _look_links("null")
    [('null', '')]
    """
    out = []
    for link in _split_significant(look, ","):
        eq = next((i for i, c in _significant(link) if c == "="), len(link))
        raw = _strip_unescaped(link[:eq])
        at = next((i for i, c in _significant(raw) if c == "@"), len(raw))
        # No `.strip()` AFTER unescaping. `\ ` is an escaped space, so stripping
        # the unescaped result removed the space and left the backslash that
        # escaped it — `_unquote` then raised `_LookSyntaxError("\\")` from
        # OUTSIDE `_validate_look`'s try/except, so a lexically CLOSED look was
        # refused with a message that was a single backslash. Through the MCP
        # tool that reached the caller as the whole explanation.
        out.append((_unquote(raw[:at]), link[eq + 1 :]))
    return out


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
    return [name for name, _ in _look_links(look)]


def _link_options(
    args: str, positional: "Sequence[str]"
) -> "tuple[dict[str, str], list[tuple[str, bool]]]":
    r"""One link's arguments as ``{option name: raw value}``, ffmpeg's way.

    Two rules taken from libavfilter's ``process_options`` rather than from
    intuition, and both change the answer:

    - an argument with no ``=`` is POSITIONAL, filling the next slot of
      ``positional`` (the option list in declaration order, aliases collapsed);
    - **positional slots stop being offered after the first named argument**, so
      a bare argument after a ``key=value`` one goes to ``overflow`` and is
      REFUSED rather than read as a later slot. **Not because ffmpeg agrees —
      because the two builds this fleet runs do not agree with each other:**

      ==========================  ==============  ================
      fragment                    ffmpeg 9.0.1    ffmpeg 6.1.6
      ==========================  ==============  ================
      ``scale=w=100:8000``        rc=234          **100x8000**
      ``scale=w=8000:100``        rc=234          **8000x100**
      ``scale=100:h=8000``        100x8000        100x8000
      ==========================  ==============  ================

      9.0.1 discards the remaining shorthand ("No option name near '8000'"); 6.1.6
      fills the next slot. A gate that picked either reading would be wrong on the
      other binary, and on 6.1.6 it would be wrong in the dangerous direction —
      reading ``scale=w=8000:100`` as ``{w: 100}`` accepts a fragment that renders
      8000 px wide. So this is a hole where it is dropped, not merely an
      over-refusal: without the rule the trailing bare argument refills slot 0 and
      **overwrites** the named ``w``.

    Later assignments win, as ``av_opt_set`` does.

    Returns ``(options, overflow)``. ``overflow`` is one ``(value, after_named)``
    per positional argument that found no slot — **reported rather than dropped**,
    which is the half the first pass got wrong. ``positional`` stops at the slots
    muvid classifies (see :data:`_LOOK_GEOMETRY_FILTERS`), so a bare argument past
    the end is not a triviality: ``pad``'s ``aspect`` is its seventh slot, and
    ``pad=1920:1080:0:0:black:init:1/30`` reaches it without naming it. Silently
    dropping slot 7 is what let that fragment through.

    An EMPTY argument string declares nothing, which is a case rather than a
    triviality: ``scale`` and ``pad`` with no arguments at all are legal ffmpeg
    (rc=0, frame unchanged — measured), and reading their absent ``w`` as the
    empty string made the gate refuse a fragment the binary accepts. Note this is
    the whole list being empty; an empty value *within* a list (``scale=:100``)
    is left alone, because ffmpeg refuses that too ("Cannot parse expression for
    width: ''", rc=234) and the gate agreeing is the point.

    >>> _link_options("8000:8000", ("w", "h"))
    ({'w': '8000', 'h': '8000'}, [])
    >>> _link_options("w=8000:h=8000", ("w", "h"))
    ({'w': '8000', 'h': '8000'}, [])
    >>> _link_options("1:0:0:1:640x360:25", ("zoom", "x", "y", "d", "s", "fps"))[0]["s"]
    '640x360'
    >>> _link_options("", ("w", "h"))
    ({}, [])
    >>> _link_options("64:48:0:0:black", ("w", "h", "x", "y"))[1]
    [('black', False)]
    >>> _link_options("w=100:8000", ("w", "h"))
    ({'w': '100'}, [('8000', True)])
    """
    out: "dict[str, str]" = {}
    overflow: "list[tuple[str, bool]]" = []
    if not args:
        return out, overflow
    slot, named_seen = 0, False
    for arg in _split_significant(args, ":"):
        eq = next((j for j, c in _significant(arg) if c == "="), None)
        if eq is None:
            if named_seen or slot >= len(positional):
                overflow.append((arg, named_seen))
                continue
            out[positional[slot]] = arg
            slot += 1
            continue
        named_seen = True
        out[_unquote(_strip_unescaped(arg[:eq]))] = arg[eq + 1 :]
    return out, overflow


def _look_output_sizes(look: str) -> "list[tuple[str, str, str, str, int | None]]":
    r"""Every output frame DIMENSION the chain declares.

    One ``(filter, option, axis, raw text, pixels or None)`` per declared
    dimension. ``axis`` is ``"width"`` or ``"height"``; ``pixels`` is ``None``
    when the value is not a plain pixel count muvid can bound — an expression, a
    negative auto-value, an image-size abbreviation.

    Only the ``sizes`` options of :data:`_LOOK_GEOMETRY_FILTERS` are reported; an
    absent one leaves the frame unchanged (``scale``/``pad`` default to
    ``iw``/``ih``) and is not a lever. Every OTHER way of moving the frame —
    ``pad=aspect``, ``scale=force_original_aspect_ratio``, an unclassified option
    on a geometry filter, an unclassified positional slot — is not a size this
    function can report at all, and is refused by
    :func:`_look_unclassified_options` instead. The two together are the bound;
    this one alone was the muvid#75 leak.

    ``zoompan``'s absent ``s`` is the one documented exception to that last
    sentence: its default is a literal ``hd720``, so a ``zoompan`` with no ``s``
    outputs 1280x720 whatever the canvas is. It is left unreported deliberately —
    a fixed 0.9 Mpix (36.5 MB measured) cannot be the memory hazard this bound
    exists for, and reporting it would refuse a harmless look on a small canvas
    for a reason that is not memory.

    >>> _look_output_sizes("scale=8000:8000")
    [('scale', 'w', 'width', '8000', 8000), ('scale', 'h', 'height', '8000', 8000)]
    >>> _look_output_sizes("zoompan=d=1:s=640x360:fps=25")
    [('zoompan', 's', 'width', '640x360', 640), ('zoompan', 's', 'height', '640x360', 360)]
    >>> _look_output_sizes("scale=w='iw*80':h='ih*80'")
    [('scale', 'w', 'width', 'iw*80', None), ('scale', 'h', 'height', 'ih*80', None)]
    >>> _look_output_sizes("hue=s=0,crop=w='iw*0.5':h='ih*0.5'")
    []
    """
    out = []
    for name, args in _look_links(look):
        spec = _LOOK_GEOMETRY_FILTERS.get(name)
        if spec is None:
            continue
        opts, _overflow = _link_options(args, spec.positional)
        for opt, value in opts.items():
            axis = spec.sizes.get(opt)
            if axis is None:
                continue
            text = _unquote(_strip_unescaped(value))
            if axis == "size":
                m = _LOOK_PIXEL_SIZE.match(text)
                for ax, group in (("width", 1), ("height", 2)):
                    out.append(
                        (name, opt, ax, text, int(m.group(group)) if m else None)
                    )
            else:
                px = int(text) if _LOOK_PIXELS.match(text) else None
                out.append((name, opt, axis, text, px))
    return out


def _allowed_options(filt: str) -> "set[str]":
    """Every option name a look may set on ``filt``. Empty for an unlisted filter.

    >>> sorted(_allowed_options("zoompan"))
    ['d', 'fps', 's', 'x', 'y', 'z', 'zoom']
    >>> _allowed_options("hue")
    set()
    """
    spec = _LOOK_GEOMETRY_FILTERS.get(filt)
    return set() if spec is None else set(spec.sizes) | set(spec.free)


def _look_unclassified_options(look: str) -> "list[tuple[str, str, str]]":
    r"""Every way this chain could move the frame that muvid has NOT classified.

    One ``(filter, what, why)`` per offence, on the filters in
    :data:`_LOOK_GEOMETRY_FILTERS` only — the four that can change the output
    geometry at all. The other thirteen allowlisted filters are not
    option-checked, because no option of any of them moves the frame (measured;
    see the table's docstring and ``tests/test_edl_look_options.py``, which
    re-reads the option list out of the installed binary so a new one cannot
    arrive unnoticed).

    Three offences, and the third is the one a named-option check alone misses:

    - an option with a recorded reason (:data:`_LOOK_REFUSED_OPTIONS`);
    - an option the allowlist does not name at all;
    - a POSITIONAL argument past the slots muvid classifies — ``pad``'s
      ``aspect`` is its seventh slot and needs no name to reach.

    >>> _look_unclassified_options("scale=1920:1080,hue=s=0")
    []
    >>> [(f, w) for f, w, _ in _look_unclassified_options("pad=aspect=4/1")]
    [('pad', "the 'aspect' option")]
    >>> [(f, w) for f, w, _ in _look_unclassified_options("pad=64:48:0:0:black")]
    [('pad', "the positional argument 'black'")]
    """
    out = []
    for name, args in _look_links(look):
        spec = _LOOK_GEOMETRY_FILTERS.get(name)
        if spec is None:
            continue
        opts, overflow = _link_options(args, spec.positional)
        for opt in opts:
            if opt in spec.sizes or opt in spec.free:
                continue
            why = _LOOK_REFUSED_OPTIONS.get((name, opt))
            out.append((name, f"the {opt!r} option", why or ""))
        for value, after_named in overflow:
            why = (
                "a bare argument after a `key=value` one means DIFFERENT THINGS "
                "on different ffmpeg builds, so muvid declines to guess: 9.0.1 "
                'discards it (`scale=w=8000:100` exits 234, "No option name '
                "near '100'\") while 6.1.6 fills the next slot and renders "
                "8000x100. Spell every option by name"
                if after_named
                else (
                    f"{name} takes {len(spec.positional)} positional arguments in "
                    "muvid's table and this is one more. The slots past that end "
                    "are not the same on every ffmpeg build (`scale`'s fifth is "
                    "`size` on 9.0.1, and 6.1.6 does not declare `size` on "
                    "`scale` at all) and one of `pad`'s is `aspect`, which grows "
                    "the frame. Spell the option by name"
                )
            )
            out.append((name, f"the positional argument {value!r}", why))
    return out


def _validate_look_size(i, look: str, canvas) -> None:
    """Bound the frame a look asks for against the delivery canvas. Raises.

    The allowlist :data:`LOOK_FILTERS` is a vocabulary; it does not bound what a
    member is asked to DO, and frame size is memory. Measured on ffmpeg 9.0.1,
    three frames from a 64x48 source, ``/usr/bin/time -l`` peak RSS: 18.8 MB for
    ``scale=64:48``, **327.6 MB** for ``scale=64:48,scale=8000:8000``, **312.5 MB**
    for ``zoompan=d=1:s=8000x8000:fps=25``, **306.6 MB** for ``pad=8000:8000`` and
    **118.4 MB** for ``scale=w='iw*80':h='ih*80'``. All five were accepted before
    muvid#75, from a number a remote OAuth caller writes into
    ``assemble_music_video``'s free-form ``edl=``, on the box muvid#21/#24 was
    OOM-killed on.

    Three rules. The first is the one a *list of size options* alone misses, and
    it is what the first pass at this got wrong:

    - **On a filter that can change the geometry, only options muvid classifies
      may be set** (:data:`_LOOK_GEOMETRY_FILTERS`) — including through an
      unnamed positional slot. Two options move the frame while DECLARING no
      dimension the rule below can read, and both are bigger than the case that
      rule refuses: ``pad=w=1920:h=1080:aspect=1/30`` renders a 1920x57600 frame
      at 590 MB with w and h sitting at canvas size, and
      ``crop=w=1920:h=200,scale=w=7680:h=4320:force_original_aspect_ratio=increase``
      renders 41472x4320 at 941 MB with both sizes sitting exactly on the bound
      (measured on a 1920x1080 canvas against 110 MB at canvas size and 403 MB
      for the refused ``scale=8000:8000``). So the classification is an
      ALLOWLIST: an option muvid has not measured is refused, and the census of
      what the binary offers is a CI test rather than the guard, because the
      census is itself incomplete (ffmpeg 6.1.6's ``-h filter=scale`` omits
      ``s``/``size``, which work there).
    - **A declared dimension may not exceed** :data:`MAX_LOOK_SCALE` **times the
      canvas.** The look is spliced after the assembler's own ``scale``/``pad``,
      so the frame entering it IS the canvas and the frame leaving it is resampled
      straight back onto the canvas — anything much larger is spending memory to
      throw pixels away.
    - **A declared dimension must be a plain pixel count.** ``iw*80`` is a legal
      width and evaluates against the input, so a literal cap does not see it;
      the same is true of ``-1``/``-2`` (derive from the input aspect, which a
      preceding ``crop`` can make extreme — measured:
      ``crop=w=64:h=2,scale=-1:4000`` asks for a 128000x4000 frame) and of
      ``av_parse_video_size``'s abbreviations (``zoompan=d=1:s=whuxga:fps=25``
      really produces 7680x4800, at 321.2 MB).

    **What stays reachable**, stated because a partial claim is worse than none —
    and this paragraph is the one the first pass overstated, so read it as the
    correction it is. A look may still ask for ``MAX_LOOK_SCALE`` x the canvas in
    BOTH dimensions — 16x the area, 268 MB measured at 7680x4320 on a 1920x1080
    canvas against 110 MB at canvas size — on every cut of an edit, and the cap is
    per invocation rather than cumulative, exactly like :data:`MAX_EDL_ENTRIES`.
    What is closed is every way a look can name a frame LARGER than that, in any
    spelling, on any of the four filters that can set one.

    Refusing an EXPRESSION rather than bounding its multiplier syntactically —
    and, for the same reason, refusing an aspect-driven option rather than
    computing the frame it produces — is a decision with a measurement behind it:
    **every size option muvid's own compilers emit is already a literal, and none
    of them sets a refused option** — ``looks.compile_motion`` writes
    ``zoompan=d=1:s=640x360``, ``looks``' geometry effects write ``scale=1280:720``
    and ``pad=1080:1080:0:236:color=0x000000`` — swept over ``punch_in`` (4 zooms
    x 4 canvases x 2 rates), ``motion``, and every ``looks`` effect at 6 targets on
    all four muvid canvases. The only muvid-compiled fragment whose size options
    are expressions is ``motion``'s constant-size ``crop=w='iw*0.5':h='ih*0.5'``,
    and ``crop``'s ``w``/``h`` are ``free`` rather than sizes because ``crop``
    structurally cannot grow a frame. So the strict rule costs the seam nothing,
    where either alternative would be a second copy of libavfilter's geometry
    negotiation to keep in agreement with two binaries.
    """
    for filt, what, why in _look_unclassified_options(look):
        raise ValueError(
            f"EDL entry {i}: look sets {what} on {filt} ({look!r}), which muvid "
            f"does not classify. "
            + (f"That option is refused because {why}. " if why else "")
            + f"A look may set only {sorted(_allowed_options(filt))} on {filt}, "
            f"and at most {len(_LOOK_GEOMETRY_FILTERS[filt].positional)} unnamed "
            f"arguments ({', '.join(_LOOK_GEOMETRY_FILTERS[filt].positional)}). "
            "The four filters that can change the output frame are allowlisted "
            "per OPTION, not per name: frame size is memory, this is a live "
            "per-caller tool on a box that has been OOM-killed, and an option "
            "muvid has not measured could move the frame without declaring a "
            "dimension the bound can read (`pad=w=W:h=H:aspect=1/30` renders "
            "57600 px high with W and H at canvas size)."
        )
    limits = {"width": MAX_LOOK_SCALE * canvas[0], "height": MAX_LOOK_SCALE * canvas[1]}
    for filt, opt, axis, text, px in _look_output_sizes(look):
        if px is None:
            raise ValueError(
                f"EDL entry {i}: look sets {filt}'s {opt!r} to {text!r}, which is "
                f"not a plain pixel count ({look!r}). A look's output frame is "
                "bounded against the delivery canvas, and a bound cannot read an "
                "expression: `iw*80` evaluates against whatever is underneath, "
                "`-1`/`-2` derive from the input aspect (which a preceding crop "
                "can make extreme — `crop=w=64:h=2,scale=-1:4000` asks for a "
                "128000x4000 frame), and a size NAME resolves through ffmpeg's "
                "own table (`s=whuxga` is 7680x4800, 321 MB). Write the size in "
                f"pixels — muvid's own compilers do (muvid.footage.look emits "
                f"`s={canvas[0]}x{canvas[1]}`)."
            )
        if px > limits[axis]:
            raise ValueError(
                f"EDL entry {i}: look asks for a frame {px} px {'wide' if axis == 'width' else 'high'} "
                f"via {filt}'s {opt!r}, more than {MAX_LOOK_SCALE}x the "
                f"{canvas[0]}x{canvas[1]} canvas ({look!r}). Frame size is memory, "
                "and this is a live per-caller tool on a box that has been "
                "OOM-killed: `scale=8000:8000` peaks at 328 MB against 19 MB for a "
                "look that stays at canvas size, and `pad`/`zoompan` reach the "
                "same magnitude. Scaling past the canvas buys nothing either — the "
                f"delivered frame is the canvas — so the limit is {limits['width']}x"
                f"{limits['height']}, which still leaves room to supersample."
            )


def _validate_look(i, e, canvas) -> None:
    """What a look fragment has to satisfy. Raises ``ValueError``.

    Split out only for length; it is part of :func:`validate_edl`, which remains
    the ONE gate. Nothing else may check these.

    A look is **executable ffmpeg supplied by a caller**, and
    ``assemble_music_video`` is a live per-caller MCP tool, so this is the
    trust boundary for the whole seam. Five rules:

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
    - **A bounded frame.** The allowlist is a vocabulary and says nothing about
      the PARAMETERS a member is given; one of those is the output frame size,
      which is memory. See :func:`_validate_look_size`, which is why this
      function needs the ``canvas`` — the assembler knew it and the gate did not
      (muvid#75).

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
    _validate_look_size(i, look, canvas)
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
                    look_time_varying=e.look_time_varying,
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
                look_time_varying=e.look_time_varying,
            )
        )
    return cuts
