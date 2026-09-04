"""Compile a ``looks`` artifact into the fragment the assembler splices.

This is muvid's half of the ``looks`` seam — ``looks``' build-order item 10, and
the first real consumer of that package. The division of labour is the whole
point and it is worth stating before any code:

    **``looks`` decides what a pixel becomes. muvid decides which pixels exist,
    when they appear, and how the process is run.**

So ``looks`` emits a ``-vf`` chain fragment and nothing else. It owns no encode
setting: ``-c:v``, ``-crf``, ``-preset``, ``-pix_fmt``, the frame counts, the
concat, the mux and the per-invocation timeout all stay in
:mod:`muvid.footage.assemble`, where the bounded-memory invariant lives. The
fragment rides in :attr:`muvid.footage.edl.EdlEntry.look` and is spliced by
:func:`muvid.footage.assemble._part_filter`.

Why a ``-vf`` fragment is the *cheapest possible* seam, which is why it was
chosen: it adds no ``-i``. muvid's assembler was rewritten (muvid#21/#24) because
one ``-filter_complex`` over all cuts held a decoder per cut and was OOM-killed at
30 cuts on a 3.7 GB box; the guarantee it bought is O(1) decoders per invocation.
A filter fragment cannot touch that.

**A look cannot reach a second source at all, and the earlier claim that it could
via ``movie=`` was wrong twice over.** ``movie=`` is a zero-input source filter,
so at the solo site — a *simple* filtergraph, one input and one output — it leaves
the preceding chain unconsumed and ffmpeg refuses the whole graph before decoding
a frame (*"had 1 input(s) and 2 output(s)"*, measured on ffmpeg 9.0.1; every
composited form, ``movie=X,overlay=10:10`` included, fails identically, and the
forms that DO render all need ``[``/``]``/``;``). At the transition site it does
run, and what it does there is call ``avformat_open_input`` on a path from inside
the fragment — a second container decoder that no invocation accounted for, which
is the muvid#21/#24 invariant going out the back door. So
:func:`muvid.footage.edl._validate_look` refuses it, along with every other filter
outside :data:`muvid.footage.edl.LOOK_FILTERS`. Compositing needs a second splice
site the assembler does not have; that is a change to the assembler, not a look.

Three functions, in the order you are likely to want them:

- :func:`punch_in` — the in-shot zoom the design partner asked for (muvid#66).
- :func:`motion` — an arbitrary camera path over the cut; ``punch_in`` is a
  named case of it.
- :func:`stylize` — a whole :class:`looks.Look` (grade, LUT, posterise …)
  compiled against the binary muvid will actually run.

:func:`chain` puts two of them on one cut.

**Which binary.** The fleet has two ffmpegs in routine use and neither is a
superset of the other (``looks``' research note 11 §3.4 measured 484 vs 481
filters, with ``zscale`` in one and ``drawtext`` in the other). ``looks`` refuses
to resolve one — the environment is an argument — and muvid is the party entitled
to answer, because muvid owns the invocation. muvid runs the bare name ``ffmpeg``
from ``PATH`` (:func:`muvid.visualize.ffmpeg.run_ffmpeg`), so that is what
:func:`stylize` probes. Pass ``env=`` to compile against a different one.

**One limitation, measured rather than guessed at, and not fixable here.** A
*time-varying* look on a cut that borders a :class:`~muvid.footage.edl.Transition`
**restarts its ramp on the blended part**. The assembler renders a transitioned
boundary as a separate two-input invocation whose inputs are input-side-seeked to
the blend window, so the filter clock there begins at 0 again instead of
continuing the cut's.

Measured on a 3.0 s cut at 25 fps with a 0.4 s fade and a 1.12x punch: the solo
part's last frame is drawn at zoom 1.109 (mean |diff| 23.1/255 against the same
frame rendered without the look), while the blend part's first frame is drawn at
zoom 1.000 (mean |diff| 0.5/255 against its own unlooked twin — i.e.
indistinguishable from no punch at all). The move snaps back for the length of
the blend and then the next cut begins.

A **static** look — a grade, a LUT, a posterise — is unaffected, because it never
reads the clock, and it is what the seam is mostly for. For a *moving* look,
either keep it off transitioned boundaries or accept the hitch. muvid cannot
rebase the fragment without rewriting an arbitrary ffmpeg expression, which is
exactly what ``looks`` refuses to do for itself (its rule 27), and a wrong rebase
is worse than a documented one: it would exit 0 having moved the effect to a
different second of the clip. Tracked as muvid#73.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional, Sequence

#: How far :func:`punch_in` zooms by default. A *slight* move: the design partner
#: described what he wanted as a "slight zoom/punch-in jump" (muvid#66), and a
#: punch-in is a punctuation mark, not a transition. Larger values magnify the
#: source, so a value that looks fine on a 4K clip is soft on a 720p one — which
#: is why it is a named constant a caller overrides rather than a hidden literal.
DEFAULT_PUNCH_ZOOM = 1.12

#: Where :func:`punch_in` zooms toward, as a fraction of the canvas. Centre.
DEFAULT_PUNCH_ANCHOR = (0.5, 0.5)


class LookError(ValueError):
    """A look could not be compiled. Carries what to do about it."""


def _require_looks():
    """Import ``looks``, translating its absence into an actionable error.

    ``looks`` is a declared dependency and stdlib-only, so this practically
    always succeeds; the branch exists because an ``ImportError`` from three
    frames down names a module and not a remedy.
    """
    try:
        import looks
    except ImportError as exc:  # pragma: no cover - a declared dependency
        raise LookError(
            "the `looks` package is needed to compile a look, and it is not "
            "importable. It is a declared muvid dependency and has no "
            "dependencies of its own: `pip install looks`."
        ) from exc
    return looks


@lru_cache(maxsize=8)
def _probe(ffmpeg: str):
    """Probe one ffmpeg binary, once per process.

    Cached because a probe is a subprocess and a score-driven edit compiles a
    look per cut — 30-70 of them. Keyed on the binary NAME as given, which is
    the same key muvid runs under; ``looks`` caches again on the resolved path.
    """
    return _require_looks().probe(ffmpeg)


def _canvas(canvas) -> "tuple[int, int]":
    w, h = int(canvas[0]), int(canvas[1])
    if w <= 0 or h <= 0:
        raise LookError(f"canvas must be positive, got {w}x{h}.")
    return w, h


def chain(*fragments: "Optional[str]") -> "Optional[str]":
    """Join look fragments into one, dropping the empty ones.

    A cut carries at most one look, so two effects on one cut are one chain.
    Returns ``None`` when nothing survives, which is the value the EDL field
    wants for "no look" — an empty string is refused by ``validate_edl``
    deliberately, so this does not produce one.

    >>> chain("hue=s=0", None, "", "unsharp=5:5:1")
    'hue=s=0,unsharp=5:5:1'
    >>> chain(None, "") is None
    True
    """
    kept = [f.strip() for f in fragments if f and f.strip()]
    return ",".join(kept) or None


def motion(keyframes: Sequence, *, canvas, fps: float) -> str:
    """A camera path over the cut, as a filter fragment. ``looks`` picks the filter.

    Args:
        keyframes: ``(t_seconds, window)`` pairs, or :class:`looks.Keyframe`s.
            The window is anything with ``x``/``y``/``w``/``h`` as fractions —
            :class:`muvid.footage.edl.CropWindow` satisfies that structurally,
            with no adapter, because both packages use ``burns.Rect``'s
            convention on purpose.
        canvas: ``(width, height)`` — the assembler's delivery canvas.
        fps: the assembler's delivery frame rate.

    Returns:
        One linear ffmpeg filter chain, ready for the ``look`` field.

    **The windows are fractions of the CANVAS, not of the source.** That follows
    from where the fragment is spliced: after ``scale``/``pad``, so the frame it
    sees is the canvas with the source letterboxed into it. It is also what makes
    the move well-defined across a mixed-device edit — the same punch reads the
    same on a portrait phone clip and a landscape one, where a source-relative
    window would not. Use ``crop``/``crop_end`` on the EDL entry for the
    source-relative framing decision; the two compose, crop first.

    Which ffmpeg filter this becomes is ``looks``' decision and not a matter of
    taste: a constant-size window is ``crop``, a resizing one is ``zoompan``, and
    ``crop`` cannot resize at all (its ``w``/``h`` are evaluated once, at
    configure time, when ``t`` is NAN — it either refuses to configure or, worse,
    exits 0 having rendered every frame at one wrong size).

    Raises:
        LookError: If the path needs something that was not supplied.
    """
    looks = _require_looks()
    w, h = _canvas(canvas)
    frames = [
        k if isinstance(k, looks.Keyframe) else looks.Keyframe(float(k[0]), k[1])
        for k in keyframes
    ]
    try:
        return looks.compile_motion(frames, output=looks.Size(w, h), fps=float(fps))
    except looks.MotionError as exc:
        raise LookError(str(exc)) from exc


def punch_in(
    *,
    canvas,
    fps: float,
    duration_s: float,
    zoom: float = DEFAULT_PUNCH_ZOOM,
    anchor: "tuple[float, float]" = DEFAULT_PUNCH_ANCHOR,
    start_s: float = 0.0,
    end_s: "Optional[float]" = None,
) -> str:
    """An in-shot punch-in: hold, then push in, WITHOUT leaving the shot (muvid#66).

    The design partner asked for "roughly 2N" of these and was explicit that it
    is **not** a transition between two clips — it stays on the same shot. Before
    this there was no punch-in, zoom or Ken Burns move anywhere in the footage
    path; the only two mentions of ``zoom`` under ``footage/`` were comments
    explaining why ``zoompan`` was unusable, and that reading has since been
    measured wrong: ``t`` is undefined inside ``zoompan``, but ``in_time`` works
    and ``d=1`` is exactly 1:1 rather than the frame-duplicating default. Both
    corrections live in ``looks.compile_motion``, which is why this function is
    six lines of geometry and no ffmpeg.

    Args:
        canvas: ``(width, height)`` — the assembler's delivery canvas.
        fps: the assembler's delivery frame rate.
        duration_s: the cut's length in seconds. The move ends here by default.
        zoom: final magnification. ``1.12`` shows ~89% of the frame.
        anchor: what stays put, as a fraction of the canvas. ``(0.5, 0.5)``
            centres it; ``(0.5, 0.35)`` pushes toward a face in the upper third.
        start_s: hold the full frame until here, then move.
        end_s: reach the final framing here and hold. Defaults to ``duration_s``.

    Returns:
        One linear ffmpeg filter chain, ready for the ``look`` field.

    A move is a ramp between two windows, and the windows are fractions of the
    canvas (see :func:`motion`). The end window keeps the canvas's aspect ratio,
    which is what lets ``zoompan`` deliver at the canvas size with no stretch and
    no reframing crop.

    >>> frag = punch_in(canvas=(640, 360), fps=25, duration_s=3.0)
    >>> frag.startswith("zoompan=d=1:s=640x360:fps=25:")
    True

    A pull-out is this move backwards, so it goes through :func:`motion` with the
    windows in the order you want them, rather than a boolean here.

    Raises:
        LookError: If ``zoom``, the timings or the anchor are out of range.
    """
    from muvid.footage.edl import CropWindow

    w, h = _canvas(canvas)
    if zoom <= 1.0:
        raise LookError(
            f"punch_in needs zoom > 1 (got {zoom}); it magnifies. A move that "
            "pulls OUT starts zoomed and ends full, which is motion() with the "
            "windows in that order — a direction, not a flag."
        )
    ax, ay = float(anchor[0]), float(anchor[1])
    if not (0.0 <= ax <= 1.0 and 0.0 <= ay <= 1.0):
        raise LookError(
            f"punch_in anchor {anchor!r} is outside the canvas. It is the point "
            "that stays put, as fractions: 0 <= x, y <= 1."
        )
    end = duration_s if end_s is None else end_s
    if duration_s <= 0:
        raise LookError(f"punch_in needs a positive duration_s (got {duration_s}).")
    if not (0.0 <= start_s < end <= duration_s):
        raise LookError(
            f"punch_in needs 0 <= start_s < end_s <= duration_s (got start_s="
            f"{start_s}, end_s={end}, duration_s={duration_s}). A move with no "
            "span is a request that renders nothing, which is the silent no-op "
            "muvid refuses elsewhere too."
        )
    side = 1.0 / zoom
    # The anchor is the fixed point: it keeps the same canvas fraction before and
    # after, which for a window of side `side` puts the window's origin at
    # anchor * (1 - side). Centre anchor => a centred window, corner anchor => a
    # window pinned to that corner, both without a special case.
    tight = CropWindow(x=ax * (1.0 - side), y=ay * (1.0 - side), w=side, h=side)
    return motion(
        [(start_s, CropWindow(0.0, 0.0, 1.0, 1.0)), (end, tight)],
        canvas=(w, h),
        fps=fps,
    )


def stylize(
    look,
    *,
    canvas,
    fps: float,
    duration_s: "Optional[float]" = None,
    ffmpeg: str = "ffmpeg",
    env=None,
    policy=None,
) -> str:
    """A :class:`looks.Look` compiled against the binary muvid will run.

    Args:
        look: a :class:`looks.Look` — an ordered stack of named effects.
        canvas: ``(width, height)`` — the assembler's delivery canvas.
        fps: the assembler's delivery frame rate.
        duration_s: the cut's length, when a step needs to know it.
        ffmpeg: which binary to probe. Defaults to the bare name muvid runs.
        env: a :class:`looks.FfmpegEnv` to compile against, instead of probing.
        policy: a :class:`looks.Policy` — the licence ceiling. ``looks``' default
            applies when omitted.

    Returns:
        One linear ffmpeg filter chain, ready for the ``look`` field.

    **The clip is declared, not measured.** ``looks`` compiles against a
    *declared* clip, and at this splice point muvid knows the geometry and the
    rate exactly — they are the canvas and the delivery rate the assembler is
    about to impose, not properties of whichever source is underneath. That is
    the payoff of splicing after ``scale``/``pad``/``fps`` rather than before it.

    ``origin_s=0.0`` is declared for the same kind of reason and is true for a
    stated cause rather than by default: ``looks`` treats a span as being in the
    host's decoder time, and *input-side* ``-ss`` rebases the filter timeline to
    0 where output-side ``-ss`` does not. :func:`muvid.footage.assemble._render_part`
    seeks input-side. Move that seek and this declaration becomes false.

    **A refusal is the feature.** ``looks`` resolves each step's licence tier
    against the probed binary, so a look reaching a GPL-only filter on an
    LGPL build is either substituted or refused by name — rather than working on
    a laptop and quietly raising the licence tier of a shipped product.

    Raises:
        LookError: If the look cannot be compiled for this binary, this ceiling
            or this clip. The message is ``looks``', which names the remedy.
    """
    looks = _require_looks()
    w, h = _canvas(canvas)
    resolved = _probe(ffmpeg) if env is None else env
    clip = looks.ClipSpec(
        width=w,
        height=h,
        fps=float(fps),
        duration_s=duration_s,
        origin_s=0.0,
    )
    try:
        plan = looks.compile_look(look, clip=clip, env=resolved, policy=policy)
        return looks.vf(plan)
    except (
        looks.CompileError,
        looks.LooksLicenceError,
        looks.SpecError,
        looks.FfmpegBackendError,
    ) as exc:
        raise LookError(str(exc)) from exc


def punch_in_cuts(
    entries: Sequence,
    *,
    canvas,
    fps: float,
    every: int = 2,
    zoom: float = DEFAULT_PUNCH_ZOOM,
    anchor: "tuple[float, float]" = DEFAULT_PUNCH_ANCHOR,
    offset: int = 0,
) -> "list[Any]":
    """Put a punch-in on every ``every``-th footage entry — muvid#66's other half.

    The request was for *roughly 2N* punch-ins "evenly redistributed so they
    occur about twice as often", explicitly **not** two extra tacked on the end.
    Redistribution is therefore the whole job, and it is a stride over the cuts
    rather than a wall-clock interval: cuts are already beat-snapped by the
    selector, so a stride lands the moves on musical time for free, where a
    seconds-based interval would drift off it.

    Args:
        entries: validated :class:`~muvid.footage.edl.EdlEntry` objects.
        canvas: ``(width, height)`` — the delivery canvas.
        fps: the delivery frame rate.
        every: stride. ``2`` punches every other footage cut; ``1`` punches all.
        zoom: passed to :func:`punch_in`.
        anchor: passed to :func:`punch_in`.
        offset: which footage cut in each stride gets the move.

    Returns:
        A NEW list of entries. Gaps are skipped (they have no footage to punch
        into, and ``validate_edl`` refuses a look on one), and an entry that
        already carries a look keeps it — this composes with hand-authoring
        rather than overwriting it, because silently replacing an authored
        direction is the failure this package guards against elsewhere.
    """
    from dataclasses import replace

    if every < 1:
        raise LookError(f"`every` must be >= 1 (got {every}); it is a stride.")
    out, n = [], 0
    for e in entries:
        if e.is_gap or e.look is not None:
            out.append(e)
            continue
        if n % every == offset % every:
            e = replace(
                e,
                look=punch_in(
                    canvas=canvas,
                    fps=fps,
                    duration_s=e.song_end - e.song_start,
                    zoom=zoom,
                    anchor=anchor,
                ),
            )
        n += 1
        out.append(e)
    return out
