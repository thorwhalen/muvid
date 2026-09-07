"""Align a set of footage clips to the song — a thin wrapper over ``mixing.audio``.

Each clip is a video file; its audio track (the different-device capture of the song) is
what we align. ``mixing.audio.align_clips_to_reference`` loads each clip's audio via
pydub/ffmpeg (so a video path works directly — no separate extraction, no moviepy), and
returns offsets + a scale-invariant confidence + coverage clamped to the song timeline.

This module also owns the **verdict**: whether the aligner vouches for an offset at all
(muvid#59). muvid does not estimate offsets itself — the estimator lives in ``mixing``,
which is where the multi-device primitive and its measurements belong — but muvid is the
one that turns a measurement into a cut, so muvid is where "we are not sure" has to stop
being a number and start being a refusal. :func:`vouches_for` is that single decision;
:func:`~muvid.footage.edl.validate_edl` enforces it.
"""

from __future__ import annotations

import os
from typing import Sequence

from muvid.footage.edl import FootageAlignment

#: Analysis sample rate for alignment (mono). 16 kHz is plenty for offset precision.
ALIGN_SAMPLE_RATE = 16000

#: Below this, a whole-clip correlation coefficient does not vouch for its offset
#: (env ``MUVID_FOOTAGE_MIN_CONFIDENCE``). Calibrated for the clean-master-vs-phone
#: regime the connector actually sees, on the onset-envelope feature (muvid#15):
#: measured against a studio master, four provably-correct clips scored 0.173-0.603
#: while the one genuinely unrelated clip scored 0.021 — so 0.1 separates them ~2x/5x,
#: where the old 0.3 (a defensible number for the EASIER clip-to-clip regime) flagged
#: five of six correct alignments as suspect.
#:
#: **Read it as the weak instrument it is.** muvid#59 is the demonstration: on a
#: repetitive track the three wrong offsets scored 0.086, 0.121 and 0.086, so this
#: threshold catches two of the three and lets the third — 83 s wrong — through. A
#: coefficient cannot see the runner-up peak that makes it a coin flip; only
#: :data:`FootageAlignment.support` can. This is the fallback for an aligner that
#: reports no support, not the measure of record.
MIN_CONFIDENCE = float(os.environ.get("MUVID_FOOTAGE_MIN_CONFIDENCE", "0.1"))

#: Below this fraction of agreeing windows, a consensus offset does not vouch for
#: itself (env ``MUVID_FOOTAGE_MIN_SUPPORT``). On the shoot behind muvid#59 the three
#: verified-correct offsets carried 10/24, 17/37 and 45/61 windows — 0.42, 0.46 and
#: 0.74 — so 0.25 sits comfortably under the worst correct case while still refusing
#: an offset that only a handful of windows ever saw. A spurious peak is an accident
#: of local content and lands at a DIFFERENT lag in each window, so it cannot
#: accumulate support the way a true offset does.
MIN_SUPPORT = float(os.environ.get("MUVID_FOOTAGE_MIN_SUPPORT", "0.25"))


def vouches_for(*, confidence: float, support: float | None) -> bool:
    """Does the aligner vouch for this offset? The ONE place that verdict is reached.

    ``support`` (how much of the clip agrees) decides when the aligner measured it,
    because it is the only one of the two numbers that can tell a clear winner from a
    coin flip — see :data:`FootageAlignment.support`. ``confidence`` decides only when
    there is no support to read, which is the case for a whole-clip estimator, and is
    documented at :data:`MIN_CONFIDENCE` as the weaker test it is.

    Args:
        confidence: The estimator's correlation coefficient at the chosen lag.
        support: Fraction of independent windows agreeing, or ``None`` if unmeasured.

    Returns:
        True when the offset may be cut to without the caller opting in.
    """
    if support is not None:
        return support >= MIN_SUPPORT
    return confidence >= MIN_CONFIDENCE


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
