r"""The default lyric-video renderer: a :class:`~muvid.lyricvid.scene.Scene` as ASS.

One :class:`~muvid.lyricvid.scene.Scene` in, an ``.ass`` subtitle document and a
burnt-in mp4 out. This is the *default* backend for the lyric-video subgenre for
three reasons, in the order they mattered:

* **It is frame-exact.** libass positions and animates text against the video's
  own clock, so a word that the aligner measured at 12.34 s is drawn at 12.34 s
  — not at "whatever frame the compositor got to".
* **It adds no non-Python dependency.** ``ffmpeg`` is already a hard system
  requirement of muvid, and its ``subtitles`` filter *is* libass. The only new
  Python dependency is ``pysubs2`` (MIT, pure Python, zero dependencies of its
  own), behind the ``lyricvid`` extra.
* **The intermediate is a deliverable.** The ``.ass`` file is a plain text
  document a human can open, retime and restyle, then re-burn — so a render that
  is 95% right is *editable* rather than a reason to re-run the pipeline.

The whole of the renderer's opinion lives in one table, ``Cue`` -> ASS:

============  ========================================================================
``Cue`` field ASS
============  ========================================================================
``x``/``y``   ``\pos(x*W, y*H)`` with the style's alignment at 5 (centre-centre), so
              the anchor is the one :class:`~muvid.lyricvid.scene.Cue` promises.
``size``      ``\fs`` in pixels — see :func:`_fontsize` for why it is 1:1 with the
              canvas height rather than divided by a cap-height ratio.
``colour``    ``\c&HBBGGRR&`` — see :func:`_ass_colour`; the bytes REVERSE.
``motion``    an entry in :data:`MOTION_FNS`; ``cut`` emits nothing, ``fade`` a
              ``\fad``, ``pop`` a ``\fscx/\fscy`` overshoot settled by ``\t``,
              ``rise`` a ``\move`` from below, ``wipe`` the karaoke primitive
              ``\kf``, and ``typewriter`` one Dialogue event per letter revealed.
``t_in``      the event's Start.
``t_out`` /   the event's End (``t_gone`` first, then ``t_out``); ``t_out=None`` is
``t_gone``    persistence, and simply becomes an End at the scene's end.
``dim_from``  a ``\t(t,t+ramp,\c&Hdim&)`` colour transition, relative to Start.
``layer``     the Dialogue Layer field, unchanged.
============  ========================================================================

Fonts are resolved through fontconfig when it is available: a family the machine
does not have falls back (to the next of :data:`FONT_FALLBACKS`) rather than
failing the render, and the substitution is recorded in the result's ``meta`` so
a caller can see that the type is not what the treatment asked for.

Import-safe: stdlib plus the two stdlib-only muvid modules it types against.
``pysubs2``, ``ffmpeg`` and the verifier are imported inside the functions that
need them, so importing this module costs a caller nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from muvid.lyricvid.scene import Canvas, Cue, Scene
from muvid.subgenres._contract import RenderResult

__all__ = [
    "RENDERER_NAME",
    "STYLE_NAME",
    "FONT_FALLBACKS",
    "MOTION_FNS",
    "register_motion",
    "list_motions",
    "scene_to_ass",
    "render",
]

#: What the result's ``meta`` reports as the renderer that made the file.
RENDERER_NAME = "lyricvid.render_ass"

#: The single ASS style every event references. One style plus per-event
#: ``\fs``/``\c`` overrides, rather than a style per (size, colour) pair: a
#: scene routinely holds hundreds of distinct sizes, and a styles table that
#: long is unreadable to the human this file is also written for.
STYLE_NAME = "Lyric"

#: Families to try, in order, when the treatment's family is not installed.
#: DejaVu first because it is what a Linux render box actually has.
FONT_FALLBACKS: tuple[str, ...] = (
    "DejaVu Sans",
    "Liberation Sans",
    "Noto Sans",
    "Arial",
    "Helvetica",
    "sans-serif",
)

#: ASS times are centiseconds. Nothing shorter than this is expressible, so an
#: event (or a typewriter step) below it is collapsed rather than rounded to zero.
ASS_TIME_QUANTUM_S = 0.01

#: Floor on an event's length. A cue whose out-time is at or before its in-time
#: still gets a visible frame rather than a zero-length event libass drops.
MIN_EVENT_S = 0.04

#: How long the colour ramp to ``dim_colour`` takes. ``dim_from`` is a moment,
#: not a ramp, and an instant colour switch reads as a glitch.
DIM_RAMP_S = 0.25

#: ``pop``'s scale overshoot, in ASS percent, and the floor on how long it takes
#: to settle. A treatment's ``attack_s`` is deliberately small (0.12 s), and an
#: overshoot that settles that fast is not visible as one.
POP_OVERSHOOT_PCT = 118
POP_SETTLE_S = 0.16

#: ``rise``'s travel, as a fraction of the cue's font size, and the floor on how
#: long the move takes.
RISE_FRACTION = 0.55
RISE_S = 0.22

#: How long ``typewriter`` takes when the cue gives no usable window of its own.
TYPEWRITER_SPAN_S = 0.6

#: Audio, exactly as ``muvid.visualize.verify.verify_video`` demands it.
AUDIO_SAMPLE_RATE = 48000
AUDIO_BITRATE = "192k"

#: x264 preset for the burn. The picture is flat colour plus text, so the slower
#: presets buy nothing a viewer can see.
X264_PRESET = "medium"

#: Keyframe interval, in seconds.
GOP_SECONDS = 2.0

#: The style's own colour. Every event overrides it, so this is what a document
#: someone has hand-edited falls back to rather than a colour with any meaning.
DEFAULT_STYLE_COLOUR = "#ffffff"


# --------------------------------------------------------------------------
# colour
# --------------------------------------------------------------------------


def _hex_rgb(colour: str) -> tuple[int, int, int]:
    """Parse ``#rgb`` / ``#rrggbb`` (the ``#`` optional) into ``(r, g, b)``.

    Args:
        colour: The colour, as :class:`muvid.lyricvid.spec.Palette` writes them.

    Returns:
        The three 0-255 components.

    Raises:
        ValueError: ``colour`` is not hex RGB. Deliberately strict: a palette
            entry that is a *name* would render in ffmpeg's ``color`` source and
            be silently dropped by ASS, so the two halves of one render would
            disagree about the colour.

    Examples:
        >>> _hex_rgb('#e0533d')
        (224, 83, 61)
        >>> _hex_rgb('f00')
        (255, 0, 0)
    """
    raw = colour.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    if len(raw) != 6 or any(c not in "0123456789abcdefABCDEF" for c in raw):
        raise ValueError(
            f"{colour!r} is not a hex RGB colour. A lyric-video palette is "
            "'#rrggbb' (or '#rgb') — see muvid.lyricvid.spec.Palette."
        )
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _ass_colour(colour: str) -> str:
    r"""``#rrggbb`` -> the ASS override literal ``&HBBGGRR&``.

    ASS stores colours **little-endian**, so the byte order reverses: the red
    channel is the *last* pair, not the first. Getting this backwards produces a
    render that works, looks deliberate, and is wrong — red lyrics come out blue
    — which is why it has its own function and its own test.

    Examples:
        >>> _ass_colour('#ff0000')            # red -> BB=00 GG=00 RR=FF
        '&H0000FF&'
        >>> _ass_colour('#0000ff')            # blue -> BB=FF GG=00 RR=00
        '&HFF0000&'
        >>> _ass_colour('#e0533d')            # the default accent
        '&H3D53E0&'
        >>> _ass_colour('#ffffff'), _ass_colour('#101014')
        ('&HFFFFFF&', '&H141010&')
    """
    r, g, b = _hex_rgb(colour)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def _ffmpeg_colour(colour: str) -> str:
    """``#rrggbb`` -> the ``0xrrggbb`` ffmpeg's ``color`` source takes.

    Examples:
        >>> _ffmpeg_colour('#101014')
        '0x101014'
    """
    r, g, b = _hex_rgb(colour)
    return f"0x{r:02x}{g:02x}{b:02x}"


# --------------------------------------------------------------------------
# fonts
# --------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _fontconfig_family(name: str) -> str | None:
    """The family fontconfig would actually use for ``name`` (``None``: no fc).

    ``fc-match`` always answers, so "is this font installed?" is asked by
    comparing what came back with what was asked for. Where fontconfig is not on
    the machine we cannot know, and saying so (``None``) is better than guessing:
    libass has its own fallback and will do something reasonable.
    """
    if shutil.which("fc-match") is None:
        return None
    proc = subprocess.run(
        ["fc-match", "-f", "%{family}", name], capture_output=True, text=True
    )
    if proc.returncode != 0:
        return None
    # fontconfig returns a comma-separated alias list ("DejaVu Sans,DejaVu Sans
    # Book"); the first entry is the family proper.
    return (proc.stdout.split(",")[0] or "").strip() or None


def _resolve_font(requested: str) -> tuple[str, str | None]:
    """``(family to use, family asked for but missing)``.

    A treatment naming a font the render box does not have must not fail the
    render — the type is a preference, the video is the deliverable — but the
    substitution has to be *reported*, or a caller comparing two treatments is
    comparing two fallbacks.

    Args:
        requested: The family the treatment asked for.

    Returns:
        The family to write into the ASS style, and the requested family when it
        was substituted (``None`` when it was honoured, or when there is no
        fontconfig to ask).
    """
    for candidate in (requested, *FONT_FALLBACKS):
        found = _fontconfig_family(candidate)
        if found is None:  # no fontconfig — believe the caller, let libass cope
            return requested, None
        if found.casefold() == candidate.casefold():
            return candidate, None if candidate == requested else requested
    # Every candidate was substituted by fontconfig; take what it offers for the
    # requested family, which is the closest thing this machine has.
    return _fontconfig_family(requested) or requested, requested


# --------------------------------------------------------------------------
# geometry and time
# --------------------------------------------------------------------------


def _fontsize(size: float, canvas: Canvas) -> int:
    """A cue's ``size`` as an ASS font size in pixels.

    Mapped **1:1** with the canvas height rather than divided by a cap-height
    ratio, and that is a layout decision rather than a typographic one: the
    compiler placed the text using ``scene._CHAR_W`` (0.62) as the advance width
    of one character *per unit of size*, which is an em-size reading of ``size``.
    Inflating the font by 1/0.7 to make ``size`` a true cap height would make
    every glyph advance ~0.86 units instead, and the archetypes' own
    fit-to-width would start overflowing the canvas it just fitted.

    Examples:
        >>> _fontsize(0.2, Canvas())
        216
    """
    return max(8, round(size * canvas.height))


def _scene_end(scene: Scene) -> float:
    """When the ASS document stops — the scene's duration, or the last cue.

    ``duration`` comes from the timed text and is normally the song's; a scene
    assembled by hand may not have one, and a persisted cue (``t_out is None``)
    needs *some* End to be written against.
    """
    if scene.duration > 0:
        return scene.duration
    ends = [
        t
        for c in scene.cues
        for t in (c.t_gone, c.t_out, c.t_full, c.t_in)
        if t is not None
    ]
    return max(ends, default=0.0) + MIN_EVENT_S


@dataclass(frozen=True, slots=True, kw_only=True)
class _Event:
    """One Dialogue line, before it becomes a ``pysubs2`` event.

    A motion returns a *list* of these rather than a tag string, which is what
    lets ``typewriter`` — one event per letter — be an ordinary entry in
    :data:`MOTION_FNS` instead of a branch in the writer.
    """

    start: float
    end: float
    layer: int
    tags: str
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class _Placed:
    """A cue with every renderer-side number already resolved.

    Computed once, in :func:`_place`, so a motion function does arithmetic about
    *movement* and nothing else.
    """

    cue: Cue
    text: str
    x: int
    y: int
    fontsize: int
    colour: str
    tracking_px: int
    start: float
    end: float
    fade_in_s: float
    fade_out_s: float

    @property
    def fade_in_ms(self) -> int:
        return max(0, round(self.fade_in_s * 1000))

    @property
    def fade_out_ms(self) -> int:
        return max(0, round(self.fade_out_s * 1000))


def _escape_text(text: str) -> str:
    r"""Make ``text`` safe as an ASS Dialogue payload.

    ``{`` opens an override block and ASS has no escape for it, so a lyric
    containing one is transliterated to a paren — losing a brace is better than
    losing the rest of the line, which is what a renderer that passed it through
    would do.

    Examples:
        >>> _escape_text('a {b}\nc')
        'a (b)\\Nc'
    """
    out = text.replace("{", "(").replace("}", ")")
    out = out.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N")
    return out


def _place(cue: Cue, *, canvas: Canvas, tracking: float, scene_end: float) -> _Placed:
    """Resolve one cue's pixels, colours and times."""
    fontsize = _fontsize(cue.size, canvas)
    start = max(0.0, cue.t_in)
    if cue.t_gone is not None:
        end = cue.t_gone
    elif cue.t_out is not None:
        end = cue.t_out
    else:  # persistence: the cue never leaves, so it leaves with the scene
        end = scene_end
    end = max(min(end, scene_end), start + MIN_EVENT_S)
    return _Placed(
        cue=cue,
        text=_escape_text(cue.text),
        x=round(cue.x * canvas.width),
        y=round(cue.y * canvas.height),
        fontsize=fontsize,
        colour=_ass_colour(cue.colour),
        tracking_px=round(tracking * fontsize),
        start=start,
        end=end,
        fade_in_s=max(0.0, cue.t_full - cue.t_in),
        fade_out_s=(
            max(0.0, cue.t_gone - cue.t_out)
            if cue.t_out is not None and cue.t_gone is not None
            else 0.0
        ),
    )


# --------------------------------------------------------------------------
# tag fragments
# --------------------------------------------------------------------------


def _pos(p: _Placed) -> str:
    r"""``\pos`` at the cue's centre. Paired with the style's alignment 5."""
    return f"\\pos({p.x},{p.y})"


def _style_tags(p: _Placed) -> str:
    r"""The per-event overrides every motion shares: size, tracking, colour."""
    spacing = f"\\fsp{p.tracking_px}" if p.tracking_px else ""
    return f"\\fs{p.fontsize}{spacing}\\c{p.colour}"


def _dim_tags(p: _Placed) -> str:
    r"""A ``\t`` colour ramp to ``dim_colour``, or ``''`` when there is none.

    ``\t``'s times are milliseconds from the event's own Start, which is why the
    ramp is computed here and not from the song clock.
    """
    cue = p.cue
    if not cue.dim_colour or cue.dim_from is None:
        return ""
    at = max(0.0, cue.dim_from - p.start)
    if at >= p.end - p.start:  # the cue is gone before it would recede
        return ""
    t1 = round(at * 1000)
    t2 = round((at + DIM_RAMP_S) * 1000)
    return f"\\t({t1},{t2},\\c{_ass_colour(cue.dim_colour)})"


def _fade_tag(p: _Placed, *, fade_in: bool = True) -> str:
    r"""``\fad(in,out)``, or ``''`` when neither end has an envelope."""
    in_ms = p.fade_in_ms if fade_in else 0
    if not in_ms and not p.fade_out_ms:
        return ""
    return f"\\fad({in_ms},{p.fade_out_ms})"


def _one(p: _Placed, motion_tags: str, *, position: str | None = None) -> list[_Event]:
    """The common case: one Dialogue event carrying ``motion_tags``."""
    tags = f"{position or _pos(p)}{_style_tags(p)}{motion_tags}{_dim_tags(p)}"
    return [_Event(start=p.start, end=p.end, layer=p.cue.layer, tags=tags, text=p.text)]


# --------------------------------------------------------------------------
# motions
# --------------------------------------------------------------------------

MotionFn = Callable[[_Placed], list[_Event]]

#: ``motion`` name -> the events it draws. muvid's house registry idiom
#: (``register_visual``, ``register_archetype``, ``register_selection_strategy``):
#: a new motion is a function plus an entry in
#: :data:`muvid.lyricvid.spec.MOTIONS`, never a branch in the writer.
MOTION_FNS: dict[str, MotionFn] = {}


def register_motion(name: str) -> Callable[[MotionFn], MotionFn]:
    """Register how one ``motion`` family draws itself.

    Examples:
        >>> @register_motion('doctest-demo')
        ... def _demo(p): return []
        >>> 'doctest-demo' in list_motions()
        True
        >>> del MOTION_FNS['doctest-demo']
    """

    def deco(fn: MotionFn) -> MotionFn:
        MOTION_FNS[name] = fn
        return fn

    return deco


def list_motions() -> list[str]:
    """The motion families this renderer can draw.

    Examples:
        >>> list_motions()
        ['cut', 'fade', 'pop', 'rise', 'typewriter', 'wipe']
    """
    return sorted(MOTION_FNS)


@register_motion("cut")
def _motion_cut(p: _Placed) -> list[_Event]:
    """Appears instantly: position, style, nothing else."""
    return _one(p, "")


@register_motion("fade")
def _motion_fade(p: _Placed) -> list[_Event]:
    r"""``\fad``, whose two halves are the cue's own arrive/leave envelopes."""
    return _one(p, _fade_tag(p))


@register_motion("pop")
def _motion_pop(p: _Placed) -> list[_Event]:
    r"""Scale overshoot settling to 100, via ``\t`` over the arrival window.

    The settle is floored at :data:`POP_SETTLE_S`: a treatment's ``attack_s`` is
    ~0.12 s by design, and an overshoot that resolves inside three frames reads
    as a glitch rather than as a pop.
    """
    settle = max(p.fade_in_s, POP_SETTLE_S)
    o = POP_OVERSHOOT_PCT
    tags = (
        f"{_fade_tag(p)}\\fscx{o}\\fscy{o}"
        f"\\t(0,{round(settle * 1000)},\\fscx100\\fscy100)"
    )
    return _one(p, tags)


@register_motion("rise")
def _motion_rise(p: _Placed) -> list[_Event]:
    r"""``\move`` from below to the cue's position, fading up as it travels.

    Emits ``\move`` *instead of* ``\pos`` — ASS honours only one of the two, and
    a document carrying both is a document whose motion silently does not happen.
    """
    travel = max(1, round(RISE_FRACTION * p.fontsize))
    dur = round(max(p.fade_in_s, RISE_S) * 1000)
    move = f"\\move({p.x},{p.y + travel},{p.x},{p.y},0,{dur})"
    return _one(p, _fade_tag(p), position=move)


@register_motion("wipe")
def _motion_wipe(p: _Placed) -> list[_Event]:
    r"""The karaoke primitive: ``\kf`` sweeps the fill across the text.

    ``\kf`` reveals from SecondaryColour to PrimaryColour over its own duration,
    measured from the event's Start — which is exactly ``t_in``. The secondary is
    made fully transparent (``\2a&HFF&``) rather than dim, because the
    ``karaoke_wipe`` archetype already draws the un-sung line underneath on a
    lower layer; a second dim copy would double-print it.

    The sweep ends at ``extra['wipe_end']`` when the compiler measured one (it
    does: the word's end), and at ``t_full`` otherwise.
    """
    wipe_end = float(p.cue.extra.get("wipe_end", p.cue.t_full))
    span = max(wipe_end - p.start, ASS_TIME_QUANTUM_S)
    tags = f"{_fade_tag(p, fade_in=False)}\\2a&HFF&\\kf{round(span * 100)}"
    return _one(p, tags)


@register_motion("typewriter")
def _motion_typewriter(p: _Placed) -> list[_Event]:
    r"""One Dialogue event per letter revealed.

    The reveal runs from ``t_in`` to ``t_out`` — "across the word's duration", as
    :data:`muvid.lyricvid.spec.MOTIONS` puts it — falling back to the arrival
    window, and then to :data:`TYPEWRITER_SPAN_S`, for a cue that persists and so
    has no out-time. Below one ASS centisecond per letter the reveal is not
    expressible, so it collapses to a single event rather than being rounded into
    a pile of zero-length ones.

    Each event is centred like the finished word, so the prefix grows outward
    from the centre. That is what a centre-anchored typewriter does; a
    left-anchored one is a different ``Cue.x``, not a different renderer.
    """
    letters = p.text
    n = len(letters)
    if n < 2:
        return _one(p, _fade_tag(p))
    reveal_end = p.cue.t_out if p.cue.t_out is not None else max(p.cue.t_full, 0.0)
    span = max(reveal_end - p.start, 0.0) or TYPEWRITER_SPAN_S
    step = span / n
    if step < ASS_TIME_QUANTUM_S:
        return _one(p, _fade_tag(p))
    base = f"{_pos(p)}{_style_tags(p)}{_dim_tags(p)}"
    events: list[_Event] = []
    for i in range(n):
        first, last = i == 0, i == n - 1
        start = p.start + i * step
        end = p.end if last else min(p.start + (i + 1) * step, p.end)
        if end <= start:
            continue
        tags = base
        if first and p.fade_in_ms:
            tags += f"\\fad({p.fade_in_ms},0)"
        elif last and p.fade_out_ms:
            tags += f"\\fad(0,{p.fade_out_ms})"
        events.append(
            _Event(
                start=start,
                end=end,
                layer=p.cue.layer,
                tags=tags,
                text=letters[: i + 1],
            )
        )
    return events


def _events_for(p: _Placed) -> list[_Event]:
    """Draw one placed cue with its motion.

    An unknown motion falls back to ``fade``. :func:`muvid.lyricvid.spec.repair`
    guarantees the vocabulary, but a renderer handed a hand-built scene stays
    total rather than raising in the middle of a document.
    """
    return MOTION_FNS.get(p.cue.motion, _motion_fade)(p)


# --------------------------------------------------------------------------
# the ASS document
# --------------------------------------------------------------------------


def scene_to_ass(scene: Scene, *, font: str | None = None) -> str:
    r"""Render a Scene as an ASS (Advanced SubStation Alpha) document.

    The document is built through ``pysubs2`` (MIT, pure Python, no dependencies
    of its own) rather than by string-formatting the format's header by hand: it
    owns the ``[Script Info]`` / ``[V4+ Styles]`` field order and the
    centisecond time format, and it parses back, so the output is round-trip
    checkable rather than merely plausible.

    ``PlayResX``/``PlayResY`` are the canvas, so libass's coordinates are the
    video's pixels 1:1 and the same document burns correctly at 1080p or 4K only
    by changing the canvas it was compiled for.

    Args:
        scene: The compiled scene. Every number in it is already resolved.
        font: Family to typeset in. Defaults to the scene's typography; an
            uninstalled family falls back (see :func:`_resolve_font`).

    Returns:
        The ``.ass`` document, as text.

    Examples:
        >>> from muvid.lyricvid.scene import Canvas, Cue, Scene
        >>> from muvid.lyricvid.spec import Typography
        >>> scene = Scene(
        ...     canvas=Canvas(width=1920, height=1080, fps=30),
        ...     duration=2.0,
        ...     background='#101014',
        ...     cues=(
        ...         Cue(text='red', x=0.25, y=0.5, size=0.2, t_in=0.0, t_full=0.0,
        ...             t_out=1.0, t_gone=1.0, colour='#ff0000', motion='cut'),
        ...         Cue(text='fade', x=0.5, y=0.25, size=0.1, t_in=1.0, t_full=1.2,
        ...             t_out=1.8, t_gone=2.0, colour='#e0533d', layer=2),
        ...     ),
        ...     typography=Typography(family='DejaVu Sans'),
        ... )
        >>> doc = scene_to_ass(scene)

        The normalised centre becomes absolute pixels, and the style anchors at
        centre-centre (alignment 5) so ``\pos`` means what ``Cue`` says it means:

        >>> r'\pos(480,540)' in doc      # 0.25*1920, 0.5*1080
        True
        >>> r'\pos(960,270)' in doc      # 0.5*1920, 0.25*1080
        True

        Colours reverse to ``&HBBGGRR&`` — red is ``&H0000FF&``, not ``&HFF0000&``:

        >>> r'\c&H0000FF&' in doc
        True
        >>> r'\c&HFF0000&' in doc
        False

        ``size`` is a fraction of canvas height, so 0.2 of 1080 is a 216px font,
        and the motions emit their own tags:

        >>> r'\fs216' in doc, r'\fad(200,200)' in doc
        (True, True)

        It parses back — the round trip is what makes the document a deliverable
        rather than a guess:

        >>> import pysubs2
        >>> subs = pysubs2.SSAFile.from_string(doc)
        >>> len(subs.events), subs.events[0].style, subs.events[1].layer
        (2, 'Lyric', 2)
        >>> subs.info['PlayResX'], subs.info['PlayResY']
        ('1920', '1080')
    """
    import pysubs2

    family, _substituted = _resolve_font(font or scene.typography.family)
    canvas = scene.canvas
    scene_end = _scene_end(scene)

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(canvas.width)
    subs.info["PlayResY"] = str(canvas.height)
    # 2 = never wrap. The archetypes already fitted every string to the canvas;
    # letting libass re-wrap would move text the compiler placed.
    subs.info["WrapStyle"] = "2"
    subs.info["ScaledBorderAndShadow"] = "yes"
    # Declared to stop libass guessing (and warning) about the colour matrix.
    subs.info["YCbCr Matrix"] = "TV.709"
    subs.styles = {STYLE_NAME: _style(scene, family=family)}

    for cue in scene.cues:
        placed = _place(
            cue,
            canvas=canvas,
            tracking=scene.typography.tracking,
            scene_end=scene_end,
        )
        for ev in _events_for(placed):
            subs.append(
                pysubs2.SSAEvent(
                    start=pysubs2.make_time(s=ev.start),
                    end=pysubs2.make_time(s=ev.end),
                    layer=ev.layer,
                    style=STYLE_NAME,
                    text=f"{{{ev.tags}}}{ev.text}" if ev.tags else ev.text,
                )
            )
    return subs.to_string("ass")


def _style(scene: Scene, *, family: str) -> Any:
    """The one :class:`pysubs2.SSAStyle` every event references.

    Alignment 5 (centre-centre) is the load-bearing field: it is what makes
    ``\\pos`` address the text's centre, which is the anchor
    :class:`~muvid.lyricvid.scene.Cue` documents. Margins are zeroed for the same
    reason — a margin would shift a position that was computed against the full
    canvas.

    The primary colour is only a default — every event overrides it with its
    cue's own ``\\c`` — so it is :data:`DEFAULT_STYLE_COLOUR` rather than one
    arbitrary cue's colour, and an edited document that drops an override still
    shows readable text.

    Outline and shadow are off, and the outline colour is the scene's background:
    over the flat background this renderer burns, an outline is invisible work,
    and a human editing the ``.ass`` to burn it over *footage* instead only has
    to raise one number.
    """
    import pysubs2

    typo = scene.typography
    fg = _hex_rgb(DEFAULT_STYLE_COLOUR)
    bg = _hex_rgb(scene.background)
    return pysubs2.SSAStyle(
        fontname=family,
        fontsize=float(_fontsize(0.08, scene.canvas)),
        primarycolor=pysubs2.Color(*fg),
        secondarycolor=pysubs2.Color(*fg),
        outlinecolor=pysubs2.Color(*bg),
        backcolor=pysubs2.Color(*bg),
        bold=typo.weight >= 600,
        alignment=pysubs2.Alignment.MIDDLE_CENTER,
        borderstyle=1,
        outline=0.0,
        shadow=0.0,
        marginl=0,
        marginr=0,
        marginv=0,
    )


# --------------------------------------------------------------------------
# the burn
# --------------------------------------------------------------------------


def _subtitles_filter(ass_path: Path) -> str:
    r"""The ``subtitles`` filter, with ``ass_path`` escaped for the filtergraph.

    ffmpeg unescapes a filter option **twice** — once as it splits the graph,
    once as it splits that filter's options — so a path containing ``:`` (every
    Windows path, and any workdir named after a timestamp) has to be escaped
    twice. :func:`muvid.visualize.canvas.escape_filter_value` is that escaper and
    is reused rather than mirrored; it is the one muvid already broke this on.

    The path is written POSIX-style first: after unescaping, ``C:/x/y.ass`` opens
    on Windows exactly as ``C:\x\y.ass`` does, and forward slashes keep the
    escaped form readable when someone has to debug the filtergraph.
    """
    from muvid.visualize.canvas import escape_filter_value

    return f"subtitles=filename={escape_filter_value(ass_path.as_posix())}"


def _video_args(*, crf: int, fps: int) -> list[str]:
    """H.264 the way ``muvid.visualize.verify.verify_video`` demands it.

    High profile, yuv420p, closed GOP, then ``-use_editlist 0`` +
    ``+negative_cts_offsets`` — the pair that removes the ``elst`` box YouTube
    refuses and that ``+faststart`` alone does *not* remove.

    Spelled out here rather than reached for across
    :mod:`muvid.visualize.video`'s module-private helpers: the flags are a
    contract with :func:`~muvid.visualize.verify.verify_video`, which this
    renderer runs on its own output, so the check and not the copy is what keeps
    them honest.
    """
    return [
        "-c:v",
        "libx264",
        "-preset",
        X264_PRESET,
        "-crf",
        str(crf),
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-g",
        str(max(1, round(fps * GOP_SECONDS))),
        "-sc_threshold",
        "0",
        "-bf",
        "2",
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-ac",
        "2",
        "-use_editlist",
        "0",
        "-movflags",
        "+faststart+negative_cts_offsets",
    ]


def render(
    scene: Scene,
    *,
    audio: Path,
    output: Path,
    workdir: Path,
    ass_path: Path | None = None,
    crf: int = 18,
) -> RenderResult:
    """Burn the scene over a solid background and mux the song.

    Three steps, and the middle one is the whole renderer: write the ``.ass``,
    generate a flat ``color`` source at the canvas's size and rate, and let
    libass draw the document onto it while the song is mapped through untouched.

    The video runs for the *song's* measured duration rather than the scene's, so
    a treatment that stops short of the last bar still produces a video the
    length of the track — which is what
    :func:`~muvid.visualize.verify.verify_video`'s duration check compares
    against, and it is run here on the finished file.

    Args:
        scene: The compiled scene.
        audio: The song. Its duration sets the video's.
        output: Where the mp4 goes.
        workdir: Directory for intermediates — this render owns it.
        ass_path: Where to keep the subtitle document (default:
            ``workdir/<output stem>.ass``). It is a deliverable, not a temp file.
        crf: x264 quality, lower is better. 18 is visually lossless for flat
            colour and text.

    Returns:
        A :class:`~muvid.subgenres._contract.RenderResult` carrying the mp4, its
        measured duration, the ``.ass`` under ``artifacts['ass']``, and a ``meta``
        recording the renderer, the font actually used (and what was asked for,
        when they differ), the cue/event counts and any verification failure.

    Raises:
        muvid.visualize.ffmpeg.FfmpegError: This ffmpeg has no ``subtitles``
            filter (it is a libass build option, so a working ffmpeg is not
            enough), or the burn failed.
    """
    from muvid.visualize.ffmpeg import media_duration, require_filter, run_ffmpeg
    from muvid.visualize.verify import verify_video

    require_filter("subtitles", needed_for="lyric video rendering")

    audio, output, workdir = Path(audio), Path(output), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    ass_path = Path(ass_path) if ass_path else workdir / f"{output.stem}.ass"
    ass_path.parent.mkdir(parents=True, exist_ok=True)

    family, substituted = _resolve_font(scene.typography.family)
    document = scene_to_ass(scene, font=family)
    ass_path.write_text(document, encoding="utf-8")

    canvas = scene.canvas
    song_s = media_duration(audio)
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c={_ffmpeg_colour(scene.background)}"
            f":s={canvas.width}x{canvas.height}:r={canvas.fps}:d={song_s:.3f}",
            "-i",
            str(audio),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-vf",
            _subtitles_filter(ass_path),
            *_video_args(crf=crf, fps=canvas.fps),
            "-shortest",
            str(output),
        ]
    )

    checks = verify_video(
        output, audio=audio, expected_canvas=(canvas.width, canvas.height)
    )
    meta: dict[str, Any] = {
        "renderer": RENDERER_NAME,
        "font": family,
        "n_cues": len(scene.cues),
        "n_events": sum(l.startswith("Dialogue:") for l in document.splitlines()),
        "canvas": [canvas.width, canvas.height],
        "fps": canvas.fps,
        "crf": crf,
        "verify_failures": [f"{c.name}: {c.detail}" for c in checks if not c],
    }
    if substituted:
        # Reported rather than raised: the type is a preference, the video is the
        # deliverable — but a caller comparing two treatments has to be able to
        # see that it is comparing two fallbacks.
        meta["font_requested"] = substituted
    return RenderResult(
        output=output,
        duration_s=media_duration(output),
        artifacts={"ass": ass_path},
        meta=meta,
    )
