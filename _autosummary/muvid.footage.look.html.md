# muvid.footage.look

Compile a `looks` artifact into the fragment the assembler splices.

This is muvid’s half of the `looks` seam — `looks`’ build-order item 10, and
the first real consumer of that package. The division of labour is the whole
point and it is worth stating before any code:

> **\`\`looks\`\` decides what a pixel becomes. muvid decides which pixels exist,
> when they appear, and how the process is run.**

So `looks` emits a `-vf` chain fragment and nothing else. It owns no encode
setting: `-c:v`, `-crf`, `-preset`, `-pix_fmt`, the frame counts, the
concat, the mux and the per-invocation timeout all stay in
[`muvid.footage.assemble`](muvid.footage.assemble.html.md#module-muvid.footage.assemble), where the bounded-memory invariant lives. The
fragment rides in [`muvid.footage.edl.EdlEntry.look`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry.look) and is spliced by
`muvid.footage.assemble._part_filter()`.

Why a `-vf` fragment is the *cheapest possible* seam, which is why it was
chosen: it adds no `-i`. muvid’s assembler was rewritten (muvid#21/#24) because
one `-filter_complex` over all cuts held a decoder per cut and was OOM-killed at
30 cuts on a 3.7 GB box; the guarantee it bought is O(1) decoders per invocation.
A filter fragment cannot touch that.

\*\*A look cannot reach a second source at all, and the earlier claim that it could
via `movie=` was wrong twice over.\*\* `movie=` is a zero-input source filter,
so at the solo site — a *simple* filtergraph, one input and one output — it leaves
the preceding chain unconsumed and ffmpeg refuses the whole graph before decoding
a frame ( *“had 1 input(s) and 2 output(s)”*, measured on ffmpeg 9.0.1; every
composited form, `movie=X,overlay=10:10` included, fails identically, and the
forms that DO render all need `[`/`]`/`;`). At the transition site it does
run, and what it does there is call `avformat_open_input` on a path from inside
the fragment — a second container decoder that no invocation accounted for, which
is the muvid#21/#24 invariant going out the back door. So
`muvid.footage.edl._validate_look()` refuses it, along with every other filter
outside [`muvid.footage.edl.LOOK_FILTERS`](muvid.footage.edl.html.md#muvid.footage.edl.LOOK_FILTERS). Compositing needs a second splice
site the assembler does not have; that is a change to the assembler, not a look.

Three functions, in the order you are likely to want them:

- [`punch_in()`](#muvid.footage.look.punch_in) — the in-shot zoom the design partner asked for (muvid#66).
- [`motion()`](#muvid.footage.look.motion) — an arbitrary camera path over the cut; `punch_in` is a
  named case of it.
- [`stylize()`](#muvid.footage.look.stylize) — a whole `looks.Look` (grade, LUT, posterise …)
  compiled against the binary muvid will actually run.

[`chain()`](#muvid.footage.look.chain) puts two of them on one cut. Each returns a [`LookFragment`](#muvid.footage.look.LookFragment) —
a `str` that also answers `time_varying` — except that
[`chain()`](#muvid.footage.look.chain) returns `None` when nothing survives. Read the answer back with
[`is_time_varying()`](#muvid.footage.look.is_time_varying), which treats a plain `str` as static.

**Which binary.** The fleet has two ffmpegs in routine use and neither is a
superset of the other (`looks`’ research note 11 §3.4 measured 484 vs 481
filters, with `zscale` in one and `drawtext` in the other). `looks` refuses
to resolve one — the environment is an argument — and muvid is the party entitled
to answer, because muvid owns the invocation. muvid runs the bare name `ffmpeg`
from `PATH` ([`muvid.visualize.ffmpeg.run_ffmpeg()`](muvid.visualize.ffmpeg.html.md#muvid.visualize.ffmpeg.run_ffmpeg)), so that is what
[`stylize()`](#muvid.footage.look.stylize) probes. Pass `env=` to compile against a different one.

\*\*One limitation, measured rather than guessed at, and not fixable here — but no
longer silent.\*\* A *time-varying* look on a cut that borders a
[`Transition`](muvid.footage.edl.html.md#muvid.footage.edl.Transition) **restarts its ramp on the blended part**.
The assembler renders a transitioned boundary as a separate two-input invocation
whose inputs are input-side-seeked to the blend window, so the filter clock there
begins at 0 again instead of continuing the cut’s.

Measured on a 3.0 s cut at 25 fps with a 0.4 s fade and a 1.12x punch: the solo
part’s last frame is drawn at zoom 1.109 (mean 

```
|diff|
```

 28.1/255 against the same
frame rendered without the look), while the blend part’s first frame is drawn at
zoom 1.000 (mean 

```
|diff|
```

 0.7/255 against its own unlooked twin — i.e.
indistinguishable from no punch at all). The move snaps back for the length of
the blend and then the next cut begins.

A **static** look — a grade, a LUT, a posterise — is unaffected, because it never
reads the clock, and it is what the seam is mostly for. muvid still cannot rebase
the fragment: that means rewriting an arbitrary ffmpeg expression, which is
exactly what `looks` refuses to do for itself (its rule 27), and a wrong rebase
is worse than a documented one — it would exit 0 having moved the effect to a
different second of the clip.

What muvid *can* do, and now does (muvid#73), is **say which kind a look is** so
the assembler can warn instead of rendering the hitch quietly. Every function
here returns a [`LookFragment`](#muvid.footage.look.LookFragment) — a plain `str` that also answers
`.time_varying` — and [`punch_in_cuts()`](#muvid.footage.look.punch_in_cuts) copies that answer onto
[`look_time_varying`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry.look_time_varying), which is what the
assembler reads. A caller assembling entries by hand should do the same; the flag
defaults to `False`, so an *undeclared* moving look is still silent, and that
limit is stated on the field rather than left to be discovered.

### Module Attributes

| [`DEFAULT_PUNCH_ZOOM`](#muvid.footage.look.DEFAULT_PUNCH_ZOOM)   | the design partner described what he wanted as a "slight zoom/punch-in jump" (muvid#66), and a punch-in is a punctuation mark, not a transition.   |
|-----------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| [`DEFAULT_PUNCH_ANCHOR`](#muvid.footage.look.DEFAULT_PUNCH_ANCHOR) | Where [`punch_in()`](#muvid.footage.look.punch_in) zooms toward, as a fraction of the canvas.                                       |
| [`TIME_VARYING_EFFECTS`](#muvid.footage.look.TIME_VARYING_EFFECTS) | `looks` effects whose compiled fragment READS THE FILTER CLOCK, so a cut carrying one is affected by muvid#73's restart on a blended boundary.     |

### Functions

| [`chain`](#muvid.footage.look.chain)(\*fragments)                                 | Join look fragments into one, dropping the empty ones.                        |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`is_time_varying`](#muvid.footage.look.is_time_varying)(fragment)                          | Whether `fragment` declares that it reads the clock.                          |
| [`motion`](#muvid.footage.look.motion)(keyframes, \*, canvas, fps)                 | A camera path over the cut, as a filter fragment.                             |
| [`punch_in`](#muvid.footage.look.punch_in)(\*, canvas, fps, duration_s[, zoom, ...]) | An in-shot punch-in: hold, then push in, WITHOUT leaving the shot (muvid#66). |
| [`punch_in_cuts`](#muvid.footage.look.punch_in_cuts)(entries, \*, canvas, fps[, ...])     | Put a punch-in on every `every`-th footage entry — muvid#66's other half.     |
| [`stylize`](#muvid.footage.look.stylize)(look, \*, canvas, fps[, duration_s, ...])  | A `looks.Look` compiled against the binary muvid will run.                    |

### Classes

| [`LookFragment`](#muvid.footage.look.LookFragment)(fragment, \*, time_varying)   | A compiled filter chain that remembers whether it READS THE CLOCK.   |
|---------------------------------------------------------------------------------------------|----------------------------------------------------------------------|

### Exceptions

| [`LookError`](#muvid.footage.look.LookError)   | A look could not be compiled.   |
|--------------------------------------------------------------|---------------------------------|

### muvid.footage.look.DEFAULT_PUNCH_ANCHOR *= (0.5, 0.5)*

Where [`punch_in()`](#muvid.footage.look.punch_in) zooms toward, as a fraction of the canvas. Centre.

### muvid.footage.look.DEFAULT_PUNCH_ZOOM *= 1.12*

the design partner
described what he wanted as a “slight zoom/punch-in jump” (muvid#66), and a
punch-in is a punctuation mark, not a transition. Larger values magnify the
source, so a value that looks fine on a 4K clip is soft on a 720p one — which
is why it is a named constant a caller overrides rather than a hidden literal.

* **Type:**
  How far [`punch_in()`](#muvid.footage.look.punch_in) zooms by default. A *slight* move

### *exception* muvid.footage.look.LookError

Bases: [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError)

A look could not be compiled. Carries what to do about it.

### *class* muvid.footage.look.LookFragment(fragment, , time_varying)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

A compiled filter chain that remembers whether it READS THE CLOCK.

A plain `str` in every way that matters — it splices, compares, serialises
and JSON-encodes identically, so nothing downstream needs to know it exists —
that additionally answers `time_varying`. That is what lets
[`punch_in_cuts()`](#muvid.footage.look.punch_in_cuts) set
[`muvid.footage.edl.EdlEntry.look_time_varying`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry.look_time_varying) *from the fragment*
instead of hardcoding a value beside the call that produced it, and what lets
[`chain()`](#muvid.footage.look.chain) combine two fragments without either caller re-deriving the
answer.

A subclass rather than a `(str, bool)` pair because the fragment is already
a wire value: the EDL field is a string, the MCP reply is a string, and the
`looks` API returns a string. Changing that shape would push a rename table
into every consumer to carry one bit that only muvid’s own compilers can
know.

**The bit does not survive a round trip, and is not meant to.** `str`
operations return plain `str`, and JSON has no place to put it — the
durable home is the EDL entry’s own field, which is exactly why muvid#73 put
it there. [`is_time_varying()`](#muvid.footage.look.is_time_varying) reads a fragment of either kind, treating a
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

### muvid.footage.look.TIME_VARYING_EFFECTS *= frozenset({'motion'})*

`looks` effects whose compiled fragment READS THE FILTER CLOCK, so a cut
carrying one is affected by muvid#73’s restart on a blended boundary.

\*\*Pinned here rather than derived from `looks`, for the same reason
[`muvid.footage.edl.LOOK_FILTERS`](muvid.footage.edl.html.md#muvid.footage.edl.LOOK_FILTERS) is\*\* — a new `looks` effect must be a
decision someone records, not a silent change to what muvid warns about. The
drift test in `tests/test_look_time_varying.py` fails when
`looks.effects()` grows,
which is what forces the classification.

`ImplRef.timeline` is NOT this property and must not be mistaken for it: it
says the implementation supports ffmpeg’s `enable=` option. `motion` — the
one genuinely moving effect — declares `timeline=False`, and every static
grade declares `True`, so reading it would get every row backwards.

### muvid.footage.look.chain(\*fragments)

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
[`is_time_varying()`](#muvid.footage.look.is_time_varying) and the EDL field’s own default — so hand-writing one
half of a chain quietly downgrades only that half’s claim, never the other’s.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`LookFragment`](#muvid.footage.look.LookFragment)]

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

### muvid.footage.look.is_time_varying(fragment)

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

### muvid.footage.look.motion(keyframes, , canvas, fps)

A camera path over the cut, as a filter fragment. `looks` picks the filter.

* **Parameters:**
  * **keyframes** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)) – `(t_seconds, window)` pairs, or `looks.Keyframe`s.
    The window is anything with ``x``/`y`/`w`/`h` as fractions —
    [`muvid.footage.edl.CropWindow`](muvid.footage.edl.html.md#muvid.footage.edl.CropWindow) satisfies that structurally,
    with no adapter, because both packages use `burns.Rect`’s
    convention on purpose.
  * **canvas** – `(width, height)` — the assembler’s delivery canvas.
  * **fps** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – the assembler’s delivery frame rate.
* **Return type:**
  [`LookFragment`](#muvid.footage.look.LookFragment)
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
  [**LookError**](#muvid.footage.look.LookError) – If the path needs something that was not supplied.
* **Return type:**
  [*LookFragment*](#muvid.footage.look.LookFragment)

### muvid.footage.look.punch_in(, canvas, fps, duration_s, zoom=1.12, anchor=(0.5, 0.5), start_s=0.0, end_s=None)

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
  [`LookFragment`](#muvid.footage.look.LookFragment)
* **Returns:**
  One linear ffmpeg filter chain, ready for the `look` field, declaring
  itself **time-varying** (it goes through [`motion()`](#muvid.footage.look.motion)). Put that on the
  entry as `look_time_varying` — [`punch_in_cuts()`](#muvid.footage.look.punch_in_cuts) does — or the
  assembler cannot warn you when the cut borders a transition and the move
  restarts (muvid#73).

A move is a ramp between two windows, and the windows are fractions of the
canvas (see [`motion()`](#muvid.footage.look.motion)). The end window keeps the canvas’s aspect ratio,
which is what lets `zoompan` deliver at the canvas size with no stretch and
no reframing crop.

```pycon
>>> frag = punch_in(canvas=(640, 360), fps=25, duration_s=3.0)
>>> frag.startswith("zoompan=d=1:s=640x360:fps=25:")
True
>>> frag.time_varying
True
```

A pull-out is this move backwards, so it goes through [`motion()`](#muvid.footage.look.motion) with the
windows in the order you want them, rather than a boolean here.

* **Raises:**
  [**LookError**](#muvid.footage.look.LookError) – If `zoom`, the timings or the anchor are out of range.
* **Return type:**
  [*LookFragment*](#muvid.footage.look.LookFragment)

### muvid.footage.look.punch_in_cuts(entries, , canvas, fps, every=2, zoom=1.12, anchor=(0.5, 0.5), offset=0)

Put a punch-in on every `every`-th footage entry — muvid#66’s other half.

The request was for *roughly 2N* punch-ins “evenly redistributed so they
occur about twice as often”, explicitly **not** two extra tacked on the end.
Redistribution is therefore the whole job, and it is a stride over the cuts
rather than a wall-clock interval: cuts are already beat-snapped by the
selector, so a stride lands the moves on musical time for free, where a
seconds-based interval would drift off it.

* **Parameters:**
  * **entries** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)) – validated [`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry) objects.
  * **canvas** – `(width, height)` — the delivery canvas.
  * **fps** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – the delivery frame rate.
  * **every** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – stride. `2` punches every other footage cut; `1` punches all.
  * **zoom** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – passed to [`punch_in()`](#muvid.footage.look.punch_in).
  * **anchor** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float)]) – passed to [`punch_in()`](#muvid.footage.look.punch_in).
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
  fragment via [`is_time_varying()`](#muvid.footage.look.is_time_varying) rather than written as a literal
  `True` beside the `punch_in` call. The two would agree today and the
  literal is the one that would stop agreeing — this is the same reason
  `_edl_json` is a table rather than four `if`s. Without it the
  assembler cannot warn that a punch bordering a transition restarts its
  ramp (muvid#73).

### muvid.footage.look.stylize(look, , canvas, fps, duration_s=None, ffmpeg='ffmpeg', env=None, policy=None)

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
  [`LookFragment`](#muvid.footage.look.LookFragment)
* **Returns:**
  One linear ffmpeg filter chain, ready for the `look` field, declaring
  whether it is time-varying **from the compiled plan** rather than by
  assumption. A grade, a LUT, a posterise is static; two shapes are not,
  and both are reachable from here:
  - a step naming a [`TIME_VARYING_EFFECTS`](#muvid.footage.look.TIME_VARYING_EFFECTS) member — `motion` is one
    of `looks`’ registered effects, so `stylize` can emit exactly the
    `zoompan` ramp [`punch_in()`](#muvid.footage.look.punch_in) does (verified by compiling it);
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
  [**LookError**](#muvid.footage.look.LookError) – If the look cannot be compiled for this binary, this ceiling
      or this clip. The message is `looks`’, which names the remedy.
* **Return type:**
  [*LookFragment*](#muvid.footage.look.LookFragment)
