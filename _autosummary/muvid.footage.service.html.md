# muvid.footage.service

The footage operations — one function per thing you can do to a music-video project.

\*\*This module is the single source of truth for the `music_video` genre’s operations.\*\*
Every surface reaches them from here and lists them from `FOOTAGE_OP_SPECS`, never
again by hand:

- the MCP connector ([`muvid.mcp.footage_tools`](muvid.mcp.footage_tools.html.md#module-muvid.mcp.footage_tools), [`muvid.mcp.scoring_tools`](muvid.mcp.scoring_tools.html.md#module-muvid.mcp.scoring_tools))
  resolves `project_id` to the caller’s workspace project, calls the function, and
  turns a [`FootageError`](muvid.footage.errors.html.md#muvid.footage.errors.FootageError) into a `ToolError`;
- a host that serves the genre (reelee’s studio) reads the catalogue nw holds —
  `nw.genre_ops("music_video")`, registered from `FOOTAGE_OP_SPECS` by
  `muvid.genre_music_video` — and calls the same functions on a host-placed
  `muvid.Project`’s `footage`.

Contract of every operation here:

- the first argument is a [`MusicVideoFootageProject`](muvid.footage.workspace.html.md#muvid.footage.workspace.MusicVideoFootageProject)
  (`fp`), everything else is keyword-only and JSON-able;
- the result is a JSON-able `dict` (no `project_id` — the transport knows which
  project it opened);
- a refusal raises [`FootageError`](#muvid.footage.service.FootageError) with a message naming the next action — never a
  transport’s error type, never a raw traceback for a caller mistake;
- the docstring is **model-facing**: it is what an assistant reads to decide whether and
  how to call the operation, so it says what the operation changes and what to read in
  its reply.

Media arrives as a LOCAL file (`set_song(fp, path=...)`, `add_clip(fp, path=...)`):
fetching a URL is the MCP connector’s business, streaming an upload the host’s. Either
way the file is COPIED into the project, so the caller may delete it afterwards.

**Import-light by design** (stdlib + [`muvid.footage.edl`](muvid.footage.edl.html.md#module-muvid.footage.edl) at module top): a host
imports the genre module, which imports this one to read the operations’ signatures, and
must not pay for numpy/ffmpeg/fastmcp to build a catalogue.

The named-edit operations (`save_edit`, `set_cut`, `split_cut`, `merge_cut`,
`replace_edit`, `render(edit_id=...)`) are what make “change cut 7 and render again”
possible: before them, an EDL was persisted only inside a render’s `meta.json`. An edit
lives at `footage/edits/<edit_id>.json` and every change to it goes through
`validate_edl` — structurally, with `allow_unreliable=True` (an edit is a plan; the
trust refusal belongs where the encode does, in [`render()`](#muvid.footage.service.render)).

### Module Attributes

| [`EDL_OPTIONAL_FIELDS`](#muvid.footage.service.EDL_OPTIONAL_FIELDS)   | Every optional [`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry) field [`edl_json()`](#muvid.footage.service.edl_json) carries, and how to render it.   |
|------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

### Functions

| [`status`](#muvid.footage.service.status)(fp)                                       | Where the music video stands: the song, the videos and where each sits on the song, the saved edits, the finished videos, and the next useful step.                                                                          |
|---------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`set_song`](#muvid.footage.service.set_song)(fp, \*, path[, ext, filename, ...])     | Set the project's song — the clean master every video is aligned to and whose audio the finished video uses.                                                                                                                 |
| [`add_clip`](#muvid.footage.service.add_clip)(fp, \*, path[, name, filename, ...])    | Add one footage video — a recording of the song — to the project.                                                                                                                                                            |
| [`remove_clip`](#muvid.footage.service.remove_clip)(fp, \*, clip_id)                     | Remove one footage video from the project — its stored file and its entry.                                                                                                                                                   |
| [`align`](#muvid.footage.service.align)(fp)                                        | Find where each video sits on the song by listening to its own audio, and save it.                                                                                                                                           |
| [`set_offset`](#muvid.footage.service.set_offset)(fp, \*, clip_id, offset_s)            | Place one video on the song BY HAND: the song time at which the video's own first frame plays (negative = the video starts before the song does).                                                                            |
| [`clear_offset`](#muvid.footage.service.clear_offset)(fp, \*, clip_id)                    | Forget where I placed this video: remove a hand-declared offset, so the next `align` measures the clip by its audio instead.                                                                                                 |
| [`timeline`](#muvid.footage.service.timeline)(fp)                                     | Which videos cover which spans of the song (overlaps shown), from the saved alignment — the map for choosing what to cut to.                                                                                                 |
| [`beat_grid`](#muvid.footage.service.beat_grid)(fp)                                    | The song's beat grid — tempo and beat instants on the song timeline — without looking at the footage.                                                                                                                        |
| [`peaks`](#muvid.footage.service.peaks)(fp, \*[, n])                               | The song's waveform, to draw under the timeline: `n` equal slices of the song, each the loudest moment in it (mono), scaled so the loudest slice is 1.0.                                                                     |
| [`beat_signals`](#muvid.footage.service.beat_signals)(fp, \*[, source, max_points])       | Where the beat is in the song or in one video — CONTINUOUS signals, to look at, threshold and bend, not only beat instants.                                                                                                  |
| [`filmstrips`](#muvid.footage.service.filmstrips)(fp)                                   | Every video's filmstrip — thumbnails to draw each camera's lane.                                                                                                                                                             |
| [`filmstrip`](#muvid.footage.service.filmstrip)(fp, \*, clip_id)                       | One video's filmstrip (the same record `filmstrips` gives per clip, with its `clip_id` and `fps`).                                                                                                                           |
| [`score`](#muvid.footage.service.score)(fp, \*[, hop_s, metrics, should_cancel])   | Look at the footage: score every placed video, on the song's own timeline — picture quality and how its movement sits on the beat — and save the curves.                                                                     |
| [`scores`](#muvid.footage.service.scores)(fp, \*[, clip_id, metrics, max_points])   | The saved footage curves — for the lanes under each video, and for inspection.                                                                                                                                               |
| [`strategies`](#muvid.footage.service.strategies)([fp])                                 | The ways to cut on offer — the selection strategies `propose_edit` accepts (`weighted` reads the footage scores; the rest use only the alignment).                                                                           |
| [`propose_edit`](#muvid.footage.service.propose_edit)(fp, \*[, strategy, preset, ...])    | Cut it for me: build an edit of the whole song — or of `span` (`[start_s, end_s]`, the part of the song the video covers) — from the placed videos, and (by default) save it as a named edit, without rendering anything.    |
| [`save_edit`](#muvid.footage.service.save_edit)(fp, \*, edl[, name, how_made, ...])    | Save a cut list as a new named edit.                                                                                                                                                                                         |
| [`edits`](#muvid.footage.service.edits)(fp)                                        | The saved edits, oldest first: each one's `edit_id`, `name`, how it was made, how many cuts it has, and `problem` — why it would not validate against the current alignment (`null` when it does).                           |
| [`get_edit`](#muvid.footage.service.get_edit)(fp, \*, edit_id)                        | One saved edit: its cut list (`edl`, every span of the song, gaps as `clip_id: null`), its name and history, and a `coverage` report.                                                                                        |
| [`replace_edit`](#muvid.footage.service.replace_edit)(fp, \*, edit_id, edl)               | Replace a saved edit's whole cut list — the power tool for rewriting an edit at once.                                                                                                                                        |
| [`set_cut`](#muvid.footage.service.set_cut)(fp, \*, edit_id, index[, clip_id, ...])  | Change one cut of a saved edit (`index` is its position in `get_edit`'s edl).                                                                                                                                                |
| [`split_cut`](#muvid.footage.service.split_cut)(fp, \*, edit_id, at_s)                 | Split the cut playing at song time `at_s` into two cuts of the same video.                                                                                                                                                   |
| [`merge_cut`](#muvid.footage.service.merge_cut)(fp, \*, edit_id, index[, into])        | Join cut `index` to its neighbour: the neighbour (`into` "previous" or "next") takes over its span, so the neighbour's video must cover it.                                                                                  |
| [`set_span`](#muvid.footage.service.set_span)(fp, \*, edit_id, start_s, end_s)        | Choose which part of the song the video covers — where it starts and ends.                                                                                                                                                   |
| [`rename_edit`](#muvid.footage.service.rename_edit)(fp, \*, edit_id, name)               | Give an edit a new name — what the edit picker and the renders made from it show.                                                                                                                                            |
| [`looks`](#muvid.footage.service.looks)([fp])                                      | The looks a cut can take — camera moves (punch in, slow push, slow pull, pans) and grades (vivid, black and white, posterize, cartoon) — each with its `params_schema`.                                                      |
| [`undo_edit`](#muvid.footage.service.undo_edit)(fp, \*, edit_id)                       | Undo the last change to a saved edit (a cut changed, split, joined, the span, a whole replacement — by a person or by the assistant).                                                                                        |
| [`redo_edit`](#muvid.footage.service.redo_edit)(fp, \*, edit_id)                       | Redo the change `undo_edit` last took back.                                                                                                                                                                                  |
| [`delete_edit`](#muvid.footage.service.delete_edit)(fp, \*, edit_id)                     | Delete a saved edit.                                                                                                                                                                                                         |
| [`render`](#muvid.footage.service.render)(fp, \*, edit_id[, canvas, ...])           | Make the video: render a saved edit onto the canvas, over the clean song.                                                                                                                                                    |
| [`renders`](#muvid.footage.service.renders)(fp)                                      | The finished videos, newest first: each one's `render_id`, speakable `ref`, the `edit_id` it was made from, its `label`, canvas, `ok`, the number of `warnings`, and `artifact_id` to play it by when the project is hosted. |
| [`editor_document`](#muvid.footage.service.editor_document)(fp)                              | The project as lacing-native standoff annotations, for a multitrack editor.                                                                                                                                                  |
| [`assemble`](#muvid.footage.service.assemble)(fp, \*[, strategy, edl, preset, ...])   | Assemble and render a music video — auto (a `strategy`) or an explicit `edl`.                                                                                                                                                |
| [`declared_alignment`](#muvid.footage.service.declared_alignment)(clip_id, offset_s, \*, ...)   | The alignment record of a DECLARED offset — coverage computed, nothing measured.                                                                                                                                             |
| [`edl_from_annotations`](#muvid.footage.service.edl_from_annotations)(fp, \*, annotations)        | The editor's DECISION-tier annotations turned back into a cut list (`edl`), ready for `save_edit` / `replace_edit` — a faithful read, not a re-selection.                                                                    |
| [`edl_json`](#muvid.footage.service.edl_json)(e)                                      | One EDL entry as JSON — full precision (it must feed back verbatim), gaps as null.                                                                                                                                           |
| [`coverage_report`](#muvid.footage.service.coverage_report)(entries, aligns, song_dur, \*)   | What the song's timeline looks like under `entries` — covered, weak, MISSING.                                                                                                                                                |
| [`exclusion_note`](#muvid.footage.service.exclusion_note)(x)                                | One `warnings` line per span the auto path set aside (muvid#88).                                                                                                                                                             |
| [`assemble_refusal`](#muvid.footage.service.assemble_refusal)(entries, aligns, song_dur, ...) | `None` if rendering this edit would go ahead; the refusal if it would not — put to the GATE rather than re-implemented here.                                                                                                 |
| [`resolve_canvas`](#muvid.footage.service.resolve_canvas)(fp, canvas)                       | The render canvas: an explicit per-render override, else the project's.                                                                                                                                                      |
| [`refresh_cover`](#muvid.footage.service.refresh_cover)(fp)                                | Take the project's cover frame again — from the newest render, else from the first clip — and register it.                                                                                                                   |
| [`grab_cover_frame`](#muvid.footage.service.grab_cover_frame)(video, dest)                    | Write one JPEG frame of `video` to `dest` — `COVER_AT_FRACTION` of the way in, `COVER_WIDTH` wide.                                                                                                                           |
| [`import_render`](#muvid.footage.service.import_render)(fp, \*, path, render_id[, ...])    | Bring a video finished ELSEWHERE into the project as a render (the importer's).                                                                                                                                              |
| [`frame_size`](#muvid.footage.service.frame_size)(video)                                | `[width, height]` of a video as DISPLAYED (a ±90° rotation swaps them).                                                                                                                                                      |
| [`frame_rate`](#muvid.footage.service.frame_rate)(video)                                | A video's average frame rate (frames per second), or `None` if unreadable.                                                                                                                                                   |
| [`public_render`](#muvid.footage.service.public_render)(fp, meta)                          | A render record as a HOSTED surface may return it: `video` made relative to the project (`renders/<id>/final.mp4`) — never an absolute server path; play it by its `artifact_id`.                                            |
| [`require_scorable`](#muvid.footage.service.require_scorable)(fp)                             | The alignments a scoring run would use; refuses without a song or an alignment.                                                                                                                                              |
| [`run_scoring`](#muvid.footage.service.run_scoring)(fp, \*[, hop_s, metrics, ...])       | Score every aligned clip and persist the tensor (the engine behind [`score()`](#muvid.footage.service.score), with the job hooks a background runner passes).                                                 |

### Classes

| [`OpSpec`](#muvid.footage.service.OpSpec)(name, title, effect[, runs, hide, ...])   | One operation's catalogue row: the function (by `name` in this module), a plain-language `title` (it becomes a button and a command title), what it does to the project (`effect`: read | write | render | destroy) and how a host runs it (`runs`: now | job).   |
|---------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

### Exceptions

| [`FootageError`](#muvid.footage.service.FootageError)     | An operation refused — the message says why and what to do next.   |
|-------------------------------------------------------------------|--------------------------------------------------------------------|
| [`FootageCancelled`](#muvid.footage.service.FootageCancelled) | An operation stopped between steps because its host asked it to.   |

### muvid.footage.service.EDL_OPTIONAL_FIELDS *= (('transition', <function <lambda>>, None), ('crop', <function <lambda>>, None), ('crop_end', <function <lambda>>, None), ('look', <class 'str'>, None), ('look_time_varying', <class 'bool'>, False), ('look_spec', <class 'dict'>, None))*

Every optional [`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry) field [`edl_json()`](#muvid.footage.service.edl_json) carries,
and how to render it. **The list is the round trip.** `_as_entry` reads all of
these back by name, so a field missing from here is a direction the caller gave, the
renderer honoured, and the returned/persisted edit does not contain. Each row is
`(field, render, absent)`, where `absent` is the value that means “omit this
key” — a column rather than a hardcoded `None` because `look_time_varying`
(muvid#73) is a boolean whose absent value is `False`.

### *exception* muvid.footage.service.FootageCancelled

Bases: [`Exception`](https://docs.python.org/3/builtins/exceptions.html#Exception)

An operation stopped between steps because its host asked it to.

### *exception* muvid.footage.service.FootageError

Bases: [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError)

An operation refused — the message says why and what to do next.

### *class* muvid.footage.service.OpSpec(name, title, effect, runs='now', hide=(), host_params=(), max_upload_bytes=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One operation’s catalogue row: the function (by `name` in this module), a
plain-language `title` (it becomes a button and a command title), what it does to
the project (`effect`: read | write | render | destroy) and how a host runs it
(`runs`: now | job). `hide` names parameters a transport fills that a caller
must not (`duration_s` — a probed fact, not a claim a caller makes; `annotate` —
a transport’s hook).

Host-agnostic data: `muvid.genre_music_video` turns these into `nw.GenreOp`
rows, and [`muvid.mcp`](muvid.mcp.html.md#module-muvid.mcp) derives its footage tools from the same names.

#### host_params *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]* *= ()*

an upload’s
server-side `path` and original `filename`. Never in a client’s schema.

* **Type:**
  Parameters only the HOST supplies (`nw.GenreOp.host_params`)

#### max_upload_bytes *: [int](https://docs.python.org/3/builtins/functions.html#int) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

The op’s own ceiling for a host-streamed upload (`nw.GenreOp.max_upload_bytes`).

### muvid.footage.service.add_clip(fp, , path, name='', filename='', clip_id='', ext='', duration_s=None)

Add one footage video — a recording of the song — to the project.

The file arrives from the host (an upload): `path` is where the host put it and
`filename` its original name; it is copied into the project. `name` is what the
video is called on screen (default: the original file name without its extension). `clip_id` fixes the id (default: a fresh one); an id already in the
project is refused. Size-, duration- and count-capped.

Run `align` afterwards: a new clip has no place on the song until then. Returns
the `clip_id`, its `name` and `duration` (and `artifact_id` when hosted).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.align(fp)

Find where each video sits on the song by listening to its own audio, and save it.

Returns each clip’s offset, a confidence in [0,1], its `support` (the fraction of
the clip that agrees on that offset, `null` when the aligner took a single
whole-clip measurement), and its coverage of the song, plus these lists:

- `low_confidence` — clips that matched weakly, for reporting;
- `unreliable` — clips whose offset the aligner will NOT vouch for. These stay in
  the project and stay addressable, and the auto path simply prefers other clips
  over them: a span another clip covers goes to that clip, and a span only an
  unreliable clip covers is left as a gap and reported in `coverage.excluded`
  (muvid#88). Rendering still REFUSES an explicit edit that cuts to one, and still
  refuses an auto edit when NO clip is trustworthy, unless called with
  `allow_unreliable=true` — because a wrong offset does not fail, it renders a
  video out of sync with the song (muvid#59). Re-align, place the clip by hand
  (`set_offset`), accept the smaller edit, or opt in deliberately;
- `no_consensus` — clips too short to be put to a vote at all (under about 4.5 s).
  **A clip in this list can be marked reliable and still be wrong**, and no other
  field will say so: its offset rests on one measurement, judged by a confidence
  score that does not rank correctness in this band — measured on the muvid#59
  shoot, the WRONG offset scored highest of three (0.834 against 0.566 and 0.621),
  and on a repeating fixture a 4.4 s clip landing 8 s out is vouched at 0.381.
  Nothing is refused on this basis, because refusing would take the correct short
  clips with it. So if a short clip looks out of sync in the render, this list is
  the first place to look — and muvid#91 is where that trade-off is being decided.

A clip whose offset a person DECLARED (`set_offset`) is ALWAYS left alone — a
person placed it, usually because the aligner got it wrong (muvid#59) — and is named
in `kept_declared`. To have one measured again, `clear_offset` it first. Every
measured record says `source: "measured"`.

Run this after adding/removing clips and before cutting.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.assemble(fp, , strategy='', edl=None, preset='', weights=None, config=None, canvas='', allow_unreliable=False, edit_id=None, label='', annotate=None, span=None, should_cancel=None)

Assemble and render a music video — auto (a `strategy`) or an explicit `edl`.

The engine behind [`render()`](#muvid.footage.service.render) and the MCP `assemble_music_video` tool (whose
docstring is the full caller-facing contract). Validation is the ONE gate
(`validate_edl`), with the trust refusal ON unless `allow_unreliable`. Writes
`renders/<render_id>/final.mp4` + `meta.json` and returns the meta.

`edit_id` / `label` are recorded in the meta; `annotate(render_id, ref_n)` is
the transport’s hook for keys only it can fill (the MCP download claim) — merged into
the meta before it is written. `span` (an explicit `edl`’s part of the song)
renders only that stretch: the video AND the song cut to it, the song faded out over
`TAIL_FADE_S` when the span ends before the song does.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.assemble_refusal(entries, aligns, song_dur, canvas)

`None` if rendering this edit would go ahead; the refusal if it would not —
put to the GATE rather than re-implemented here.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### muvid.footage.service.beat_grid(fp)

The song’s beat grid — tempo and beat instants on the song timeline — without
looking at the footage.

Computed once on the song (never per clip — clips map to it through their offsets)
and cached under the project keyed on the song’s content hash, so the second call is
a file read; a project that has been scored is served from that run instead.
`source` says which (`computed` | `cache` | `scores`).

Needs the `scoring` extra (librosa); without it the refusal names the install.
Needs a song; no alignment is required.

Returns `tempo_bpm`, `beats` (seconds, ascending), `n_beats`,
`song_duration` and `source`, and for numbering bars: `downbeats` (the beats
that start a bar), `downbeats_source` — `measured` (the estimator found them),
`derived` (the beat phase carrying the most onset energy, `beats_per_bar` beats
to a bar — muvid.montage’s rule) or `first_beat` (no onset energy to vote with, so
bars start on the first beat) — `beats_per_bar` and `bar_of_beat` (each beat’s
bar number, 1 for the first bar, 0 for a pickup before it).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.beat_signals(fp, , source='song', max_points=1000)

Where the beat is in the song or in one video — CONTINUOUS signals, to look at,
threshold and bend, not only beat instants.

`source` is `"song"` or a clip id. The song gets its sound (`audio_onset`: the
onset envelope the beat grid is estimated from). A video gets its own soundtrack’s
`audio_onset` when it has one, and two visual signals: `motion` (how much the
people in the picture move, the camera’s own move taken out) and `visual_impact`
(moves stopping dead and turning — the visual beat).

Each signal is in the media’s OWN time: sample `i` is at `t0 + i * hop_s` s of the
song, or of the clip (song time `offset + t`). Values are unnormalised, with
`min`, `max` and `p99` beside them; `None` is a sample that was not measured.
`max_points` pools each signal to at most that many samples by their maximum, so
a peak survives (0 = every sample; an editor drawing it wants that).

Measured once per media and kept (a video’s first call reads every frame and takes
tens of seconds; a second call for the same video waits for the first rather than
measuring again). Needs the `scoring` extra.

Returns `{source, kind: audio|video, duration_s, tempo_bpm, beats, signals:
{name: {name, label, domain, t0, hop_s, n, min, max, p99, values}}}` — `beats`
and `tempo_bpm` are the soundtrack’s (`[]` / `None` without one).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.clear_offset(fp, , clip_id)

Forget where I placed this video: remove a hand-declared offset, so the next
`align` measures the clip by its audio instead.

Only a DECLARED offset can be forgotten (a measured one is replaced by aligning
again); an unknown clip, or one with no declared offset, is refused. Until `align`
runs again the clip has no place on the song, and footage scores made with the old
offset are dropped.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.coverage_report(entries, aligns, song_dur, , excluded=(), span=None)

What the song’s timeline looks like under `entries` — covered, weak, MISSING.

Pass only FOOTAGE entries: a gap renders fill, and filled is not covered. Uncovered
audio is named with explicit start/end times; a span whose only footage is weakly
aligned is listed with the numbers that make it weak; `excluded` (muvid#88) names
spans the auto path gave up because only an unvouched clip covered them.
`span` (a trimmed edit’s `(start, end)`) bounds what counts as uncovered.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.declared_alignment(clip_id, offset_s, , clip_duration, song_duration)

The alignment record of a DECLARED offset — coverage computed, nothing measured.

`confidence` is 1.0 and `reliable` True because a person vouched for the offset;
`support`/`margin` stay `None` because no vote was held. `source` says so.

* **Return type:**
  [`FootageAlignment`](muvid.footage.edl.html.md#muvid.footage.edl.FootageAlignment)

```pycon
>>> a = declared_alignment("c1", -8.5, clip_duration=260.0, song_duration=249.6)
>>> a.coverage, a.overlaps, a.source
((0.0, 249.6), True, 'declared')
```

### muvid.footage.service.delete_edit(fp, , edit_id)

Delete a saved edit. Videos already rendered from it are kept (they still name
the edit they came from). An unknown `edit_id` is refused, naming the edits.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.editor_document(fp)

The project as lacing-native standoff annotations, for a multitrack editor.

One tier per clip (its `clip-alignment/v1` and, once scored, its
`clip-score-track/v1` curves) plus a `DECISION` tier holding the current default
proposal as `music-video-edl/v1` entries — everything referenced to the song by
content hash, on one shared song-time axis. Needs the `editor` extra (lacing) and
an alignment.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.edits(fp)

The saved edits, oldest first: each one’s `edit_id`, `name`, how it was made,
how many cuts it has, and `problem` — why it would not validate against the
current alignment (`null` when it does). `unreliable` names clips it cuts to
whose offsets rendering would refuse.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.edl_from_annotations(fp, , annotations)

The editor’s DECISION-tier annotations turned back into a cut list (`edl`),
ready for `save_edit` / `replace_edit` — a faithful read, not a re-selection.
Annotations referencing another song are refused (muvid#35).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.edl_json(e)

One EDL entry as JSON — full precision (it must feed back verbatim), gaps as null.

Optional fields are emitted ONLY when set ([`EDL_OPTIONAL_FIELDS`](#muvid.footage.service.EDL_OPTIONAL_FIELDS)), which keeps
every existing `renders/*/meta.json` byte-identical and the render -> edit ->
re-render round trip (muvid#21 item 3) exact.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.exclusion_note(x)

One `warnings` line per span the auto path set aside (muvid#88).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.footage.service.filmstrip(fp, , clip_id)

One video’s filmstrip (the same record `filmstrips` gives per clip, with its
`clip_id` and `fps`).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.filmstrips(fp)

Every video’s filmstrip — thumbnails to draw each camera’s lane.

Per clip: sprite sheets of `frame_w` x `frame_h` frames (`cols` x `rows` to
a sheet, left to right then down), sampled at `fps` frames per second of the
CLIP’s own time — frame `i` is the clip at `i / fps` s, which sits at song time
`offset + i / fps`. Each sheet is an `artifact_id` (when the project is hosted),
with its `first_frame` and `n_frames`. Made once per clip and kept; a clip that
has none yet takes a few seconds the first time.

Returns `{fps, clips: {clip_id: {duration_s, n_frames, frame_w, frame_h, sheets:
[{artifact_id, cols, rows, first_frame, n_frames}]}}}`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.frame_rate(video)

A video’s average frame rate (frames per second), or `None` if unreadable.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### muvid.footage.service.frame_size(video)

`[width, height]` of a video as DISPLAYED (a ±90° rotation swaps them).

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)]

### muvid.footage.service.get_edit(fp, , edit_id)

One saved edit: its cut list (`edl`, every span of the song, gaps as
`clip_id: null`), its name and history, and a `coverage` report. Cut indexes in
`set_cut`/`merge_cut` refer to positions in this `edl`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.grab_cover_frame(video, dest)

Write one JPEG frame of `video` to `dest` — `COVER_AT_FRACTION` of the
way in, `COVER_WIDTH` wide. The cover of every hosted muvid production.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.footage.service.import_render(fp, , path, render_id, label='', edit_id='')

Bring a video finished ELSEWHERE into the project as a render (the importer’s).

Copied to `renders/<render_id>/final.mp4` with a meta that says it was imported:
`edit_id` names the saved edit it was cut from (when known), and there are no
`checks` — muvid did not make it and does not claim to have verified it.
Idempotent: the same bytes under the same id change nothing.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.looks(fp=None)

The looks a cut can take — camera moves (punch in, slow push, slow pull, pans)
and grades (vivid, black and white, posterize, cartoon) — each with its
`params_schema`. Give one to `set_cut` as `look={"name": ..., **params}`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.merge_cut(fp, , edit_id, index, into='previous')

Join cut `index` to its neighbour: the neighbour (`into` “previous” or
“next”) takes over its span, so the neighbour’s video must cover it. The joined
cut keeps the neighbour’s video, framing and look. Returns the changed edit.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.peaks(fp, , n=2000)

The song’s waveform, to draw under the timeline: `n` equal slices of the song,
each the loudest moment in it (mono), scaled so the loudest slice is 1.0.

Returns `{duration_s, n, peaks: [0..1, ...]}`; slice `i` covers song time
`i * duration_s / n` to `(i + 1) * duration_s / n`. Kept per song and `n`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.propose_edit(fp, , strategy='', preset='', weights=None, config=None, save=True, name='', span=None)

Cut it for me: build an edit of the whole song — or of `span` (`[start_s,
end_s]`, the part of the song the video covers) — from the placed videos, and (by
default) save it as a named edit, without rendering anything.

`strategy` picks how (see `strategies`; default `best_confidence`). Giving a
`preset` (“energetic”/”contemplative”), per-metric `weights` or a `config`
(`lambda_switch`/`l_min_s`/`l_max_s`/`boundary_mode`) selects the
score-driven `weighted` strategy, which needs `score` first.

Returns the `edl` (spans the WHOLE song; spans no footage covers are explicit gap
entries, `clip_id: null`, rendered as black), the `strategy` used, a
`coverage` report naming every uncovered span and every weakly-aligned segment,
`warnings`, and `assemble_refusal` (non-null when rendering it would be refused
because no clip is trustworthy). With `save` it also returns the `edit_id` to
change it (`set_cut` …) and render it (`render`).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.public_render(fp, meta)

A render record as a HOSTED surface may return it: `video` made relative to the
project (`renders/<id>/final.mp4`) — never an absolute server path; play it by its
`artifact_id`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.redo_edit(fp, , edit_id)

Redo the change `undo_edit` last took back. A new change after an undo
discards what could be redone. Returns the edit as it now is.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.refresh_cover(fp)

Take the project’s cover frame again — from the newest render, else from the
first clip — and register it. Only for hosted projects (`None` otherwise).

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.footage.service.remove_clip(fp, , clip_id)

Remove one footage video from the project — its stored file and its entry.

Irreversible for the clip (add it again if it was a mistake); existing renders are
untouched. Removal INVALIDATES every measured offset and every footage score, as
changing the song does — the alignment describes the clip set it was measured on —
so run `align` again before cutting. Offsets a person DECLARED for the remaining
clips are kept. An unknown `clip_id` is refused, naming the project’s clips.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.rename_edit(fp, , edit_id, name)

Give an edit a new name — what the edit picker and the renders made from it show.

Only the name changes; the cuts, the span and the edit’s id stay as they are, and
the rename can be undone like any other change. Returns the edit.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.render(fp, , edit_id, canvas='', allow_unreliable=False, annotate=None, should_cancel=None)

Make the video: render a saved edit onto the canvas, over the clean song.

Slow (minutes of encoding for a full song), so hosts run it in the background. The
video is exactly as long as the part of the song the edit covers (its `span`,
default the whole song; see `set_span`), with the song cut to match and faded out
at the end when it stops early; gaps render black. `canvas` (“landscape” /
“portrait” / “square”) re-renders the same edit in another shape; default: the
project’s.

Refused when the edit cuts to a clip whose offset the aligner will not vouch for —
a wrong offset renders a video out of sync with the song (muvid#59) — unless
`allow_unreliable`; fix it with `set_offset` or by changing those cuts. Returns
the render record: `render_id`, `edit_id`, its `coverage`, `ok` and the
`checks` behind it, `warnings` (read them — they are what the render plan found),
and `artifact_id` to play it by when the project is hosted.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.renders(fp)

The finished videos, newest first: each one’s `render_id`, speakable `ref`,
the `edit_id` it was made from, its `label`, canvas, `ok`, the number of
`warnings`, and `artifact_id` to play it by when the project is hosted.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.replace_edit(fp, , edit_id, edl)

Replace a saved edit’s whole cut list — the power tool for rewriting an edit at
once. The new list is checked exactly as `save_edit` checks one; on refusal the
edit is left as it was. The previous list is not kept.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.require_scorable(fp)

The alignments a scoring run would use; refuses without a song or an alignment.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)

### muvid.footage.service.resolve_canvas(fp, canvas)

The render canvas: an explicit per-render override, else the project’s.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]

### muvid.footage.service.run_scoring(fp, , hop_s=0.1, metrics=None, enable_lipsync=None, progress_cb=None, should_cancel=None)

Score every aligned clip and persist the tensor (the engine behind [`score()`](#muvid.footage.service.score),
with the job hooks a background runner passes).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.save_edit(fp, , edl, name='', how_made='by hand', edit_id='', span=None)

Save a cut list as a new named edit.

`edl` is a list of `{song_start, song_end, clip_id}` spans (plus optional
`transition`/`crop`/`crop_end`/`look`/`look_time_varying`), in the same
form `get_edit` returns and `propose_edit` produces. Holes are filled with gap
entries; the list is checked (order, overlap, every span inside its clip’s coverage)
and refused with the reason if it does not hold. `edit_id` fixes the id (an
existing one is refused — use `replace_edit`). `span` (`[start_s, end_s]`)
makes the edit cover only that part of the song — its render is that long, the song
cut to it; default the whole song. Returns the saved edit.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.score(fp, , hop_s=0.1, metrics=None, should_cancel=None)

Look at the footage: score every placed video, on the song’s own timeline —
picture quality and how its movement sits on the beat — and save the curves.

Slow (it decodes every clip), so hosts run it in the background. Needs a song and an
alignment. Every core metric is computed; weighting happens later, when cutting, so
re-weighting never re-scores. The lip-sync tier is off unless the operator enabled
it. Returns what was scored and what was skipped (and why).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.scores(fp, , clip_id='', metrics=None, max_points=1500)

The saved footage curves — for the lanes under each video, and for inspection.

- no `clip_id` → a SUMMARY (metrics, per-clip coverage, beats, tempo, the decimated
  `selection_margin`, grid geometry) — bounded, safe as the default;
- `clip_id` → that clip’s curves (values as `null`-masked arrays, decimated to
  `max_points` per metric).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.set_cut(fp, , edit_id, index, clip_id=None, song_start=None, song_end=None, look=None, look_time_varying=None)

Change one cut of a saved edit (`index` is its position in `get_edit`’s edl).

- `clip_id`: show another video over this span (`""` makes it a gap). The new
  video must cover the span. Its framing (`crop`) is dropped, since it was chosen
  for the old video’s frame; its `look` is kept.
- `song_start` / `song_end`: move the cut’s boundaries. The neighbouring cut’s
  boundary moves with it, so the edit stays one continuous timeline; a move that
  would swallow a neighbour whole is refused (join them with `merge_cut`).
- `look`: a NAMED look from `looks` — `{"name": "slow_push", "zoom": 1.08}`,
  compiled for this cut’s length and the project’s canvas and kept on the cut as
  `look_spec` (with every parameter’s value) so it can be shown and changed —
  or, for power users,
  one raw ffmpeg filter chain (allowlisted; set `look_time_varying` for one that
  moves). `""` removes it.

Parameters left out are unchanged. The changed edit is checked and saved; returns it.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.set_offset(fp, , clip_id, offset_s)

Place one video on the song BY HAND: the song time at which the video’s own
first frame plays (negative = the video starts before the song does).

Use it when `align` gets a clip wrong — on long, repetitive songs it can land a
whole chorus away (muvid#59) — or when you already know the offset. The offset is
recorded as `source: "declared"` and trusted for rendering (a person vouched for
it); how much of the song the clip covers is computed from the two durations.
`align` keeps it unless told otherwise. Changing an offset makes the footage
scores stale, so they are dropped.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.set_song(fp, , path, ext='', filename='', duration_s=None)

Set the project’s song — the clean master every video is aligned to and whose
audio the finished video uses. Replaces any previous song.

The file arrives from the host (an upload): `path` is where the host put it and
`filename` its original name, so the song keeps its name and extension; it is
copied into the project, so the host may delete its copy afterwards.
Replacing the song THROWS AWAY every clip’s offset and every footage score, because
they were measured against the old song — re-run `align` afterwards. Size- and
duration-capped.

Returns `song_duration` (seconds), the stored `song` (name, and the
`artifact_id` to play it by when the project is hosted) and whether an alignment
was dropped.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.set_span(fp, , edit_id, start_s, end_s)

Choose which part of the song the video covers — where it starts and ends.

**Trimming loses nothing.** The span is a window on the edit, not a cut of it: every
cut is kept whole, and only what is RENDERED is limited to `start_s`..\`\`end_s\`\`
(the song cut to match, faded out at the end when it stops before the song does;
cuts across an edge are shortened in the render only). Widening the span again —
`start_s=0` and `end_s` = the song’s length is the whole song — brings back
exactly what was there. Returns the edit, with its `span`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.split_cut(fp, , edit_id, at_s)

Split the cut playing at song time `at_s` into two cuts of the same video.

The two halves keep the cut’s video, framing and look; a moving framing (a pan) is
divided where it was at `at_s`. Refused on a boundary (nothing to split). Returns
the changed edit; `changed` is the index of the second half.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.status(fp)

Where the music video stands: the song, the videos and where each sits on the
song, the saved edits, the finished videos, and the next useful step.

`aligned` lists the clips that have an offset; `alignments` says for each one
whether the offset was `measured` (by `align`) or `declared` (`set_offset`)
and whether it is trusted for rendering (`reliable`). `renders` is newest first
(the same rows `renders` gives — no server paths).
`next_step` names the operation that moves the project forward and why.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.strategies(fp=None)

The ways to cut on offer — the selection strategies `propose_edit` accepts
(`weighted` reads the footage scores; the rest use only the alignment).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.timeline(fp)

Which videos cover which spans of the song (overlaps shown), from the saved
alignment — the map for choosing what to cut to. Run `align` first.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.service.undo_edit(fp, , edit_id)

Undo the last change to a saved edit (a cut changed, split, joined, the span, a
whole replacement — by a person or by the assistant). Returns the edit as it now
is; `redo_edit` puts the change back. Up to 100 changes are kept per edit.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
