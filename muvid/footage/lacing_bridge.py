"""muvid project → lacing standoff records, and the DECISION tier back to an EDL.

The multichannel editor (thorwhalen/reelee-web#203) renders three record kinds that
until now existed only as prose in the design docs (muvid#31): ``clip-alignment/v1``
(where each clip sits on the song), ``clip-score-track/v1`` (one (clip, metric) curve on
the shared song-time grid), and ``music-video-edl/v1`` (the DECISION lane — one entry
per cut, gaps included). This module is the bridge, both directions:

- :func:`editor_document` — a muvid project as ``{tiers, annotations}``, everything in
  SONG TIME on one shared axis, referenced to the song's content hash (``MediaRef``), so
  any lacing-native surface (lacing-ui's multitrack Timeline first) renders it without
  knowing muvid exists.
- :func:`edl_from_annotations` — the timeline-to-EDL half: a DECISION tier, after human
  edits, exports verbatim as ``assemble_music_video(edl=...)`` input.

Times quantize to lacing's rational grid at ``TIME_RATE`` (μs): far finer than
``validate_edl``'s 1 ms tolerance and the frame grid, so annotate → edit → export →
render reproduces the same cuts.

Score arrays are inlined (a 205 s song at the current hop is ~2k floats per metric);
they move behind a ContentRef when they outgrow JSON — a body-schema major bump.
"""

from __future__ import annotations

import time as _time
import uuid
from typing import Sequence

#: Rational-time rate for all bridge annotations: microseconds.
TIME_RATE = 1_000_000

#: Body-schema URIs this bridge emits (single source of truth for the names).
CLIP_ALIGNMENT_SCHEMA = "annot://schema/clip-alignment/v1"
CLIP_SCORE_TRACK_SCHEMA = "annot://schema/clip-score-track/v1"
MUSIC_VIDEO_EDL_SCHEMA = "annot://schema/music-video-edl/v1"

#: Tier names. Clip lanes are per-clip (``clip:<id>``); these are the shared ones.
DECISION_TIER = "DECISION"
_PROVENANCE_AGENT = "processor:muvid.footage.lacing_bridge"


def _interval(start_s: float, end_s: float):
    from lacing.time import RationalTime, TimeInterval

    return TimeInterval(
        start=RationalTime.from_seconds_lossy(start_s, rate=TIME_RATE, mode="round"),
        end=RationalTime.from_seconds_lossy(end_s, rate=TIME_RATE, mode="round"),
    )


def _annotation(
    *,
    tier: str,
    song_asset_id: str,
    start_s: float,
    end_s: float,
    body: dict,
    schema: str,
    attributed_to: str,
    confidence: float | None = None,
):
    from lacing.model import Annotation, MediaRef, Provenance
    from lacing.time import RationalTime

    return Annotation(
        id=uuid.uuid4(),
        tier=tier,
        reference=MediaRef(asset_id=song_asset_id, interval=_interval(start_s, end_s)),
        body=body,
        body_schema_uri=schema,
        provenance=Provenance(
            was_generated_by=_PROVENANCE_AGENT,
            was_attributed_to=attributed_to,
            generated_at_time=RationalTime.from_seconds_lossy(
                _time.time(), rate=TIME_RATE, mode="round"
            ),
            activity="import",
        ),
        confidence=confidence,
    )


def alignment_annotations(aligns, *, song_asset_id: str, attributed_to: str) -> list:
    """One ``clip-alignment/v1`` per clip, spanning the clip's coverage of the song."""
    out = []
    for a in aligns:
        lo, hi = a.coverage
        if not a.overlaps:
            # No song span to hang it on; the record still exists, referenced to a
            # zero-length interval at 0 so the clip stays addressable in the document.
            lo = hi = 0.0
        out.append(
            _annotation(
                tier=f"clip:{a.clip_id}",
                song_asset_id=song_asset_id,
                start_s=lo,
                end_s=hi,
                body={
                    "clip_id": a.clip_id,
                    "offset_s": a.offset_s,
                    "duration_s": a.duration_s,
                    "overlaps": a.overlaps,
                },
                schema=CLIP_ALIGNMENT_SCHEMA,
                attributed_to=attributed_to,
                confidence=max(0.0, min(1.0, a.confidence)),
            )
        )
    return out


def score_track_annotations(tensor, *, song_asset_id: str, attributed_to: str) -> list:
    """One ``clip-score-track/v1`` per (clip, metric): the whole curve as one record.

    Dense JAMS-style arrays on the shared grid — values normalized to [0,1], ``mask``
    saying where the clip actually covers the song (blank, never flat-zero, in the UI).
    """
    out = []
    n = tensor.n
    t0, hop = tensor.t0, tensor.hop_s
    for ci, clip_id in enumerate(tensor.clip_ids):
        for mi, metric in enumerate(tensor.metrics):
            # S is [clip, frame, metric] normalized; M is the matching valid mask.
            values = tensor.S[ci, :, mi]
            mask = tensor.M[ci, :, mi]
            out.append(
                _annotation(
                    tier=f"clip:{clip_id}",
                    song_asset_id=song_asset_id,
                    start_s=t0,
                    end_s=t0 + n * hop,
                    body={
                        "clip_id": clip_id,
                        "metric": metric,
                        "t0": t0,
                        "hop_s": hop,
                        "values": [
                            None if not m else round(float(v), 6)
                            for v, m in zip(values, mask)
                        ],
                    },
                    schema=CLIP_SCORE_TRACK_SCHEMA,
                    attributed_to=attributed_to,
                )
            )
    return out


def _edl_body(e) -> dict:
    """The DECISION body for one entry — each optional field present only when set.

    Emitting one unconditionally would change the body of every document that does
    not use it, which is the difference between an additive field and a format
    change a browser surface has to move with (muvid#34).

    **Every optional field has to be here, and that is the contract rather than a
    courtesy.** ``.claude/CLAUDE.md``: "Round-trip is a contract: EDL -> annotations
    -> EDL must be identity — which is *why* a new ``EdlEntry`` field has to reach
    the body. A field the bridge does not carry is a field the editor silently
    DROPS on the way back." ``look`` was added to ``EdlEntry`` without reaching
    here, so an edit opened in the editor and exported came back ungraded.
    """
    body = {"clip_id": e.clip_id or None}
    if getattr(e, "transition", None) is not None:
        body["transition"] = e.transition.to_dict()
    for field in ("crop", "crop_end"):
        v = getattr(e, field, None)
        if v is not None:
            body[field] = v.to_dict()
    if getattr(e, "look", None) is not None:
        body["look"] = str(e.look)
    # Omit-when-FALSE, not omit-when-None: `look_time_varying` (muvid#73) is a
    # bool whose absent value is `False`, and `False is not None` — an
    # `is not None` test here would have put the key in every DECISION body ever
    # written, which is the format change this rule exists to avoid. `str(e.look)`
    # above is the same care one field over: a `LookFragment` is a `str` subclass
    # and the body must carry the plain value.
    if getattr(e, "look_time_varying", False):
        body["look_time_varying"] = True
    return body


def edl_annotations(entries, *, song_asset_id: str, attributed_to: str) -> list:
    """The DECISION lane: one ``music-video-edl/v1`` per EDL entry, gaps included."""
    return [
        _annotation(
            tier=DECISION_TIER,
            song_asset_id=song_asset_id,
            start_s=e.song_start,
            end_s=e.song_end,
            body=_edl_body(e),
            schema=MUSIC_VIDEO_EDL_SCHEMA,
            attributed_to=attributed_to,
        )
        for e in entries
    ]


def edl_from_annotations(
    annotations: Sequence, *, expected_song_asset_id: str | None = None
) -> list[dict]:
    """DECISION-tier annotations → the ``edl=`` argument, verbatim.

    The timeline-to-EDL half: whatever the editor did to the DECISION lane — moved,
    split, retargeted, deleted — exports as plain ``{song_start, song_end, clip_id}``
    dicts (plus ``transition``/``crop``/``crop_end``/``look``/``look_time_varying``
    where the editor set one) ready for ``assemble_music_video``. Sorting and
    validation stay the render path's business (``fill_gaps`` + ``validate_edl``);
    this is a faithful read.

    Annotations are untrusted editor input, so anything shaped wrong is SKIPPED rather
    than crashing the export: wrong schema/tier, or (an editor could in principle attach
    a ``music-video-edl/v1`` body to an ``AnnotationRef``, whose ``interval`` is
    optional) a reference with no interval to read a span from.

    ``expected_song_asset_id`` is the one thing that RAISES instead (muvid#35). A
    DECISION record pointing at a different song is not another record to filter past —
    it is the whole export being about the wrong project (a stale clipboard, the easy
    mistake in a copy-paste editor UI). Skipping it silently yields an empty or
    half-empty EDL whose eventual ``validate_edl`` complaint names a symptom, never the
    cause. The check is opt-in and evidence-based: no expected id, or a reference kind
    carrying no ``asset_id`` at all (only ``MediaRef`` has one), is nothing to
    contradict — it reports a WRONG song, it does not demand proof of the right one.
    """
    out = []
    for a in annotations:
        if a.body_schema_uri != MUSIC_VIDEO_EDL_SCHEMA or a.tier != DECISION_TIER:
            continue
        # getattr, not isinstance(MediaRef): this function stays lacing-free (it
        # duck-types already-parsed annotations), so importing the model here would
        # newly bind the export path to the 'editor' extra.
        asset_id = getattr(a.reference, "asset_id", None)
        if expected_song_asset_id and asset_id and asset_id != expected_song_asset_id:
            raise ValueError(
                f"DECISION annotation {a.id} is about a different song: its reference "
                f"asset_id is {asset_id!r}, this project's song is "
                f"{expected_song_asset_id!r}. Exporting it would splice another "
                "project's timeline into this one."
            )
        iv = a.reference.interval
        if iv is None:
            continue
        entry = {
            "song_start": iv.start.to_seconds(),
            "song_end": iv.end.to_seconds(),
            "clip_id": a.body.get("clip_id"),
        }
        # Skip-shaped, NOT raising — deliberately the opposite of `_as_entry`, which
        # raises on the same malformed input. The difference is the author: that one
        # reads a caller's request, where dropping a direction silently is the bug;
        # this one reads a browser's output, where crashing the whole export over one
        # bad record is. A transition that survives here is validated by
        # `validate_edl` on the way back in, like everything else in this dict.
        raw = a.body.get("transition")
        if isinstance(raw, dict) and isinstance(raw.get("duration_s"), (int, float)):
            entry["transition"] = {
                "duration_s": float(raw["duration_s"]),
                "curve": str(raw.get("curve", "fade")),
            }
        for field in ("crop", "crop_end"):
            raw = a.body.get(field)
            if isinstance(raw, dict) and all(
                isinstance(raw.get(k), (int, float)) for k in ("x", "y", "w", "h")
            ):
                entry[field] = {k: float(raw[k]) for k in ("x", "y", "w", "h")}
        # Same skip-shaped read: a look is a filter fragment, and whether THIS one
        # is acceptable is `validate_edl`'s single-gate business on the way back in
        # — never this function's, which only settles the type. A non-string is an
        # editor bug, not an edit, so it is dropped rather than crashing the export.
        raw = a.body.get("look")
        if isinstance(raw, str):
            entry["look"] = raw
        # Same skip-shaped read, and `isinstance(..., bool)` rather than a truth
        # test on purpose: `_as_entry` RAISES on a non-bool (bool("false") is
        # True, which would arm a warning the caller asked to be off), so
        # forwarding a string from here would turn an editor's bug into an
        # export the render path refuses. A missing or wrong-typed flag reads as
        # the field's own default.
        raw = a.body.get("look_time_varying")
        if isinstance(raw, bool) and raw:
            entry["look_time_varying"] = True
        out.append(entry)
    return sorted(out, key=lambda e: e["song_start"])


def editor_document(proj, *, attributed_to: str = "") -> dict:
    """The whole project as one lacing-native document for a multitrack editor.

    Tiers: one lane group per clip (alignment + its score sub-tracks) + the DECISION
    lane. The EDL rendered into DECISION is the current default proposal; an editor
    mutates that tier and exports it back through :func:`edl_from_annotations`.
    """
    from muvid.footage.edl import fill_gaps, validate_edl
    from muvid.footage.scoring.grid import load_tensor
    from muvid.footage.strategy import DEFAULT_STRATEGY, select_edl

    if not proj.has_song():
        raise ValueError("no song set — call set_song first")
    aligns = proj.load_alignments()
    song_dur = proj.song_duration()
    song_asset_id = proj.song_hash()
    who = attributed_to or f"user:{proj.email}"

    annotations = alignment_annotations(
        aligns, song_asset_id=song_asset_id, attributed_to=who
    )
    tensor = load_tensor(proj.root)
    if tensor is not None:
        annotations += score_track_annotations(
            tensor, song_asset_id=song_asset_id, attributed_to=who
        )
    overlapping = [a for a in aligns if a.overlaps]
    decision_error = None
    if overlapping:
        try:
            entries = validate_edl(
                fill_gaps(select_edl(DEFAULT_STRATEGY, aligns, song_dur), song_dur),
                aligns,
                song_dur,
                canvas=proj.canvas(),
            )
        except (ValueError, KeyError) as e:
            # The alignment + score-track annotations below are independently good —
            # only the DEFAULT proposal failed to build (e.g. a self-inconsistent
            # persisted alignment, or a legitimate selection past MAX_EDL_ENTRIES). An
            # editor can still open the document and place its own DECISION entries;
            # losing the whole export over an unrelated failure would not.
            decision_error = str(e)
        else:
            annotations += edl_annotations(
                entries, song_asset_id=song_asset_id, attributed_to=who
            )

    tiers = [{"name": DECISION_TIER, "stereotype": "NONE"}] + [
        {"name": f"clip:{a.clip_id}", "stereotype": "NONE"} for a in aligns
    ]
    return {
        "project_id": proj.project_id,
        "song_asset_id": song_asset_id,
        "song_duration": song_dur,
        "time_rate": TIME_RATE,
        "tiers": tiers,
        "annotations": [a.model_dump(mode="json") for a in annotations],
        # None on the healthy path. When set, the DECISION tier has no default proposal
        # (everything else in the document is still good) — an editor should say so
        # rather than silently show an empty lane.
        "decision_error": decision_error,
    }
