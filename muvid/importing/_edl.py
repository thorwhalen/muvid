"""Turn a production's own edit list into muvid's — the EDL converter of the importer.

Finished productions were cut before muvid could hold their edits, and wrote them in
their own shape. The one this knows besides muvid's own is the Que Calor planner's
(``work/build/edl_v1d.json`` and the v3 list), a document::

    {"out_w": .., "out_h": .., "fps": .., "song_end": .., "offsets": {..},
     "sources": {clip_id: path}, "edl": [
        {"song_start": 0.162, "song_end": 7.7267, "clip_id": "c03",
         "clip_in": 8.621, "framing": {"w": 1024, "h": 576,
                                       "x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0}, ...}]}

``framing`` is a crop window in the SOURCE's pixels that moves from ``(x0, y0)`` to
``(x1, y1)`` across the cut. muvid's :class:`~muvid.footage.edl.CropWindow` is the same
window as fractions of the source frame, and a moving one is ``crop`` → ``crop_end`` —
so the conversion is a division by the source's DISPLAYED size (after rotation), and a
window that is the whole frame is no crop at all. The planner's own fields
(``clip_in``, ``bars``, ``energy``, ``score``) are dropped: ``clip_in`` is derived by
muvid from the offset (``derive_cuts`` is the ONE place), the rest is planning evidence.

A document that is already a list of muvid EDL dicts (or ``{"edl": [...]}`` of them)
passes through unchanged.
"""

from __future__ import annotations

from typing import Mapping, Sequence

#: A framing move smaller than this (source pixels) is float noise, not a pan — the same
#: threshold the Que Calor pipeline's own muvid bridge (``via_muvid.py``) used.
PAN_THRESHOLD_PX = 0.5
#: A window within this many pixels of the whole frame, at the origin, is no crop.
FULL_FRAME_TOLERANCE_PX = 0.5


def is_framing_edl(doc) -> bool:
    """Whether ``doc`` is a planner document whose entries carry pixel ``framing``."""
    entries = doc.get("edl") if isinstance(doc, Mapping) else doc
    return isinstance(entries, Sequence) and any(
        isinstance(e, Mapping) and "framing" in e for e in entries
    )


def edl_from_document(
    doc, *, source_sizes: Mapping[str, tuple[int, int]]
) -> list[dict]:
    """The muvid EDL (a list of ``EdlEntry`` dicts) a production's edit document means.

    ``source_sizes`` maps each clip id to its DISPLAYED ``(width, height)`` — needed only
    for a framing document; a clip it does not name is refused rather than guessed.

    >>> doc = {"edl": [
    ...     {"song_start": 0.0, "song_end": 2.0, "clip_id": "a",
    ...      "framing": {"w": 100, "h": 50, "x0": 0, "y0": 0, "x1": 0, "y1": 0}},
    ...     {"song_start": 2.0, "song_end": 4.0, "clip_id": "a",
    ...      "framing": {"w": 50, "h": 25, "x0": 10, "y0": 5, "x1": 40, "y1": 5}}]}
    >>> edl = edl_from_document(doc, source_sizes={"a": (100, 50)})
    >>> "crop" in edl[0], edl[1]["crop"], edl[1]["crop_end"]["x"]
    (False, {'x': 0.1, 'y': 0.1, 'w': 0.5, 'h': 0.5}, 0.4)
    """
    entries = doc.get("edl") if isinstance(doc, Mapping) else doc
    if not isinstance(entries, Sequence):
        raise ValueError("an edit document is a list of entries or {'edl': [...]}")
    return [_entry(e, source_sizes) for e in entries]


def _entry(e: Mapping, source_sizes) -> dict:
    out = {
        "song_start": float(e["song_start"]),
        "song_end": float(e["song_end"]),
        "clip_id": e.get("clip_id") or None,
    }
    if "framing" not in e:
        # Already muvid-shaped: carry every optional field it has, verbatim.
        return dict(e) | out
    framing = e["framing"]
    if out["clip_id"] is None or not framing:
        return out
    if out["clip_id"] not in source_sizes:
        raise ValueError(
            f"no frame size for clip {out['clip_id']!r}; the framing is in its pixels"
        )
    width, height = source_sizes[out["clip_id"]]
    crop, crop_end = _crop_windows(framing, width=width, height=height)
    if crop is not None:
        out["crop"] = crop
    if crop_end is not None:
        out["crop_end"] = crop_end
    return out


def _crop_windows(framing: Mapping, *, width: int, height: int):
    """``(crop, crop_end)`` as fraction dicts; ``(None, None)`` for the whole frame."""
    w, h = float(framing["w"]), float(framing["h"])
    x0, y0 = float(framing.get("x0", 0.0)), float(framing.get("y0", 0.0))
    x1, y1 = float(framing.get("x1", x0)), float(framing.get("y1", y0))
    moves = abs(x1 - x0) > PAN_THRESHOLD_PX or abs(y1 - y0) > PAN_THRESHOLD_PX
    whole = (
        not moves
        and abs(w - width) <= FULL_FRAME_TOLERANCE_PX
        and abs(h - height) <= FULL_FRAME_TOLERANCE_PX
        and abs(x0) <= FULL_FRAME_TOLERANCE_PX
        and abs(y0) <= FULL_FRAME_TOLERANCE_PX
    )
    if whole:
        return None, None

    def window(x: float, y: float) -> dict:
        return {"x": x / width, "y": y / height, "w": w / width, "h": h / height}

    return window(x0, y0), (window(x1, y1) if moves else None)


__all__ = ["edl_from_document", "is_framing_edl", "PAN_THRESHOLD_PX"]
