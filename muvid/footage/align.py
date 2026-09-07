"""Align a set of footage clips to the song — a thin wrapper over ``mixing.audio``.

Each clip is a video file; its audio track (the different-device capture of the song) is
what we align. ``mixing.audio.align_clips_to_reference`` loads each clip's audio via
pydub/ffmpeg (so a video path works directly — no separate extraction, no moviepy), and
returns offsets + a scale-invariant confidence + coverage clamped to the song timeline.

muvid does not estimate offsets itself — the estimator lives in ``mixing``, which is
where the multi-device primitive and its measurements belong — but muvid is the one that
turns a measurement into a cut, so muvid is where "we are not sure" stops being a number
and becomes a refusal (muvid#59). The **verdict** that makes that possible is
:func:`~muvid.footage.edl.vouches_for`, re-exported here beside the aligner that applies
it; it is defined in :mod:`muvid.footage.edl`, beside the record it judges and the gate
(:func:`~muvid.footage.edl.validate_edl`) that enforces it.
"""

from __future__ import annotations

from typing import Sequence

from muvid.footage.edl import (
    MIN_CONFIDENCE,
    MIN_SUPPORT,
    FootageAlignment,
    vouches_for,
)

#: Analysis sample rate for alignment (mono). 16 kHz is plenty for offset precision.
ALIGN_SAMPLE_RATE = 16000

#: Re-exported so ``muvid.footage.align`` reads as the whole alignment story — the
#: measurement AND the verdict — without either of them having two definitions.
__all__ = [
    "ALIGN_SAMPLE_RATE",
    "MIN_CONFIDENCE",
    "MIN_SUPPORT",
    "align_footage",
    "vouches_for",
]


def align_footage(
    song_path: str,
    clips: Sequence[tuple],
    *,
    song_duration: float | None = None,
    sample_rate: int = ALIGN_SAMPLE_RATE,
    **estimator_kwargs,
) -> list[FootageAlignment]:
    """Align ``clips`` (``[(clip_id, clip_path), ...]``) to the song at ``song_path``.

    Returns one :class:`~muvid.footage.edl.FootageAlignment` per clip, keyed by ``clip_id``
    — including clips that do not overlap the song, which carry ``overlaps=False`` rather
    than being omitted, and clips the aligner could not vouch for, which carry
    ``reliable=False`` rather than being omitted. Nothing may vanish from the record just
    because it matched badly; what a bad match loses is the right to be cut to silently
    (:func:`~muvid.footage.edl.validate_edl` is where that is enforced).
    Heavy deps (mixing.audio → numpy/scipy/pydub) are imported lazily here so importing the
    genre stays light.

    Args:
        song_path: The clean master every clip is aligned to.
        clips: ``(clip_id, clip_path)`` pairs; a video path works directly.
        song_duration: The song timeline length; probed from ``song_path`` when omitted.
        sample_rate: Analysis sample rate (mono).
        **estimator_kwargs: Passed straight to ``mixing.audio.align_clips_to_reference``
            — the seam for the windowed-consensus estimator's parameters (muvid#59).
            Forwarding rather than enumerating keeps muvid out of the business of
            tracking ``mixing``'s estimator vocabulary, and an argument the installed
            ``mixing`` does not accept raises :class:`TypeError` from ``mixing`` naming
            it. That failure is the point: a window parameter silently ignored is a
            caller believing they tuned an estimator that never saw the value.
    """
    from mixing.audio import align_clips_to_reference  # lazy: heavy

    clip_ids = [cid for cid, _ in clips]
    clip_paths = [str(p) for _, p in clips]
    aligned = align_clips_to_reference(
        song_path,
        clip_paths,
        reference_duration=song_duration,
        sample_rate=sample_rate,
        **estimator_kwargs,
    )
    return [_as_alignment(clip_ids[a.index], a) for a in aligned]


def _as_alignment(clip_id: str, a) -> FootageAlignment:
    """One ``mixing.audio.ClipAlignment`` → muvid's record, verdict included.

    ``support`` is read BY NAME off whatever ``mixing`` returned, and its absence is
    ``None`` rather than an error: an estimator that takes a single whole-clip
    measurement has no support to report, and that is a supported estimator, not a
    broken one. Reading it this way is also the wiring: when ``mixing``'s consensus
    estimator starts reporting a support fraction, muvid picks it up and
    :func:`vouches_for` switches to the stronger test with no change here.
    """
    support = getattr(a, "support", None)
    support = None if support is None else float(support)
    return FootageAlignment(
        clip_id=clip_id,
        offset_s=a.offset_s,
        confidence=a.confidence,
        duration_s=a.duration_s,
        coverage=a.coverage,
        overlaps=a.overlaps,
        support=support,
        # A clip that does not overlap the song has no offset worth vouching for, but
        # it is `overlaps` that says so — stacking a second False on it would make two
        # different facts read as one, and the caller-facing report distinguishes them.
        reliable=vouches_for(confidence=a.confidence, support=support),
    )
