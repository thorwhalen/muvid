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
    MIN_MARGIN,
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
    "MIN_MARGIN",
    "MIN_SUPPORT",
    "WINDOW_PARAMETERS",
    "align_footage",
    "vouches_for",
]

#: Estimator parameters :func:`align_footage` refuses to forward, because changing them
#: changes what :data:`~muvid.footage.edl.MIN_SUPPORT` MEANS. As of ``mixing>=0.0.51``
#: (mixing#43), an explicit ``window_s`` wider than a clip can hold — above
#: ``clip_duration - hop_s`` — is refused by ``mixing`` itself with a typed
#: ``WindowTooWideForClip``, so that direction no longer reaches muvid as a silent
#: ``support=None``. Narrowing the window is not caught by that refusal and still
#: deflates every support — measured across eleven clip lengths, correct alignments read
#: 0.38-0.70 at a 20 s window and 0.00-1.00 at a 4 s one — so correct alignments fall
#: under the threshold instead. Refusing here regardless of what ``mixing`` does about
#: the wide-window case keeps the two packages from disagreeing about it later, and keeps
#: the calibration decision (moving ``MUVID_FOOTAGE_MIN_SUPPORT`` if the window moves)
#: something a person makes out loud rather than a side effect of a keyword — the
#: refusal at ``mixing``'s own entry point is a different failure mode for a different
#: caller, not a substitute for muvid's own gate.
#:
#: (Since ``mixing>=0.0.48``, passing ``window_s`` alone also pairs it with
#: ``hop = window/2``, so the two are one knob at the estimator too. Another reason not
#: to accept half of it through a keyword.)
#:
#: Refused rather than merely documented for the reason the rest of this package refuses
#: things: an argument that quietly turns a safety check off is worse than one that is
#: not accepted at all. Tuning the window is legitimate — it just has to move
#: ``MUVID_FOOTAGE_MIN_SUPPORT`` with it, which is a decision someone has to make out
#: loud rather than a side effect of a keyword.
WINDOW_PARAMETERS = ("window_s", "hop_s")


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

    **A verdict already on disk is never re-judged, and re-aligning is how you re-judge
    it.** A ``reliable=True`` written by muvid 0.0.52 or earlier was reached against the
    UNGRADED support scale, where the muvid#59 shoot's own correct offsets scored 0.42
    and 0.46 — numbers the current threshold refuses. Those records keep their verdicts
    deliberately (:meth:`~muvid.footage.edl.FootageAlignment.from_dict` derives only an
    ABSENT verdict), because a stored verdict is a measurement someone's project already
    depends on, not a value to reinterpret under a scale it never saw. Calling this
    function again is the supported way to get a verdict on today's terms.
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

            **The window parameters are the exception and are REFUSED** — see
            :data:`WINDOW_PARAMETERS`. They do not tune the estimator so much as re-scale
            the gate that reads it, and muvid refuses them at this entry point regardless
            of whether ``mixing`` itself would also refuse or silently answer.
    """
    from mixing.audio import align_clips_to_reference  # lazy: heavy

    _refuse_window_parameters(estimator_kwargs)
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


def _refuse_window_parameters(estimator_kwargs: dict) -> None:
    """Refuse the kwargs that silently re-scale the support gate — see
    :data:`WINDOW_PARAMETERS`.

    Raises ``TypeError``, matching what ``mixing`` itself raises for a keyword it does
    not accept, so a caller sweeping estimator parameters meets one failure mode rather
    than two.
    """
    named = [k for k in WINDOW_PARAMETERS if k in estimator_kwargs]
    if not named:
        return
    raise TypeError(
        f"align_footage() will not forward {', '.join(named)}: the analysis window is "
        f"what MIN_SUPPORT is calibrated against, so changing it here would re-scale "
        f"the trust gate without saying so. Narrowing it deflates every support, which "
        f"still reaches the gate silently; widening it past what a clip can hold is now "
        f"refused by mixing itself (WindowTooWideForClip, mixing>=0.0.51), but muvid "
        f"refuses the keyword regardless, so the calibration decision stays a deliberate "
        f"one on this side too. There is no in-pipeline escape: the window is not "
        f"tunable through this entry point, deliberately. If what you want is a "
        f"different THRESHOLD, set MUVID_FOOTAGE_MIN_SUPPORT. If you genuinely need a "
        f"different window, call mixing.audio.align_clips_to_reference yourself and "
        f"build your own FootageAlignment records from what it returns — you are then "
        f"outside this gate, and choosing a threshold for that window is on you."
    )


def _as_alignment(clip_id: str, a) -> FootageAlignment:
    """One ``mixing.audio.ClipAlignment`` → muvid's record, verdict included.

    ``support``, ``window_s`` and ``hop_s`` are read BY NAME, and absence is ``None``
    rather than an error. The read stays defensive even though the ``mixing>=0.0.48``
    floor guarantees all three: a floor is a claim about what pip *declared*, and this
    is the one line that would turn a wrong claim into an ``AttributeError`` mid-shoot
    rather than into a clip that quietly aligns. The grid comes with the number because
    the estimator fits its window to the clip, so ``support`` without ``window_s`` is a
    fraction whose denominator nobody can see.
    """

    def read(name: str) -> "float | None":
        v = getattr(a, name, None)
        return None if v is None else float(v)

    support = read("support")
    window_s = read("window_s")
    margin = read("margin")
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
        reliable=vouches_for(
            confidence=a.confidence,
            support=support,
            margin=margin,
            window_s=window_s,
        ),
        window_s=window_s,
        hop_s=read("hop_s"),
        margin=margin,
    )
