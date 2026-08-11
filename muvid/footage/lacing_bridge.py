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


def edl_annotations(entries, *, song_asset_id: str, attributed_to: str) -> list:
    """The DECISION lane: one ``music-video-edl/v1`` per EDL entry, gaps included."""
    return [
        _annotation(
            tier=DECISION_TIER,
            song_asset_id=song_asset_id,
            start_s=e.song_start,
            end_s=e.song_end,
            body={"clip_id": e.clip_id or None},
            schema=MUSIC_VIDEO_EDL_SCHEMA,
            attributed_to=attributed_to,
        )
        for e in entries
    ]


def edl_from_annotations(annotations: Sequence) -> list[dict]:
    """DECISION-tier annotations → the ``edl=`` argument, verbatim.

    The timeline-to-EDL half: whatever the editor did to the DECISION lane — moved,
    split, retargeted, deleted — exports as plain ``{song_start, song_end, clip_id}``
    dicts ready for ``assemble_music_video``. Sorting and validation stay the render
    path's business (``fill_gaps`` + ``validate_edl``); this is a faithful read.
    """
    out = []
    for a in annotations:
        if a.body_schema_uri != MUSIC_VIDEO_EDL_SCHEMA or a.tier != DECISION_TIER:
            continue
        iv = a.reference.interval
        out.append(
            {
                "song_start": iv.start.to_seconds(),
                "song_end": iv.end.to_seconds(),
                "clip_id": a.body.get("clip_id"),
            }
        )
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
    if overlapping:
        entries = validate_edl(
            fill_gaps(select_edl(DEFAULT_STRATEGY, aligns, song_dur), song_dur),
            aligns,
            song_dur,
        )
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
    }
