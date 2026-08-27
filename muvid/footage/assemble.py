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


def _part_plan(cuts: Sequence[AssemblyCut], fps: int) -> list[_Part]:
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

    Raises ``ValueError`` — BEFORE any encoding — if a transition does not fit at
    this fps. :func:`~muvid.footage.edl.validate_edl` gates the fit in SONG time
    with a 1 ms tolerance and cannot know the render rate, so a span within a frame
    of the limit can still fail here.
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
            warnings.warn(
                f"cut {i}: a {t.duration_s:.3f}s transition rounds to zero frames at "
                f"{fps} fps and renders as a hard cut.",
                RuntimeWarning,
                stacklevel=3,
            )
        return n

    n_trans = [_n_transition(i) for i in range(len(cuts))]
    parts: list[_Part] = []
    for i, (cut, n) in enumerate(zip(cuts, counts)):
        d = n_trans[i]
        pre = round(d * TRANSITION_SPLIT)
        post = d - pre
        tail = round(n_trans[i + 1] * TRANSITION_SPLIT) if i + 1 < len(cuts) else 0
        if d:
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
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
        f"tpad=stop=-1:stop_mode=clone"
    )
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

    norm = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
        f"tpad=stop=-1:stop_mode=clone,format=yuv420p"
    )
    n = part.n_frames
    run_ffmpeg(
        [
            *_xfade_input(part.prev, part.prev_in, n, w=w, h=h, fps=fps),
            *_xfade_input(part.cut, part.clip_in, n, w=w, h=h, fps=fps),
            "-filter_complex",
            f"[0:v]{norm}[a];[1:v]{norm}[b];"
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
) -> Path:
    """Render ``cuts`` (a validated, contiguous, gap-filled EDL) into ``out_path``.

    Bounded stages: one single-input ffmpeg run per cut (gaps render black), a stream-copy
    concat, and a final mux of the clean song for ``[cuts[0].song_start,
    cuts[-1].song_end]`` — which, for EDLs produced by ``fill_gaps``, is the whole song.
    Returns ``out_path``.
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
        plan = _part_plan(cuts, fps)
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
                    # One side yielded nothing. The window still exists in song time,
                    # so re-render it from the OTHER side alone: a hard cut displaced
                    # by at most duration/2, with the frame count unchanged. Falling
                    # through would shorten the whole render.
                    part.unlink()
                    if p.cut.clip_path:
                        solo_side = replace(p.cut, clip_in=p.clip_in)
                    elif p.prev.clip_path:
                        solo_side = replace(p.prev, clip_in=p.prev_in)
                    else:
                        solo_side = replace(p.cut, clip_path="", clip_in=0.0)
                    _render_part(solo_side, part, **render_kwargs)
            else:
                # `clip_in` is the PART's in-point, not the cut's: a cut whose head
                # was consumed by an incoming transition starts later in its clip.
                _render_part(replace(p.cut, clip_in=p.clip_in), part, **render_kwargs)
                if p.cut.clip_path and part.exists() and not _has_video_frames(part):
                    # The source yielded no frames at all for this span (audio outlived
                    # the video). The span still exists in song time — fill it black
                    # rather than silently shortening the render.
                    part.unlink()
                    _render_part(
                        replace(p.cut, clip_path="", clip_in=0.0), part, **render_kwargs
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
