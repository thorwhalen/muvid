# muvid.footage.align

Align a set of footage clips to the song — a thin wrapper over `mixing.audio`.

Each clip is a video file; its audio track (the different-device capture of the song) is
what we align. `mixing.audio.align_clips_to_reference` loads each clip’s audio via
pydub/ffmpeg (so a video path works directly — no separate extraction, no moviepy), and
returns offsets + a scale-invariant confidence + coverage clamped to the song timeline.

muvid does not estimate offsets itself — the estimator lives in `mixing`, which is
where the multi-device primitive and its measurements belong — but muvid is the one that
turns a measurement into a cut, so muvid is where “we are not sure” stops being a number
and becomes a refusal (muvid#59). The **verdict** that makes that possible is
[`vouches_for()`](muvid.footage.edl.md#muvid.footage.edl.vouches_for), re-exported here beside the aligner that applies
it; it is defined in [`muvid.footage.edl`](muvid.footage.edl.md#module-muvid.footage.edl), beside the record it judges and the gate
([`validate_edl()`](muvid.footage.edl.md#muvid.footage.edl.validate_edl)) that enforces it.

### Module Attributes

| [`ALIGN_SAMPLE_RATE`](#muvid.footage.align.ALIGN_SAMPLE_RATE)   | Analysis sample rate for alignment (mono).                                                                                                                                                                                                     |
|----------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`WINDOW_PARAMETERS`](#muvid.footage.align.WINDOW_PARAMETERS)   | Estimator parameters [`align_footage()`](#muvid.footage.align.align_footage) refuses to forward, because changing them changes what [`MIN_SUPPORT`](muvid.footage.edl.md#muvid.footage.edl.MIN_SUPPORT) MEANS. |

### Functions

| [`align_footage`](#muvid.footage.align.align_footage)(song_path, clips, \*[, ...])   | Align `clips` (`[(clip_id, clip_path), ...]`) to the song at `song_path`.      |
|-----------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`vouches_for`](#muvid.footage.align.vouches_for)(\*, confidence, support[, ...])  | Does the aligner vouch for this offset? The ONE place that verdict is reached. |

### muvid.footage.align.ALIGN_SAMPLE_RATE *= 16000*

Analysis sample rate for alignment (mono). 16 kHz is plenty for offset precision.

### muvid.footage.align.WINDOW_PARAMETERS *= ('window_s', 'hop_s')*

Estimator parameters [`align_footage()`](#muvid.footage.align.align_footage) refuses to forward, because changing them
changes what [`MIN_SUPPORT`](muvid.footage.edl.md#muvid.footage.edl.MIN_SUPPORT) MEANS. Per mixing#43 (mixing PR
#51), an explicit `window_s` wider than a clip can hold a second, independent look
at is refused by `mixing` itself with a typed `WindowTooWideForClip`, so that
direction no longer reaches muvid as a silent `support=None`. (Where exactly that
bound falls is `mixing`’s call to make and to change; muvid does not restate it
here.) Narrowing the window is not caught by that refusal and still
deflates every support — measured across eleven clip lengths, correct alignments read
0.38-0.70 at a 20 s window and 0.00-1.00 at a 4 s one — so correct alignments fall
under the threshold instead. Refusing here regardless of what `mixing` does about
the wide-window case keeps the two packages from disagreeing about it later, and keeps
the calibration decision (moving `MUVID_FOOTAGE_MIN_SUPPORT` if the window moves)
something a person makes out loud rather than a side effect of a keyword — the
refusal at `mixing`’s own entry point is a different failure mode for a different
caller, not a substitute for muvid’s own gate.

(Since `mixing>=0.0.48`, passing `window_s` alone also pairs it with
`hop = window/2`, so the two are one knob at the estimator too. Another reason not
to accept half of it through a keyword.)

Refused rather than merely documented for the reason the rest of this package refuses
things: an argument that quietly turns a safety check off is worse than one that is
not accepted at all. Tuning the window is legitimate — it just has to move
`MUVID_FOOTAGE_MIN_SUPPORT` with it, which is a decision someone has to make out
loud rather than a side effect of a keyword.

### muvid.footage.align.align_footage(song_path, clips, , song_duration=None, sample_rate=16000, \*\*estimator_kwargs)

Align `clips` (`[(clip_id, clip_path), ...]`) to the song at `song_path`.

Returns one [`FootageAlignment`](muvid.footage.edl.md#muvid.footage.edl.FootageAlignment) per clip, keyed by `clip_id`
— including clips that do not overlap the song, which carry `overlaps=False` rather
than being omitted, and clips the aligner could not vouch for, which carry
`reliable=False` rather than being omitted. Nothing may vanish from the record just
because it matched badly; what a bad match loses is the right to be cut to silently
([`validate_edl()`](muvid.footage.edl.md#muvid.footage.edl.validate_edl) is where that is enforced).

\*\*A verdict already on disk is never re-judged, and re-aligning is how you re-judge
it.\*\* A `reliable=True` written by muvid 0.0.52 or earlier was reached against the
UNGRADED support scale, where the muvid#59 shoot’s own correct offsets scored 0.42
and 0.46 — numbers the current threshold refuses. Those records keep their verdicts
deliberately (`from_dict()` derives only an
ABSENT verdict), because a stored verdict is a measurement someone’s project already
depends on, not a value to reinterpret under a scale it never saw. Calling this
function again is the supported way to get a verdict on today’s terms.
Heavy deps (mixing.audio → numpy/scipy/pydub) are imported lazily here so importing the
genre stays light.

* **Parameters:**
  * **song_path** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The clean master every clip is aligned to.
  * **clips** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)]) – `(clip_id, clip_path)` pairs; a video path works directly.
  * **song_duration** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – The song timeline length; probed from `song_path` when omitted.
  * **sample_rate** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Analysis sample rate (mono).
  * **\*\*estimator_kwargs** – 

    Passed straight to `mixing.audio.align_clips_to_reference`
    — the seam for the windowed-consensus estimator’s parameters (muvid#59).
    Forwarding rather than enumerating keeps muvid out of the business of
    tracking `mixing`’s estimator vocabulary, and an argument the installed
    `mixing` does not accept raises [`TypeError`](https://docs.python.org/3/builtins/exceptions.html#TypeError) from `mixing` naming
    it. That failure is the point: a window parameter silently ignored is a
    caller believing they tuned an estimator that never saw the value.

    **The window parameters are the exception and are REFUSED** — see
    [`WINDOW_PARAMETERS`](#muvid.footage.align.WINDOW_PARAMETERS). They do not tune the estimator so much as re-scale
    the gate that reads it, and muvid refuses them at this entry point regardless
    of whether `mixing` itself would also refuse or silently answer.
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`FootageAlignment`](muvid.footage.edl.md#muvid.footage.edl.FootageAlignment)]

### muvid.footage.align.vouches_for(, confidence, support, margin=None, window_s=None)

Does the aligner vouch for this offset? The ONE place that verdict is reached.

Lives here, beside the record it judges and the gate that enforces it, rather than
with the aligner: `FootageAlignment.from_dict()` has to reach it to derive a
verdict for a record written before the field existed, and a module that owns a
dataclass should not need its own consumer to interpret one.

`support` (how much of the clip agrees) decides when the aligner measured it,
because it is the only one of the two numbers that can tell a clear winner from a
coin flip — see `FootageAlignment.support`.

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
`MIN_SUPPORT`. Measured on six pure-noise clips at a fitted 6.67 s window,
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

So `MIN_CONFIDENCE` was deliberately NOT re-tuned. There is no value to tune it
to: raising it past the noise floor refuses correct footage, and a number chosen to
make a noise-floor test pass would hide that behind a green run. That fallback is now
reached only by a clip too short to vote at all, which is the narrowest it has been.

**Two numbers, asking different questions, and BOTH are required.**
`MIN_SUPPORT` asks whether enough of the clip’s evidence reached this offset;
`MIN_MARGIN` asks whether that evidence prefers this offset over every other
one it considered. Neither subsumes the other, and the measurement that proves it is
in `MIN_SUPPORT`’s note: margin alone scores 24/24 on real material where the
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
