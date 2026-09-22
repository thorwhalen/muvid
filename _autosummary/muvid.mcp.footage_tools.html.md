# muvid.mcp.footage_tools

MCP tools for the footage-aligned `music_video` genre (thorwhalen/reelee#229).

Module-level tool functions (referenced `muvid.mcp.footage_tools:<name>`) a host
aggregates via [`muvid.mcp.register_tools()`](muvid.mcp.html.md#muvid.mcp.register_tools). All FREE (ffmpeg + numpy only, no AI/keys).
The caller is resolved from the OAuth token; all work lands in that caller’s stateful
[`FootageWorkspace`](muvid.footage.workspace.html.md#muvid.footage.workspace.FootageWorkspace) project. Media URLs are fetched
server-side through the SSRF-guarded, size/time-bounded fetch (video streams straight to
disk); alignment + assembly are bounded by hard resource caps and
`$MUVID_FFMPEG_TIMEOUT_S` (assembly runs one bounded single-input ffmpeg per cut, so
memory does not grow with cut count) — the connector renders synchronously over HTTP.

Workflow: `create_project(genre='music_video')` → `set_song` → `add_footage` ×N →
`align_footage` → (`footage_timeline` to inspect) → `assemble_music_video`.
Lifecycle around it (muvid#22): `list_music_video_projects` finds a project whose id
was lost, and `remove_footage` takes a clip back out — which invalidates the
alignment, exactly as `set_song` does.

### Functions

| [`add_footage`](#muvid.mcp.footage_tools.add_footage)(project_id, \*, url[, name])          | Add a footage video clip from an http(s) URL (a recording of the song).                                |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| [`add_footage_folder`](#muvid.mcp.footage_tools.add_footage_folder)(project_id, \*, url[, ...])    | Add EVERY clip in a shared folder (Drive / Dropbox / OneDrive) in one call.                            |
| [`align_footage`](#muvid.mcp.footage_tools.align_footage)(project_id)                         | Align every uploaded clip to the song by audio, and persist the result.                                |
| [`assemble_music_video`](#muvid.mcp.footage_tools.assemble_music_video)(project_id, \*[, ...])       | Assemble the music video — auto (a selection `strategy`) or an explicit `edl`.                         |
| [`beat_grid`](#muvid.mcp.footage_tools.beat_grid)(project_id)                             | The song's beat grid — tempo and beat instants on the song timeline — WITHOUT running the scoring job. |
| [`footage_editor_document`](#muvid.mcp.footage_tools.footage_editor_document)(project_id)               | The project as lacing-native standoff annotations, for a multitrack editor.                            |
| [`footage_edl_from_annotations`](#muvid.mcp.footage_tools.footage_edl_from_annotations)(project_id, \*, ...) | The DECISION tier's annotations, turned back into an `edl=` argument.                                  |
| [`footage_status`](#muvid.mcp.footage_tools.footage_status)(project_id)                        | Your project's song, clips, alignment summary, and renders.                                            |
| [`footage_timeline`](#muvid.mcp.footage_tools.footage_timeline)(project_id)                      | The coverage map: which clips cover which spans of the song (overlaps shown).                          |
| [`list_music_video_projects`](#muvid.mcp.footage_tools.list_music_video_projects)()                       | List YOUR music_video (footage) projects, newest-modified first.                                       |
| [`list_strategies`](#muvid.mcp.footage_tools.list_strategies)()                                 | The selection strategies available for full-auto assembly.                                             |
| [`propose_edit`](#muvid.mcp.footage_tools.propose_edit)(project_id, \*[, strategy, ...])     | Propose an EDL **without rendering it** — the cheap half of assembly.                                  |
| [`remove_footage`](#muvid.mcp.footage_tools.remove_footage)(project_id, \*, clip_id)           | Remove one footage clip from the project — its stored file and its entry.                              |
| [`set_song`](#muvid.mcp.footage_tools.set_song)(project_id, \*, url)                     | Set the project's fixed clean song from an http(s) URL.                                                |

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

### muvid.mcp.footage_tools.align_footage(project_id)

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

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.assemble_music_video(project_id, , strategy='', edl=None, preset='', weights=None, config=None, canvas='', allow_unreliable=False)

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
  clock. All five fields survive verbatim in the returned `edl`.
- `strategy='weighted'` (score-driven): the beat-snapped Viterbi selector reads the
  persisted score tracks (run `score_footage` first) and the selection config —
  `preset` (“energetic”/”contemplative”) and/or `weights` (per-metric) and/or
  `config` (`lambda_switch`/`l_min_s`/`l_max_s`/`boundary_mode`). Re-weighting
  is cheap: it re-selects from the SAME scores without re-scoring.
- otherwise **full-auto**: a registered alignment-only `strategy` (see
  `list_strategies`; default `best_confidence`) builds the edit from the alignments.
- `canvas`: render-time override (“landscape”/”portrait”/”square”) — the same edit
  re-rendered in another shape, no new project needed. Default: the project’s canvas.
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

### muvid.mcp.footage_tools.footage_status(project_id)

Your project’s song, clips, alignment summary, and renders. Free.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.mcp.footage_tools.footage_timeline(project_id)

The coverage map: which clips cover which spans of the song (overlaps shown). Free.

The surface for choosing which parts to use before `assemble_music_video`. Built from
the persisted alignment (run `align_footage` first).

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

### muvid.mcp.footage_tools.propose_edit(project_id, , strategy='', preset='', weights=None, config=None)

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
