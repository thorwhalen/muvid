# muvid.visualize.reactive

Precomputed audio-reactivity: pulse a video filter in time with the music.

Because `muvid` renders from an audio *file*, not a live stream, the whole
loudness envelope is knowable up front. This module turns that envelope into an
ffmpeg `sendcmd` script that rewrites a named `lutyuv`’s lookup table frame
by frame — a beat-reactive “flash” baked deterministically into the render. No
realtime, and no dependency beyond `numpy` and the ffmpeg
[`muvid.visualize`](muvid.visualize.html.md#module-muvid.visualize) already requires.

It is a general seam, not spectrum-specific: any visual can attach a flash to a
named filter in its chain (see [`flash_filter()`](#muvid.visualize.reactive.flash_filter)). The vectorscope reacts to
the music through its own amplitude; the spectrogram uses this.

The whole chain degrades to *nothing* rather than to an error: a track that will
not decode, or an ffmpeg build without [`FLASH_FILTERS`](#muvid.visualize.reactive.FLASH_FILTERS), yields an empty
fragment, so a visual can append it unconditionally.

### Module Attributes

| [`ENVELOPE_SR`](#muvid.visualize.reactive.ENVELOPE_SR)         | Sample rate the envelope is measured at.                                                                                                      |
|----------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| [`FLASH_DECAY`](#muvid.visualize.reactive.FLASH_DECAY)         | Per-frame persistence of a pulse, 0 (no trail) to <1 (longer afterglow), so a beat flashes and fades rather than blinking for a single frame. |
| [`FLASH_BRIGHTNESS`](#muvid.visualize.reactive.FLASH_BRIGHTNESS)    | Peak brightness boost at a full-strength pulse, in `eq`'s units (an additive offset as a fraction of full scale, -1..1).                      |
| [`FLASH_SATURATION`](#muvid.visualize.reactive.FLASH_SATURATION)    | Peak saturation boost at a full-strength pulse, *added to* 1.0.                                                                               |
| [`DEFAULT_FLASH_LABEL`](#muvid.visualize.reactive.DEFAULT_FLASH_LABEL) | Default `sendcmd` label for the pulsing lookup table.                                                                                         |
| [`FLASH_FILTERS`](#muvid.visualize.reactive.FLASH_FILTERS)       | The ffmpeg filters a flash chain is built from.                                                                                               |
| [`FLASH_COMPONENTS`](#muvid.visualize.reactive.FLASH_COMPONENTS)    | one per `lutyuv` component.                                                                                                                   |

### Functions

| [`flash_filter`](#muvid.visualize.reactive.flash_filter)(audio, \*, fps, duration, workdir)   | A filter fragment that makes the stream it follows pulse with the beat.   |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`onset_envelope`](#muvid.visualize.reactive.onset_envelope)(audio, \*, fps[, duration, ...])   | Per-video-frame onset strength in `[0, 1]`, with phosphor-style decay.    |

### muvid.visualize.reactive.DEFAULT_FLASH_LABEL *= 'flash'*

Default `sendcmd` label for the pulsing lookup table. Distinct per flash, so
one filtergraph can carry several without their commands crossing.

The label is NOT the `sendcmd` target, and reading it as one is how muvid#72
came to report the flash as inert. `avfilter_graph_send_command` matches three
things and a bare label is none of them, so the target is the *instance* name —
the whole `lutyuv@flash` spelling, which [`flash_filter()`](#muvid.visualize.reactive.flash_filter) derives from the
filter it emits rather than composing a second time. Measured on ffmpeg 9.0.1 and
6.1.6, driving a grey source with one full-strength pulse on frame 5 in a graph
that also carries `background_chain`’s own unlabelled `lutyuv` — and in BOTH
graph orders, because the type name’s answer depends on the order:

> target          plate first                  flash first (muvid’s own order)
> : plate      flash             plate      flash

> [lutyuv@flash](mailto:lutyuv@flash)    unmoved    126 -> 189        unmoved    126 -> 189  <- shipped
> lutyuv          33 -> 67   unmoved           unmoved    126 -> 189
> flash           unmoved    unmoved           unmoved    unmoved
> all             ffmpeg SIGSEGVs on both builds

`sendcmd` dispatches with `AVFILTER_CMD_FLAG_ONE`, so the type name reaches
only the FIRST `lutyuv` the parser created and stops. Which one that is comes
down to where the chains sit in the composed string, and in muvid’s real graph
the flash is FIRST — `_reactive_plan` emits `[aviz]…lutyuv@flash…[_viz]`
before `background_chain` (measured on the composed spectrum graph: char 279
against char 493). So the type name would happen to work today, silently, and
break on any reordering; muvid#72’s prediction that it would flash the plate
instead is what happens in the other order. The instance name is the address
that does not depend on the accident — measured identical in both orders above.
`all` hands `y <lut expression>` to every commandable filter in the graph,
including the `crop` in `background_chain`, whose `y` is a geometry
expression; it fails to parse and the process dies (exit 139, both builds).

### muvid.visualize.reactive.ENVELOPE_SR *= 22050*

Sample rate the envelope is measured at. Low is fine — we only need a
per-frame loudness curve, not audio quality.

### muvid.visualize.reactive.FLASH_BRIGHTNESS *= 0.25*

Peak brightness boost at a full-strength pulse, in `eq`’s units (an additive
offset as a fraction of full scale, -1..1). At rest the filter is a no-op; this
is how far a beat pushes it.

### muvid.visualize.reactive.FLASH_COMPONENTS *= ('y', 'u', 'v')*

one per `lutyuv` component.
`eq` took two (`brightness`, `saturation`); a LUT is addressed per plane,
so chroma costs two commands carrying the same expression.

* **Type:**
  The `sendcmd` commands one flash frame sends

### muvid.visualize.reactive.FLASH_DECAY *= 0.5*

Per-frame persistence of a pulse, 0 (no trail) to <1 (longer afterglow), so a
beat flashes and fades rather than blinking for a single frame.

### muvid.visualize.reactive.FLASH_FILTERS *= ('sendcmd', 'lutyuv')*

The ffmpeg filters a flash chain is built from. Both are core filters, but a
stripped build can omit either — and the flash is a garnish, so a build that
cannot do it should render the visual *without* the flash rather than fail.

This tuple is the *probe*, so it has to name the filters the chain really
contains. It said `eq` while the point of muvid#69 was to stop needing `eq`
(GPL-only): left stale, an LGPL build — the very build this change exists to
serve — would have failed the probe and silently rendered with no flash at all.

### muvid.visualize.reactive.FLASH_SATURATION *= 0.8*

Peak saturation boost at a full-strength pulse, *added to* 1.0.

### muvid.visualize.reactive.flash_filter(audio, , fps, duration, workdir, label='flash', brightness=0.25, saturation=0.8, decay=0.5)

A filter fragment that makes the stream it follows pulse with the beat.

Computes the envelope, writes the `sendcmd` script into `workdir`, and
returns the chain `,sendcmd=f=…,lutyuv@<label>=…` to append after the visual
filter (e.g. `showspectrum`).

Returns `""` — a fragment that changes nothing — when the audio yields no
envelope or this ffmpeg build lacks [`FLASH_FILTERS`](#muvid.visualize.reactive.FLASH_FILTERS), so a caller can
append it unconditionally and still render.

The `lutyuv` starts as an identity table (brightness 0, saturation 1); the
script drives it. It is a LUT rather than an `eq` because `eq` exists only
in a GPL-configured ffmpeg (muvid#69) — see
[`brightness_saturation_lut()`](muvid.visualize.canvas.html.md#muvid.visualize.canvas.brightness_saturation_lut).

* **Parameters:**
  * **audio** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The track whose beats drive the flash.
  * **fps** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – The render’s frame rate (one command per component per frame).
  * **duration** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Clamp the flash to this many seconds (`None` = whole track).
  * **workdir** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Directory to write the `sendcmd` script into.
  * **label** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – `sendcmd` label for this flash’s `lutyuv`.
  * **brightness** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Peak brightness boost on a beat.
  * **saturation** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Peak saturation boost on a beat.
  * **decay** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Per-frame afterglow of a pulse.
* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.visualize.reactive.onset_envelope(audio, , fps, duration=None, sr=22050, decay=0.5)

Per-video-frame onset strength in `[0, 1]`, with phosphor-style decay.

Decodes `audio` to mono, measures frame-wise loudness, takes the
half-wave-rectified *rise* in loudness (an onset/transient measure, so
sustained loud passages don’t stay lit — only attacks do), scales it
robustly to `[0, 1]`, then lets each pulse fade by `decay` per frame so a
beat flashes and trails off rather than blinking for a single frame.

* **Parameters:**
  * **audio** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The track to analyse.
  * **fps** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Video frame rate — one envelope value per frame.
  * **duration** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Clamp the envelope to this many seconds (defaults to the whole
    track).
  * **sr** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Analysis sample rate.
  * **decay** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Per-frame persistence of a pulse, 0 (no trail) to <1 (longer
    afterglow).
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]
* **Returns:**
  One value per frame. Empty if the audio could not be decoded.
