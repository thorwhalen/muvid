# muvid.renderers.animation

Render strategy: animation — handoff to the `an` package.

We synthesize a minimal `an` scene for this shot’s interval: each
lyric line becomes a dialogue beat for the singing character; the shot’s
environment becomes the entity backdrop.

Lipsync alignment: muvid already owns the SSOT for word timings (the
`lacing` alignment store written by `muvid align`). We build a
`an.audio.WordTimingsLipSync` from those timings and pass it
into `an.orchestrate` so `an` does NOT re-transcribe the same
audio with whisper. Falls back to `an`’s default lipsync provider
when no alignment store exists yet (e.g. user skipped `muvid align`).

Camera: muvid and `an` do not share a camera vocabulary and must not
pretend to. `muvid.schema.ShotSpec.camera` is free prose a director
writes into the script (`**camera**: slow push-in`); `an`’s
`camera.move` is a closed set of named moves, and a name outside it is a
hard refusal at both validate and compile — deliberately, because a camera
move that silently no-ops is the failure it exists to prevent. So the prose
is TRANSLATED here, at the boundary, and never passed through
(muvid#44: this module emitted `move: static`, which `an` has never
implemented, so every animation render failed validate and fell back).

Failure handling: an engine that never ran is not an engine that ran and
refused, and muvid#46 was filed because this module collapsed the two. `an`
states a refusal as *data* (`OrchestratorReport.success is False`), so the old
handling was one `if` that discarded `report.error`, `report.validation`
and `report.verifications` and returned a still image under the shot’s own
filename. The output was wrong rather than absent (a freeze frame reads as a
creative choice), the provenance line recorded the REQUESTED strategy so the
affected shots could not be found afterwards, and `still` can reach
`falaw.generate_image` — a silent degradation that bills, under a gate that
`cost.py` had already told the shot was free. Now: a missing `an` raises
`RendererUnavailable` and the DISPATCHER
degrades and journals it, because the dispatcher is where the provenance line is
written; an `an` that refuses raises
`AnimationRenderError` carrying every finding.

That closed set is not fixed, and muvid declares no `an` floor — `an` is
in no extra, the import below is soft, and a user may have any version.
`hold`/`push_in`/`pull_out`/`zoom_in`/`zoom_out` have always been
there; the four TRANSLATING moves (`pan_left`, `pan_right`, `tilt_up`,
`tilt_down`) arrived with an#109 and shipped in `an` 0.1.65 — on 0.1.64
and below, emitting one of them is muvid#44 again. So
[`an_camera_move()`](#muvid.renderers.animation.an_camera_move) checks the move against the vocabulary the INSTALLED
`an` reports (`an.ir.camera.CAMERA_MOVES`) and degrades to `hold` with
a warning rather than emitting a move that will be refused. That is the floor,
enforced at the only place that can see which `an` is actually there.

### Module Attributes

| [`DFLT_AN_CAMERA_MOVE`](#muvid.renderers.animation.DFLT_AN_CAMERA_MOVE)    | `an`'s spelling of "the camera does not move", and what a direction this table cannot name resolves to.                     |
|-------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| [`AN_CAMERA_MOVE_PHRASES`](#muvid.renderers.animation.AN_CAMERA_MOVE_PHRASES) | muvid prose -> `an` move name — the WHOLE recognised vocabulary, including the several ways a director spells "don't move". |

### Functions

| [`an_camera_move`](#muvid.renderers.animation.an_camera_move)(direction, \*[, known_moves])   | Translate a muvid camera direction into an `an` `camera.move` name.   |
|-------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`render_animation`](#muvid.renderers.animation.render_animation)(ctx, \*[, quality])           | Synthesize a tiny `an` scene for this shot and orchestrate it.        |

### muvid.renderers.animation.AN_CAMERA_MOVE_PHRASES *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)], ...]* *= (('push in', 'push_in'), ('push into', 'push_in'), ('dolly in', 'push_in'), ('zoom in', 'zoom_in'), ('pull out', 'pull_out'), ('pull back', 'pull_out'), ('dolly out', 'pull_out'), ('zoom out', 'zoom_out'), ('pan left', 'pan_left'), ('pan right', 'pan_right'), ('tilt up', 'tilt_up'), ('tilt down', 'tilt_down'), ('crane up', 'tilt_up'), ('crane down', 'tilt_down'), ('static', 'hold'), ('hold', 'hold'), ('locked', 'hold'), ('locked off', 'hold'), ('lock off', 'hold'), ('no movement', 'hold'))*

muvid prose -> `an` move name — the WHOLE recognised vocabulary, including
the several ways a director spells “don’t move”. A direction that matches
nothing here is not silently translated: [`an_camera_move()`](#muvid.renderers.animation.an_camera_move) warns, so a
dropped direction is visible in the run rather than only in the render.

Matching is by WORD, never by substring, and the move that occurs EARLIEST in
the direction wins — the director’s first-written move, not this table’s
declaration order. Ties (two phrases starting at the same word) go to the
longer phrase, so `push into` beats `push in` on specificity rather than
on which line happens to come first. Declaration order is the last
tie-break and decides nothing today.

The values are `an`’s vocabulary and nothing else. They are pinned in CI
against `tests.test_animation_camera`’s recorded snapshot of
`an.ir.camera.CAMERA_MOVES`, and — on a machine that has `an` — against
the live set. A hand-copied vocabulary is exactly how `static` survived
here after `an` tightened the rule.

`an` distinguishes a push (1.0->1.25) from a zoom (1.0->1.5), so the prose
does too: a “push” is the gentler move, “zoom” the emphatic one.

### muvid.renderers.animation.DFLT_AN_CAMERA_MOVE *= 'hold'*

`an`’s spelling of “the camera does not move”, and what a direction this
table cannot name resolves to. Not a guess dressed as a move: an
uninterpretable direction is a reason to leave the camera alone, not to
invent a push-in the director did not ask for. It is also what this module
effectively meant by the invalid `static` it used to emit.

### muvid.renderers.animation.an_camera_move(direction, , known_moves=None)

Translate a muvid camera direction into an `an` `camera.move` name.

Never returns a name the `an` in front of it cannot honour. Two
independent ways that can happen, and each one WARNS rather than passing
silently — a dropped camera direction is the failure muvid#44 was:

- the direction names no move this table knows (“handheld, drifting”), or
- it names one the *installed* `an` does not implement (`pan_left` is
  an#109, released in `an` 0.1.65; on 0.1.60 it is a hard refusal at
  validate and at compile).

`known_moves` is the vocabulary to check against; `None` means “ask the
installed `an`”, and when `an` is absent nothing is narrowed.

```pycon
>>> an_camera_move("slow push-in")
'push_in'
>>> an_camera_move("static")
'hold'
>>> an_camera_move("")
'hold'
>>> an_camera_move("Pan Left across the room")
'pan_left'
```

The director’s first-written move wins, not this table’s order, and a
negated move is refused rather than obeyed:

```pycon
>>> an_camera_move("pan left, then push in")
'pan_left'
>>> an_camera_move("static, no push-in")
'hold'
```

An `an` that cannot do the move gets the no-op instead of a refusal:

```pycon
>>> an_camera_move("pan left", known_moves={"hold", "push_in"})
'hold'
```

A direction naming nothing is not silently dropped:

```pycon
>>> import warnings
>>> with warnings.catch_warnings(record=True) as caught:
...     warnings.simplefilter("always")
...     an_camera_move("handheld, drifting")
'hold'
>>> "handheld, drifting" in str(caught[0].message)
True
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.renderers.animation.render_animation(ctx, , quality='balanced')

Synthesize a tiny `an` scene for this shot and orchestrate it.

Raises `RendererUnavailable` when `an`
is not installed — the dispatcher answers that by rendering a `still` and
journalling that it did. Raises
`AnimationRenderError` when `an` IS
installed and refuses the scene, because that is a bug in what muvid
synthesized and a still image is a wrong answer, not a lesser one (muvid#46).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
