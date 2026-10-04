# muvid.mcp.footage_tools

MCP tools for the footage-aligned `music_video` genre (thorwhalen/reelee#229).

Module-level tool functions (referenced `muvid.mcp.footage_tools:<name>`) a host
aggregates via [`muvid.mcp.register_tools()`](muvid.mcp.html.md#muvid.mcp.register_tools). All ffmpeg + numpy only, no AI/keys.

**A transport, not an implementation.** The operations live in
[`muvid.footage.service`](muvid.footage.service.html.md#module-muvid.footage.service); each tool here resolves `project_id` to the caller’s
stateful [`FootageWorkspace`](muvid.footage.workspace.html.md#muvid.footage.workspace.FootageWorkspace) project (the caller comes
from the OAuth token), calls the operation, and turns its
[`FootageError`](muvid.footage.errors.html.md#muvid.footage.errors.FootageError) into a fastmcp `ToolError`. What stays here
is what only this surface does: fetching a URL (SSRF-guarded, size/time-bounded, streamed
to disk — the service takes a local file), expanding a shared folder, the per-caller
project listing, and the download claim on a render.

The tool list is derived from the operations catalogue (`muvid.mcp._footage_ops`):
operations without a hand-written tool here get a GENERATED one, `footage_<op>`, built
at import from the operation’s own signature and docstring (the named-edit operations:
`footage_set_offset`, `footage_save_edit`, `footage_set_cut` …).

Workflow: `create_project(genre='music_video')` → `set_song` → `add_footage` ×N →
`align_footage` → (`footage_timeline` to inspect) → `propose_edit(save=true)` →
`footage_set_cut` … → `footage_render` (or `assemble_music_video` in one call).
Lifecycle around it (muvid#22): `list_music_video_projects` finds a project whose id
was lost, and `remove_footage` takes a clip back out — which invalidates the
alignment, exactly as `set_song` does.

### Functions

| [`add_footage`](#muvid.mcp.footage_tools.add_footage)(project_id, \*, url[, name])           | Add a footage video clip from an http(s) URL (a recording of the song).                                                                                                                                                                                                 |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`add_footage_folder`](#muvid.mcp.footage_tools.add_footage_folder)(project_id, \*, url[, ...])     | Add EVERY clip in a shared folder (Drive / Dropbox / OneDrive) in one call.                                                                                                                                                                                             |
| [`align_footage`](#muvid.mcp.footage_tools.align_footage)(project_id, \*[, keep_declared])     | Align every uploaded clip to the song by audio, and persist the result.                                                                                                                                                                                                 |
| [`assemble_music_video`](#muvid.mcp.footage_tools.assemble_music_video)(project_id, \*[, ...])        | Assemble the music video — auto (a selection `strategy`) or an explicit `edl`.                                                                                                                                                                                          |
| [`beat_grid`](#muvid.mcp.footage_tools.beat_grid)(project_id)                              | The song's beat grid — tempo and beat instants on the song timeline — WITHOUT running the scoring job.                                                                                                                                                                  |
| [`footage_beat_signals`](#muvid.mcp.footage_tools.footage_beat_signals)(project_id, \*[, ...])        | Where the beat is in the song or in one video — CONTINUOUS signals, to look at, threshold and bend, not only beat instants.                                                                                                                                             |
| [`footage_clear_offset`](#muvid.mcp.footage_tools.footage_clear_offset)(project_id, \*, clip_id)      | Forget where I placed this video: remove a hand-declared offset, so the next `align_footage` measures the clip by its audio instead.                                                                                                                                    |
| [`footage_delete_edit`](#muvid.mcp.footage_tools.footage_delete_edit)(project_id, \*, edit_id)       | Delete a saved edit.                                                                                                                                                                                                                                                    |
| [`footage_editor_document`](#muvid.mcp.footage_tools.footage_editor_document)(project_id)                | The project as lacing-native standoff annotations, for a multitrack editor.                                                                                                                                                                                             |
| [`footage_edits`](#muvid.mcp.footage_tools.footage_edits)(project_id)                          | The saved edits, oldest first: each one's `edit_id`, `name`, how it was made, how many cuts it has, and `problem` — why it would not validate against the current alignment (`null` when it does).                                                                      |
| [`footage_edl_from_annotations`](#muvid.mcp.footage_tools.footage_edl_from_annotations)(project_id, \*, ...)  | The DECISION tier's annotations, turned back into an `edl=` argument.                                                                                                                                                                                                   |
| [`footage_filmstrip`](#muvid.mcp.footage_tools.footage_filmstrip)(project_id, \*, clip_id)         | One video's filmstrip (the same record `footage_filmstrips` gives per clip, with its `clip_id` and `fps`).                                                                                                                                                              |
| [`footage_filmstrips`](#muvid.mcp.footage_tools.footage_filmstrips)(project_id)                     | Every video's filmstrip — thumbnails to draw each camera's lane.                                                                                                                                                                                                        |
| [`footage_fit_to_beat`](#muvid.mcp.footage_tools.footage_fit_to_beat)(project_id, \*, edit_id)       | Fit the moves to the beat: for each cut that shows a video in the part of the song the edit covers (its span) — or the cuts at `indices` — find the slip and speed that put its video's movement accents on the song's beat, and apply it where the evidence is strong. |
| [`footage_get_edit`](#muvid.mcp.footage_tools.footage_get_edit)(project_id, \*, edit_id)          | One saved edit: its cut list (`edl`, every span of the song, gaps as `clip_id: null`), its name and history, and a `coverage` report.                                                                                                                                   |
| [`footage_looks`](#muvid.mcp.footage_tools.footage_looks)(project_id)                          | The looks a cut can take — camera moves (punch in, slow push, slow pull, pans) and grades (vivid, black and white, posterize, cartoon) — each with its `params_schema`.                                                                                                 |
| [`footage_merge_cut`](#muvid.mcp.footage_tools.footage_merge_cut)(project_id, \*, edit_id, index)  | Join cut `index` to its neighbour: the neighbour (`into` "previous" or "next") takes over its span, so the neighbour's video must cover it.                                                                                                                             |
| [`footage_peaks`](#muvid.mcp.footage_tools.footage_peaks)(project_id, \*[, n])                 | The song's waveform, to draw under the timeline: `n` equal slices of the song, each the loudest moment in it (mono), scaled so the loudest slice is 1.0.                                                                                                                |
| [`footage_redo_edit`](#muvid.mcp.footage_tools.footage_redo_edit)(project_id, \*, edit_id)         | Redo the change `footage_undo_edit` last took back.                                                                                                                                                                                                                     |
| [`footage_rename_edit`](#muvid.mcp.footage_tools.footage_rename_edit)(project_id, \*, edit_id, name) | Give an edit a new name — what the edit picker and the renders made from it show.                                                                                                                                                                                       |
| [`footage_render`](#muvid.mcp.footage_tools.footage_render)(project_id, \*, edit_id[, ...])     | Render a SAVED edit (`propose_edit(save=true)` / `footage_save_edit`) into a music video.                                                                                                                                                                               |
| [`footage_renders`](#muvid.mcp.footage_tools.footage_renders)(project_id)                        | The finished videos, newest first: each one's `render_id`, speakable `ref`, the `edit_id` it was made from, its `label`, canvas, `ok`, the number of `warnings`, and `artifact_id` to play it by when the project is hosted.                                            |
| [`footage_replace_edit`](#muvid.mcp.footage_tools.footage_replace_edit)(project_id, \*, edit_id, edl) | Replace a saved edit's whole cut list — the power tool for rewriting an edit at once.                                                                                                                                                                                   |
| [`footage_save_edit`](#muvid.mcp.footage_tools.footage_save_edit)(project_id, \*, edl[, ...])      | Save a cut list as a new named edit.                                                                                                                                                                                                                                    |
| [`footage_set_cut`](#muvid.mcp.footage_tools.footage_set_cut)(project_id, \*, edit_id, index)    | Change one cut of a saved edit (`index` is its position in `footage_get_edit`'s edl).                                                                                                                                                                                   |
| [`footage_set_has_song`](#muvid.mcp.footage_tools.footage_set_has_song)(project_id, \*, clip_id, ...) | Say whether a video has the song in its own sound — overriding what listening found.                                                                                                                                                                                    |
| [`footage_set_offset`](#muvid.mcp.footage_tools.footage_set_offset)(project_id, \*, clip_id, ...)   | Place one video on the song BY HAND: the song time at which the video's own first frame plays (negative = the video starts before the song does).                                                                                                                       |
| [`footage_set_span`](#muvid.mcp.footage_tools.footage_set_span)(project_id, \*, edit_id, ...)     | Choose which part of the song the video covers — where it starts and ends.                                                                                                                                                                                              |
| [`footage_split_cut`](#muvid.mcp.footage_tools.footage_split_cut)(project_id, \*, edit_id, at_s)   | Split the cut playing at song time `at_s` into two cuts of the same video.                                                                                                                                                                                              |
| [`footage_status`](#muvid.mcp.footage_tools.footage_status)(project_id)                         | Your project's song, clips, alignment summary, and renders.                                                                                                                                                                                                             |
| [`footage_timeline`](#muvid.mcp.footage_tools.footage_timeline)(project_id)                       | The coverage map: which clips cover which spans of the song (overlaps shown).                                                                                                                                                                                           |
| [`footage_undo_edit`](#muvid.mcp.footage_tools.footage_undo_edit)(project_id, \*, edit_id)         | Undo the last change to a saved edit (a cut changed, split, joined, the span, a whole replacement — by a person or by the assistant).                                                                                                                                   |
| [`list_music_video_projects`](#muvid.mcp.footage_tools.list_music_video_projects)()                        | List YOUR music_video (footage) projects, newest-modified first.                                                                                                                                                                                                        |
| [`list_strategies`](#muvid.mcp.footage_tools.list_strategies)()                                  | The selection strategies available for full-auto assembly.                                                                                                                                                                                                              |
| [`propose_edit`](#muvid.mcp.footage_tools.propose_edit)(project_id, \*[, strategy, ...])      | Propose an EDL **without rendering it** — the cheap half of assembly.                                                                                                                                                                                                   |
| [`remove_footage`](#muvid.mcp.footage_tools.remove_footage)(project_id, \*, clip_id)            | Remove one footage clip from the project — its stored file and its entry.                                                                                                                                                                                               |
| [`set_song`](#muvid.mcp.footage_tools.set_song)(project_id, \*, url)                      | Set the project's fixed clean song from an http(s) URL.                                                                                                                                                                                                                 |

### muvid.mcp.footage_tools.add_footage(project_id, , url, name='')

Add a footage video clip from an http(s) URL (a recording of the song). Free.

Accepts a **share link** as well as a direct media URL. Fetched server-side (streamed to
disk; SSRF-guarded, size/duration-capped) and asserted to be media before anything is
stored. Returns the assigned `clip_id`. Re-run `align_footage` after adding clips.

For a whole shoot in one folder, use [`add_footage_folder()`](#muvid.mcp.footage_tools.add_footage_folder) — a folder link holds
many files and is refused here by name.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.add_footage_folder(project_id, , url, name_prefix='')

Add EVERY clip in a shared folder (Drive / Dropbox / OneDrive) in one call. Free.

A shoot is a folder, not a file — this is the natural unit for music-video footage. The
folder link is normalised and downloaded as a single archive server-side, then expanded
into one clip per media member.

Members that are skipped — wrong type, over the per-clip size limit, or past the project
clip cap — are NAMED in `skipped` with the reason. Nothing is silently truncated: a
coverage decision made on quietly-shortened input is worse than one made on a short list.

Returns the added clips and the skipped members. Run `align_footage` afterwards.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.align_footage(project_id, , keep_declared=True)

Align every uploaded clip to the song by audio, and persist the result. Free.

Returns each clip’s offset, a confidence in [0,1], its `support` (the fraction of
the clip that agrees on that offset, `null` when the aligner took a single
whole-clip measurement), and its coverage of the song, plus two lists:

- `low_confidence` — clips that matched weakly, for reporting;
- `unreliable` — clips whose offset the aligner will NOT vouch for. These stay in
  the project and stay addressable, and the auto path simply prefers other clips
  over them: a span another clip covers goes to that clip, and a span only an
  unreliable clip covers is left as a gap and reported in `coverage.excluded`
  (muvid#88). `assemble_music_video` still REFUSES an explicit `edl` that cuts
  to one, and still refuses an auto edit when NO clip is trustworthy, unless called
  with `allow_unreliable=true` — because a wrong offset does not fail, it renders
  a video out of sync with the song (muvid#59). Re-align, accept the smaller edit,
  or opt in deliberately;
- `no_consensus` — clips too short to be put to a vote at all (under about 4.5 s).
  **A clip in this list can be marked reliable and still be wrong**, and no other
  field will say so: its offset rests on one measurement, judged by a confidence
  score that does not rank correctness in this band — measured on the muvid#59
  shoot, the WRONG offset scored highest of three (0.834 against 0.566 and 0.621),
  and on a repeating fixture a 4.4 s clip landing 8 s out is vouched at 0.381.
  Nothing is refused on this basis, because refusing would take the correct short
  clips with it. So if a short clip looks out of sync in the render, this list is
  the first place to look — and muvid#91 is where that trade-off is being decided.

Run this after adding/removing clips and before assembling.

A clip placed by hand (`footage_set_offset`) is left as placed and named in
`kept_declared`; `keep_declared=false` forgets those first
(`footage_clear_offset`) and measures every clip.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.assemble_music_video(project_id, , strategy='', edl=None, preset='', weights=None, config=None, canvas='', allow_unreliable=False, span=None)

Assemble the music video — auto (a selection `strategy`) or an explicit `edl`. Free.

- `edl`: an explicit edit — a list of `{song_start, song_end, clip_id}` spans. Must
  be in order, non-overlapping, and each within its clip’s coverage. A span with no
  footage is an explicit gap entry (`clip_id: null`); spans of the song your entries
  do not reach (head, tail, interior holes) are gap-filled automatically and named in
  the `coverage` report.
- each `edl` entry may also carry `transition`:
  `{"duration_s": 0.4, "curve": "fade"}` — blend IN from the previous entry instead
  of hard-cutting. The blend is CENTRED on the boundary, so a beat-snapped cut stays
  on the beat: each side supplies `duration_s/2` of source beyond its own span, and
  both must actually have it (a gap side always does — it fades from black). Rejected,
  not ignored, if it is on the FIRST entry (nothing to blend from), names a curve
  outside `fade`/`fadeblack`/`fadewhite`/`dissolve`/`wipe*`/`slide*`/
  `smooth*`/`circleopen`/`circleclose`, is under 0.04 s, or does not fit. Omit
  it for a hard cut — the default, and what every entry without the key means.
- each `edl` entry may also carry `crop` / `crop_end`
  (`{"x":0,"y":0.25,"w":1,"h":0.5}`, fractions of the SOURCE frame — the
  framing decision; `crop_end` pans that window and must be the same size)
  and `look` (ONE linear ffmpeg filter chain — the grade, LUT, posterise or
  in-shot punch-in, applied to the delivery CANVAS after scaling).
  `look` is an **allowlist**: only the filters named by
  `muvid.footage.edl.LOOK_FILTERS` are accepted, and a chain naming
  anything else — a
  second container (`movie=`), a filter that writes a file
  (`metadata=…:file=`, `deshake=filename=`), a pad label, a graph
  separator, or an unclosed quote — is refused by name at `validate_edl`,
  not discovered as an ffmpeg side effect. Its output frame is also bounded:
  a `scale`/`pad`/`zoompan` size must be a plain pixel count (not
  `iw*80`, not `-1`, not `hd720`) and no more than
  `muvid.footage.edl.MAX_LOOK_SCALE` times the render canvas — frame size
  is memory, and `scale=8000:8000` costs 328 MB against 19 MB for a look
  that stays at canvas size. On those four filters only options muvid has
  measured may be set at all — `scale` w/h/s/size + `flags`, `pad`
  w/h/x/y + `color`, `crop` w/h/x/y, all of `zoompan` — because
  `pad=aspect` and `scale=force_original_aspect_ratio` move the frame
  while declaring no size a bound can read (590 MB and 941 MB measured on a
  1920x1080 canvas). Compile one with `muvid.footage.look`
  (`punch_in` / `motion` / `stylize`) rather than hand-writing it.
- an entry carrying a MOVING look (a punch-in, a pan) should also set
  `look_time_varying: true`. It changes no pixels; it puts a line in the
  reply’s `warnings` when a blended boundary restarts the move’s ramp,
  which it does because the blend is a separate seek (muvid#73). Leave it
  off — the default — for a grade, a LUT or a posterise, which never read the
  clock.
- an entry may carry `slip_s` (seconds, at most one beat either way,
  `muvid.footage.edl.SLIP_MAX_S`): the cut shows its video that much LATER
  (negative: earlier) without moving on the song — a local correction on top
  of the clip’s alignment, to put a dancer on the beat. The clip must still
  hold the slipped span.
- an entry may carry `rate` (within `1 +- muvid.footage.edl.RATE_MAX_DEV`):
  the cut plays its video that much faster (`1.03`) or slower, consuming
  `span * rate` seconds of it; the song is untouched. Every optional field
  survives verbatim in the returned `edl`.
- `strategy='weighted'` (score-driven): the beat-snapped Viterbi selector reads the
  persisted score tracks (run `score_footage` first) and the selection config —
  `preset` (“energetic”/”contemplative”) and/or `weights` (per-metric) and/or
  `config` (`lambda_switch`/`l_min_s`/`l_max_s`/`boundary_mode`). Re-weighting
  is cheap: it re-selects from the SAME scores without re-scoring.
- otherwise **full-auto**: a registered alignment-only `strategy` (see
  `list_strategies`; default `best_confidence`) builds the edit from the alignments.
- `canvas`: render-time override (“landscape”/”portrait”/”square”) — the same edit
  re-rendered in another shape, no new project needed. Default: the project’s canvas.
- `span`: `[start_s, end_s]` — render only that stretch of the song (a trimmed
  edit): the `edl` is gap-filled within it, the video is that long, and the song is
  cut to it and faded out at the end when it stops before the song does. A render
  of a trimmed edit records its `span`; pass it back with its `edl` to reproduce
  it. Default: the whole song.
- **A clip the aligner will not vouch for costs its own spans, not the whole edit**
  (muvid#88). On the auto path the strategy prefers a vouched clip wherever one
  covers the span, so an untrustworthy clip is simply not chosen while any other
  clip covers that stretch of the song. Where it was the ONLY footage, the span is
  set aside rather than cut to: it renders as a gap and is named in
  `coverage.excluded` with the clip, the span, and the confidence/support/margin
  the verdict rests on. So a five-clip shoot with one bad clip still assembles.
  The refusal remains for the two cases it was built for: an explicit `edl` naming
  an unvouched clip (you chose it), and an auto edit where NO clip is trustworthy —
  a black video reported as success is the failure this exists to prevent, so that
  still comes back as an error naming every clip.
- `allow_unreliable`: render even on clips whose alignment the aligner will not
  vouch for. **Off by default and it should stay off**: a wrong offset does not
  fail, it delivers a video out of sync with the song, so the refusal is the only
  thing standing between a bad measurement and a bad render (muvid#59). Set it when
  you have checked the offsets yourself, or when a slightly-off cut beats no cut at
  all — it also turns OFF the set-aside above, since someone who opted in wants that
  footage rendered rather than gapped. `align_footage` names the clips this
  applies to in its `unreliable` list.

The video is EXACTLY the song’s duration: each cut is trimmed at its aligned in-point,
scaled onto the canvas (padded, never stretched), gaps render black, and the CLEAN
song audio runs under it all. Returns the render + the same coverage report
`propose_edit` gives.

\*\*Read the returned `warnings` list.\*\* It is always present and usually
empty, and it is what the render PLAN found: a transition that rounded to
zero frames at this fps, or a moving look on a blended boundary whose ramp
therefore restarts (muvid#73). Neither fails the render — `ok` stays true —
so this list is the only place either one is ever said. It exists because
these findings used to be Python warnings on the server’s stderr, which a
remote caller has no access to.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.beat_grid(project_id)

The song’s beat grid — tempo and beat instants on the song timeline — WITHOUT
running the scoring job. Free.

`score_footage` computes this very grid as its first stage and then decodes every
clip through cv2 in the background, which is far too much to pay when all a caller
wants is where the beats are (thorwhalen/muvid#18 item 5) — a fixed-stride cut grid,
a check that the tempo came out right, a cut plan of its own. This is one call to
`mixing.audio.beat_grid` on the song alone (the “compute once on the master”
invariant: clips map to it through their offsets, never the other way round),
cached under the project as `scores/beat_grid.json` keyed on `song_hash` so the
second call is a file read; a project that has already been scored is served from
that run’s manifest instead. `source` says which (`computed` | `cache` |
`scores`).

Needs the `scoring` extra (`mixing[beats]`, i.e. librosa); without it the reply
is a clean error naming the install, never a traceback. Needs a song
(`set_song`); no alignment is required.

Returns `tempo_bpm`, `beats` (seconds, ascending), `n_beats`,
`song_duration` (so a caller can close the last bar) and `source`.
`downbeats` is present only when the estimator measured any — the librosa
backend has no downbeat tracker, and an empty list would read as “this song has
no downbeats”, a measurement nobody made (gate, don’t zero).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_beat_signals(project_id, , source='song', max_points=1000)

Where the beat is in the song or in one video — CONTINUOUS signals, to look at,
threshold and bend, not only beat instants.

`source` is `"song"` or a clip id. The song gets its sound (`audio_onset`: the
onset envelope the beat grid is estimated from). A video gets its own soundtrack’s
`audio_onset` when it has one, and three visual signals: `motion` (how much the
people in the picture move, the camera’s own move taken out), `motion_stops` (moves
stopping dead and turning — the movement accents) and `motion_stops_local` (the same,
place by place).

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

### muvid.mcp.footage_tools.footage_clear_offset(project_id, , clip_id)

Forget where I placed this video: remove a hand-declared offset, so the next
`align_footage` measures the clip by its audio instead.

Only a DECLARED offset can be forgotten (a measured one is replaced by aligning
again); an unknown clip, or one with no declared offset, is refused. Until `align_footage`
runs again the clip has no place on the song, and footage scores made with the old
offset are dropped.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_delete_edit(project_id, , edit_id)

Delete a saved edit. Videos already rendered from it are kept (they still name
the edit they came from). An unknown `edit_id` is refused, naming the edits.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_editor_document(project_id)

The project as lacing-native standoff annotations, for a multitrack editor. Free.

One tier per clip (its `clip-alignment/v1` +, once scored, its
`clip-score-track/v1` curves) plus a `DECISION` tier holding the current default
proposal as `music-video-edl/v1` entries — everything referenced to the song by
content hash, on one shared song-time axis (thorwhalen/reelee-web#203). Needs the
`editor` extra (`lacing`); requires alignment (run `align_footage` first).

After a human edits the DECISION tier, feed its annotations back to
`assemble_music_video` via `footage_edl_from_annotations`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_edits(project_id)

The saved edits, oldest first: each one’s `edit_id`, `name`, how it was made,
how many cuts it has, and `problem` — why it would not validate against the
current alignment (`null` when it does). `unreliable` names clips it cuts to
whose offsets rendering would refuse.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_edl_from_annotations(project_id, , annotations)

The DECISION tier’s annotations, turned back into an `edl=` argument. Free.

The timeline-to-EDL half: pass `footage_editor_document`’s `DECISION` tier
(after whatever an editor did to it) and get back plain
`{song_start, song_end, clip_id}` dicts, ready for `assemble_music_video(edl=...)`
or `propose_edit` — a faithful read, not a re-selection. Annotations referencing a
song other than this project’s are refused, not read (muvid#35), so a clipboard from
another project fails saying so instead of splicing in the wrong spans.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_filmstrip(project_id, , clip_id)

One video’s filmstrip (the same record `footage_filmstrips` gives per clip, with its
`clip_id` and `fps`).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_filmstrips(project_id)

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

### muvid.mcp.footage_tools.footage_fit_to_beat(project_id, , edit_id, indices=None, min_z=2.5, apply=True)

Fit the moves to the beat: for each cut that shows a video in the part of the song
the edit covers (its span) — or the cuts at `indices` — find the slip and speed
that put its video’s movement accents on the song’s beat, and apply it where the
evidence is strong.

Per cut, every slip within +-0.25 s (a frame at 60 fps apart) and every speed from
x0.92 to x1.08 (1 % apart) is tried; the best is scored against a null of the same
search on the video’s accents with their relation to the beat destroyed
(`muvid.footage.beat_fit`), giving a `z`. A cut changes only when `z >= min_z`
AND the fit is better than its current timing — and the video still holds the
footage it then reads. Cuts shorter than three beats, gaps and videos with nothing
measurable are left alone, each with its reason. `apply=False` reports without
changing anything. All the changes are ONE step of the edit’s history, so one
`footage_undo_edit` takes them all back.

**How much to expect.** On three phone videos of a crowd dancing, the accents locked
to the beat on one of them only; each video’s own SOUND locked on all three. So on
footage like that most cuts will be left as they are, which is the right answer when
the picture does not show the beat. It works best on a clearly visible dancer.

Needs the `scoring` extra: the first call measures each video’s movement (tens of
seconds a video; kept for next time). Returns the edit (`footage_get_edit`’s shape) plus
`fit`: `cuts` (`{index, slip_s, rate, z, applied, reason}` per cut looked at),
`fitted`, `kept` and `min_z`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_get_edit(project_id, , edit_id)

One saved edit: its cut list (`edl`, every span of the song, gaps as
`clip_id: null`), its name and history, and a `coverage` report. Cut indexes in
`footage_set_cut`/`footage_merge_cut` refer to positions in this `edl`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_looks(project_id)

The looks a cut can take — camera moves (punch in, slow push, slow pull, pans)
and grades (vivid, black and white, posterize, cartoon) — each with its
`params_schema`. Give one to `footage_set_cut` as `look={"name": ..., **params}`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_merge_cut(project_id, , edit_id, index, into='previous')

Join cut `index` to its neighbour: the neighbour (`into` “previous” or
“next”) takes over its span, so the neighbour’s video must cover it. The joined
cut keeps the neighbour’s video, framing and look. Returns the changed edit.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_peaks(project_id, , n=2000)

The song’s waveform, to draw under the timeline: `n` equal slices of the song,
each the loudest moment in it (mono), scaled so the loudest slice is 1.0.

Returns `{duration_s, n, peaks: [0..1, ...]}`; slice `i` covers song time
`i * duration_s / n` to `(i + 1) * duration_s / n`. Kept per song and `n`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_redo_edit(project_id, , edit_id)

Redo the change `footage_undo_edit` last took back. A new change after an undo
discards what could be redone. Returns the edit as it now is.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_rename_edit(project_id, , edit_id, name)

Give an edit a new name — what the edit picker and the renders made from it show.

Only the name changes; the cuts, the span and the edit’s id stay as they are, and
the rename can be undone like any other change. Returns the edit.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_render(project_id, , edit_id, canvas='', allow_unreliable=False)

Render a SAVED edit (`propose_edit(save=true)` / `footage_save_edit`) into a
music video. Free, minutes.

Same render, same refusal and same reply as `assemble_music_video` with that edit’s
cut list as `edl` — plus `edit_id`, so the video says which edit it came from.
`canvas` re-renders the same edit as “landscape”/”portrait”/”square”. Refused when
the edit cuts to a clip whose offset the aligner will not vouch for, unless
`allow_unreliable` (see `assemble_music_video`). Read the returned `warnings`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_renders(project_id)

The finished videos, newest first: each one’s `render_id`, speakable `ref`,
the `edit_id` it was made from, its `label`, canvas, `ok`, the number of
`warnings`, and `artifact_id` to play it by when the project is hosted.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_replace_edit(project_id, , edit_id, edl)

Replace a saved edit’s whole cut list — the power tool for rewriting an edit at
once. The new list is checked exactly as `footage_save_edit` checks one; on refusal the
edit is left as it was. The previous list is not kept.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_save_edit(project_id, , edl, name='', how_made='by hand', edit_id='', span=None)

Save a cut list as a new named edit.

`edl` is a list of `{song_start, song_end, clip_id}` spans (plus optional
`transition`/`crop`/`crop_end`/`look`/`look_time_varying`), in the same
form `footage_get_edit` returns and `propose_edit` produces. Holes are filled with gap
entries; the list is checked (order, overlap, every span inside its clip’s coverage)
and refused with the reason if it does not hold. `edit_id` fixes the id (an
existing one is refused — use `footage_replace_edit`). `span` (`[start_s, end_s]`)
makes the edit cover only that part of the song — its render is that long, the song
cut to it; default the whole song. Returns the saved edit.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_set_cut(project_id, , edit_id, index, clip_id=None, song_start=None, song_end=None, look=None, look_time_varying=None, slip_s=None, rate=None)

Change one cut of a saved edit (`index` is its position in `footage_get_edit`’s edl).

- `clip_id`: show another video over this span (`""` makes it a gap). The new
  video must cover the span. Its framing (`crop`) is dropped, since it was chosen
  for the old video’s frame; its `look` is kept. A video cut to the music (or a
  photo) is shown from its start; a video with the song in it, where it was filmed.
- `song_start` / `song_end`: move the cut’s boundaries. The neighbouring cut’s
  boundary moves with it, so the edit stays one continuous timeline; a move that
  would swallow a neighbour whole is refused (join them with `footage_merge_cut`).
- `look`: a NAMED look from `footage_looks` — `{"name": "slow_push", "zoom": 1.08}`,
  compiled for this cut’s length and the project’s canvas and kept on the cut as
  `look_spec` (with every parameter’s value) so it can be shown and changed —
  or, for power users,
  one raw ffmpeg filter chain (allowlisted; set `look_time_varying` for one that
  moves). `""` removes it.
- `slip_s`: show a slightly different moment of the same video over the same
  span — `0.1` reads the footage 0.1 s later — to put a dancer’s moves on the
  beat where the clip’s alignment is right overall but a little off here. At
  most one beat either way (`SLIP_MAX_S`); `0` removes it. A new video
  (`clip_id`) starts unslipped.
- `rate`: play this cut’s video a little faster or slower — `1.03` shows 3 %
  more footage over the same span, so the moves run 3 % faster (the song is never
  touched). Within `1 +- RATE_MAX_DEV`; `1` removes it. The video must hold the
  footage the cut then reads. A new video starts at speed 1.

Parameters left out are unchanged. The changed edit is checked and saved; returns it.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_set_has_song(project_id, , clip_id, has_song)

Say whether a video has the song in its own sound — overriding what listening found.

`"yes"`: it is a recording of the song (a concert, a dance); it is synced to the
song and never cut freely to the music. `"no"`: it is not (a day out, b-roll); it
is always cut to the music. `"auto"` (the default) believes the listening: a
video `align_footage` placed confidently is synced, any other is cut to the music. A
photo is always cut to the music. Like a hand-placed offset, the choice survives
listening again. Takes effect at the next `propose_edit`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_set_offset(project_id, , clip_id, offset_s)

Place one video on the song BY HAND: the song time at which the video’s own
first frame plays (negative = the video starts before the song does).

Use it when `align_footage` gets a clip wrong — on long, repetitive songs it can land a
whole chorus away (muvid#59) — or when you already know the offset. The offset is
recorded as `source: "declared"` and trusted for rendering (a person vouched for
it); how much of the song the clip covers is computed from the two durations.
`align_footage` keeps it unless told otherwise. Changing an offset makes the footage
scores stale, so they are dropped.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_set_span(project_id, , edit_id, start_s, end_s)

Choose which part of the song the video covers — where it starts and ends.

**Trimming loses nothing.** The span is a window on the edit, not a cut of it: every
cut is kept whole, and only what is RENDERED is limited to `start_s`..\`\`end_s\`\`
(the song cut to match, faded out at the end when it stops before the song does;
cuts across an edge are shortened in the render only). Widening the span again —
`start_s=0` and `end_s` = the song’s length is the whole song — brings back
exactly what was there. Returns the edit, with its `span`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_split_cut(project_id, , edit_id, at_s)

Split the cut playing at song time `at_s` into two cuts of the same video.

The two halves keep the cut’s video, framing and look; a moving framing (a pan) is
divided where it was at `at_s`. Refused on a boundary (nothing to split). Returns
the changed edit; `changed` is the index of the second half.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_status(project_id)

Your project’s song, clips, alignment summary, and renders. Free.

Also: each clip’s offset and whether it was measured or declared (`alignments`),
the saved `edits`, and `next_step` — the operation that moves the project on.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_timeline(project_id)

The coverage map: which clips cover which spans of the song (overlaps shown). Free.

The surface for choosing which parts to use before `assemble_music_video`. Built from
the persisted alignment (run `align_footage` first).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_undo_edit(project_id, , edit_id)

Undo the last change to a saved edit (a cut changed, split, joined, the span, a
whole replacement — by a person or by the assistant). Returns the edit as it now
is; `footage_redo_edit` puts the change back. Up to 100 changes are kept per edit.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.list_music_video_projects()

List YOUR music_video (footage) projects, newest-modified first. Free.

The way back to a lost `project_id` (muvid#22): `list_projects` spans both
muvid genres but says nothing about a footage project’s progress, and every other
footage tool needs the id first. Each row carries where the project is in the
workflow — `has_song`, `n_clips`, `aligned`, `n_renders` — so the next call
reads off the listing without a `footage_status` per project:

- `aligned` is true only when the persisted alignment covers every current clip.
  An `add_footage` or `remove_footage` after `align_footage` makes it false
  again until you re-align.
- `n_renders` counts what `footage_status` lists under `renders`; `0` is a
  positive answer (“no cut yet”), never an omitted project.

`[]` means you have no footage projects; a visualizer project is not one and
shows up in `list_projects` instead.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.list_strategies()

The selection strategies available for full-auto assembly. Free.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.propose_edit(project_id, , strategy='', preset='', weights=None, config=None, save=False, name='', pace='')

Propose an EDL **without rendering it** — the cheap half of assembly. Free, seconds.

Selection and rendering are separate concerns, and only one of them costs an encode.
This returns the edit an `assemble_music_video` call *would* have produced, so a
caller can compare several strategies/weightings, read the coverage report, edit the
list by hand, and only then pay for a render — passing the chosen EDL straight back to
`assemble_music_video(project_id, edl=...)`.

Returns the `edl` (ready to feed back verbatim — spans the WHOLE song, with spans no
footage covers as explicit gap entries, `clip_id: null`, rendered as black), the
`strategy` actually used, and a `coverage` report naming every uncovered span of
the song and every segment that made the cut despite weak alignment. Same arguments as
`assemble_music_video`’s auto path, and the same edit it would build — including the
`coverage.excluded` recovery described there.

`save=true` also keeps it as a named edit (`name`, default “Edit N”) and returns
its `edit_id` — change it cut by cut with `footage_set_cut` /
`footage_split_cut` / `footage_merge_cut` and render it with `footage_render`.

**Footage without the song in it is cut to the music.** Only videos that are
recordings of the song are synced; every span they leave is filled from the other
videos, cut on the song’s beats with each video at the stretch whose picture changes
land on the beat (`music` in the reply). `roles` says how each clip was used;
overrule one with `footage_set_has_song`. Every video must have been listened to
(`align_footage`) or declared. `pace` (`slow`/`steady`/`driving`/
`frantic`) sets how often the cuts to the music come.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.remove_footage(project_id, , clip_id)

Remove one footage clip from the project — its stored file and its entry. Free.

Irreversible for the clip (re-add it from its URL if it was a mistake); existing
renders are untouched. Removal INVALIDATES the alignment and every persisted score
track, exactly as `set_song` does (muvid#22) — the alignment describes the clip set
it was measured on, and a removed clip’s offset must not outlive the clip. So run
`align_footage` again before `propose_edit` / `assemble_music_video`; until
then `footage_status` reports the project unaligned and this project’s
`aligned` flag in `list_music_video_projects` is false.

An unknown `clip_id` is refused, naming the clip ids the project holds, and a
refusal changes nothing on disk. Returns what was removed, the clips that remain,
and whether an alignment / score tracks were actually dropped.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.set_song(project_id, , url)

Set the project’s fixed clean song from an http(s) URL. Free.

Accepts a **share link** (Google Drive / Dropbox / OneDrive) as well as a direct media
URL — the link is normalised before fetching, and the downloaded bytes are checked to be
media, so a private-file sign-in page is refused with that diagnosis rather than stored.

This is the reference every uploaded clip is aligned to and whose audio the final
video uses. Replaces any previous song. Duration/size-capped.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
