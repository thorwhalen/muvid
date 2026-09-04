"""Assemble validated cuts into a music video, in BOUNDED memory.

The previous shape — one ffmpeg ``-filter_complex`` pass with one input per cut — held a
decoder context and inter-filter queues for EVERY cut at once, so peak RSS grew roughly
linearly with cut count: a real 30-cut weighted edit needed >2.3 GB and was OOM-killed on
the 3.7 GB production box, while 15 cuts only just fit (muvid#21/#24). Score-driven edits
routinely produce 30-70 cuts, so that shape caps exactly the edits the scoring layer
exists to make.

Now three bounded stages, memory O(1) in cut count:

1. **One intermediate per PART** — a single-input ffmpeg run per cut (input-side ``-ss``,
   so a late cut no longer decodes the whole head of its clip), scaled+padded onto the
   fixed canvas at a constant ``fps``. A gap entry (``clip_path == ""`` — a span of the
   song with no footage) renders as black from a ``color`` source, which is what makes
   partial-coverage edits renderable at all. Each intermediate is cut to an EXACT frame
   count derived from the shared song-time grid (``round(end*fps) - round(start*fps)``);
   sub-frame cuts (0 grid frames) are dropped, an exhausted source clones its last frame
   up to the count (``tpad``), and a cut whose source yields no frames at all falls back
   to black — so total frames equals ``round(song_duration*fps)`` by construction,
   whatever the cut count, source rates, or a clip whose audio outlives its video.

   A boundary carrying a :class:`~muvid.footage.edl.Transition` contributes a fourth
   kind of part: a **two-input** ``xfade``, sitting between the two solos it blends,
   each of which is correspondingly shortened (see :func:`_part_plan`). TWO is the
   number that matters — the bounded-memory guarantee is O(1) in CUT count, and a
   constant number of decoders per invocation keeps it. Reaching for one filtergraph
   over all parts is exactly how the OOM below comes back. The counts still telescope:
   the ``pre``/``post`` terms cancel against the transition length, so the total is
   ``round(song_duration*fps)`` with or without transitions.
2. **Concat by stream copy** (concat demuxer) — no re-encode, no filtergraph.
3. **Mux the clean song** in the same final pass. When the master already IS the delivery
   contract (aac, 48 kHz stereo), its packets are stream-copied bit-identically; anything
   else is encoded to the contract (aac 192k, 48 kHz stereo — verify_video's audio check
   must be a property of the renderer, not of which song the user brought: muvid#24 B3).

ffmpeg auto-applies each clip's rotation metadata on decode (default ``-autorotate 1``),
so a display-matrix portrait clip lands upright and pillarboxed, never stretched.

A cut may also carry a **look** — a compiled ``looks`` filter fragment
(:mod:`muvid.footage.look`) spliced into the per-part chain by :func:`_part_filter`.
It is the federation seam and it is deliberately the cheapest possible one: a ``-vf``
fragment adds no ``-i``, so it cannot move the invariant above. That is enforced rather
than trusted, and by an ALLOWLIST rather than by refusals —
:func:`~muvid.footage.edl._validate_look` accepts only the filters
:data:`~muvid.footage.edl.LOOK_FILTERS` names, and refuses a fragment that names a
container input, that is more than one linear chain, or that is not lexically closed.
The allowlist is what closes ``movie=``/``amovie=`` (a second container opened from
*inside* the fragment, which is the invariant leaving by the back door) and, because
``assemble_music_video`` is a live per-caller MCP tool whose ``edl`` argument is
free-form, the filters that write the host's disk (``metadata=…:file=``,
``deshake=filename=``, ``sendcmd``, ``signature``). The allowlist is a
vocabulary and bounds no PARAMETER, so the frame size a look asks for is bounded
separately against the delivery canvas (muvid#75) — ``scale=8000:8000`` peaks at
328 MB from a 64x48 source against 19 MB for a look that stays at canvas size,
and ``pad``/``zoompan`` reach the same magnitude. **Nor is a size bound a bound on
the OPTIONS that set one**: ``pad``'s ``aspect`` and ``scale``'s
``force_original_aspect_ratio`` both move the frame while declaring no dimension
a bound can read, and on the production canvas both are larger than the case the
size bound refuses (590 MB and 941 MB against 403 MB). So the four filters that
can change the output geometry are allowlisted per OPTION and per positional
slot — see :data:`~muvid.footage.edl._LOOK_GEOMETRY_FILTERS`.

A cut whose look is **time-varying** — a punch-in, a pan, anything reading the
filter clock — additionally makes :func:`_part_plan` **warn** when the cut borders
a transition: the blend is a separate invocation whose inputs are
input-side-seeked, so the clock restarts and the move plays again from the start
(muvid#73). The EDL says which kind a look is
(:attr:`~muvid.footage.edl.EdlEntry.look_time_varying`) because the fragment is a
bare string muvid did not author; rebasing it would mean rewriting an arbitrary
ffmpeg expression, which is what ``looks``' rule 27 refuses.

**Both of :func:`_part_plan`'s findings go to two places**, and the second half
was missing until now: a :class:`AssemblyWarning` on stderr, *and* the ``on_note``
sink the caller's reply is built from. ``assemble_music_video`` is a live
per-caller MCP tool and its caller has no stderr, so a warning that reached only
stderr was a hitch the caller was billed for and never told about — the silent
no-op this module refuses everywhere else. See :func:`_emit`.

Deliberately NOT moviepy (its ``write_videofile`` runs in-process and would escape the
``$MUVID_FFMPEG_TIMEOUT_S`` worker guard). Every stage runs through
:func:`muvid.visualize.ffmpeg.run_ffmpeg`; note the guard is **per invocation** now, so a
render's wall-clock bound is ``(parts + 1) * MUVID_FFMPEG_TIMEOUT_S`` — the env var bounds
a hang, not the total render. Parts equal cuts for an untransitioned edit and approach
``2 * cuts`` when every boundary is transitioned.
"""

from __future__ import annotations

import shutil
import tempfile
import warnings
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from muvid.footage.edl import TRANSITION_SPLIT, AssemblyCut

#: Default output frame rate for the assembled video.
DEFAULT_FPS = 30
#: Default canvas (16:9 1080p) when the caller/genre gives none.
DEFAULT_CANVAS = (1920, 1080)


def _even(n: int) -> int:
    return n - (n % 2)  # H.264/yuv420p needs even dimensions


def _frame_counts(cuts: Sequence[AssemblyCut], fps: int) -> list[int]:
    """Exact frames per cut on the shared song-time grid — drift-proof by construction.

    Quantizing each cut's *boundaries* (not its duration) to the frame grid means the
    counts telescope: their sum is ``round(last_end*fps) - round(first_start*fps)``
    regardless of how many cuts there are. A cut that rounds to ZERO grid frames (the
    EDL's 1 ms span floor is finer than a frame) gets count 0 and is skipped by the
    renderer — flooring it at 1 would add a frame per tiny cut, and 500 hostile 1 ms
    spans would push everything after them ~15 s late. Dropping keeps the sum exact.
    """
    return [round(c.song_end * fps) - round(c.song_start * fps) for c in cuts]


@dataclass(frozen=True)
class _Part:
    """One bounded ffmpeg invocation: exactly one output file, one frame count.

    ``solo`` is the historical shape — one decoder, one cut. ``xfade`` is the only
    two-decoder part, and TWO is the number that matters: the bounded-memory
    guarantee muvid#21/#24 bought is O(1) in CUT count, and a constant number of
    inputs per invocation keeps it. Reaching for one filtergraph over all parts is
    how the OOM comes back.
    """

    kind: str  # "solo" | "xfade"
    cut: AssemblyCut  # for an xfade, the INCOMING cut
    n_frames: int
    clip_in: float
    prev: AssemblyCut | None = None  # xfade only: the outgoing cut
    prev_in: float = 0.0  # xfade only
    curve: str = "fade"


class AssemblyWarning(RuntimeWarning):
    """A render-plan finding the caller should see — not an error, not silence.

    A ``RuntimeWarning`` subclass, so every existing ``pytest.warns(RuntimeWarning)``
    and every stderr reader keeps working. It exists so the *reply* half can pick
    these out: ``warnings.warn`` reaches a developer's stderr and nothing else, and
    :func:`~muvid.mcp.footage_tools.assemble_music_video` is a live per-caller MCP
    tool whose caller has no stderr. A warning the caller cannot see is the silent
    no-op this module refuses everywhere else — so :func:`assemble_music_video`
    takes an ``on_note`` sink and the tool returns what it collects.

    A distinct class rather than a stderr filter or a ``catch_warnings`` block:
    ``catch_warnings`` mutates process-global state and the connector serves
    concurrent callers, so one render could swallow or steal another's warnings.
    """


def _emit(message: str, note=None, *, stacklevel: int = 4) -> None:
    """Raise an :class:`AssemblyWarning` AND hand it to the reply sink.

    Both, never either: the warning is what a developer sees on stderr and what
    ``pytest.warns`` catches, and ``note`` is the only thing a remote MCP caller
    can ever see. Dropping the warning would break every existing stderr reader;
    dropping the note is the muvid#73 half that shipped unnoticed — the finding
    landed in the server process and the caller got an ``ok`` render with the
    hitch in it.
    """
    warnings.warn(message, AssemblyWarning, stacklevel=stacklevel)
    if note is not None:
        note(message)


def _warn_time_varying_looks_on_transitions(
    cuts: Sequence[AssemblyCut], n_trans: Sequence[int], note=None
) -> None:
    """Warn per cut whose MOVING look borders a blended boundary (muvid#73).

    A transitioned boundary is rendered as a separate two-input invocation whose
    inputs are input-side-seeked to the blend window (:func:`_xfade_input`), and
    input-side ``-ss`` rebases the filter timeline to 0 — so a look whose
    expressions read that clock **starts its ramp again** for the length of the
    blend. Measured on a 3.0 s cut at 25 fps with a 0.4 s fade and
    ``punch_in(zoom=1.12)``: the solo part's last frame is drawn at zoom 1.109
    (mean |diff| 28.1/255 against the same frame rendered with no look), the
    blend part's first frame at zoom 1.000 (0.7/255 — indistinguishable from no
    punch at all).

    Warning rather than fixing is the decision recorded in muvid#73: rebasing
    means rewriting an ffmpeg expression muvid did not author and cannot parse,
    which is exactly what ``looks`` refuses to do for itself (its rule 27), and a
    wrong rebase moves the effect to a different second of the clip at exit 0
    with an empty stderr. A documented hitch beats that.

    Three things it deliberately does NOT warn about, each of which is the reason
    :attr:`~muvid.footage.edl.EdlEntry.look_time_varying` had to exist at all:

    - a **static** look on a transitioned boundary — a grade, a LUT, a posterise
      never reads the clock, and warning on every graded transitioned cut is what
      made this warning unimplementable while the seam was a bare string;
    - a **moving** look on a plain cut — one invocation, one clock, nothing to
      restart;
    - a moving look a caller did not DECLARE. The flag defaults to ``False``, so
      an undeclared moving look stays silent. That is the known limit of the
      chosen shape (see the field's own docstring), not an oversight.

    One warning per affected CUT, not per side of a boundary: a cut between two
    transitions has its ramp restarted on both, but that is one thing wrong with
    one cut and the caller acts on it once.
    """
    for i, cut in enumerate(cuts):
        if not (cut.look and cut.look_time_varying):
            continue
        # Its own incoming blend, or the one its successor pulls out of its tail.
        # Both render THIS cut through `_part_filter` in a freshly-seeked
        # invocation, so either is enough to restart the ramp.
        incoming = n_trans[i] > 0
        outgoing = i + 1 < len(cuts) and n_trans[i + 1] > 0
        if not (incoming or outgoing):
            continue
        where = (
            "both of its boundaries are blended"
            if incoming and outgoing
            else (
                "it blends IN from the previous cut"
                if incoming
                else "the next cut blends IN from it"
            )
        )
        _emit(
            f"cut {i}: a time-varying look ({cut.look!r}) borders a transition — "
            f"{where}. The blended part is a separate invocation whose inputs are "
            "input-side-seeked, which rebases the filter clock to 0, so the move "
            "RESTARTS for the length of the blend (measured: a 1.12x punch reaches "
            "zoom 1.109 on the solo part and is redrawn at 1.000 on the blend). "
            "Either drop the transition on this boundary or use a static look "
            "(a grade/LUT is unaffected). muvid cannot rebase the fragment — see "
            "muvid#73.",
            note,
            stacklevel=5,
        )


def _part_plan(cuts: Sequence[AssemblyCut], fps: int, note=None) -> list[_Part]:
    """The ordered ffmpeg jobs for these cuts. Pure — no encoding, no I/O.

    **Per CUT, never per boundary.** The per-boundary reading is the tempting one
    and it is wrong: for A->B->C with a transition at each boundary it emits
    ``solo(A) xfade solo(B)`` for the first and ``solo(B) xfade solo(C)`` for the
    second, so B's solo appears twice — measured at 240 frames for a 180-frame
    song. Each cut owns exactly one solo part, shortened at the head by its OWN
    incoming transition and at the tail by its SUCCESSOR's.

    The counts still telescope, which is the property the whole assembler rests
    on. Cut *i* contributes ``d_i + (n_i - post_i - pre_{i+1})``, and summing over
    all cuts the ``pre``/``post`` terms cancel against ``d = pre + post``, leaving
    ``sum(n_i) = round(song_duration*fps)`` exactly as before. Verified end to end
    on a three-cut chain: parts ``[54, 12, 48, 12, 54]``, concat by stream copy,
    180 frames for a 6.000000 s song.

    Preconditions, both raising ``ValueError`` BEFORE any encoding: no transition
    on cut 0 (there is nothing to blend from, and ``cuts[-1]`` would wrap), and
    every transition fits at this fps. :func:`~muvid.footage.edl.validate_edl` gates the fit in SONG time
    with a 1 ms tolerance and cannot know the render rate, so a span within a frame
    of the limit can still fail here.

    It also WARNS twice, and both warnings are the same "never a silent no-op"
    posture: a transition that rounds to zero frames at this rate, and a cut whose
    **time-varying look borders a blended boundary** — see
    :func:`_warn_time_varying_looks_on_transitions`. This is the one place that
    can say either, because both are properties of the render PLAN rather than of
    the EDL: only here is it known that a transitioned boundary becomes a separate
    two-input invocation, and only here is the fps known at all.

    ``note`` is the reply sink — a ``str -> None`` callable that receives the same
    text. It exists because ``warnings.warn`` reaches a developer's stderr and
    stops there: the only production caller of either of these findings is a
    remote MCP caller who has no stderr, so before it, an ``ok`` render came back
    with the hitch in it and no way to know. See :func:`_emit`.
    """
    counts = _frame_counts(cuts, fps)

    def _n_transition(i: int) -> int:
        t = cuts[i].transition
        if t is None:
            return 0
        n = round(t.duration_s * fps)
        if n == 0:
            # Never a silent no-op: a transition that rounds away at this rate is a
            # direction that did nothing, which is the muvid#44 failure shape.
            _emit(
                f"cut {i}: a {t.duration_s:.3f}s transition rounds to zero frames at "
                f"{fps} fps and renders as a hard cut.",
                note,
                stacklevel=4,
            )
        return n

    n_trans = [_n_transition(i) for i in range(len(cuts))]
    _warn_time_varying_looks_on_transitions(cuts, n_trans, note)
    parts: list[_Part] = []
    for i, (cut, n) in enumerate(zip(cuts, counts)):
        d = n_trans[i]
        pre = round(d * TRANSITION_SPLIT)
        post = d - pre
        tail = round(n_trans[i + 1] * TRANSITION_SPLIT) if i + 1 < len(cuts) else 0
        if d:
            if i == 0:
                # `cuts[i - 1]` would wrap to the LAST cut of the song and add
                # `pre` uncancelled frames, so the telescoping identity below would
                # quietly stop holding. `validate_edl` rejects this, but it is
                # another module's gate and `_part_plan` takes AssemblyCuts
                # directly — the module already raises before encoding for a
                # transition that does not fit, so this gets the same treatment.
                raise ValueError(
                    "cut 0 carries a transition but nothing precedes it; a "
                    "transition annotates an entry's entrance."
                )
            prev = cuts[i - 1]
            parts.append(
                _Part(
                    kind="xfade",
                    cut=cut,
                    n_frames=d,
                    # Seek the INCOMING clip `pre` frames BEFORE its span starts...
                    clip_in=cut.clip_in - pre / fps,
                    prev=prev,
                    # ...and the OUTGOING clip to the same instant, which is `pre`
                    # frames before ITS span ends. Both inputs therefore start at
                    # the window's first frame, which is why the filter's
                    # `offset=0` is right and no arithmetic is duplicated between
                    # the seek and the filter.
                    prev_in=prev.clip_in + prev.duration - pre / fps,
                    curve=cut.transition.curve,
                )
            )
        solo = n - post - tail
        if solo < 0:
            raise ValueError(
                f"cut {i} is {n} frames at {fps} fps, but its transitions claim "
                f"{post + tail} of them ({post} at the head, {tail} at the tail). "
                "Shorten a transition or lengthen the cut."
            )
        if solo:
            parts.append(
                _Part(
                    kind="solo",
                    cut=cut,
                    n_frames=solo,
                    # Advanced past the frames the incoming transition already
                    # showed. Without this the solo REPLAYS them and then runs
                    # post/fps behind the song for the rest of the cut.
                    clip_in=cut.clip_in + post / fps,
                )
            )
    return parts


def _crop_filter(cut: "AssemblyCut") -> str:
    """Compile a cut's normalised crop window into a ``crop`` filter, or ``""``.

    Normalised fractions only become pixels once the source dimensions are known,
    and the only thing that knows them is ffmpeg — hence ``iw``/``ih`` rather than
    arithmetic here. Emitting nothing when there is no crop is what keeps every
    pre-crop EDL byte-identical through this path.

    A moving window is a linear ramp in the filter's own ``t``, clamped at both
    ends so the last frame lands exactly on ``crop_end`` rather than overshooting
    by the one spare frame ``_render_part`` decodes. ``setpts=PTS-STARTPTS`` is
    prepended ONLY in the moving case, because ``t`` must start at 0 for the ramp
    to mean anything and adding it unconditionally would change a path that works.

    ``crop`` is the right filter HERE because the window never changes size —
    not because ``zoompan`` is unusable. What ``crop`` cannot do is vary ``w``/``h``:
    those two expressions are evaluated ONCE, at configure time, when ``t`` is NAN.
    Both of that root cause's symptoms are real, and the quiet one is the dangerous
    one — ``w='iw*0.5*(1+t)'`` refuses to configure ("Error when evaluating the
    expression" / "Failed to configure input pad"), while ``w='iw*(0.5+0.2*min(t/2,1))'``
    exits 0 and renders a plausible video frozen at ONE size (measured: 224 px for
    every frame of a window that was meant to run 160 -> 224).

    ``zoompan`` CAN vary size, and the note that used to sit here was wrong about
    why it was passed over. Measured on ffmpeg 8.1: ``t`` really is undefined in
    ``zoompan``, but the filter is not time-blind — ``in_time``/``it`` work, as do
    the counters ``on``/``in``. Nor does it inherently duplicate frames: that is
    entirely its default ``d=90`` (20 input frames -> 1800 out), and ``d=1`` is
    exactly 1:1. So a resizing window is a ``zoompan`` job; a pan at constant size
    is this one, and ``crop`` expresses it with one expression per axis.

    That ``zoompan`` job now has a home: :func:`muvid.footage.look.punch_in` rides
    in the cut's ``look`` rather than in its ``crop``, which is why this function
    is still only ever asked for a pan.
    """
    c = cut.crop
    if c is None:
        return ""
    w = f"iw*{c.w:.6f}"
    h = f"ih*{c.h:.6f}"
    e = cut.crop_end
    if e is None or (abs(e.x - c.x) < 1e-9 and abs(e.y - c.y) < 1e-9):
        return f"crop=w='{w}':h='{h}':x='iw*{c.x:.6f}':y='ih*{c.y:.6f}'"
    T = max(cut.duration, 1e-6)
    prog = f"min(max(t/{T:.6f},0),1)"
    x = f"iw*({c.x:.6f}+({e.x - c.x:.6f})*{prog})"
    y = f"ih*({c.y:.6f}+({e.y - c.y:.6f})*{prog})"
    return f"setpts=PTS-STARTPTS,crop=w='{w}':h='{h}':x='{x}':y='{y}'"


def _part_filter(
    cut: "AssemblyCut", *, w: int, h: int, fps: int, tail: str = ""
) -> str:
    """THE per-cut filter chain. ONE implementation, used by BOTH render sites.

    This template used to be written out twice — once in :func:`_render_part` and
    once inside :func:`_render_transition`'s ``_norm`` — and the two copies are
    the two sides of a blended boundary. Anything that lands on one and not the
    other is a **visible seam**: the A side and the B side of a cut disagree
    exactly where the blend puts them on top of each other. Two copies stayed in
    agreement while the chain was fixed; a per-cut ``look`` is the first thing
    that varies, so the copies became one function rather than a comment asking
    the next reader to keep them equal.

    The order is the contract:

    ``[crop,] scale, pad, setsar, fps, tpad [, look] [, tail]``

    The look is spliced **after** the normalisation and **before** ``tail``, and
    both halves of that are load-bearing:

    - *After* ``scale``/``pad``/``fps``, the look sees a frame whose geometry is
      exactly the canvas and whose rate is exactly the delivery rate — so a
      time-varying look (``muvid.footage.look.punch_in``'s ``zoompan``) can be
      given exact numbers instead of per-clip probes, and a look's normalised
      window means a fraction of the canvas rather than a fraction of whichever
      source happens to be under it. It is also the last pixel stage, which is
      where ``looks``' own ordering rule wants a quantiser.
    - *Before* ``tail`` (``format=yuv420p`` on the transition path), because
      ``xfade`` needs its two sides in one pixel format and that is the tail's
      whole job.

    ``tail`` is the only difference between the two sites, and it is a parameter
    rather than a second string.

    With no crop and no look this returns exactly the bytes it always did — the
    ``look``-absent path is unchanged character for character, which is the
    property ``tests/test_edl_look.py`` pins.
    """
    crop = _crop_filter(cut)
    return (
        f"{crop + ',' if crop else ''}"
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
        f"tpad=stop=-1:stop_mode=clone"
        f"{',' + cut.look if cut.look else ''}"
        f"{tail}"
    )


def _video_codec_args(crf: int, preset: str) -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(crf),
        "-preset",
        preset,
    ]


def _render_part(
    cut: AssemblyCut,
    part: Path,
    *,
    w: int,
    h: int,
    fps: int,
    n_frames: int,
    crf: int,
    preset: str,
) -> None:
    """Render ONE cut (footage or gap) to an intermediate — the single-decoder stage."""
    from muvid.visualize.ffmpeg import run_ffmpeg

    # tpad at the tail: -frames:v alone is a CAP, not a guarantee — a clip whose audio
    # outlives its video (alignment durations come from the AUDIO length) legitimately
    # validates a span past the last video frame, and without tpad that part comes up
    # short, silently desyncing every later cut. Cloning the last frame makes the frame
    # count exact whenever the source yields at least one frame.
    vf = _part_filter(cut, w=w, h=h, fps=fps)
    if cut.clip_path:
        args = [
            # Input-side seek: lands on the keyframe before clip_in and decodes forward
            # to it, so a cut 3 minutes into a clip does not decode 3 minutes of video.
            "-ss",
            f"{cut.clip_in:.6f}",
            # One spare frame of input beyond the target; -frames:v is the exact cap.
            "-t",
            f"{cut.duration + 1.0 / fps:.6f}",
            "-i",
            str(cut.clip_path),
            "-vf",
            vf,
        ]
    else:  # a gap entry: no footage for this span — fill black on the same grid
        vf = f"setsar=1,fps={fps}"
        args = [
            "-f",
            "lavfi",
            "-i",
            f"color=black:size={w}x{h}:rate={fps}:duration={cut.duration + 1.0 / fps:.6f}",
            "-vf",
            vf,
        ]
    args += [
        "-frames:v",
        str(n_frames),
        "-an",
        *_video_codec_args(crf, preset),
        str(part),
    ]
    run_ffmpeg(args)


def _xfade_input(cut: AssemblyCut, clip_in: float, n_frames: int, *, w, h, fps):
    """One decoder for the transition stage — a real clip, or a gap's black source.

    A gap side is always satisfiable: its ``color`` source is synthetic and
    re-parameterizable per invocation, so it can supply the blend window however far
    it reaches. That is why :func:`~muvid.footage.edl.validate_edl`'s coverage rule
    skips gap sides — not because anything at HEAD is unbounded.
    """
    if cut.clip_path:
        return [
            "-ss",
            f"{clip_in:.6f}",
            "-t",
            f"{(n_frames + 1) / fps:.6f}",
            "-i",
            str(cut.clip_path),
        ]
    return [
        "-f",
        "lavfi",
        "-i",
        f"color=black:size={w}x{h}:rate={fps}:duration={(n_frames + 1) / fps:.6f}",
    ]


def _render_transition(part, out: Path, *, w, h, fps, crf: int, preset: str) -> None:
    """Render one blended boundary — the only two-decoder stage.

    Both inputs are already seeked to the window's first frame, so ``offset=0`` is
    correct and ``-frames:v`` is the exact cap.

    **What the blend actually looks like, stated precisely rather than called
    seamless.** With ``duration = n/fps`` the filter's progress runs ``0, 1/n, ...,
    (n-1)/n`` across the emitted frames, so across the whole boundary — last solo-A
    frame, ``n`` blended frames, first solo-B frame — the ramp is ``0, 0, 1/n, ...,
    (n-1)/n, 1``: uniform, monotone, with one duplicated endpoint on the A side.
    Measured on a 12-frame fade: SSIM to a pure-A render of the same window falls
    0.9895 -> 0.4533 while SSIM to pure-B rises 0.4206 -> 0.9580, both strictly
    monotone. The endpoints approach but do not equal the neighbouring solo parts,
    which is why the guard asserts MONOTONICITY and not endpoint identity.
    """
    from muvid.visualize.ffmpeg import run_ffmpeg

    # Per-input, not one shared string: the two sides of a boundary are different
    # cuts and may carry different crops. A single `norm` would silently apply the
    # A-side framing to the B-side — the blend would still render, at the wrong
    # framing, which is exactly the kind of failure nothing downstream can see.
    def _norm(cut) -> str:
        return _part_filter(cut, w=w, h=h, fps=fps, tail=",format=yuv420p")

    n = part.n_frames
    run_ffmpeg(
        [
            *_xfade_input(part.prev, part.prev_in, n, w=w, h=h, fps=fps),
            *_xfade_input(part.cut, part.clip_in, n, w=w, h=h, fps=fps),
            "-filter_complex",
            f"[0:v]{_norm(part.prev)}[a];[1:v]{_norm(part.cut)}[b];"
            f"[a][b]xfade=transition={part.curve}:duration={n / fps:.6f}:offset=0[v]",
            "-map",
            "[v]",
            "-frames:v",
            str(n),
            "-an",
            *_video_codec_args(crf, preset),
            str(out),
        ]
    )


def _render_first_usable(candidates, part: Path, **render_kwargs) -> None:
    """Render the first candidate that actually yields frames; black if none does.

    ONE implementation, used by both part kinds, and that is the point. The
    per-cut and the transition-fallback paths were doing the same three things —
    render, check the part really has a video stream, degrade — and only the
    first got it right. The transition path re-rendered from ``p.cut``
    *unconditionally* and then never re-checked, so when the incoming side was the
    one that had failed it produced a **streamless** part: the concat demuxer
    swallows it in silence and ffmpeg exits 0, so the delivered video came up short
    by exactly the transition length with no error anywhere. Measured: 168 frames
    against a 180-frame song.

    Two rules follow, and they are why this is a loop rather than a preference:

    - **Verify every attempt.** ``-frames:v`` is a cap, not a guarantee — a clip
      whose audio outlives its video (alignment durations come from the AUDIO
      length, so this shape is normal and documented) legitimately validates a span
      past its last video frame and can yield nothing at all.
    - **Try the other side before giving up on picture.** The fallback exists
      *because* one side failed; preferring a fixed side ignores which. Black is the
      last resort, not the second option — a 0.4 s black flash where the surviving
      clip could have carried the boundary is a visible defect, not a graceful one.
    """
    for cut in candidates:
        if not cut.clip_path:
            continue
        _render_part(cut, part, **render_kwargs)
        if not part.exists() or _has_video_frames(part):
            # A part that does not exist at all is a DIFFERENT failure — ffmpeg
            # did not write one — and degrading it to black here would cost a
            # second invocation per cut, breaking the one-ffmpeg-per-part bound
            # that muvid#24's OOM fix is pinned by
            # (`test_assemble_runs_one_bounded_ffmpeg_per_cut`). This function
            # answers exactly one question: "the file is there, but is anything
            # IN it?"
            return
        part.unlink(missing_ok=True)
    _render_part(
        replace(candidates[0], clip_path="", clip_in=0.0), part, **render_kwargs
    )


def _audio_args(song_path: str) -> list[str]:
    """Stream-copy the master when it already is the delivery contract; else encode to it.

    Bit-identical audio (muvid#21 item 5) and the fixed 2ch@48kHz delivery contract
    (muvid#24 B3) are only simultaneously satisfiable when the master is already
    aac/48000/2ch — so that is exactly the copy condition, decided by probe, not hope.
    """
    from muvid.visualize.ffmpeg import probe

    try:
        astreams = [
            s
            for s in probe(song_path).get("streams", [])
            if s.get("codec_type") == "audio"
        ]
        a = astreams[0] if astreams else {}
    except Exception:  # noqa: BLE001 — un-probeable song: encode, never crash the render
        a = {}
    if (
        a.get("codec_name") == "aac"
        and str(a.get("sample_rate")) == "48000"
        and a.get("channels") == 2
    ):
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def _has_video_frames(part: Path) -> bool:
    """Whether a rendered part actually carries a video stream.

    ffmpeg exits 0 even when an input-side ``-ss`` lands wholly past the source's last
    video frame — the result is a streamless few-hundred-byte mp4 the concat demuxer
    silently swallows, shortening the video and desyncing every later cut. This is the
    check that turns that silence into the black-gap fallback.
    """
    from muvid.visualize.ffmpeg import probe

    try:
        streams = probe(part).get("streams", [])
    except Exception:  # noqa: BLE001 — unreadable part: treat as frameless, fall back
        return False
    return any(s.get("codec_type") == "video" for s in streams)


def assemble_music_video(
    cuts: Sequence[AssemblyCut],
    song_path: str,
    out_path: str,
    *,
    canvas: tuple[int, int] = DEFAULT_CANVAS,
    fps: int = DEFAULT_FPS,
    crf: int = 20,
    preset: str = "veryfast",
    on_note=None,
) -> Path:
    """Render ``cuts`` (a validated, contiguous, gap-filled EDL) into ``out_path``.

    Bounded stages: one single-input ffmpeg run per cut (gaps render black), a stream-copy
    concat, and a final mux of the clean song for ``[cuts[0].song_start,
    cuts[-1].song_end]`` — which, for EDLs produced by ``fill_gaps``, is the whole song.
    Returns ``out_path``.

    Args:
        on_note: optional ``str -> None`` sink for the render-plan findings
            :func:`_part_plan` raises (a transition that rounds to zero frames; a
            time-varying look on a blended boundary — muvid#73). They are ALWAYS
            raised as :class:`AssemblyWarning` as well; this is the additional
            path, and the only one a remote caller can see. ``assemble_music_video``
            is a live per-caller MCP tool, so a finding that reaches only the
            server's stderr is a hitch the caller is billed for and never told
            about. A callback rather than a changed return type, because the
            return type is a public contract and because ``catch_warnings``
            mutates process-global state that concurrent renders would share.
    """
    from muvid.visualize.ffmpeg import require_ffmpeg, require_filter, run_ffmpeg

    if not cuts:
        raise ValueError("no cuts to assemble")
    require_ffmpeg("ffmpeg")
    require_ffmpeg("ffprobe")
    w, h = _even(canvas[0]), _even(canvas[1])
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # A unique, per-call directory: a fixed name next to out_path would rmtree
    # whatever already lived there, and two concurrent renders into one directory
    # would interleave part files and delete each other's work.
    parts_dir = Path(tempfile.mkdtemp(dir=out.parent, prefix=".parts-"))
    try:
        # Planned in full BEFORE any encoding, so a transition that does not fit at
        # this fps fails on cut 0 rather than after forty parts are on disk.
        plan = _part_plan(cuts, fps, on_note)
        if any(p.kind == "xfade" for p in plan):
            require_filter("xfade", needed_for="EDL transitions")
        names = []
        for i, p in enumerate(plan):
            if p.n_frames == 0:  # a sub-frame cut owns no grid frame — _frame_counts
                continue
            name = f"part{i:04d}.mp4"
            part = parts_dir / name
            render_kwargs = dict(
                w=w, h=h, fps=fps, n_frames=p.n_frames, crf=crf, preset=preset
            )
            if p.kind == "xfade":
                _render_transition(p, part, w=w, h=h, fps=fps, crf=crf, preset=preset)
                if part.exists() and not _has_video_frames(part):
                    # One side yielded nothing, so the blend is not renderable. The
                    # window still exists in song time — fill it from a side that CAN
                    # supply frames: a hard cut displaced by at most duration/2, with
                    # the frame count unchanged.
                    part.unlink()
                    _render_first_usable(
                        [
                            replace(p.cut, clip_in=p.clip_in),
                            replace(p.prev, clip_in=p.prev_in),
                        ],
                        part,
                        **render_kwargs,
                    )
            else:
                # `clip_in` is the PART's in-point, not the cut's: a cut whose head
                # was consumed by an incoming transition starts later in its clip.
                _render_first_usable(
                    [replace(p.cut, clip_in=p.clip_in)], part, **render_kwargs
                )
            names.append(name)
        if not names:
            raise ValueError("no renderable cuts — every span rounds to zero frames")
        # Concat-demuxer entries resolve relative to the LIST file, and the names are
        # ours (partNNNN.mp4) — plain relative names, so the demuxer's default "safe"
        # mode is fine and no quoting/escaping surface exists here.
        concat_list = parts_dir / "parts.txt"
        concat_list.write_text("".join(f"file {n}\n" for n in names))
        song_start = cuts[0].song_start
        total = cuts[-1].song_end - song_start
        # The song is trimmed to the covered span. For a fill_gaps EDL that span IS the
        # whole song, so the -ss is omitted and -t admits every packet — which is what
        # keeps the stream-copy path bit-identical to the master.
        song_input = ["-i", str(song_path)]
        if song_start > 1e-3:
            song_input = ["-ss", f"{song_start:.6f}", *song_input]
        run_ffmpeg(
            [
                "-f",
                "concat",
                "-i",
                str(concat_list),
                *song_input,
                "-map",
                "0:v",
                # :a:0, not :a — a multi-stream master would otherwise carry extra audio
                # tracks past the contract decision, which probes only the first.
                "-map",
                "1:a:0",
                "-t",
                f"{total:.6f}",
                "-c:v",
                "copy",
                *_audio_args(song_path),
                # Delivery contract (muvid#24 B3): no edit lists, moov up front.
                "-use_editlist",
                "0",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
    finally:
        shutil.rmtree(parts_dir, ignore_errors=True)
    return out
