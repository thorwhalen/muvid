"""The web backend: a :class:`~muvid.lyricvid.scene.Scene` as a deterministic
HTML page, screenshotted frame by frame and muxed with the song.

ASS (:mod:`muvid.lyricvid.render_ass`) is the default backend and should stay
that way — it is fast, has no browser to install, and libass is what every
player already runs. This module is the **escape hatch**: CSS filters, blend
modes, variable fonts and arbitrary easing are things a subtitle format cannot
express, and a treatment that wants them should not have to become a different
product. One compiler, two renderers.

**Determinism is the whole design, not a nice property.** The page computes
every visual property as a pure function of the time handed to
``window.setTime(t)``. There are no CSS declarations that interpolate over wall
time, no keyframes, no ``requestAnimationFrame``-driven state, no ``Date.now``,
no ``Math.random`` — so a screenshot at ``t`` is the same image on every run, on
every machine, and the capture loop can take as long as it likes per frame
without the video drifting against the audio. Anything that makes the page's
appearance depend on *when* it was asked rather than *what* it was asked is a
bug in this module, and :func:`scene_to_html`'s doctests assert the absence of
the usual offenders.

The technique (deterministic ``setTime`` + Playwright capture + an ffmpeg mux)
is ported from a working 81-second render; the poem-specific parts of that
prototype are not here.

**This renderer does no layout.** A cue's centre, size and stacking come from
:func:`~muvid.lyricvid.scene.compile_scene` and are written into the markup
once, unchanged; the page never re-measures text and never nudges a coordinate.
That is what makes the two backends comparable — the same scene, drawn twice —
and it means a layout mistake upstream shows up here as a visibly wrong frame
rather than being quietly absorbed. Absorbing it would be worse: it would make
the browser and ASS renders disagree, and would then be wrong again the day the
compiler changed.

Playwright is an **optional** dependency (``pip install 'muvid[lyricvid-web]'``
plus ``playwright install chromium``). It is imported inside function bodies, so
importing this module costs nothing and stays on muvid's import-safe path.
"""

from __future__ import annotations

import json
import shutil
from html import escape as _escape
from pathlib import Path
from typing import Any, Iterable

from muvid.lyricvid.scene import Cue, Scene
from muvid.subgenres import RenderResult
from muvid.visualize.ffmpeg import media_duration, run_ffmpeg

__all__ = ["scene_to_html", "render", "WebRenderUnavailable", "INSTALL_HINT"]


class WebRenderUnavailable(RuntimeError):
    """Playwright (or its Chromium build) is not installed. Carries the remedy."""


#: What to tell a caller who has not installed the optional backend. Both lines
#: matter: the wheel and the browser binary are separate installs, and missing
#: the second is the failure people actually hit.
INSTALL_HINT = (
    "muvid's web lyric-video backend needs Playwright and a Chromium build:\n"
    "    pip install 'muvid[lyricvid-web]'\n"
    "    playwright install chromium\n"
    "The ASS backend (muvid.lyricvid.render_ass) needs neither and is the default."
)

# --------------------------------------------------------------------------
# Tunables. No magic numbers in the page: Python is the SSOT and ships them to
# the JS as `window.__CONST`, so a motion's feel is edited in one place.
# --------------------------------------------------------------------------

#: Seconds a ``pop`` overshoot takes to settle back to rest scale.
POP_S = 0.26
#: How far ``pop`` overshoots, as a fraction of the resting size.
POP_AMOUNT = 0.12
#: How far ``rise`` starts below its resting position, in em of its own size.
RISE_EM = 0.35
#: Seconds a ``dim_from`` / ``ignite_at`` colour crossfade takes.
DIM_S = 0.30
#: Seconds per character for ``typewriter``. Compressed when the word is short
#: enough that typing at this rate would outlast it.
TYPEWRITER_CHAR_S = 0.045

#: ``line-height`` on a cue. Above 1 so ascenders and descenders sit inside the
#: element's own box — which is what lets ``wipe`` clip with a plain
#: ``inset(0 X% 0 0)`` without shaving the tops off letters.
LINE_HEIGHT = 1.35

#: Appended after the treatment's own family, so an unavailable family falls
#: back rather than failing the render (:class:`muvid.lyricvid.spec.Typography`).
FONT_FALLBACK = '"DejaVu Sans", "Helvetica Neue", Helvetica, Arial, sans-serif'

#: Chromium flags that remove sources of run-to-run pixel variation: a fixed
#: colour profile, no font hinting (which is DPI- and platform-dependent) and no
#: subpixel-antialiased text (which is layout-order-dependent on some builds).
CHROMIUM_ARGS: tuple[str, ...] = (
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--disable-lcd-text",
    "--hide-scrollbars",
)

#: How long to wait for ``window.__ready`` (fonts loaded, first frame drawn).
READY_TIMEOUT_MS = 30_000

#: Names inside ``workdir``. The page is kept as an artifact — a render you can
#: open in a browser and scrub is the difference between a debuggable backend
#: and an opaque one — while the frames, which are large and reproducible, go.
PAGE_NAME = "page.html"
FRAMES_DIRNAME = "frames"
FRAME_PATTERN = "f%06d.png"

#: H.264 High / yuv420p — the profile every platform accepts.
VIDEO_CODEC_ARGS: tuple[str, ...] = (
    "-c:v",
    "libx264",
    "-preset",
    "slow",
    "-pix_fmt",
    "yuv420p",
    "-profile:v",
    "high",
    "-level",
    "4.1",
)
#: AAC, 48 kHz, stereo.
AUDIO_CODEC_ARGS: tuple[str, ...] = (
    "-c:a",
    "aac",
    "-b:a",
    "320k",
    "-ar",
    "48000",
    "-ac",
    "2",
)

_HEX = set("0123456789abcdefABCDEF")


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------


def _colour(value: str, *, what: str) -> str:
    """A validated ``#rrggbb``. Raises rather than substituting a colour.

    :func:`muvid.lyricvid.spec.repair` already guarantees this for anything that
    came through the compiler; a hand-built :class:`~muvid.lyricvid.scene.Scene`
    that carries something else is a caller error, and a silently substituted
    colour would be a wrong render reported as a good one.
    """
    ok = (
        isinstance(value, str)
        and len(value) == 7
        and value[0] == "#"
        and all(c in _HEX for c in value[1:])
    )
    if not ok:
        raise ValueError(f"{what} must be a #rrggbb colour, got {value!r}")
    return value


def _font_stack(family: str) -> str:
    """The treatment's family, quoted and de-fanged, ahead of the fallbacks."""
    clean = family.replace('"', "").replace("\\", "").replace(";", "").strip()
    return f'"{clean}", {FONT_FALLBACK}' if clean else FONT_FALLBACK


def _cue_style(cue: Cue) -> str:
    """The one thing about a cue that never changes: where it is and how big.

    ``x``/``y`` are the *centre* (:class:`~muvid.lyricvid.scene.Cue`'s contract),
    so they become ``left``/``top`` percentages plus a ``translate(-50%,-50%)``.
    ``size`` is a fraction of canvas height and becomes ``vh``, which is why one
    page renders correctly at 1080p and at 4K. Written once, into the markup —
    a cue never moves, so a word can never reflow.
    """
    return (
        f"left:{cue.x * 100:.4f}%;"
        f"top:{cue.y * 100:.4f}%;"
        f"font-size:{cue.size * 100:.4f}vh;"
        f"z-index:{int(cue.layer)}"
    )


def _cue_markup(cue: Cue) -> str:
    """One absolutely-positioned element. ``typewriter`` gets a span per glyph.

    The glyphs are all present from the start and revealed with ``visibility``,
    never by changing the text — appearing character by character must not move
    the characters already there.
    """
    inner = (
        "".join(f"<span>{_escape(ch)}</span>" for ch in cue.text)
        if cue.motion == "typewriter"
        else _escape(cue.text)
    )
    return f'<div class="cue" style="{_cue_style(cue)}">{inner}</div>'


def _cue_payload(cue: Cue) -> dict[str, Any]:
    """The time envelope and colours — everything ``setTime`` needs, and no
    geometry, because the geometry is already in the markup."""
    return {
        "motion": cue.motion,
        "t_in": cue.t_in,
        "t_full": cue.t_full,
        "t_out": cue.t_out,
        "t_gone": cue.t_gone,
        "colour": _colour(cue.colour, what="cue.colour"),
        "dim_colour": (
            None
            if cue.dim_colour is None
            else _colour(cue.dim_colour, what="cue.dim_colour")
        ),
        "dim_from": cue.dim_from,
        # per-word karaoke wipe window / concrete-page ignition, when the
        # archetype computed one (muvid.lyricvid.scene puts them in `extra`).
        "wipe_end": cue.extra.get("wipe_end"),
        "ignite_at": cue.extra.get("ignite_at"),
    }


def _inline_json(payload: Any) -> str:
    """``json.dumps`` that is safe to drop inside a ``<script>`` element."""
    return json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")


_PAGE_JS = """
'use strict';
(function () {
  var S = window.__SCENE;
  var K = window.__CONST;

  /* ---- pure helpers ---------------------------------------------------- */
  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  function easeOut(x) { return 1 - Math.pow(1 - x, 3); }
  function step(c, t) { return t >= c.t_in ? 1 : 0; }
  function eased(c, t) {
    var d = c.t_full - c.t_in;
    return d <= 0 ? step(c, t) : easeOut(clamp01((t - c.t_in) / d));
  }
  function rgb(c) {
    return [parseInt(c.slice(1, 3), 16),
            parseInt(c.slice(3, 5), 16),
            parseInt(c.slice(5, 7), 16)];
  }
  function mix(a, b, f) {
    return 'rgb(' + Math.round(a[0] + (b[0] - a[0]) * f) + ','
                  + Math.round(a[1] + (b[1] - a[1]) * f) + ','
                  + Math.round(a[2] + (b[2] - a[2]) * f) + ')';
  }

  /* ---- motion tables: one entry per muvid.lyricvid.spec.MOTIONS key ----- */
  var ARRIVE = {
    cut: step, fade: eased, pop: eased, rise: eased,
    wipe: step,        /* the reveal IS the wipe */
    typewriter: step   /* the reveal IS the typing */
  };
  var BASE = 'translate(-50%,-50%)';
  var TRANSFORM = {
    pop: function (c, t) {
      var q = clamp01((t - c.t_in) / K.pop_s);
      var k = 1 + K.pop_amount * (1 - easeOut(q));
      return BASE + ' scale(' + k.toFixed(4) + ')';
    },
    rise: function (c, t) {
      var dy = K.rise_em * (1 - eased(c, t));
      return BASE + ' translateY(' + dy.toFixed(4) + 'em)';
    }
  };

  /* ---- the frame function: everything below is f(t) --------------------- */
  function alphaOf(c, t) {
    if (t < c.t_in) { return 0; }
    var a = (ARRIVE[c.motion] || eased)(c, t);
    if (c.t_out != null) {                 /* null OR undefined: never leaves */
      var g = c.t_gone == null ? c.t_out : c.t_gone;
      if (t >= g) { return 0; }
      if (t > c.t_out) {
        var d = g - c.t_out;
        a *= d <= 0 ? 0 : (1 - easeOut(clamp01((t - c.t_out) / d)));
      }
    }
    return a;
  }
  function colourOf(it, t) {
    var c = it.cue;
    if (it.dim) {
      /* concrete_page: present from the start, dim, igniting when sung */
      if (c.ignite_at != null) {
        return mix(it.dim, it.base, easeOut(clamp01((t - c.ignite_at) / K.dim_s)));
      }
      /* persistence='dim': arrives lit, recedes once it has been sung */
      if (c.dim_from != null) {
        return mix(it.base, it.dim, easeOut(clamp01((t - c.dim_from) / K.dim_s)));
      }
    }
    return c.colour;
  }
  function clipOf(c, t) {
    if (c.motion !== 'wipe') { return 'none'; }
    var end = c.wipe_end == null ? c.t_full : c.wipe_end;
    var d = end - c.t_in;
    var f = d <= 0 ? step(c, t) : clamp01((t - c.t_in) / d);
    return 'inset(0 ' + ((1 - f) * 100).toFixed(3) + '% 0 0)';
  }
  function typedCount(it, t) {
    var c = it.cue, n = it.chars.length;
    var end = c.wipe_end != null ? c.wipe_end
            : (c.t_out != null ? c.t_out : c.t_full);
    var span = end - c.t_in;
    var per = K.type_char_s;
    if (span > 0 && span / n < per) { per = span / n; }
    if (per <= 0) { return t >= c.t_in ? n : 0; }
    var k = Math.floor((t - c.t_in) / per) + 1;
    return k < 0 ? 0 : (k > n ? n : k);
  }

  /* ---- pair the payload with the markup --------------------------------- */
  var els = document.querySelectorAll('.cue');
  var items = [];
  for (var i = 0; i < S.cues.length; i++) {
    var c = S.cues[i];
    items.push({
      cue: c,
      el: els[i],
      chars: c.motion === 'typewriter' ? els[i].querySelectorAll('span') : null,
      base: rgb(c.colour),
      dim: c.dim_colour ? rgb(c.dim_colour) : null,
      a: '', tr: '', co: '', cp: '', n: -1
    });
  }

  /* The caches below are a write-skipping optimisation ONLY: every property is
     still assigned f(t) whenever it differs from what is on the element, so the
     rendered frame does not depend on which times were asked for before. */
  function setTime(t) {
    for (var i = 0; i < items.length; i++) {
      var it = items[i], c = it.cue;
      var av = alphaOf(c, t).toFixed(4);
      if (av !== it.a) { it.el.style.opacity = av; it.a = av; }
      if (av === '0.0000') { continue; }
      var fn = TRANSFORM[c.motion];
      var tr = fn ? fn(c, t) : BASE;
      if (tr !== it.tr) { it.el.style.transform = tr; it.tr = tr; }
      var co = colourOf(it, t);
      if (co !== it.co) { it.el.style.color = co; it.co = co; }
      var cp = clipOf(c, t);
      if (cp !== it.cp) { it.el.style.clipPath = cp; it.cp = cp; }
      if (it.chars) {
        var k = typedCount(it, t);
        if (k !== it.n) {
          for (var j = 0; j < it.chars.length; j++) {
            it.chars[j].style.visibility = j < k ? 'visible' : 'hidden';
          }
          it.n = k;
        }
      }
    }
    document.documentElement.dataset.t = t.toFixed(4);
  }

  window.setTime = setTime;
  window.__ready = false;
  window.__cue_count = items.length;
  var fonts = document.fonts ? document.fonts.ready : Promise.resolve();
  fonts.then(function () {
    setTime(0);
    window.__ready = true;   /* the capture loop waits on this */
  });
})();
"""


def scene_to_html(scene: Scene) -> str:
    """A self-contained page exposing a deterministic ``setTime(t)``.

    One absolutely-positioned element per cue, placed once from the cue's
    normalised centre and sized in ``vh``; the script only ever changes
    opacity, transform, colour and clip — never content, never position — so
    words cannot reflow. Nothing in the page depends on wall-clock time.

    Args:
        scene: the compiled scene. Colours must be ``#rrggbb``.

    Returns:
        A complete HTML document, safe to write to a file and open.

    >>> from muvid.lyricvid import spec as spec_mod
    >>> from muvid.lyricvid.scene import compile_scene
    >>> from muvid.lyricvid.timed_text import from_words
    >>> tt = from_words([('one', 0.0, 0.5), ('two', 0.5, 1.0)], duration=1.0)
    >>> scene = compile_scene(spec_mod.TreatmentSpec(), tt)
    >>> page = scene_to_html(scene)

    A page, with a deterministic entry point:

    >>> page.startswith('<!doctype html>')
    True
    >>> 'function setTime(t)' in page and 'window.setTime = setTime' in page
    True
    >>> 'window.__ready' in page
    True

    One element per cue, and the words themselves are in the markup:

    >>> page.count('class="cue"') == len(scene.cues)
    True
    >>> '>one<' in page and '>two<' in page
    True

    And none of the ways a page stops being a pure function of ``t``:

    >>> forbidden = ('Math.random', 'Date.now', 'requestAnimationFrame',
    ...              'performance.now', 'transition:', 'animation:',
    ...              '@keyframes', 'setInterval', 'setTimeout')
    >>> [bad for bad in forbidden if bad in page]
    []
    """
    typo = scene.typography
    bg = _colour(scene.background, what="scene.background")
    cues: Iterable[Cue] = scene.cues
    payload = _inline_json({"cues": [_cue_payload(c) for c in cues]})
    consts = _inline_json(
        {
            "pop_s": POP_S,
            "pop_amount": POP_AMOUNT,
            "rise_em": RISE_EM,
            "dim_s": DIM_S,
            "type_char_s": TYPEWRITER_CHAR_S,
        }
    )
    # letter-spacing is added after EVERY glyph including the last, so a tracked
    # cue's box is one em of tracking wider on the right than its ink; the
    # matching left padding puts the ink back in the middle of the box, which is
    # what translate(-50%) then centres on the cue's x.
    tracking = (
        f"letter-spacing:{typo.tracking}em;padding-left:{typo.tracking}em;"
        if typo.tracking
        else ""
    )
    body = "\n".join(_cue_markup(c) for c in cues)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>muvid lyric video</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{
    width: 100%; height: 100%;
    overflow: hidden;
    background: {bg};
    -webkit-font-smoothing: antialiased;
    text-rendering: geometricPrecision;
  }}
  #stage {{ position: absolute; inset: 0; }}
  .cue {{
    position: absolute;
    white-space: pre;
    line-height: {LINE_HEIGHT};
    font-family: {_font_stack(typo.family)};
    font-weight: {int(typo.weight)};
    {tracking}
    transform: translate(-50%,-50%);
    opacity: 0;
    will-change: opacity, transform;
  }}
  .cue span {{ visibility: hidden; }}
</style>
</head>
<body>
<div id="stage">
{body}
</div>
<script>window.__SCENE = {payload};
window.__CONST = {consts};</script>
<script>{_PAGE_JS}</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# the render
# --------------------------------------------------------------------------


def _check_geometry(scene: Scene, *, scale: int) -> None:
    """Refuse a geometry that would fail later, or quietly lose pixels."""
    canvas = scene.canvas
    if scale < 1:
        raise ValueError(f"scale must be a positive integer, got {scale!r}")
    if canvas.width % scale or canvas.height % scale:
        raise ValueError(
            f"canvas {canvas.width}x{canvas.height} is not divisible by scale={scale}; "
            "the viewport would be rounded down and the render would be the wrong size"
        )
    if canvas.width % 2 or canvas.height % 2:
        raise ValueError(
            f"canvas {canvas.width}x{canvas.height} has an odd dimension; "
            "H.264 yuv420p needs both to be even"
        )
    if canvas.fps <= 0:
        raise ValueError(f"canvas.fps must be positive, got {canvas.fps!r}")


def _sync_playwright():
    """Playwright's sync entry point, or a :class:`WebRenderUnavailable`."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # the optional extra is not installed
        raise WebRenderUnavailable(INSTALL_HINT) from exc
    return sync_playwright


def _launch(pw):
    """Launch headless Chromium, translating "no browser installed" only.

    Any other Playwright failure is re-raised untouched: a browser that crashed
    or a flag it rejected is a real problem, and relabelling it as a missing
    install would send the caller to the wrong remedy.
    """
    from playwright.sync_api import Error as PlaywrightError

    try:
        return pw.chromium.launch(args=list(CHROMIUM_ARGS))
    except PlaywrightError as exc:
        text = str(exc).lower()
        if "executable doesn't exist" in text or "playwright install" in text:
            raise WebRenderUnavailable(INSTALL_HINT) from exc
        raise


def _capture(page: Path, frames: Path, *, scene: Scene, scale: int) -> int:
    """Drive ``setTime(i/fps)`` and screenshot, frame by frame. Returns the count.

    The loop never waits on the page to "settle": there is nothing to settle to.
    Every property was assigned synchronously inside ``setTime``, so the next
    screenshot is already the frame for that time.
    """
    canvas = scene.canvas
    n = max(1, int(round(scene.duration * canvas.fps)))
    sync_playwright = _sync_playwright()
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            tab = browser.new_page(
                viewport={
                    "width": canvas.width // scale,
                    "height": canvas.height // scale,
                },
                device_scale_factor=scale,
            )
            tab.goto(page.resolve().as_uri())
            tab.wait_for_function("window.__ready === true", timeout=READY_TIMEOUT_MS)
            for i in range(n):
                tab.evaluate("t => window.setTime(t)", i / canvas.fps)
                tab.screenshot(
                    path=str(frames / (FRAME_PATTERN % i)),
                    animations="disabled",
                    caret="hide",
                )
        finally:
            browser.close()
    return n


def _encode(
    frames: Path, audio: Path, output: Path, *, fps: int, crf: int, duration: float
) -> None:
    """PNG frames + the song → an H.264/AAC mp4 every platform accepts.

    ``-t duration`` bounds the OUTPUT to the scene's length: the capture rounds
    ``duration * fps`` up to a whole frame, and ``-shortest`` alone still let the
    video run one or two frames past the audio (2.2 s of video over 2.0 s of
    song at 10 fps). Nothing reported it because it sat inside
    ``verify_video``'s tolerance — but a lyric video is by definition the length
    of its song.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-framerate",
            str(fps),
            "-i",
            str(frames / FRAME_PATTERN),
            # INPUT-side -t on the song bounds what the AAC encoder is given to
            # the scene's length. It is not what fixed the 2-frame overshoot
            # (that was the muxer, see -movflags below) but it is the honest
            # bound, and it is stable across ffmpeg 6 and 9 where the muxer
            # flag `-fflags +shortest` is not.
            "-t",
            f"{max(0.0, duration):.3f}",
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{max(0.0, duration):.3f}",
            *VIDEO_CODEC_ARGS,
            "-crf",
            str(crf),
            "-g",
            str(fps * 2),
            *AUDIO_CODEC_ARGS,
            # +faststart for streaming; +negative_cts_offsets WITH -use_editlist 0
            # is the pair render_ass uses and the one that matters here: with
            # B-frames (x264's default reorder delay of two frames) and no edit
            # list, the muxer otherwise shifts every video pts UP by the delay,
            # so the video started 2 frames late and the audio was front-padded
            # to match — the container came out exactly 2 frames longer than
            # the song at every fps (0.2 s at 10, 0.067 s at 30). Negative CTS
            # offsets let the first frame present at 0 without an elst box.
            "-movflags",
            "+faststart+negative_cts_offsets",
            # -use_editlist 0: keep ffmpeg from writing the elst boxes some
            # platforms (YouTube) trip on.
            "-use_editlist",
            "0",
            "-shortest",
            str(output),
        ]
    )


def render(
    scene: Scene,
    *,
    audio: Path,
    output: Path,
    workdir: Path,
    crf: int = 18,
    scale: int = 1,
) -> RenderResult:
    """Capture the scene's page with Playwright and mux the song.

    Args:
        scene: the compiled scene. Its canvas fixes the size and frame rate.
        audio: the song. Its stream is copied in unmodified.
        output: where the mp4 goes.
        workdir: scratch space owned by this render. The page is left behind as
            an artifact; the frames are deleted.
        crf: libx264 quality (lower is better; 18 is visually lossless-ish).
        scale: device pixel ratio for the capture. The viewport is
            ``canvas / scale`` CSS pixels and the screenshot comes back at the
            canvas size either way, so the output resolution and the (all-``vh``)
            layout are unchanged — what moves is the CSS pixel grid the browser
            lays out and rasterises on. Leave it at ``1`` unless a font is
            rasterising badly at the canvas's own grid.

    Returns:
        A :class:`~muvid.subgenres.RenderResult` whose ``artifacts['page']`` is
        the HTML that produced it — open it in a browser and call ``setTime``.

    Raises:
        WebRenderUnavailable: Playwright or its Chromium build is missing.
        FileNotFoundError: ``audio`` does not exist.
        ValueError: the canvas cannot be captured or encoded as asked.
    """
    audio, output, workdir = Path(audio), Path(output), Path(workdir)
    _check_geometry(scene, scale=scale)
    if not audio.exists():
        raise FileNotFoundError(f"audio not found: {audio}")

    workdir.mkdir(parents=True, exist_ok=True)
    page = workdir / PAGE_NAME
    page.write_text(scene_to_html(scene), encoding="utf-8")

    frames = workdir / FRAMES_DIRNAME
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True)
    try:
        n_frames = _capture(page, frames, scene=scene, scale=scale)
        _encode(
            frames,
            audio,
            output,
            fps=scene.canvas.fps,
            crf=crf,
            duration=scene.duration,
        )
    finally:
        shutil.rmtree(frames, ignore_errors=True)

    # Self-check the way render_ass does, and REPORT rather than raise: the file
    # exists and plays; a failed check is something the caller should see in
    # meta, not a reason to throw away a render.
    from muvid.visualize.verify import verify_video

    checks = verify_video(output, audio=audio)
    verify_failures = [c.name for c in checks if not c.ok]

    return RenderResult(
        output=output,
        duration_s=media_duration(output),
        artifacts={"page": page},
        meta={
            "backend": "web",
            "verify_failures": verify_failures,
            "frames": n_frames,
            "fps": scene.canvas.fps,
            "width": scene.canvas.width,
            "height": scene.canvas.height,
            "scale": scale,
            "crf": crf,
            "n_cues": len(scene.cues),
        },
    )
