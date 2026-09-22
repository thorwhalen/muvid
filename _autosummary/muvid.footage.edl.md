# muvid.footage.edl

EDL data types + the `validate_edl` single-source-of-truth gate.

An **EDL** (edit decision list) says which clip covers which span of the SONG timeline:
an ordered list of [`EdlEntry`](#muvid.footage.edl.EdlEntry) `{song_start, song_end, clip_id}`, where an empty/
null `clip_id` is an explicit **gap** (no footage — rendered as fill). A strategy or a
caller produces one; [`fill_gaps()`](#muvid.footage.edl.fill_gaps) pads it to the full song (head/tail/interior holes
become gap entries); [`validate_edl()`](#muvid.footage.edl.validate_edl) is the ONE gate every path (explicit and
auto/strategy) passes before any cutting, and [`derive_cuts()`](#muvid.footage.edl.derive_cuts) centralizes the sign
convention (`clip_in = song_start - offset_s`) so no third-party strategy can desync the
result. Times are seconds (float).

The gate is a TRUST gate as well as a structural one: an alignment the aligner could
not vouch for (`FootageAlignment.reliable` False) is refused here with
[`UnreliableAlignmentError`](#muvid.footage.edl.UnreliableAlignmentError) rather than quietly cut to, because a wrong offset
does not fail — it renders a video out of sync with the song (muvid#59).

That refusal is right when the CALLER named the clip, and used to be blunt on the auto
path, where one badly-aligned clip out of five failed an edit the other four could have
covered (muvid#88). The auto path now recovers in two steps that leave the single gate
alone: a strategy treats `reliable` as a **preference** (it ranks a vouched clip above
an unvouched one for the same span, and falls back to unvouched only where nothing else
covers it), and [`exclude_unvouched()`](#muvid.footage.edl.exclude_unvouched) sets those forced spans aside as reported
[`ExcludedSpan`](#muvid.footage.edl.ExcludedSpan) records, gap-filled like any other hole. What reaches the gate is
therefore a smaller edit; the gate still refuses, with the same error, when the recovery
would leave no footage at all.

An entry may carry an optional [`Transition`](#muvid.footage.edl.Transition) — a blend IN from its predecessor
instead of a hard cut (muvid#34). It is an annotation on a boundary that already exists
implicitly, so spans stay one-per-song-span and nothing about reading an EDL changes.

### Module Attributes

| [`MAX_EDL_ENTRIES`](#muvid.footage.edl.MAX_EDL_ENTRIES)     | Cap on EDL entries (env-tunable), on BOTH the auto and the explicit-`edl` paths.                                                                                                                                                                                |
|----------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`MAX_LOOK_SCALE`](#muvid.footage.edl.MAX_LOOK_SCALE)      | How many times the delivery canvas a `look` may ask for, PER DIMENSION (env-tunable).                                                                                                                                                                           |
| [`MAX_LOOK_LINKS`](#muvid.footage.edl.MAX_LOOK_LINKS)      | How many filter links one look may carry, and how many of those may set a size.                                                                                                                                                                                 |
| [`DFLT_LOOK_CANVAS`](#muvid.footage.edl.DFLT_LOOK_CANVAS)    | The canvas [`validate_edl()`](#muvid.footage.edl.validate_edl) bounds a look against when its caller names none: the element-wise maximum of `workspace.CANVASES`, so the default is the LOOSEST bound that is still a bound — never an absent one. |
| [`TRANSITION_SPLIT`](#muvid.footage.edl.TRANSITION_SPLIT)    | the fraction taken from BEFORE the boundary.                                                                                                                                                                                                                    |
| [`MIN_TRANSITION_S`](#muvid.footage.edl.MIN_TRANSITION_S)    | Shortest transition that is not a lie.                                                                                                                                                                                                                          |
| [`TRANSITION_CURVES`](#muvid.footage.edl.TRANSITION_CURVES)   | The transition curves muvid offers.                                                                                                                                                                                                                             |
| [`MIN_CONFIDENCE`](#muvid.footage.edl.MIN_CONFIDENCE)      | Below this, a whole-clip correlation coefficient does not vouch for its offset (env `MUVID_FOOTAGE_MIN_CONFIDENCE`).                                                                                                                                            |
| [`MIN_SUPPORT`](#muvid.footage.edl.MIN_SUPPORT)         | Support must EXCEED this for an offset to be vouched for (env `MUVID_FOOTAGE_MIN_SUPPORT`).                                                                                                                                                                     |
| [`MIN_MARGIN`](#muvid.footage.edl.MIN_MARGIN)          | Margin must EXCEED this for an offset to be vouched for (env `MUVID_FOOTAGE_MIN_MARGIN`).                                                                                                                                                                       |
| [`NO_VOUCHED_COVERAGE`](#muvid.footage.edl.NO_VOUCHED_COVERAGE) | nothing the aligner vouches for covers it at all.                                                                                                                                                                                                               |
| [`UNVOUCHED_SELECTION`](#muvid.footage.edl.UNVOUCHED_SELECTION) | A vouched clip DOES cover the span and the strategy cut to an unvouched one anyway — so the loss is the SELECTOR's, not the footage's.                                                                                                                          |
| [`EXCLUSION_REASONS`](#muvid.footage.edl.EXCLUSION_REASONS)   | Every reason [`exclude_unvouched()`](#muvid.footage.edl.exclude_unvouched) can give, for a caller matching on the value.                                                                                                                                 |
| [`LOOK_FILTERS`](#muvid.footage.edl.LOOK_FILTERS)        | The filters a `look` may name.                                                                                                                                                                                                                                  |

### Functions

| [`derive_cuts`](#muvid.footage.edl.derive_cuts)(edl, alignments, clip_paths)         | Turn a *validated* EDL into render-ready cuts — the ONE place `clip_in` is derived.   |
|---------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| [`exclude_unvouched`](#muvid.footage.edl.exclude_unvouched)(edl, alignments)               | Set aside the spans of an AUTO edit whose only footage is unvouched (muvid#88).       |
| [`fill_gaps`](#muvid.footage.edl.fill_gaps)(entries, song_duration)                | Make an edit span the WHOLE song by inserting explicit gap entries.                   |
| [`validate_edl`](#muvid.footage.edl.validate_edl)(edl, alignments, song_duration, \*) | Validate an EDL (from a strategy OR a caller) — the ONE gate before any cutting.      |
| [`vouches_for`](#muvid.footage.edl.vouches_for)(\*, confidence, support[, ...])      | Does the aligner vouch for this offset? The ONE place that verdict is reached.        |

### Classes

| [`AssemblyCut`](#muvid.footage.edl.AssemblyCut)(song_start, song_end, clip_id, ...)   | A validated cut ready to render: the EDL span + the derived in-point + clip path.       |
|----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| [`CropWindow`](#muvid.footage.edl.CropWindow)(x, y, w, h)                            | A rectangle to take from the source frame, as fractions of its width/height.            |
| [`EdlEntry`](#muvid.footage.edl.EdlEntry)(song_start, song_end, clip_id[, ...])    | One cut: show `clip_id` over the song span `[song_start, song_end]`.                    |
| [`ExcludedSpan`](#muvid.footage.edl.ExcludedSpan)(clip_id, song_start, song_end)       | One span an AUTO-selected edit gave up rather than cut to unvouched footage (muvid#88). |
| [`FootageAlignment`](#muvid.footage.edl.FootageAlignment)(clip_id, offset_s, ...[, ...])   | Where one uploaded clip sits on the song timeline (muvid's per-clip record).            |
| [`Transition`](#muvid.footage.edl.Transition)(duration_s[, curve])                   | How this entry blends IN from its predecessor.                                          |

### Exceptions

| [`UnreliableAlignmentError`](#muvid.footage.edl.UnreliableAlignmentError)(unvouched)   | An edit cuts to a clip whose OFFSET the aligner could not vouch for (muvid#59).   |
|----------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------|

### *class* muvid.footage.edl.AssemblyCut(song_start, song_end, clip_id, clip_in, clip_path, transition=None, crop=None, crop_end=None, look=None, look_time_varying=False)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A validated cut ready to render: the EDL span + the derived in-point + clip path.

#### crop *: [CropWindow](#muvid.footage.edl.CropWindow) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Carried through from the EDL entry, unchanged — the assembler compiles these
to a `crop` filter, because normalised fractions only become pixels once
you know the source dimensions, which only ffmpeg knows.

#### look *: [str](https://docs.python.org/3/builtins/stdtypes.html#str) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Carried through from the EDL entry, unchanged and already validated — the
`looks` seam. The assembler splices it into the ONE filter template both
of its render sites share, so a look lands identically on a solo cut and on
each side of a blended boundary. See [`EdlEntry.look`](#muvid.footage.edl.EdlEntry.look).

#### look_time_varying *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= False*

only it
knows which boundaries become a separate two-input invocation, which is
where a moving look’s ramp restarts (muvid#73). See
[`EdlEntry.look_time_varying`](#muvid.footage.edl.EdlEntry.look_time_varying) for the measurement.

* **Type:**
  Carried through unchanged, and the assembler is its ONE consumer

#### transition *: [Transition](#muvid.footage.edl.Transition) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Carried through from the EDL entry, unchanged. `derive_cuts` gains no
transition arithmetic: the extra source material a blend needs is measured
in FRAMES at the render fps, which only the assembler knows.

### *class* muvid.footage.edl.CropWindow(x, y, w, h)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A rectangle to take from the source frame, as fractions of its width/height.

Normalised rather than pixels so one window is valid for every clip in a
multi-device edit regardless of its resolution, and so an EDL survives a source
being re-encoded at a different size. `(0, 0, 1, 1)` is the whole frame.

The convention is `burns.Rect`’s, deliberately — top-left origin, window
fraction — so a crop authored here and a Ken Burns path computed there
interoperate with no rename table.

This is the spatial half the EDL lacked: without it every source is letterboxed
onto the canvas, so a portrait clip in a landscape edit is ~68% black bars and a
caller has no way to say which two-thirds of the frame to keep. That choice is
editorial — on a real 478x850 clip of dancers a whole body does not fit in a
full-width 16:9 window at all (315-380px of subject into 269px), so “heads or
feet” is a decision per cut, not a default.

### muvid.footage.edl.DFLT_LOOK_CANVAS *= (1920, 1920)*

The canvas [`validate_edl()`](#muvid.footage.edl.validate_edl) bounds a look against when its caller names
none: the element-wise maximum of `workspace.CANVASES`, so the default is
the LOOSEST bound that is still a bound — never an absent one.

A `None` default would have been the smaller diff and the wrong shape: it
makes “nobody threaded the canvas through” indistinguishable from “this look
is fine”, which is the silent no-op this module refuses everywhere else. The
default only covers a direct caller of [`validate_edl()`](#muvid.footage.edl.validate_edl).

\*\*Every muvid path that can carry a caller’s look passes the real canvas, and
that is asserted by an AST scan of the call sites\*\* rather than by behaviour.
The distinction is load-bearing and an earlier version of this comment glossed
it: three of the five sites validate machine-generated entries from
`select_edl`, which has no `look`, so deleting `canvas=` from any of
them left the whole suite green — the claim was true of one site and prose
about the rest. The scan holds all five (it found the fifth, in
`select_score`), and the one site that deliberately passes no canvas is
recorded with its reason plus a test of the premise that reason rests on. See
`tests/test_edl_look_size_bound.py`. Pinned against
`workspace.CANVASES` by a test rather than imported from it, because
`muvid.footage.edl` is on the import-safe path and `workspace` is not on
it for free.

### muvid.footage.edl.EXCLUSION_REASONS *= ('no_vouched_coverage', 'unvouched_selection')*

Every reason [`exclude_unvouched()`](#muvid.footage.edl.exclude_unvouched) can give, for a caller matching on the value.

### *class* muvid.footage.edl.EdlEntry(song_start, song_end, clip_id, transition=None, crop=None, crop_end=None, look=None, look_time_varying=False)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One cut: show `clip_id` over the song span `[song_start, song_end]`.

`clip_id == ""` is a **gap entry** — no footage covers this span, and the renderer
fills it (black in v1). Gaps are explicit entries rather than absences so that an EDL
is always contiguous over its span, every span of the song is accounted for by
exactly one entry, and “no clip here” survives a JSON round trip (`clip_id: null`).

#### crop *: [CropWindow](#muvid.footage.edl.CropWindow) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Take only this rectangle of the source frame. `None` keeps the whole frame
letterboxed onto the canvas, which is what every EDL written before this
field existed means — additive in both directions, like `transition`.

#### crop_end *: [CropWindow](#muvid.footage.edl.CropWindow) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

With `crop`, makes the window MOVE linearly from `crop` to `crop_end`
across the cut — a pan. Same size as `crop` (see [`validate_edl()`](#muvid.footage.edl.validate_edl)): a
window that changes size mid-cut resizes the filter’s output every frame,
which is a different and much less robust thing than a pan. A push-in is
expressed as a *different* fixed window on the *next* cut, or — since the
`looks` seam below — as a `look` carrying a `zoompan` ramp, which is
the one filter that CAN resize its window mid-cut (muvid#66).

#### look *: [str](https://docs.python.org/3/builtins/stdtypes.html#str) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

\*\*The `looks` seam.\*\* A compiled ffmpeg filter-chain fragment applied to
this cut’s picture once it has been normalised onto the canvas. `None`
(the default) emits nothing at all, so an EDL written before this field
existed renders byte-identically — additive in both directions, like
`transition` and `crop`.

muvid does not author this string: [`muvid.footage.look`](muvid.footage.look.md#module-muvid.footage.look) compiles it
from a `looks.Look` or from a punch-in request. That split is the
whole point of the seam — `looks` decides what a pixel becomes, muvid
keeps `-c:v` and the process shape.

**A caller may still hand one over, so this field is a trust boundary.**
`assemble_music_video` is a live per-caller MCP tool taking free-form
`edl` dicts, and this string becomes ffmpeg the renderer runs. So
`_validate_look()` gates it against the ALLOWLIST
[`LOOK_FILTERS`](#muvid.footage.edl.LOOK_FILTERS) — not against a list of refusals — and also refuses a
fragment that names a container input, that is more than ONE linear chain,
that is not lexically closed, that sets an option muvid has not classified
on one of the four filters that can change the output geometry, or that
asks for a frame more than [`MAX_LOOK_SCALE`](#muvid.footage.edl.MAX_LOOK_SCALE) times the delivery
canvas. The first two of those would break the bounded-memory invariant
the assembler rests on; the allowlist is what keeps a look from writing
this machine’s disk; and the last two are what keep an allowlisted filter
from spending 900 MB of it (muvid#75).

#### look_time_varying *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= False*

Whether [`look`](#muvid.footage.edl.EdlEntry.look) READS THE FILTER CLOCK — a punch-in, a pan, anything
whose expressions mention `t` / `in_time` / `n`. `False` (the
default) means a grade, a LUT, a posterise: a look that draws every frame
the same way and is therefore unaffected by where the clock starts.

**It exists because the string throws that answer away** (muvid#73). A
transitioned boundary renders as a separate two-input invocation whose
inputs are input-side-seeked to the blend window, and input-side `-ss`
rebases the filter timeline to 0 — so a moving look **restarts its ramp**
for the length of the blend. Measured on a 3.0 s cut at 25 fps with a
0.4 s fade and `punch_in(zoom=1.12)`: the solo part’s last frame is
drawn at zoom 1.109 (mean 

```
|diff|
```

 28.1/255 against the same frame rendered
with no look), the blend part’s first frame at zoom 1.000 (0.7/255 —
indistinguishable from no punch at all). muvid cannot rebase the fragment
without rewriting an arbitrary ffmpeg expression, which is exactly what
`looks` refuses to do for itself (its rule 27), so the assembler WARNS
rather than silently rendering the hitch
(`muvid.footage.assemble._part_plan()`) — the same “never a silent
no-op” posture as the zero-frame-transition warning beside it.

\*\*A caller-supplied look defaults to `False`, so an UNDECLARED moving
look stays silent.\*\* That is the known limit of the chosen shape, not an
oversight: the alternative is muvid deciding by reading the fragment,
which is the same rewriting-an-arbitrary-expression problem one step
earlier. muvid’s own compilers declare it for you —
[`muvid.footage.look.punch_in()`](muvid.footage.look.md#muvid.footage.look.punch_in) and
[`motion()`](muvid.footage.look.md#muvid.footage.look.motion) return a fragment that says `True`,
[`stylize()`](muvid.footage.look.md#muvid.footage.look.stylize) one that answers from the compiled
plan, and [`punch_in_cuts()`](muvid.footage.look.md#muvid.footage.look.punch_in_cuts) sets this field FROM
the fragment rather than hardcoding it.

#### transition *: [Transition](#muvid.footage.edl.Transition) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Blend in from the predecessor rather than hard-cutting. `None` (the
default) is a hard cut, so an EDL written before this field existed is a
valid EDL now, and one written with it, read by older code, renders hard
cuts — degraded, never wrong, in both directions.

### *class* muvid.footage.edl.ExcludedSpan(clip_id, song_start, song_end, reason='no_vouched_coverage', confidence=0.0, support=None, margin=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One span an AUTO-selected edit gave up rather than cut to unvouched footage (muvid#88).

A typed record, not a log line: it is returned to the caller (and over the MCP wire),
because “your video is shorter than the song” is only actionable next to *which* clip
was set aside, *where*, and *on what numbers*. The numbers are the same three
[`vouches_for()`](#muvid.footage.edl.vouches_for) reached its verdict on, so the report and the gate can never
disagree about why.

#### to_dict()

JSON-ready. `support`/`margin` stay `None` — “not measured” is not zero.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### *class* muvid.footage.edl.FootageAlignment(clip_id, offset_s, confidence, duration_s, coverage, overlaps=True, support=None, reliable=True, window_s=None, hop_s=None, margin=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Where one uploaded clip sits on the song timeline (muvid’s per-clip record).

Mirrors `mixing.audio.ClipAlignment` but keyed by the caller-facing `clip_id` and
JSON-round-trippable (persisted in the project manifest).

#### margin *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

How far `support`’s tally for this offset sits ABOVE the tally for the best
offset outside the tolerance — `None` on the same quorum as `support`.

`support` says how much evidence reaches this offset; this says whether
anything disputes it, and they are different questions. A NEGATIVE value means
the clip’s own evidence prefers somewhere else, which is a refusal rather than a
weak endorsement. See [`MIN_MARGIN`](#muvid.footage.edl.MIN_MARGIN) — this is the number muvid#59 was
actually missing, since its near-ties (0.993/0.989/0.987) are invisible to any
fraction that does not look at the runner-up.

#### overlaps *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= True*

Whether the clip intersects the song timeline at all. A clip that does NOT is
still recorded — a source must never leave the addressable set as a side effect
of being measured. Selection filters on this; reporting shows it with a reason.

#### reliable *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= True*

Whether the aligner VOUCHES for `offset_s`. False means “measured, recorded,
and not to be cut to without the caller saying so” — see
[`UnreliableAlignmentError`](#muvid.footage.edl.UnreliableAlignmentError), which [`validate_edl()`](#muvid.footage.edl.validate_edl) raises.

Defaults True for a record built in code; a record read from disk that predates
the field gets its verdict DERIVED instead (see `from_dict()`), never
assumed.

#### support *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Fraction of the clip’s independent analysis windows that agree on `offset_s`.
`None` means no vote was held — since `mixing>=0.0.48` fits the window to the
clip, that is now only a clip shorter than `window_floor + hop` = 4.5 s
(measured: 4.4 s unvoted, 4.5 s the first with a number). It is a different fact from “the windows disagreed”,
which is why it is not 0.0. `window_s` records the grid it was measured on and
is reported as a diagnostic; it does NOT qualify the number — see the note above
[`MIN_SUPPORT`](#muvid.footage.edl.MIN_SUPPORT) for why a window-based qualifier was removed rather than
re-tuned.

\*\*This, not `confidence`, is the honest number\*\* (muvid#59). A correlation
coefficient says how well the winning lag scored; it cannot say whether the
runner-up scored the same, which is the entire failure mode on repetitive
music. A support fraction says how much of the clip actually agrees — on the
shoot that produced this issue the three correct offsets carried 10/24, 17/37
and 45/61, while the confidently-wrong ones the old estimator returned had no
agreement to speak of at all.

#### window_s *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

The analysis grid `support` was measured on — `None` in exactly the cases
`support` is. Recorded because since `mixing>=0.0.48` the estimator fits the
window to the clip, so the SCALE of `support` is a per-clip quantity: without
these, comparing two clips’ support means comparing numbers that answer different
questions. [`vouches_for()`](#muvid.footage.edl.vouches_for) reads `window_s`; `hop_s` is carried because
the pair is what makes a support fraction reproducible (which windows were
eligible to agree depends on the hop), and a record that cannot reproduce its own
measurement is a record you have to take on faith.

### muvid.footage.edl.LOOK_FILTERS *= frozenset({'bilateral', 'boxblur', 'colorchannelmixer', 'colorlevels', 'crop', 'eq', 'gblur', 'hue', 'lut3d', 'lutrgb', 'lutyuv', 'null', 'pad', 'scale', 'setpts', 'unsharp', 'zoompan'})*

The filters a `look` may name. \*\*An allowlist, because a look is executable
ffmpeg arriving from a remote caller.\*\*

`assemble_music_video` is a live MCP tool on the per-caller reelee AV
connector and its `edl` argument is free-form dicts, so this string is
attacker-supplied input to a process that can write the host’s filesystem.
Measured, on this branch before the allowlist existed: a look of
`metadata=mode=print:file=<any path the renderer can write>` passed the gate,
rendered normally, returned a success payload, and truncated the named file to
zero bytes. `deshake=filename=` is a second, structurally different write
primitive; `movie=`/`amovie=` open an unaccounted container; `sendcmd`,
`signature`, `ssim` and `psnr` each name a file of their own.

A blocklist cannot close that — there are ~481 filters and the dangerous ones
have nothing lexical in common. So the rule is the one this module already uses
for [`TRANSITION_CURVES`](#muvid.footage.edl.TRANSITION_CURVES) and that `an`’s camera table uses for moves:
\*\*a curated vocabulary we own, refused at the gate rather than discovered as an
ffmpeg side effect three stages later.\*\*

Two groups, and the split is the maintenance rule:

- **Compiled** — every filter the two compilers on this seam can emit:
  [`muvid.footage.look`](muvid.footage.look.md#module-muvid.footage.look) (`zoompan`/`crop`/`scale`/`setpts`, via
  > `looks.compile_motion`) and `looks`’ registered ffmpeg implementations
  > (their declared `ImplRef.requires_filters`). This set is \*pinned against
  > `looks` by a test\*, deliberately rather than derived from it at import
  > time: deriving would let a new `looks` effect widen muvid’s remote-input
  > surface silently, where the test makes it a decision someone records here.
- **Hand-authored** — `hue`, the one filter this repo’s own docstrings reach
  for and nothing compiles. It is LGPL, takes no path, and is what a person
  writes when they want “desaturate that shot”.

**What earns a place here**, and it is checked by
`tests/test_edl_look.py::test_no_allowlisted_filter_can_name_a_file`: the
filter must declare no filesystem-path option at all. `lut3d`’s `file` is
the single recorded exception (`_LOOK_FILE_OPTIONS`) — it *loads* a
`.cube`, which is how `looks`’ flagship grade reaches its LUT, and it reads
rather than writes. Nothing else may name a path, so adding a filter here is a
two-place edit and the second place is a measurement of the real binary.

**The frame size an allowlisted filter may ask for is bounded separately**
(muvid#75) — see `_LOOK_GEOMETRY_FILTERS` and [`MAX_LOOK_SCALE`](#muvid.footage.edl.MAX_LOOK_SCALE).
The allowlist is a vocabulary; it says nothing about the PARAMETERS a member
is given, and one of those parameters is memory. Nor is a size bound a bound
on the OPTIONS that set a size: `pad`’s `aspect` and `scale`’s
`force_original_aspect_ratio` both move the frame while declaring no
dimension a bound can read, so the four filters that can change the output
geometry are allowlisted per OPTION as well as by name.

**What this still does NOT bound**, stated because a partial claim is worse
than none: `lut3d=file=` will *attempt* to open any path the renderer can
read. It cannot write, and a non-`.cube` file fails to parse. That is a
failure inside the caller’s OWN render; writing to someone else’s disk is the
class this closes.

### muvid.footage.edl.MAX_EDL_ENTRIES *= 500*

Cap on EDL entries (env-tunable), on BOTH the auto and the explicit-`edl` paths. The
assembler runs one bounded ffmpeg per PART (memory O(1) in cut count), so this caps
total WORK — N encoder invocations on a shared box — not a single command’s inputs.
It counts footage CUTS, and parts are no longer one-per-cut: a transitioned boundary
adds a part, so a fully-transitioned edit approaches `2 * cuts` invocations. The cap
stays on cuts deliberately — it is the number the caller wrote and can act on — but
read it as bounding work only to within that factor.

### muvid.footage.edl.MAX_LOOK_LINKS *= 16*

How many filter links one look may carry, and how many of those may set a
size. **Bounding the frame does not bound the memory** — that is the half
[`MAX_LOOK_SCALE`](#muvid.footage.edl.MAX_LOOK_SCALE) misses, and it misses it by a factor of ten.

Measured, ffmpeg 9.0.1, three frames, `/usr/bin/time -l` peak RSS, every
link sitting exactly AT the size cap (7680x4320 for a 1920x1080 canvas), so
every fragment below is one `_validate_look_size()` accepts:

| look                  | peak RSS   |
|-----------------------|------------|
| `scale=7680:4320` x1  | 171 MB     |
| `scale=7680:4320` x8  | 505 MB     |
| `scale=7680:4320` x32 | 1648 MB    |
| `scale=7680:4320` x64 | 3171 MB    |

~47 MB per additional link, unbounded, on a box that has been OOM-killed at
30 cuts (muvid#21/#24). The size cap refuses `scale=8000:8000` at 241 MB
while accepting thirty-two links of 7680x4320 at 1648 MB — which is the
shape of a bound that measures the wrong quantity.

TWO caps rather than one, because they bound different things and one number
cannot do both: the total bounds the graph, and the resize count bounds how
many LARGE frames can exist in it. Together the measured worst case is
**292 MB** (2 links at the size cap plus 14 cheap ones) against 3171 MB
today. A single total-link cap loose enough for a rich grade would leave the
multiplicative worst case an order of magnitude higher.

The values are generous by construction: muvid’s own compilers emit **1**
link for `punch_in` and **2** for a two-effect `stylize`, and at most one
of those ever sets a size.

### muvid.footage.edl.MAX_LOOK_SCALE *= 4*

How many times the delivery canvas a `look` may ask for, PER DIMENSION
(env-tunable). So the area bound is `MAX_LOOK_SCALE ** 2` — 16x the canvas.

**Four, chosen against measurements rather than taste.** Three of them:

- `2` is the standard supersample and would be the tempting answer, but it
  REFUSES a look muvid itself compiles: `stylize(fill, target="1080x1080")`
  on a 640x360 canvas emits `scale=1920:1080,crop=1080:1080:420:0`, which is
  exactly 3x linear. A bound that refuses the seam it protects is not a bound,
  it is an outage.
- `3` accepts that one *exactly on the boundary*, which is one rounding away
  from the same outage.
- `4` caps the worst case at 4x muvid’s largest canvas — 7680x4320 — which
  measures **184.8 MB** peak RSS, against **327.6 MB** for the
  `scale=8000:8000` this closes and the ~2 GB `scale=20000:20000`
  extrapolates to. (ffmpeg 9.0.1, three frames from a 64x48 source,
  `/usr/bin/time -l`; the same harness reads 18.8 MB for `scale=64:48` and
  32.8 MB for `scale=1920:1080`.)

Bigger than the canvas is never useful — the delivered frame IS the canvas, so
anything past it is resampled straight back down — which is why a *small*
multiple is the whole of the legitimate range.

### muvid.footage.edl.MIN_CONFIDENCE *= 0.1*

Below this, a whole-clip correlation coefficient does not vouch for its offset
(env `MUVID_FOOTAGE_MIN_CONFIDENCE`). Calibrated for the clean-master-vs-phone
regime the connector actually sees, on the onset-envelope feature (muvid#15):
measured against a studio master, four provably-correct clips scored 0.173-0.603
while the one genuinely unrelated clip scored 0.021 — so 0.1 separates them ~2x/5x,
where the old 0.3 (a defensible number for the EASIER clip-to-clip regime) flagged
five of six correct alignments as suspect.

**Read it as the weak instrument it is.** muvid#59 is the demonstration: on a
repetitive track the three wrong offsets scored 0.086, 0.121 and 0.086, so this
threshold catches two of the three and lets the third — 83 s wrong — through. A
coefficient cannot see the runner-up peak that makes it a coin flip; only
[`FootageAlignment.support`](#muvid.footage.edl.FootageAlignment.support) can. This is the fallback for an aligner that
reports no support, not the measure of record.

**0.1 is kept, not re-derived, and that is a deliberate refusal to invent a number.**
The consensus estimator changed what the coefficient IS — a median over the winning
window group rather than one whole-clip argmax’s score — so the muvid#15 calibration
no longer describes it, and 8 s of white noise that used to fall under 0.1 now scores
0.102-0.139 across ten seeds. The obvious response is to raise the threshold past
that floor. **It does not work**, measured on clips of the muvid#59 master under the
30 s vote boundary: worst CORRECT alignment 0.129, worst NOISE 0.139. The two
distributions overlap, so every threshold either admits noise or refuses correct
footage, and one picked to pass a noise-floor test would refuse real clips while
looking green. See [`vouches_for()`](#muvid.footage.edl.vouches_for) for the rest of the evidence and muvid#91 for
the decision this leaves open.

### muvid.footage.edl.MIN_MARGIN *= 0.0*

Margin must EXCEED this for an offset to be vouched for (env
`MUVID_FOOTAGE_MIN_MARGIN`). This is the SEPARATOR, and zero is the meaningful
value rather than a tunable one: `mixing`’s margin is the graded tally at the
chosen offset minus the tally at the best offset outside the tolerance, so
`> 0` means “the clip’s own evidence prefers THIS offset over every other one it
considered”, `== 0` means it is indifferent, and `< 0` means \*\*the evidence
actually prefers somewhere else\*\*.

A negative margin is therefore a refusal in its own right, not merely a failure to
endorse — and it is the cleanest signal measured anywhere in muvid#59: five of six
pure-noise clips came back negative (-0.167, -0.172, -0.176, -0.218, -0.317) and not
one correct alignment did.

**Why this is the quantity that was missing.** muvid#59’s diagnosis was near-tied
peaks — the second-to-first ratios on that shoot measured 0.993, 0.989 and 0.987 —
and no support fraction, however graded, can see a runner-up at all. Three scalars
failed to separate this population before this one: the raw coefficient, the
envelope coefficient, and support.

One boundary worth knowing before tuning it: a rival closer than `mixing`’s
`offset_tolerance_s` is the same hypothesis by construction and never subtracts
(measured upstream: a rival 0.20 s away leaves the margin at 1.000, one 0.30 s away
takes it to 0.505). \*\*Margin separates different offsets; it is not a precision
claim about the one it chose.\*\*

### muvid.footage.edl.MIN_SUPPORT *= 0.5*

Support must EXCEED this for an offset to be vouched for (env
`MUVID_FOOTAGE_MIN_SUPPORT`). Strictly greater, and the value is the meaning:
`mixing` grades a window 1.0 when its own argmax reached the offset and at most
`BALLOT_VOTE_WEIGHT` (0.5) when the offset was merely on its ballot, so \*\*0.5 is
exactly the ceiling of ballot-only evidence\*\* — at 0.5 nothing found the offset
unaided; above it, something did.

\*\*This nearly went to 0.25, and the measurement that stopped it is the one worth
keeping.\*\* On the muvid#59 master — 24 correct alignments against six pure-noise
clips — `margin > 0` alone scored 24/24 and 0/6 where this threshold scores 18/24,
so dropping the floor and letting [`MIN_MARGIN`](#muvid.footage.edl.MIN_MARGIN) separate looked strictly better.
It is not. **Every wrong case in that set was pure NOISE; not one was an ALIAS** — a
bar-multiple repeat of the true offset — which is the case muvid#59 is actually made
of. Measured on a tiled fixture, clips of the same source at a true offset of 28.0 s:

| clip     | offset     | correct?   | support   | margin     |
|----------|------------|------------|-----------|------------|
| 8 s      | 27.988     | yes        | 0.572     | +0.237     |
| **10 s** | **35.974** | **NO**     | **0.487** | **+0.115** |
| 12 s     | 27.990     | yes        | 0.659     | +0.217     |
| **14 s** | **11.956** | **NO**     | 0.451     | **-0.349** |
| 16 s     | 27.986     | yes        | 0.722     | +0.353     |
| 20 s     | 27.990     | yes        | 0.638     | +0.197     |

The 10 s clip lands on a repeat with BOTH numbers positive, so a 0.25 floor vouches
for an offset 7.97 s wrong that this one refuses. The 14 s repeat *is* caught by a
negative margin — margin catches some aliases and not others, which is worse than a
clean signal because it looks like one. Across both data sets:

So the floor is load-bearing on repeating references, which is the material this
whole issue is about, and it costs six correct short clips (support 0.30-0.38) to
keep. That is the safe direction: a refusal is visible and recoverable, a repeat
rendered as if true is the failure muvid#59 exists to prevent.

Thirty real cases and six synthetic ones is encouraging and not proof. Re-run BOTH
tables before moving this — and make sure the set you re-run contains aliases, not
only noise, which is the mistake that nearly lowered it.

### muvid.footage.edl.MIN_TRANSITION_S *= 0.04*

Shortest transition that is not a lie. Below roughly one frame the xfade emits
no blended frames at all and the “transition” is a hard cut wearing a label —
the same class of silent no-op as muvid#44’s `camera: {move: static}`, which
`an` refused precisely so it could not happen quietly. The renderer ALSO warns
if a transition rounds to zero frames at the actual render fps, which this
song-time floor cannot know.

### muvid.footage.edl.NO_VOUCHED_COVERAGE *= 'no_vouched_coverage'*

nothing the aligner vouches for covers it at all. The
honest answer is a gap, and the remedy is to re-align or re-shoot.

* **Type:**
  The span had no alternative

### muvid.footage.edl.TRANSITION_CURVES *= frozenset({'circleclose', 'circleopen', 'dissolve', 'fade', 'fadeblack', 'fadewhite', 'slidedown', 'slideleft', 'slideright', 'slideup', 'smoothleft', 'smoothright', 'wipedown', 'wipeleft', 'wiperight', 'wipeup'})*

The transition curves muvid offers. A curated subset of ffmpeg’s 58 `xfade`
transitions, not all of them: this is a vocabulary we own and must keep working
across ffmpeg builds, and an unrecognised name is refused at
[`validate_edl()`](#muvid.footage.edl.validate_edl) rather than discovered as an ffmpeg error three stages
later. Same posture as the `an` camera-move table — translate at the boundary,
never pass a name through and hope.

### muvid.footage.edl.TRANSITION_SPLIT *= 0.5*

the fraction taken from
BEFORE the boundary. `0.5` is centred — each side supplies `duration/2` of
extra source, and the perceptual midpoint of the blend lands exactly on the
authored boundary.

Not a tuning knob. A trailing transition (`1.0`) would need spare coverage on
only one side and is therefore satisfiable at more boundaries — but it puts the
perceived cut `duration/2` LATE on every transition, which is precisely what
the beat-snapped Viterbi selector in [`muvid.footage.select_score`](muvid.footage.select_score.md#module-muvid.footage.select_score) exists to
prevent. Centring is also the NLE convention (“centered on cut”).

* **Type:**
  Where a transition sits relative to the cut it is on

### *class* muvid.footage.edl.Transition(duration_s, curve='fade')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

How this entry blends IN from its predecessor.

A small frozen record rather than a bare float, so `curve` (and anything
after it) is an additive field rather than another shape change on a wire
record.

The transition belongs to the entry it is *on* — an annotation of that entry’s
**entrance**. That is what keeps the EDL’s defining invariant intact: spans
stay one-per-song-span, so the thing you can read is still the thing that
renders. (The two rejected shapes both break it: silently widening a span
makes the EDL stop meaning what it says, and a synthetic third entry between
two real ones makes authoring one transition a three-entry edit — the class of
mistake muvid#35 was filed about — while doubling the wire record count of a
heavily-cut edit for a purely presentational reason.)

A transition on the FIRST entry is rejected, not ignored: there is no
predecessor to blend from, so it is a request that cannot be honoured, and
honouring nothing quietly is how a direction gets lost.

### muvid.footage.edl.UNVOUCHED_SELECTION *= 'unvouched_selection'*

A vouched clip DOES cover the span and the strategy cut to an unvouched one anyway —
so the loss is the SELECTOR’s, not the footage’s. The two are separate values because
they were measured to be separate events and they have different remedies: this one
says try another strategy or another selection config. `weighted` produces it
structurally — see `_absorb_neighbour()`, which repairs the repairable half.

### *exception* muvid.footage.edl.UnreliableAlignmentError(unvouched)

Bases: [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError)

An edit cuts to a clip whose OFFSET the aligner could not vouch for (muvid#59).

The defect this exists to make impossible: on a rigidly programmed, repetitive
track, a whole-clip cross-correlation surface has near-tied peaks spaced at
musical periods (measured on one real shoot: second-peak-to-first ratios of
0.993, 0.989, 0.987), so the estimator picks the argmax of a coin-flip and
reports the coefficient of whichever side it landed on. That number is *low*
but never looks like failure — and every stage after it (`derive_cuts`, the
score tracks, the assembled mp4) is built on the offset, so the failure
presented as a silently desynced video rather than as an error. Three clips
came back 83 s, 174 s and 83 s wrong, all with `overlaps=True`.

So an offset the aligner will not vouch for is a **refusal**, not a number: a
caller who renders anyway must say so with `allow_unreliable=True`, exactly
as an unpriceable shot must be passed with `--allow-unpriced` rather than
being read as free.

Note what this does NOT yet claim. The verdict is only as good as
[`vouches_for()`](#muvid.footage.edl.vouches_for), which falls back to the confidence coefficient wherever
`support` was never measured — which is every alignment today’s `mixing`
produces. That fallback would have caught two of muvid#59’s three wrong
offsets and missed the third. The refusal is the half that makes a bad
measurement stop being silent; the half that makes the measurement good is
the windowed consensus in thorwhalen/mixing#30.

A refusal is NOT a removal: the clip keeps its record, its coverage and its
place in the project (see [`FootageAlignment`](#muvid.footage.edl.FootageAlignment)). What it loses is the
right to be cut to without the caller saying so.

#### alignments

The offending clips, in EDL order — so a caller can re-align exactly these.

### muvid.footage.edl.derive_cuts(edl, alignments, clip_paths)

Turn a *validated* EDL into render-ready cuts — the ONE place `clip_in` is derived.

Strategies emit only `{song_start, song_end, clip_id}`; the sign convention
`clip_in = song_start - offset` lives here (SSOT), so no strategy can desync the cut.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`AssemblyCut`](#muvid.footage.edl.AssemblyCut)]

### muvid.footage.edl.exclude_unvouched(edl, alignments)

Set aside the spans of an AUTO edit whose only footage is unvouched (muvid#88).

The auto path’s recovery, and deliberately NOT a second gate. A strategy already
prefers a vouched clip wherever one covers the span (see
[`muvid.footage.strategy`](muvid.footage.strategy.md#module-muvid.footage.strategy)), so an unvouched entry surviving into its output means
exactly one thing: **nothing else covered that span**. This drops those entries, which
[`fill_gaps()`](#muvid.footage.edl.fill_gaps) then turns into explicit gap entries — the encoding muvid already
uses for “no footage here” — and returns them as [`ExcludedSpan`](#muvid.footage.edl.ExcludedSpan) records so the
loss is reported rather than silent.

Run it BEFORE [`fill_gaps()`](#muvid.footage.edl.fill_gaps) and pass the result through [`validate_edl()`](#muvid.footage.edl.validate_edl) as
usual. It is a transform, not a verdict: it re-decides nothing about trust (it reads
`FootageAlignment.reliable`, which [`vouches_for()`](#muvid.footage.edl.vouches_for) set) and it refuses nothing —
[`validate_edl()`](#muvid.footage.edl.validate_edl) remains the ONE gate.

**It declines to empty the edit, and that is what keeps the refusal alive.** If every
footage entry would be excluded, the original list is returned unchanged with an empty
excluded list, so [`validate_edl()`](#muvid.footage.edl.validate_edl) sees an edit that still cuts to unvouched clips
and raises [`UnreliableAlignmentError`](#muvid.footage.edl.UnreliableAlignmentError) exactly as before. A shoot where nothing
is trustworthy must not come back as a black video reported as success — that is the
plausible-artifact failure muvid#59 is about, one level up. A *smaller* edit is a
recovery; an *empty* one is the same wrong answer wearing a coverage report.

**A gap is the last resort, not the first.** Before dropping an entry it is offered
to its neighbours: if the cut on either side is a vouched clip that ALREADY covers the
span, the span is absorbed into that cut instead. That case is not hypothetical and it
is not the aligner’s fault — `weighted`’s DP produces it structurally. Its
transition window is capped at `max_seg_s` (4x `l_max`, a performance bound) and
consecutive segments must be different clips, so a single vouched take longer than the
cap CANNOT be one segment and the optimizer is forced to cut away and back. Measured
on a 60 s song under the DEFAULT config, a 2 s opener plus one 58 s vouched clip
yields `[(0,2,O), (2,34,V), (34,36,BAD), (36,60,V)]` — and gapping that 2 s would
punch an avoidable black hole through the middle of a continuous take, blamed on an
alignment that had nothing to do with it. A caller-raised `l_max_overrun_penalty`
does the same thing at every `l_max` boundary. Absorbing loses nothing: the frames
come from a clip the aligner vouches for, which the caller’s own edit already used on
both sides, and `validate_edl` re-checks containment afterwards either way.

Absorption is refused into a cut whose meaning depends on its length or on the
boundary it starts at — one carrying a `transition`, a `crop_end` pan or a
`look` — and across a following `transition`, since the blend’s other side would
change clip underneath it. Strategies emit none of those, so the auto path always
absorbs; a hand-written EDL degrades to the gap rather than to a silently restretched
pan.

Returns `(entries, excluded)`.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](#muvid.footage.edl.EdlEntry)], [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ExcludedSpan`](#muvid.footage.edl.ExcludedSpan)]]

### muvid.footage.edl.fill_gaps(entries, song_duration)

Make an edit span the WHOLE song by inserting explicit gap entries.

Three holes become gap entries (muvid#21 items 1+2, one mechanism): the head
(`[0, first.song_start]` — without this, footage starting at t=5 s silently loses
the intro), interior holes between consecutive entries, and the tail
(`[last.song_end, song_duration]`). Entries are sorted by start; overlap and
containment stay [`validate_edl()`](#muvid.footage.edl.validate_edl)’s business — call this BEFORE it, never after.

An empty selection stays empty (an all-black video is not a first cut worth
rendering silently — the caller decides what “no usable footage” means).

\*\*A transition blends in from whatever precedes it on the timeline, which after
this function may be an inserted gap\*\* — i.e. a fade from black. That is a
consequence worth stating rather than a bug to guard: a transition annotates its
entry’s ENTRANCE, and the entrance is wherever the entry actually starts once the
edit spans the whole song. So a transition on the caller’s first entry is
REJECTED when footage starts at t=0 (nothing precedes it) and becomes a fade-in
from black when it does not (the head gap precedes it). Both follow from the same
rule; the tests pin both so the coherence stays deliberate.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](#muvid.footage.edl.EdlEntry)]

### muvid.footage.edl.validate_edl(edl, alignments, song_duration, , canvas=(1920, 1920), allow_unreliable=False)

Validate an EDL (from a strategy OR a caller) — the ONE gate before any cutting.

Enforces, raising `ValueError` with a specific message otherwise:

- non-empty; every non-gap `clip_id` is a known alignment;
- each span is positive and lies within `[0, song_duration]`;
- spans are in ascending order and **non-overlapping**;
- spans are **contiguous** (gapless) — a hole must be an explicit gap entry
  (`clip_id` empty/null, rendered as fill), which [`fill_gaps()`](#muvid.footage.edl.fill_gaps) inserts; an
  *implicit* hole is still an error, so nothing goes missing silently;
- each non-gap span lies within its clip’s aligned coverage, AND the derived
  `clip_in = song_start - offset` satisfies `0 <= clip_in` and
  `clip_in + span_duration <= clip_duration` (the clip actually contains that span);
- a `look` (the `looks` seam) names only filters in [`LOOK_FILTERS`](#muvid.footage.edl.LOOK_FILTERS),
  is ONE lexically-closed linear filter chain, names no container input, is
  not on a gap, sets only options `_LOOK_GEOMETRY_FILTERS` classifies on
  the four filters that can change the output frame, and asks for a frame no
  more than [`MAX_LOOK_SCALE`](#muvid.footage.edl.MAX_LOOK_SCALE) times `canvas` — see
  `_validate_look()`, which is the trust boundary for a caller-supplied
  > filter string;
- `look_time_varying` is a boolean, and is not set on an entry with no
  `look` (a declaration about a look that is not there is a request that
  cannot be honoured);
- every non-gap entry cuts to a clip whose alignment the aligner VOUCHES for
  (`FootageAlignment.reliable`), unless `allow_unreliable=True` — see
  [`UnreliableAlignmentError`](#muvid.footage.edl.UnreliableAlignmentError). This is a trust check rather than a
  > structural one, so it runs LAST: an EDL that does not even parse should be
  > reported as the malformed EDL it is, not blamed on the footage;
- a [`Transition`](#muvid.footage.edl.Transition) is on an entry that HAS a predecessor, names a known curve,
  is at least [`MIN_TRANSITION_S`](#muvid.footage.edl.MIN_TRANSITION_S) long, fits in song time counting BOTH
  transitions an entry can carry, and fits in each side’s aligned coverage — see
  `_validate_transition()`.

`canvas` is the DELIVERY canvas the assembler will render onto — the only
thing a look’s output frame can honestly be bounded against, since the look is
spliced after the assembler’s own `scale`/`pad` onto it. Every muvid path
passes the real one; [`DFLT_LOOK_CANVAS`](#muvid.footage.edl.DFLT_LOOK_CANVAS) covers a direct caller and is
the loosest bound rather than an absent one.

Returns the normalized list of [`EdlEntry`](#muvid.footage.edl.EdlEntry).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](#muvid.footage.edl.EdlEntry)]

### muvid.footage.edl.vouches_for(, confidence, support, margin=None, window_s=None)

Does the aligner vouch for this offset? The ONE place that verdict is reached.

Lives here, beside the record it judges and the gate that enforces it, rather than
with the aligner: `FootageAlignment.from_dict()` has to reach it to derive a
verdict for a record written before the field existed, and a module that owns a
dataclass should not need its own consumer to interpret one.

`support` (how much of the clip agrees) decides when the aligner measured it,
because it is the only one of the two numbers that can tell a clear winner from a
coin flip — see [`FootageAlignment.support`](#muvid.footage.edl.FootageAlignment.support).

\*\*When support is absent, this falls back to `confidence`, and that fallback is
known to be inadequate.\*\* It is the same instrument muvid#59 was filed about: on
that shoot it would have refused two of the three wrong offsets and passed the
third, which was 83 s out. So this is not “unknown forces approval” — an unmeasured
support is judged by the weaker test rather than refused outright.

The estimator fits its window to the clip and grades each window’s evidence, so a
vote is held and is READABLE for almost everything — `support is None` now only
for a clip too short to hold two windows at the 3 s floor (measured: 4 s yes, 6 s
no). The support is used **at whatever window it was measured at**, which is a
deliberate reversal: an earlier revision of this function refused to read a support
from a fitted window, on the reasoning that an argmax headcount over few short
windows is a weaker statistic than the same fraction at 20 s. That was true of the
ungraded tally and is now actively wrong — see the note above
[`MIN_SUPPORT`](#muvid.footage.edl.MIN_SUPPORT). Measured on six pure-noise clips at a fitted 6.67 s window,
the window guard vouches for FOUR of them by routing to the coefficient, while
reading the graded support refuses all six.

\*\*Where it still falls back to the coefficient, it is guessing, and the
measurements say so plainly.\*\* On heavily degraded cross-device clips of the
muvid#59 master, the worst CORRECT alignment scored 0.129 while the worst pure-noise
clip scored 0.139 — the distributions overlap, so no threshold admits the correct
ones and refuses the noise. Worse, the wrong offsets scored 0.183-0.252, *higher*
than the correct ones. The real material agrees: of the three 10 s excerpts, the
WRONG one carried the highest coefficient of the three (0.834 against 0.566 and
0.621).

So [`MIN_CONFIDENCE`](#muvid.footage.edl.MIN_CONFIDENCE) was deliberately NOT re-tuned. There is no value to tune it
to: raising it past the noise floor refuses correct footage, and a number chosen to
make a noise-floor test pass would hide that behind a green run. That fallback is now
reached only by a clip too short to vote at all, which is the narrowest it has been.

**Two numbers, asking different questions, and BOTH are required.**
[`MIN_SUPPORT`](#muvid.footage.edl.MIN_SUPPORT) asks whether enough of the clip’s evidence reached this offset;
[`MIN_MARGIN`](#muvid.footage.edl.MIN_MARGIN) asks whether that evidence prefers this offset over every other
one it considered. Neither subsumes the other, and the measurement that proves it is
in [`MIN_SUPPORT`](#muvid.footage.edl.MIN_SUPPORT)’s note: margin alone scores 24/24 on real material where the
support threshold scores 18/24, and then vouches for a bar-multiple REPEAT that the
support threshold refuses. Aliases and noise fail differently; margin catches noise
and only some aliases.

So this is a strict tightening of what muvid 0.0.53 shipped — it can only refuse
more, never less.

**What muvid#91 becomes.** Since the estimator fits its window down to a 3 s floor,
the regime with no vote at all is now exactly clips shorter than
`window_floor + hop` — measured, **4.5 s**: 4.4 s gives `support is None` and
4.5 s is the first with a number. Those still fall back to the coefficient, which
produces an inversion worth naming: a 4.4 s clip is VOUCHED while a 4.5 s one is
refused, because falling off the bottom of the vote hands the decision to the weaker
instrument. That is not defensible on its own terms, and it is left alone HERE only
because changing it means separating a fresh verdict from a DERIVED one — a record
predating these fields also has `support is None`, and refusing those would
re-break what muvid#87 fixed. muvid#91 is now that specific decision, over a band too
short to be usable music-video footage, rather than the open question about a third
of a shoot that it started as.

* **Parameters:**
  * **confidence** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – The estimator’s correlation coefficient at the chosen lag.
  * **support** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Graded fraction of window evidence reaching the offset, or `None`
    when no vote could be held.
  * **margin** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – That tally minus the tally at the best offset outside the tolerance.
    `None` on exactly the same quorum as `support`. Required to vouch when a
    vote WAS held: an aligner reporting support without it has not answered the
    separating question, and unknown does not vouch. The `mixing>=0.0.51` floor
    guarantees both, and `tests/test_ci_extras_canary.py` asserts the
    capability so a mis-resolved floor fails loudly rather than quietly refusing
    every clip.
  * **window_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – The window support was measured at. Recorded and reported as a
    diagnostic; it does **not** enter the verdict, for the reason above.
* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)
* **Returns:**
  True when the offset may be cut to without the caller opting in.
