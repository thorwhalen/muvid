# muvid.footage

Footage-aligned music video — align several device recordings of one song, assemble.

The engine behind muvid’s `music_video` genre (thorwhalen/reelee#229): a user uploads
several video clips, each a *different-device* recording of the SAME fixed song (so the
captured audio is time-shifted, noisy, drifting — never a perfect match). Each clip is
aligned to the clean song’s timeline by audio cross-correlation (`mixing.audio`), and a
chosen or auto-selected edit is assembled into a music video over the clean song audio.

Modules:

- [`muvid.footage.align`](muvid.footage.align.md#module-muvid.footage.align) — align a clip set to the song (thin over `mixing.audio`).
- [`muvid.footage.strategy`](muvid.footage.strategy.md#module-muvid.footage.strategy) — the pluggable `SelectionStrategy` registry that turns
  alignments into an EDL (which clip covers which span of the song), preferring a clip
  the aligner vouches for wherever one covers the span (muvid#88).
- [`muvid.footage.edl`](muvid.footage.edl.md#module-muvid.footage.edl) — the `validate_edl` SSOT + EDL/alignment data types,
  including the per-cut `CropWindow` (the EDL’s spatial half, muvid#60).
- [`muvid.footage.look`](muvid.footage.look.md#module-muvid.footage.look) — the `looks` seam: compile a grade, a LUT or an
  in-shot punch-in (muvid#66) into the per-cut `-vf` fragment the assembler splices.
- [`muvid.footage.assemble`](muvid.footage.assemble.md#module-muvid.footage.assemble) — the bounded single-ffmpeg-pass assembler.
- [`muvid.footage.workspace`](muvid.footage.workspace.md#module-muvid.footage.workspace) — the per-user stateful project (song + clips + manifest).

### Functions

| [`exclude_unvouched`](#muvid.footage.exclude_unvouched)(edl, alignments)                 | Set aside the spans of an AUTO edit whose only footage is unvouched (muvid#88).     |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|
| [`validate_edl`](#muvid.footage.validate_edl)(edl, alignments, song_duration, \*)   | Validate an EDL (from a strategy OR a caller) — the ONE gate before any cutting.    |
| [`derive_cuts`](#muvid.footage.derive_cuts)(edl, alignments, clip_paths)           | Turn a *validated* EDL into render-ready cuts — the ONE place `clip_in` is derived. |
| [`chain`](#muvid.footage.chain)(\*fragments)                                 | Join look fragments into one, dropping the empty ones.                              |
| [`is_time_varying`](#muvid.footage.is_time_varying)(fragment)                          | Whether `fragment` declares that it reads the clock.                                |
| [`motion`](#muvid.footage.motion)(keyframes, \*, canvas, fps)                 | A camera path over the cut, as a filter fragment.                                   |
| [`punch_in`](#muvid.footage.punch_in)(\*, canvas, fps, duration_s[, zoom, ...]) | An in-shot punch-in: hold, then push in, WITHOUT leaving the shot (muvid#66).       |
| [`punch_in_cuts`](#muvid.footage.punch_in_cuts)(entries, \*, canvas, fps[, ...])     | Put a punch-in on every `every`-th footage entry — muvid#66's other half.           |
| [`stylize`](#muvid.footage.stylize)(look, \*, canvas, fps[, duration_s, ...])  | A `looks.Look` compiled against the binary muvid will run.                          |
| [`register_selection_strategy`](#muvid.footage.register_selection_strategy)(slug, fn)              | Register a selection strategy under `slug` (returns it, for inline use).            |
| [`list_strategies`](#muvid.footage.list_strategies)()                                  | All strategy slugs (eager + lazy), sorted.                                          |
| [`resolve_strategy`](#muvid.footage.resolve_strategy)(strategy)                         | Resolve a strategy name OR a bare callable to a `SelectionStrategy`.                |
| [`select_edl`](#muvid.footage.select_edl)(strategy, alignments, ...[, context])   | Run `strategy` (name or callable) to produce an EDL from `alignments`.              |

### Classes

| [`FootageAlignment`](#muvid.footage.FootageAlignment)(clip_id, offset_s, ...[, ...])   | Where one uploaded clip sits on the song timeline (muvid's per-clip record).            |
|----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| [`EdlEntry`](#muvid.footage.EdlEntry)(song_start, song_end, clip_id[, ...])    | One cut: show `clip_id` over the song span `[song_start, song_end]`.                    |
| [`CropWindow`](#muvid.footage.CropWindow)(x, y, w, h)                            | A rectangle to take from the source frame, as fractions of its width/height.            |
| [`ExcludedSpan`](#muvid.footage.ExcludedSpan)(clip_id, song_start, song_end)       | One span an AUTO-selected edit gave up rather than cut to unvouched footage (muvid#88). |
| [`LookFragment`](#muvid.footage.LookFragment)(fragment, \*, time_varying)          | A compiled filter chain that remembers whether it READS THE CLOCK.                      |

### Exceptions

| [`UnreliableAlignmentError`](#muvid.footage.UnreliableAlignmentError)(unvouched)   | An edit cuts to a clip whose OFFSET the aligner could not vouch for (muvid#59).   |
|----------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| [`LookError`](#muvid.footage.LookError)                             | A look could not be compiled.                                                     |

### *class* muvid.footage.CropWindow(x, y, w, h)

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

### *class* muvid.footage.EdlEntry(song_start, song_end, clip_id, transition=None, crop=None, crop_end=None, look=None, look_time_varying=False)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One cut: show `clip_id` over the song span `[song_start, song_end]`.

`clip_id == ""` is a **gap entry** — no footage covers this span, and the renderer
fills it (black in v1). Gaps are explicit entries rather than absences so that an EDL
is always contiguous over its span, every span of the song is accounted for by
exactly one entry, and “no clip here” survives a JSON round trip (`clip_id: null`).

#### crop *: [CropWindow](muvid.footage.edl.md#muvid.footage.edl.CropWindow) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Take only this rectangle of the source frame. `None` keeps the whole frame
letterboxed onto the canvas, which is what every EDL written before this
field existed means — additive in both directions, like `transition`.

#### crop_end *: [CropWindow](muvid.footage.edl.md#muvid.footage.edl.CropWindow) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

With `crop`, makes the window MOVE linearly from `crop` to `crop_end`
across the cut — a pan. Same size as `crop` (see [`validate_edl()`](#muvid.footage.validate_edl)): a
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
`LOOK_FILTERS` — not against a list of refusals — and also refuses a
fragment that names a container input, that is more than ONE linear chain,
that is not lexically closed, that sets an option muvid has not classified
on one of the four filters that can change the output geometry, or that
asks for a frame more than `MAX_LOOK_SCALE` times the delivery
canvas. The first two of those would break the bounded-memory invariant
the assembler rests on; the allowlist is what keeps a look from writing
this machine’s disk; and the last two are what keep an allowlisted filter
from spending 900 MB of it (muvid#75).

#### look_time_varying *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= False*

Whether [`look`](muvid.footage.look.md#module-muvid.footage.look) READS THE FILTER CLOCK — a punch-in, a pan, anything
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

#### transition *: [Transition](muvid.footage.edl.md#muvid.footage.edl.Transition) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Blend in from the predecessor rather than hard-cutting. `None` (the
default) is a hard cut, so an EDL written before this field existed is a
valid EDL now, and one written with it, read by older code, renders hard
cuts — degraded, never wrong, in both directions.

### *class* muvid.footage.ExcludedSpan(clip_id, song_start, song_end, reason='no_vouched_coverage', confidence=0.0, support=None, margin=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One span an AUTO-selected edit gave up rather than cut to unvouched footage (muvid#88).

A typed record, not a log line: it is returned to the caller (and over the MCP wire),
because “your video is shorter than the song” is only actionable next to *which* clip
was set aside, *where*, and *on what numbers*. The numbers are the same three
`vouches_for()` reached its verdict on, so the report and the gate can never
disagree about why.

#### to_dict()

JSON-ready. `support`/`margin` stay `None` — “not measured” is not zero.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### *class* muvid.footage.FootageAlignment(clip_id, offset_s, confidence, duration_s, coverage, overlaps=True, support=None, reliable=True, window_s=None, hop_s=None, margin=None)

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
weak endorsement. See `MIN_MARGIN` — this is the number muvid#59 was
actually missing, since its near-ties (0.993/0.989/0.987) are invisible to any
fraction that does not look at the runner-up.

#### overlaps *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= True*

Whether the clip intersects the song timeline at all. A clip that does NOT is
still recorded — a source must never leave the addressable set as a side effect
of being measured. Selection filters on this; reporting shows it with a reason.

#### reliable *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= True*

Whether the aligner VOUCHES for `offset_s`. False means “measured, recorded,
and not to be cut to without the caller saying so” — see
[`UnreliableAlignmentError`](#muvid.footage.UnreliableAlignmentError), which [`validate_edl()`](#muvid.footage.validate_edl) raises.

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
`MIN_SUPPORT` for why a window-based qualifier was removed rather than
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
questions. `vouches_for()` reads `window_s`; `hop_s` is carried because
the pair is what makes a support fraction reproducible (which windows were
eligible to agree depends on the hop), and a record that cannot reproduce its own
measurement is a record you have to take on faith.

### *exception* muvid.footage.LookError

Bases: [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError)

A look could not be compiled. Carries what to do about it.

### *class* muvid.footage.LookFragment(fragment, , time_varying)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

A compiled filter chain that remembers whether it READS THE CLOCK.

A plain `str` in every way that matters — it splices, compares, serialises
and JSON-encodes identically, so nothing downstream needs to know it exists —
that additionally answers `time_varying`. That is what lets
[`punch_in_cuts()`](#muvid.footage.punch_in_cuts) set
[`muvid.footage.edl.EdlEntry.look_time_varying`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry.look_time_varying) *from the fragment*
instead of hardcoding a value beside the call that produced it, and what lets
[`chain()`](#muvid.footage.chain) combine two fragments without either caller re-deriving the
answer.

A subclass rather than a `(str, bool)` pair because the fragment is already
a wire value: the EDL field is a string, the MCP reply is a string, and the
`looks` API returns a string. Changing that shape would push a rename table
into every consumer to carry one bit that only muvid’s own compilers can
know.

**The bit does not survive a round trip, and is not meant to.** `str`
operations return plain `str`, and JSON has no place to put it — the
durable home is the EDL entry’s own field, which is exactly why muvid#73 put
it there. [`is_time_varying()`](#muvid.footage.is_time_varying) reads a fragment of either kind, treating a
plain string as static, which is the same default the field has.

```pycon
>>> frag = LookFragment("zoompan=d=1:s=64x48:fps=25", time_varying=True)
>>> frag.time_varying, frag == "zoompan=d=1:s=64x48:fps=25"
(True, True)
>>> is_time_varying("hue=s=0")
False
```

Copyable and picklable, which needs saying because it is not free: `str`
subclasses are reconstructed through `__new__`, and without
`__getnewargs_ex__` both `copy.deepcopy` and `pickle` raise
`TypeError: __new__() missing 1 required keyword-only argument` — measured.
An `EdlEntry` carrying one is an ordinary dataclass a caller may well copy.

```pycon
>>> import copy, pickle
>>> copy.deepcopy(frag).time_varying, pickle.loads(pickle.dumps(frag)) == frag
(True, True)
```

### *exception* muvid.footage.UnreliableAlignmentError(unvouched)

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
`vouches_for()`, which falls back to the confidence coefficient wherever
`support` was never measured — which is every alignment today’s `mixing`
produces. That fallback would have caught two of muvid#59’s three wrong
offsets and missed the third. The refusal is the half that makes a bad
measurement stop being silent; the half that makes the measurement good is
the windowed consensus in thorwhalen/mixing#30.

A refusal is NOT a removal: the clip keeps its record, its coverage and its
place in the project (see [`FootageAlignment`](#muvid.footage.FootageAlignment)). What it loses is the
right to be cut to without the caller saying so.

#### alignments

The offending clips, in EDL order — so a caller can re-align exactly these.

### muvid.footage.chain(\*fragments)

Join look fragments into one, dropping the empty ones.

A cut carries at most one look, so two effects on one cut are one chain.
Returns `None` when nothing survives, which is the value the EDL field
wants for “no look” — an empty string is refused by `validate_edl`
deliberately, so this does not produce one.

**The result is time-varying if ANY component is** — the OR, not the AND and
not the last one’s answer. A chain runs every link on every frame, so one
moving link makes the whole fragment move, and muvid#73’s restart hits it.
Getting this wrong in the safe-looking direction (AND) would silence the
warning on exactly the chains most likely to have one: a punch composed with
a grade.

A plain `str` component contributes `False`, matching
[`is_time_varying()`](#muvid.footage.is_time_varying) and the EDL field’s own default — so hand-writing one
half of a chain quietly downgrades only that half’s claim, never the other’s.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`LookFragment`](muvid.footage.look.md#muvid.footage.look.LookFragment)]

```pycon
>>> chain("hue=s=0", None, "", "unsharp=5:5:1")
'hue=s=0,unsharp=5:5:1'
>>> chain(None, "") is None
True
>>> chain("hue=s=0", LookFragment("zoompan=d=1", time_varying=True)).time_varying
True
>>> chain("hue=s=0", "unsharp=5:5:1").time_varying
False
```

### muvid.footage.derive_cuts(edl, alignments, clip_paths)

Turn a *validated* EDL into render-ready cuts — the ONE place `clip_in` is derived.

Strategies emit only `{song_start, song_end, clip_id}`; the sign convention
`clip_in = song_start - offset` lives here (SSOT), so no strategy can desync the cut.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`AssemblyCut`](muvid.footage.edl.md#muvid.footage.edl.AssemblyCut)]

### muvid.footage.exclude_unvouched(edl, alignments)

Set aside the spans of an AUTO edit whose only footage is unvouched (muvid#88).

The auto path’s recovery, and deliberately NOT a second gate. A strategy already
prefers a vouched clip wherever one covers the span (see
[`muvid.footage.strategy`](muvid.footage.strategy.md#module-muvid.footage.strategy)), so an unvouched entry surviving into its output means
exactly one thing: **nothing else covered that span**. This drops those entries, which
`fill_gaps()` then turns into explicit gap entries — the encoding muvid already
uses for “no footage here” — and returns them as [`ExcludedSpan`](#muvid.footage.ExcludedSpan) records so the
loss is reported rather than silent.

Run it BEFORE `fill_gaps()` and pass the result through [`validate_edl()`](#muvid.footage.validate_edl) as
usual. It is a transform, not a verdict: it re-decides nothing about trust (it reads
`FootageAlignment.reliable`, which `vouches_for()` set) and it refuses nothing —
[`validate_edl()`](#muvid.footage.validate_edl) remains the ONE gate.

**It declines to empty the edit, and that is what keeps the refusal alive.** If every
footage entry would be excluded, the original list is returned unchanged with an empty
excluded list, so [`validate_edl()`](#muvid.footage.validate_edl) sees an edit that still cuts to unvouched clips
and raises [`UnreliableAlignmentError`](#muvid.footage.UnreliableAlignmentError) exactly as before. A shoot where nothing
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
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry)], [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ExcludedSpan`](muvid.footage.edl.md#muvid.footage.edl.ExcludedSpan)]]

### muvid.footage.is_time_varying(fragment)

Whether `fragment` declares that it reads the clock. Plain strings: no.

The one place that default lives, so a caller reading a fragment and the EDL
field’s own default cannot drift apart.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

```pycon
>>> is_time_varying(LookFragment("zoompan=d=1", time_varying=True))
True
>>> is_time_varying("hue=s=0"), is_time_varying(None)
(False, False)
```

### muvid.footage.list_strategies()

All strategy slugs (eager + lazy), sorted. Lazy slugs are NOT imported to list them.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.footage.motion(keyframes, , canvas, fps)

A camera path over the cut, as a filter fragment. `looks` picks the filter.

* **Parameters:**
  * **keyframes** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)) – `(t_seconds, window)` pairs, or `looks.Keyframe`s.
    The window is anything with ``x``/`y`/`w`/`h` as fractions —
    [`muvid.footage.edl.CropWindow`](muvid.footage.edl.md#muvid.footage.edl.CropWindow) satisfies that structurally,
    with no adapter, because both packages use `burns.Rect`’s
    convention on purpose.
  * **canvas** – `(width, height)` — the assembler’s delivery canvas.
  * **fps** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – the assembler’s delivery frame rate.
* **Return type:**
  [`LookFragment`](muvid.footage.look.md#muvid.footage.look.LookFragment)
* **Returns:**
  One linear ffmpeg filter chain, ready for the `look` field, declaring
  itself **time-varying** — a camera path is a ramp in the filter’s own
  clock (`in_time` under `zoompan`, `t` under `crop`), whichever
  filter `looks` picks, so it is muvid#73’s affected kind. A path whose
  keyframes happen to hold still is still declared moving: the fragment
  reads the clock either way, and that is the property the warning is
  about.

**The windows are fractions of the CANVAS, not of the source.** That follows
from where the fragment is spliced: after `scale`/`pad`, so the frame it
sees is the canvas with the source letterboxed into it. It is also what makes
the move well-defined across a mixed-device edit — the same punch reads the
same on a portrait phone clip and a landscape one, where a source-relative
window would not. Use `crop`/`crop_end` on the EDL entry for the
source-relative framing decision; the two compose, crop first.

Which ffmpeg filter this becomes is `looks`’ decision and not a matter of
taste: a constant-size window is `crop`, a resizing one is `zoompan`, and
`crop` cannot resize at all (its `w`/`h` are evaluated once, at
configure time, when `t` is NAN — it either refuses to configure or, worse,
exits 0 having rendered every frame at one wrong size).

* **Raises:**
  [**LookError**](#muvid.footage.LookError) – If the path needs something that was not supplied.
* **Return type:**
  [*LookFragment*](muvid.footage.look.md#muvid.footage.look.LookFragment)

### muvid.footage.punch_in(, canvas, fps, duration_s, zoom=1.12, anchor=(0.5, 0.5), start_s=0.0, end_s=None)

An in-shot punch-in: hold, then push in, WITHOUT leaving the shot (muvid#66).

The design partner asked for “roughly 2N” of these and was explicit that it
is **not** a transition between two clips — it stays on the same shot. Before
this there was no punch-in, zoom or Ken Burns move anywhere in the footage
path; the only two mentions of `zoom` under `footage/` were comments
explaining why `zoompan` was unusable, and that reading has since been
measured wrong: `t` is undefined inside `zoompan`, but `in_time` works
and `d=1` is exactly 1:1 rather than the frame-duplicating default. Both
corrections live in `looks.compile_motion`, which is why this function is
six lines of geometry and no ffmpeg.

* **Parameters:**
  * **canvas** – `(width, height)` — the assembler’s delivery canvas.
  * **fps** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – the assembler’s delivery frame rate.
  * **duration_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – the cut’s length in seconds. The move ends here by default.
  * **zoom** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – final magnification. `1.12` shows ~89% of the frame.
  * **anchor** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float)]) – what stays put, as a fraction of the canvas. `(0.5, 0.5)`
    centres it; `(0.5, 0.35)` pushes toward a face in the upper third.
  * **start_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – hold the full frame until here, then move.
  * **end_s** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]) – reach the final framing here and hold. Defaults to `duration_s`.
* **Return type:**
  [`LookFragment`](muvid.footage.look.md#muvid.footage.look.LookFragment)
* **Returns:**
  One linear ffmpeg filter chain, ready for the `look` field, declaring
  itself **time-varying** (it goes through [`motion()`](#muvid.footage.motion)). Put that on the
  entry as `look_time_varying` — [`punch_in_cuts()`](#muvid.footage.punch_in_cuts) does — or the
  assembler cannot warn you when the cut borders a transition and the move
  restarts (muvid#73).

A move is a ramp between two windows, and the windows are fractions of the
canvas (see [`motion()`](#muvid.footage.motion)). The end window keeps the canvas’s aspect ratio,
which is what lets `zoompan` deliver at the canvas size with no stretch and
no reframing crop.

```pycon
>>> frag = punch_in(canvas=(640, 360), fps=25, duration_s=3.0)
>>> frag.startswith("zoompan=d=1:s=640x360:fps=25:")
True
>>> frag.time_varying
True
```

A pull-out is this move backwards, so it goes through [`motion()`](#muvid.footage.motion) with the
windows in the order you want them, rather than a boolean here.

* **Raises:**
  [**LookError**](#muvid.footage.LookError) – If `zoom`, the timings or the anchor are out of range.
* **Return type:**
  [*LookFragment*](muvid.footage.look.md#muvid.footage.look.LookFragment)

### muvid.footage.punch_in_cuts(entries, , canvas, fps, every=2, zoom=1.12, anchor=(0.5, 0.5), offset=0)

Put a punch-in on every `every`-th footage entry — muvid#66’s other half.

The request was for *roughly 2N* punch-ins “evenly redistributed so they
occur about twice as often”, explicitly **not** two extra tacked on the end.
Redistribution is therefore the whole job, and it is a stride over the cuts
rather than a wall-clock interval: cuts are already beat-snapped by the
selector, so a stride lands the moves on musical time for free, where a
seconds-based interval would drift off it.

* **Parameters:**
  * **entries** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)) – validated [`EdlEntry`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry) objects.
  * **canvas** – `(width, height)` — the delivery canvas.
  * **fps** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – the delivery frame rate.
  * **every** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – stride. `2` punches every other footage cut; `1` punches all.
  * **zoom** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – passed to [`punch_in()`](#muvid.footage.punch_in).
  * **anchor** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float)]) – passed to [`punch_in()`](#muvid.footage.punch_in).
  * **offset** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – which footage cut in each stride gets the move.
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]
* **Returns:**
  A NEW list of entries. Gaps are skipped (they have no footage to punch
  into, and `validate_edl` refuses a look on one), and an entry that
  already carries a look keeps it — this composes with hand-authoring
  rather than overwriting it, because silently replacing an authored
  direction is the failure this package guards against elsewhere.

  Each punched entry also carries `look_time_varying`, taken FROM the
  fragment via [`is_time_varying()`](#muvid.footage.is_time_varying) rather than written as a literal
  `True` beside the `punch_in` call. The two would agree today and the
  literal is the one that would stop agreeing — this is the same reason
  `_edl_json` is a table rather than four `if`s. Without it the
  assembler cannot warn that a punch bordering a transition restarts its
  ramp (muvid#73).

### muvid.footage.register_selection_strategy(slug, fn)

Register a selection strategy under `slug` (returns it, for inline use).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry)]]

### muvid.footage.resolve_strategy(strategy)

Resolve a strategy name OR a bare callable to a `SelectionStrategy`.

A lazy slug is imported here (and cached into the eager table) on first resolution.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry)]]

### muvid.footage.select_edl(strategy, alignments, song_duration, , context=None)

Run `strategy` (name or callable) to produce an EDL from `alignments`.

`context` (a `SelectionContext`) is passed to score-driven strategies that declare it;
the alignment-only built-ins ignore it. See `SelectionStrategy`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry)]

### muvid.footage.stylize(look, , canvas, fps, duration_s=None, ffmpeg='ffmpeg', env=None, policy=None)

A `looks.Look` compiled against the binary muvid will run.

* **Parameters:**
  * **look** – a `looks.Look` — an ordered stack of named effects.
  * **canvas** – `(width, height)` — the assembler’s delivery canvas.
  * **fps** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – the assembler’s delivery frame rate.
  * **duration_s** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]) – the cut’s length, when a step needs to know it.
  * **ffmpeg** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – which binary to probe. Defaults to the bare name muvid runs.
  * **env** – a `looks.FfmpegEnv` to compile against, instead of probing.
  * **policy** – a `looks.Policy` — the licence ceiling. `looks`’ default
    applies when omitted.
* **Return type:**
  [`LookFragment`](muvid.footage.look.md#muvid.footage.look.LookFragment)
* **Returns:**
  One linear ffmpeg filter chain, ready for the `look` field, declaring
  whether it is time-varying **from the compiled plan** rather than by
  assumption. A grade, a LUT, a posterise is static; two shapes are not,
  and both are reachable from here:
  - a step naming a `TIME_VARYING_EFFECTS` member — `motion` is one
    of `looks`’ registered effects, so `stylize` can emit exactly the
    `zoompan` ramp [`punch_in()`](#muvid.footage.punch_in) does (verified by compiling it);
  - a step with an `at` `looks.Span`, which compiles to
    `enable='between(t,…)'` — measured: `Effect("blur", at=Span(0.5,
    1.5))` becomes `gblur=sigma=2:enable='between(t,0.5,1.5)'`. Reading
    the clock to decide *whether* to apply is the same restart, and the
    effect is not on any list of moving ones.

  A blanket `False` here — the obvious reading, since `stylize` is the
  grade-shaped door — would have been wrong for both.

**The clip is declared, not measured.** `looks` compiles against a
*declared* clip, and at this splice point muvid knows the geometry and the
rate exactly — they are the canvas and the delivery rate the assembler is
about to impose, not properties of whichever source is underneath. That is
the payoff of splicing after `scale`/`pad`/`fps` rather than before it.

`origin_s=0.0` is declared for the same kind of reason and is true for a
stated cause rather than by default: `looks` treats a span as being in the
host’s decoder time, and *input-side* `-ss` rebases the filter timeline to
0 where output-side `-ss` does not. `muvid.footage.assemble._render_part()`
seeks input-side. Move that seek and this declaration becomes false.

**A refusal is the feature.** `looks` resolves each step’s licence tier
against the probed binary, so a look reaching a GPL-only filter on an
LGPL build is either substituted or refused by name — rather than working on
a laptop and quietly raising the licence tier of a shipped product.

* **Raises:**
  [**LookError**](#muvid.footage.LookError) – If the look cannot be compiled for this binary, this ceiling
      or this clip. The message is `looks`’, which names the remedy.
* **Return type:**
  [*LookFragment*](muvid.footage.look.md#muvid.footage.look.LookFragment)

### muvid.footage.validate_edl(edl, alignments, song_duration, , canvas=(1920, 1920), allow_unreliable=False)

Validate an EDL (from a strategy OR a caller) — the ONE gate before any cutting.

Enforces, raising `ValueError` with a specific message otherwise:

- non-empty; every non-gap `clip_id` is a known alignment;
- each span is positive and lies within `[0, song_duration]`;
- spans are in ascending order and **non-overlapping**;
- spans are **contiguous** (gapless) — a hole must be an explicit gap entry
  (`clip_id` empty/null, rendered as fill), which `fill_gaps()` inserts; an
  *implicit* hole is still an error, so nothing goes missing silently;
- each non-gap span lies within its clip’s aligned coverage, AND the derived
  `clip_in = song_start - offset` satisfies `0 <= clip_in` and
  `clip_in + span_duration <= clip_duration` (the clip actually contains that span);
- a `look` (the `looks` seam) names only filters in `LOOK_FILTERS`,
  is ONE lexically-closed linear filter chain, names no container input, is
  not on a gap, sets only options `_LOOK_GEOMETRY_FILTERS` classifies on
  the four filters that can change the output frame, and asks for a frame no
  more than `MAX_LOOK_SCALE` times `canvas` — see
  `_validate_look()`, which is the trust boundary for a caller-supplied
  > filter string;
- `look_time_varying` is a boolean, and is not set on an entry with no
  `look` (a declaration about a look that is not there is a request that
  cannot be honoured);
- every non-gap entry cuts to a clip whose alignment the aligner VOUCHES for
  (`FootageAlignment.reliable`), unless `allow_unreliable=True` — see
  [`UnreliableAlignmentError`](#muvid.footage.UnreliableAlignmentError). This is a trust check rather than a
  > structural one, so it runs LAST: an EDL that does not even parse should be
  > reported as the malformed EDL it is, not blamed on the footage;
- a `Transition` is on an entry that HAS a predecessor, names a known curve,
  is at least `MIN_TRANSITION_S` long, fits in song time counting BOTH
  transitions an entry can carry, and fits in each side’s aligned coverage — see
  `_validate_transition()`.

`canvas` is the DELIVERY canvas the assembler will render onto — the only
thing a look’s output frame can honestly be bounded against, since the look is
spliced after the assembler’s own `scale`/`pad` onto it. Every muvid path
passes the real one; `DFLT_LOOK_CANVAS` covers a direct caller and is
the loosest bound rather than an absent one.

Returns the normalized list of [`EdlEntry`](#muvid.footage.EdlEntry).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry)]

### Modules

| [`align`](muvid.footage.align.md#module-muvid.footage.align)                 | Align a set of footage clips to the song — a thin wrapper over `mixing.audio`.              |
|---------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| [`assemble`](muvid.footage.assemble.md#module-muvid.footage.assemble)           | Assemble validated cuts into a music video, in BOUNDED memory.                              |
| [`edl`](muvid.footage.edl.md#module-muvid.footage.edl)                     | EDL data types + the `validate_edl` single-source-of-truth gate.                            |
| [`lacing_bridge`](muvid.footage.lacing_bridge.md#module-muvid.footage.lacing_bridge) | muvid project → lacing standoff records, and the DECISION tier back to an EDL.              |
| [`look`](muvid.footage.look.md#module-muvid.footage.look)                   | Compile a `looks` artifact into the fragment the assembler splices.                         |
| [`scoring`](muvid.footage.scoring.md#module-muvid.footage.scoring)             | Footage scoring — per-clip score tracks on the shared song-time grid (thorwhalen/muvid#13). |
| [`select_score`](muvid.footage.select_score.md#module-muvid.footage.select_score)   | The score-driven `weighted` selection strategy: a beat-snapped semi-Markov Viterbi DP.      |
| [`strategy`](muvid.footage.strategy.md#module-muvid.footage.strategy)           | The pluggable `SelectionStrategy` registry — alignments → an EDL.                           |
| [`workspace`](muvid.footage.workspace.md#module-muvid.footage.workspace)         | Per-user, STATEFUL project for the footage-aligned `music_video` genre.                     |
