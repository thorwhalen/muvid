"""Visual strategies: how the *picture* of an audio-driven video is produced.

A strategy is a function from a :class:`VisualContext` (the audio, the optional
cover, the duration, the canvas) to a :class:`VisualPlan` (the ffmpeg inputs
and filter chains that yield one video stream). :mod:`muvid.visualize.video` owns the
muxing, encoding, and loudness; a strategy only says what the frames look like.

That split is what keeps this open-closed: the built-ins are registered by name
(``"still"``, ``"ken_burns"``, ``"cqt"``, ...), and anything else you can express
as a callable — a librosa/matplotlib animation, a projectM render, a shader —
plugs in through the same seam, either by returning a :class:`VisualPlan` or by
returning the path of a silent video it rendered itself.

    >>> sorted(list_visuals())  # doctest: +NORMALIZE_WHITESPACE
    ['bars', 'cqt', 'ken_burns', 'scope', 'spectrum', 'still', 'waves']

Conventions a strategy must honour:

- **ffmpeg input 0 is always the audio.** Inputs a plan adds are numbered from
  1, in the order they appear in :attr:`VisualPlan.inputs`.
- To react to the audio, set ``uses_audio=True`` and consume the ``[aviz]``
  label — a dedicated copy of the audio, split off so the output track stays
  untouched.
- Emit exactly one video stream, labelled :attr:`VisualPlan.video`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from muvid.visualize.canvas import (
    CoverLayout,
    TitleStyle,
    background_chain,
    canvas_image,
    cover_chain,
    overlay_chain,
)
from muvid.visualize.ffmpeg import PathLike, require_filter
from muvid.visualize.reactive import (
    FLASH_BRIGHTNESS,
    FLASH_SATURATION,
    flash_filter,
)

#: Over a reactive background the cover fills nearly the whole frame (with a
#: little padding), and is made slightly transparent so the visualizer plays on
#: through it rather than being hidden behind an opaque card.
REACTIVE_COVER_FRACTION = 0.95
REACTIVE_COVER_ALPHA = 0.85

#: The vectorscope fills the whole frame, so it keeps the same big cover as the
#: other visuals but a touch more transparent, letting the line-work read both
#: through the cover and in the space around it.
SCOPE_COVER_ALPHA = 0.72

#: Default accent for the line/bar visualizers, applied as a *tint*. Several
#: ffmpeg visualizers ignore their ``colors`` option (``showfreqs`` draws white
#: whatever you ask for), so rather than fight each filter we render them white
#: and recolour the whole visualization here — one knob, identical accent across
#: every method, so a whole album's videos read as one release. It is a
#: ``colorchannelmixer`` mapping white (r=g=b) to a light teal; override per call
#: with the ``tint`` option (an empty string leaves the visualizer's own colour).
DEFAULT_TINT = "colorchannelmixer=rr=0.16:gg=0.80:bb=0.85"

#: Reactive backgrounds are pushed dark and near-neutral so the teal accent reads
#: (a bright, saturated blurred cover both tints everything its own colour and,
#: under the screen blend, washes the accent out to white). The sharp centred
#: cover still carries the artwork's colour; only the surround is muted.
REACTIVE_BG_SATURATION = 0.18
REACTIVE_BG_DIM = 0.5


@dataclass(frozen=True)
class VisualContext:
    """Everything a visual strategy needs to know about the render.

    Attributes:
        audio: The audio file (ffmpeg input 0).
        image: The cover art, if the caller supplied one.
        duration: Audio duration in seconds.
        size: Canvas size (width, height).
        fps: Output frame rate.
        layout: How the cover sits on the canvas.
        title: Title to burn in, if any.
        title_style: How to draw that title.
        workdir: A directory the strategy may write intermediate files into.
        options: Strategy-specific knobs, passed straight through by the caller.
    """

    audio: Path
    image: Path | None
    duration: float
    size: tuple[int, int]
    fps: int
    layout: CoverLayout = field(default_factory=CoverLayout)
    title: str | None = None
    title_style: TitleStyle | None = None
    workdir: Path = field(default_factory=Path)
    options: dict = field(default_factory=dict)

    def require_image(self, visual: str) -> Path:
        """The cover image, or a :class:`ValueError` naming what to do instead."""
        if self.image is None:
            raise ValueError(
                f"The {visual!r} visual needs an image. Pass image=..., or pick an "
                "audio-reactive visual that works without one "
                "(e.g. visual='cqt')."
            )
        return self.image


@dataclass
class VisualPlan:
    """The ffmpeg fragments that render one strategy's video stream.

    Attributes:
        inputs: Extra ffmpeg input argument groups (each ends with ``-i PATH``),
            numbered from input 1.
        filters: ``filter_complex`` chains, joined with ``;`` by the renderer.
        video: Label of the video stream the chains emit.
        uses_audio: The plan consumes the ``[aviz]`` audio copy.
        has_cover: The plan already placed the cover; the renderer must not
            overlay it again.
        has_title: The plan already burnt in the title; the renderer must not
            draw it again.
        still: When set, the video *is* this static image — the renderer takes a
            much cheaper path (encode one short segment, then loop it) and
            ignores ``inputs``/``filters``.
    """

    inputs: list[list[str]] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    video: str = "vbg"
    uses_audio: bool = False
    has_cover: bool = False
    has_title: bool = False
    still: Path | None = None


#: A strategy: context in, plan out. Returning a path to an already-rendered
#: silent video is also accepted (see :func:`resolve_visual`).
Visual = Callable[[VisualContext], "VisualPlan | Path | str"]

_VISUALS: dict[str, Visual] = {}


def register_visual(name: str) -> Callable[[Visual], Visual]:
    """Register a visual strategy under ``name`` (the open-closed seam).

    Examples:
        >>> @register_visual("black")
        ... def _black(ctx):
        ...     w, h = ctx.size
        ...     return VisualPlan(filters=[f"color=c=black:s={w}x{h}[vbg]"])
        >>> "black" in list_visuals()
        True
        >>> _ = _VISUALS.pop("black")  # (keep the registry tidy for the next doctest)
    """

    def decorate(fn: Visual) -> Visual:
        _VISUALS[name] = fn
        return fn

    return decorate


def list_visuals() -> list[str]:
    """The names of every registered visual strategy."""
    return sorted(_VISUALS)


def resolve_visual(visual: str | Visual, ctx: VisualContext) -> VisualPlan:
    """Turn ``visual`` (a name, or any callable) into a :class:`VisualPlan`.

    ``"auto"`` picks the cheapest strategy that suits the inputs: a still cover
    when there is an image, an audio-reactive CQT when there is not.

    A callable may return a :class:`VisualPlan`, or the path of a silent video
    it rendered itself — the latter is the escape hatch for backends that do not
    express themselves as an ffmpeg filtergraph (librosa/matplotlib, projectM,
    a headless-browser capture...).

    Raises:
        ValueError: ``visual`` names a strategy that is not registered.
    """
    if isinstance(visual, str):
        name = visual
        if name == "auto":
            name = "still" if ctx.image else "cqt"
        if name not in _VISUALS:
            raise ValueError(
                f"Unknown visual {visual!r}. Registered: {', '.join(list_visuals())}. "
                "You can also pass a callable, or register your own with "
                "@register_visual."
            )
        fn = _VISUALS[name]
    else:
        fn = visual

    plan = fn(ctx)
    if isinstance(plan, (str, Path)):  # a pre-rendered silent video
        return VisualPlan(
            inputs=[["-i", str(plan)]],
            filters=["[1:v]setsar=1[vbg]"],
            video="vbg",
            has_cover=True,
        )
    return plan


# --------------------------------------------------------------------------
# Built-in strategies
# --------------------------------------------------------------------------


@register_visual("still")
def still_visual(ctx: VisualContext) -> VisualPlan:
    """The cover, composed on a 16:9 canvas, held for the whole song."""
    image = ctx.require_image("still")
    canvas = canvas_image(
        image,
        saveas=ctx.workdir / "canvas.png",
        size=ctx.size,
        layout=ctx.layout,
        title=ctx.title,
        title_style=ctx.title_style,
    )
    return VisualPlan(still=canvas, has_cover=True, has_title=True)


@register_visual("ken_burns")
def ken_burns_visual(ctx: VisualContext) -> VisualPlan:
    """A slow pan/zoom across the cover, lasting exactly as long as the song.

    Renders through the ``burns`` package (via ``mixing.video``). By default it
    pans across the *composed canvas* rather than the raw cover, so a square or
    portrait image still fills a 16:9 frame instead of being letterboxed by the
    pan.

    Frames are rendered in Python (Pillow), so this is by far the slowest
    visual — budget several times the song's duration. The ffmpeg-native
    visuals are an order of magnitude faster.

    Options:
        ``source``: ``"canvas"`` (default) or ``"image"`` — what to pan across.
    """
    image = ctx.require_image("ken_burns")
    try:
        from mixing.video import ken_burns_video
    except ImportError as e:  # pragma: no cover - environment dependent
        raise ImportError(
            "The 'ken_burns' visual renders through 'burns' (normally installed "
            "with 'mixing'), which is not importable. Reinstall with "
            "'pip install burns moviepy', or use visual='still' for a pan-free "
            "cover video, which needs nothing beyond ffmpeg."
        ) from e

    if ctx.options.get("source", "canvas") == "canvas":
        source = canvas_image(
            image,
            saveas=ctx.workdir / "canvas.png",
            size=ctx.size,
            layout=ctx.layout,
            title=ctx.title,
            title_style=ctx.title_style,
        )
    else:
        source = image

    silent = ken_burns_video(
        str(source),
        duration=ctx.duration,
        fps=ctx.fps,
        output_size=ctx.size,
        output=str(ctx.workdir / "ken_burns.mp4"),
    )
    return VisualPlan(
        inputs=[["-i", str(silent)]],
        filters=["[1:v]setsar=1[vbg]"],
        video="vbg",
        has_cover=True,
        has_title=ctx.options.get("source", "canvas") == "canvas",
    )


def _reactive_plan(
    ctx: VisualContext,
    viz: str,
    *,
    filter_name: str,
    cover_fraction: float = REACTIVE_COVER_FRACTION,
    cover_alpha: float = REACTIVE_COVER_ALPHA,
    bg_dim: float | None = REACTIVE_BG_DIM,
    bg_saturation: float | None = REACTIVE_BG_SATURATION,
    tint: str = DEFAULT_TINT,
) -> VisualPlan:
    """Compose an audio-reactive filter over the cover, with the cover centred.

    With a cover image: the blurred cover fills the frame, the visualization is
    screened over it, and the sharp cover sits centred on top — a "now playing"
    card. Without one: the visualization alone.

    The per-visual defaults here (``cover_fraction``, ``cover_alpha``,
    ``bg_dim``, ``bg_saturation``) let a dark, sparse visualizer trade cover size
    and background brightness for legibility — a spectrogram or vectorscope needs
    a darker frame and more room than the CQT bars do. Each is overridable per
    call through the matching key in the visual ``options`` dict.

    Args:
        cover_fraction: How much of the frame the centred cover fills.
        cover_alpha: Cover opacity, 0–1; below 1 lets the visualizer show through.
        bg_dim: Background darkening (``None`` keeps the layout's value). Darker
            backgrounds make a faint visualizer stand out.
        bg_saturation: Background saturation (``None`` keeps the layout's value).

    Options:
        ``blurred_background``: put the visualization over the blurred cover
            (default ``True``).
        ``blend``: ffmpeg blend mode for that (default ``"screen"``).
        ``cover_fraction`` / ``cover_alpha`` / ``bg_dim`` / ``bg_saturation``:
            override the corresponding argument for this call.
    """
    require_filter(filter_name, needed_for=f"the {filter_name!r} visual")

    tint = ctx.options.get("tint", tint)
    # Two format hops that matter: colorchannelmixer is a silent no-op on YUV
    # (so tint over ``rgba``), and the screen blend must run in *alpha-free* RGB —
    # both YUV and alpha-carrying ``rgba`` corrupt its colours — so end on gbrp.
    viz_tinted = (f"{viz},format=rgba,{tint}" if tint else viz) + ",format=gbrp"

    if ctx.image is None:
        return VisualPlan(
            filters=[f"[aviz]{viz_tinted}[vbg]"],
            video="vbg",
            uses_audio=True,
            has_cover=True,
        )

    opt = ctx.options
    layout = replace(
        ctx.layout,
        cover_fraction=opt.get("cover_fraction", cover_fraction),
        cover_alpha=opt.get("cover_alpha", cover_alpha),
        dim=opt.get("bg_dim", ctx.layout.dim if bg_dim is None else bg_dim),
        saturation=opt.get(
            "bg_saturation",
            ctx.layout.saturation if bg_saturation is None else bg_saturation,
        ),
    )
    over_cover = ctx.options.get("blurred_background", True)
    blend = ctx.options.get("blend", "screen")

    filters = ["[1:v]split=2[_bgsrc][_fgsrc]", f"[aviz]{viz_tinted}[_viz]"]
    if over_cover:
        filters += [
            background_chain(ctx.size, layout, src="_bgsrc", out="_bgc"),
            # Match the visualization's alpha-free RGB so the screen blend is clean.
            "[_bgc]format=gbrp[_bg]",
            f"[_bg][_viz]blend=all_mode={blend}:shortest=1[_bgviz]",
        ]
    else:
        filters += ["[_bgsrc]nullsink", "[_viz]null[_bgviz]"]
    filters += [
        cover_chain(ctx.size, layout, src="_fgsrc", out="_fg"),
        overlay_chain(background="_bgviz", cover="_fg", out="vbg", shortest=True),
    ]
    return VisualPlan(
        inputs=[["-loop", "1", "-framerate", str(ctx.fps), "-i", str(ctx.image)]],
        filters=filters,
        video="vbg",
        uses_audio=True,
        has_cover=True,
    )


@register_visual("cqt")
def cqt_visual(ctx: VisualContext) -> VisualPlan:
    """Constant-Q transform bars — pitch-aligned, the most *musical* reactive look.

    Rendered white; colour comes from the accent ``tint`` (recolour it with the
    ``tint`` option — *not* a per-filter colour, which the tint would multiply).

    ``showcqt`` can draw two panes: the bargraph, and beneath it a *sonogram*
    that keeps every past frame and scrolls it downward, so the music leaves a
    trail of where it has been. The sonogram is off by default (bars only, the
    cleanest read). Give ``sono_fraction`` a value in (0, 1) to hand that share
    of the frame's height to the trail — the bargraph keeps the rest.

    Options:
        ``sono_fraction``: share of the height given to the scrolling sonogram,
            0 (default, bars only) to just under 1.
        ``sono_v`` / ``bar_v``: sonogram and bargraph volume (sensitivity).
        ``sono_g`` / ``bar_g``: sonogram and bargraph gamma (contrast).
        Plus the shared background/cover keys of :func:`_reactive_plan`.

    Examples:
        >>> ctx = VisualContext(Path("a.wav"), None, 10.0, (1920, 1080), 24)
        >>> "sono_h=0" in cqt_visual(ctx).filters[0]  # bars only, by default
        True
        >>> trail = replace(ctx, options={"sono_fraction": 0.6})
        >>> f = cqt_visual(trail).filters[0]
        >>> "bar_h=432" in f and "sono_h=648" in f  # 40% bars / 60% trail
        True
    """
    width, height = ctx.size
    opt = ctx.options
    # showcqt lays the frame out as bar_h + axis_h + sono_h, and rejects a set
    # that does not add up to the height — so derive one pane from the other
    # rather than letting a caller specify both and get an ffmpeg error.
    sono_fraction = opt.get("sono_fraction", 0.0)
    if not 0 <= sono_fraction < 1:
        raise ValueError(
            f"sono_fraction must be in [0, 1), got {sono_fraction!r}. It is the "
            "share of the frame the scrolling sonogram takes; the bargraph gets "
            "the rest, so 1 would leave the bargraph no height."
        )
    sono_h = int(height * sono_fraction)
    viz = (
        f"showcqt=s={width}x{height}:r={ctx.fps}:count=6"
        f":sono_v={opt.get('sono_v', 16)}:bar_v={opt.get('bar_v', 16)}"
        f":gamma={opt.get('sono_g', 3)}:bar_g={opt.get('bar_g', 2)}"
        f":cscheme=1|1|1|1|1|1:bar_h={height - sono_h}:axis_h=0:sono_h={sono_h}"
        ":axis=0"
    )
    return _reactive_plan(ctx, viz, filter_name="showcqt")


@register_visual("bars")
def bars_visual(ctx: VisualContext) -> VisualPlan:
    """Frequency bars (the classic EQ look), via ffmpeg's ``showfreqs``.

    Rendered white; colour comes from the accent ``tint`` (see :data:`DEFAULT_TINT`).
    """
    width, height = ctx.size
    viz = (
        f"showfreqs=s={width}x{height}:rate={ctx.fps}:mode=bar:ascale=log"
        ":fscale=log:win_size=2048:colors=white"
    )
    return _reactive_plan(ctx, viz, filter_name="showfreqs")


@register_visual("spectrum")
def spectrum_visual(ctx: VisualContext) -> VisualPlan:
    """A scrolling spectrogram, via ffmpeg's ``showspectrum``.

    A ``log`` *frequency* axis spreads out the low-mid range where music lives
    (a linear axis crams it into the bottom edge), lifted gain makes the detail
    read, and ``overlap`` quickens the right-to-left scroll. Rendered over a
    darker background so it stands out. The default ``green`` colormap is tinted
    to the teal accent; pass a ``color`` to use ffmpeg's colormap instead.

    The whole spectrogram also **pulses with the beat**: a precomputed onset
    envelope (see :mod:`muvid.visualize.reactive`) drives a ``sendcmd``-controlled
    brightness/saturation flash, so attacks in the music read as flashes at the
    live leading edge instead of the display feeling merely synced to playback.
    The flash is appended last, after the recolour, so it modulates the colours
    the frame actually shows.

    Options: ``color`` (colormap — the teal tint applies only to the default),
    ``gain``, ``saturation``, ``overlap`` (scroll speed, 0–1), ``flash`` (bool,
    default on), ``flash_brightness``, ``flash_saturation``, plus the shared
    background/cover keys.
    """
    width, height = ctx.size
    color = ctx.options.get("color", "green")
    viz = (
        f"showspectrum=s={width}x{height}:slide=scroll:mode=combined"
        f":color={color}:scale=log:fscale=log"
        f":gain={ctx.options.get('gain', 4)}"
        f":saturation={ctx.options.get('saturation', 2)}"
        f":overlap={ctx.options.get('overlap', 0.5)}:fps={ctx.fps}"
    )
    if color == "green":  # green magnitude → teal (add a little red, lots of blue)
        viz += ",format=rgba,colorchannelmixer=rg=0.05:bg=0.9"
    if ctx.options.get("flash", True):
        viz += flash_filter(
            ctx.audio,
            fps=ctx.fps,
            duration=ctx.duration,
            workdir=ctx.workdir,
            brightness=ctx.options.get("flash_brightness", FLASH_BRIGHTNESS),
            saturation=ctx.options.get("flash_saturation", FLASH_SATURATION),
        )
    # spectrum colours itself, so skip the shared line tint (tint="").
    return _reactive_plan(ctx, viz, filter_name="showspectrum", bg_dim=0.5, tint="")


@register_visual("waves")
def waves_visual(ctx: VisualContext) -> VisualPlan:
    """The waveform, via ffmpeg's ``showwaves``.

    Rendered white; colour comes from the accent ``tint``. ``options={"mode":
    ...}`` sets the waveform *shape* (``cline``/``line``/``p2p``/``point``).
    """
    width, height = ctx.size
    viz = (
        f"showwaves=s={width}x{height}:rate={ctx.fps}"
        f":mode={ctx.options.get('mode', 'cline')}:colors=white"
    )
    return _reactive_plan(ctx, viz, filter_name="showwaves")


@register_visual("scope")
def scope_visual(ctx: VisualContext) -> VisualPlan:
    """The stereo Lissajous figure, via ffmpeg's ``avectorscope``.

    Tuned for drama *and* dynamics. Anti-aliased lines, ``mirror=xy`` to fill all
    four quadrants symmetrically, and a generous ``zoom`` so the figure spills
    into the space around the cover. Crucially the amplitude scale is **linear**,
    not ``sqrt``: a compressive scale keeps a steady mix pinned wide the whole
    time (a constant scribble), whereas linear lets the figure *breathe* — wide
    and full when the music swells, small and calm when it settles. The cover
    stays the same big size as the other visuals, a touch more transparent so
    the line-work reads through it too.

    Options: ``zoom``, ``scale`` (``lin``/``sqrt``/``cbrt``/``log``), ``mirror``
    (``none``/``x``/``y``/``xy``), ``draw``, plus the shared background/cover keys.
    """
    width, height = ctx.size
    viz = (
        f"avectorscope=s={width}x{height}:rate={ctx.fps}"
        f":draw={ctx.options.get('draw', 'aaline')}"
        f":scale={ctx.options.get('scale', 'lin')}"
        f":zoom={ctx.options.get('zoom', 5)}"
        f":mirror={ctx.options.get('mirror', 'xy')}"
        ":rc=200:gc=200:bc=200:rf=10:gf=10:bf=10"  # white; teal via the tint
    )
    return _reactive_plan(
        ctx,
        viz,
        filter_name="avectorscope",
        cover_alpha=SCOPE_COVER_ALPHA,
        bg_dim=0.55,
    )
