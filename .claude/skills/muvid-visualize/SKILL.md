---
name: muvid-visualize
description: >
  Turn a song (+ optional cover) into a video with muvid.visualize — the
  lightweight, ffmpeg-only half of muvid: a still cover, a Ken Burns pan, or an
  audio-reactive visualizer (cqt/spectrum/waves/bars/scope) on a 16:9 canvas,
  loudness-normalized, plus a matching thumbnail. Use when the user wants to
  render an audio-visualizer or cover video from audio (no AI, no upload), pick
  or tune a visual, add a custom visualizer, or verify a rendered video. For
  *publishing* the result to YouTube, see `yb`'s `music2video` skill (it ships at
  `yb/data/skills/music2video/`, so it may not appear in an enabled-skill list).
---

# muvid-visualize

`muvid.visualize` renders audio + a cover into a publishable music video. It is
deterministic and ffmpeg-only — no AI, no network — and independent of muvid's
narrative pipeline. Needs **ffmpeg** on the PATH; `pip install muvid` (core).

```python
from muvid.visualize import render_audio_video, list_visuals, verify_video, report

r = render_audio_video("song.wav", image="cover.png")  # still cover, 1080p, -14 LUFS
render_audio_video("song.wav", image="cover.png", visual="cqt", normalize=True)
render_audio_video("song.wav", visual="cqt")  # no image → reactive only

print(report(verify_video(r.path, audio="song.wav")))  # check before shipping
```

## Visuals

`visual=` takes a name, `"auto"` (still if there's an image, else `cqt`), or your
own callable. ×realtime figures are for a 2-core box — divide by your cores.

| `visual` | Look | Cost |
|---|---|---|
| `still` *(default w/ image)* | cover held on a 16:9 canvas | **0.4×** (loop-copy fast path) |
| `cqt` *(default w/o image)* | constant-Q bars, pitch-aligned — most musical | 2.5× |
| `bars` | classic EQ bars | 2.1× |
| `spectrum` | scrolling spectrogram, **beat-flashing** | 2.2× |
| `waves` | waveform | 2.5× (biggest files) |
| `scope` | stereo Lissajous, dynamics-driven | 1.7× |
| `ken_burns` | slow pan/zoom (Python/Pillow) | **6.5×** (slowest) |

Reactive visuals composite on a muted dark background with a consistent **teal**
accent, the sharp cover centred on top. Tune via `options=`:
`cover_fraction`, `cover_alpha`, `tint` (a `colorchannelmixer=...` recolouring
white→accent; `""` keeps the visualizer's own colour), `bg_dim`, `bg_saturation`,
`blurred_background`, plus per-visual knobs (`zoom`/`scale`/`mirror` for scope,
`gain`/`overlap`/`color`/`flash`/`flash_brightness`/`flash_saturation` for
spectrum, `colors`/`mode` for waves/bars).

**`bg_dim` is a SCALING, and the numbers moved because of it** (muvid#70). It used
to subtract `dim * 255` from the plate's luma, which does not dim a picture — it
clamps everything under the offset to black, and at the reactive defaults that
deleted 57–99% of the plate. It now multiplies luma by `1 - dim` about **the
plate's own black**, so a shadow gets darker instead of disappearing. The two forms
are not comparable at the same number: the shipped values grew from 0.25/0.5/0.55 to
**0.65** (`CoverLayout.dim`, the still cover), **0.945** (`REACTIVE_BG_DIM`) and
**0.965** (`SCOPE_BG_DIM`) to land the plate at the same mean brightness. A value
carried over from an older recipe will come out far too bright.

"The plate's own black" is `lutyuv`'s `minval`, and it is **not a constant** — the
cover's encoding decides it. Measured on ffmpeg 9.0.1 and 6.1.6, inside
`background_chain` itself: a PNG cover runs the plate at `yuv444p` and `minval` is
16; a JPEG cover runs it at `yuvj420p` (full range) and `minval` is 0. Writing 16
there hazes every JPEG cover's blacks up to ~15/255 — which is why `_DIM_PIVOT`
derives the value rather than spelling it, and why the plate test corpus carries one
source of each range.

## Beat-reactivity — the flash seam

**`spectrum` pulses on every onset, and it does so BY DEFAULT.** Nothing else in
this repo's docs said so, which is why an agent reading them could neither
discover the effect nor turn it off (muvid#5).

Because muvid renders from an audio *file* rather than a live stream, the whole
loudness envelope is known before the first frame is drawn.
`muvid.visualize.onset_envelope()` turns it into a per-frame transient curve with
phosphor-style decay, and `flash_filter()` bakes that curve into an ffmpeg
`sendcmd` script that rewrites a `lutyuv`'s lookup table every frame — brightness
as an additive luma offset, saturation as a scaling of chroma about neutral. Pure
numpy + ffmpeg — no realtime path, no extra dependency.

| knob | default | effect |
|---|---|---|
| `flash` | `True` | `options={"flash": False}` renders `spectrum` without it |
| `flash_brightness` | `0.25` | peak brightness boost at a full-strength pulse, as a fraction of full scale |
| `flash_saturation` | `0.8` | peak saturation boost, added to 1.0 |

**There is no `flash_decay` option.** `FLASH_DECAY = 0.5` is `flash_filter`'s own
keyword default and `spectrum_visual` never forwards an option to it, so a call
passing one is silently ignored. Change it by calling `flash_filter` yourself.

**It is a general seam, not a spectrum feature.** Any visual can append
`flash_filter(ctx.audio, fps=ctx.fps, duration=ctx.duration, workdir=ctx.workdir)`
to its own chain — both it and `onset_envelope` are exported from
`muvid.visualize`. Note the hook is `flash_filter` itself, not `_reactive_plan`,
which has none.

**It degrades rather than failing.** `flash_filter` returns `""` — a fragment that
changes nothing — when the ffmpeg build lacks one of `FLASH_FILTERS` (`sendcmd`,
`lutyuv`) or the envelope comes back empty. The render loses its flash; it never
errors. So "no flash in the output" has two causes, and the build is the one to
check first — read `FLASH_FILTERS` rather than a filter name written in prose,
which is how that probe went stale once already (muvid#69).

**It needs no GPL ffmpeg.** Both the flash and the darkened background used to run
on `eq`, which ffmpeg compiles only under `--enable-gpl`. They are `lutyuv` now —
same arithmetic, LGPL filter (muvid#69).

## Baked-in defaults (change only with reason)

- **16:9, never pillarboxed** — square/portrait art onto 1080p filled with a
  blurred, darkened copy of itself; the same composition is the thumbnail.
- **−14 LUFS**, two-pass EBU R128 (`normalize=True`).
- **Video exactly as long as the song**; H.264 High / yuv420p / closed GOP /
  2 B-frames, AAC-LC 48 kHz, `+faststart`, **no edit lists** — YouTube's spec.

## ffmpeg gotchas (all handled here; watch for them if you hand-roll)

- **`-shortest` does not bound an infinitely looping stream** — it runs forever
  and fills the disk. Use a finite `-stream_loop N` **and** a hard `-t`.
- **A screen `blend` must run in alpha-free RGB (`gbrp`)** — in YUV, or in RGBA
  with an alpha channel, colours are silently mangled to magenta. `colorchannel-
  mixer` is also a no-op on YUV: `format=rgba` before it, `format=gbrp` before the
  blend.
- **`-fflags +shortest` truncates ~59 ms off the end** of the song. Don't.
- **`loudnorm` resamples to 192 kHz** unless `-ar 48000` is pinned; bare
  `loudnorm` targets −24 LUFS (broadcast), not YouTube's −14; single-pass
  compresses the master — use two-pass (`measured_*`, `linear=true`).
- **`yuv420p` can't encode odd dimensions** (album art is often 1401×1401) —
  `muvid` raises a clear error.
- **ffmpeg writes an `elst` edit list by default**; `+faststart` doesn't remove
  it — use `-use_editlist 0 -movflags +faststart+negative_cts_offsets`.
- **Pin a reactive filter's fps** (`showcqt=...:r=24` + an `fps` filter) or you
  get rates like 30.62 fps.

## Verify

```python
from muvid.visualize import verify_video, report, failures

checks = verify_video(
    "song.mp4", audio="song.wav", thumbnail="song.thumb.jpg", check_loudness=True
)
assert not failures(checks)
```
Covers codec/pix_fmt/16:9/≥720p/48 kHz/duration-match/edit-lists/loudness/thumbnail.

## Extending: your own visual

```python
from muvid.visualize import register_visual, VisualPlan


@register_visual("pulse")
def pulse(ctx):  # ctx: VisualContext (audio is input 0)
    w, h = ctx.size
    return VisualPlan(
        filters=[f"[aviz]showvolume=w={w}:h={h}[vbg]"], video="vbg", uses_audio=True
    )
```
Escape hatch: **return a path** to a silent video you rendered yourself and the
renderer muxes the audio in — that's how a numpy/Pillow, moderngl/EGL, or
projectM backend plugs in without expressing an ffmpeg filtergraph.
