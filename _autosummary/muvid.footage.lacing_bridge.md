# muvid.footage.lacing_bridge

muvid project → lacing standoff records, and the DECISION tier back to an EDL.

The multichannel editor (thorwhalen/reelee-web#203) renders three record kinds that
until now existed only as prose in the design docs (muvid#31): `clip-alignment/v1`
(where each clip sits on the song), `clip-score-track/v1` (one (clip, metric) curve on
the shared song-time grid), and `music-video-edl/v1` (the DECISION lane — one entry
per cut, gaps included). This module is the bridge, both directions:

- [`editor_document()`](#muvid.footage.lacing_bridge.editor_document) — a muvid project as `{tiers, annotations}`, everything in
  SONG TIME on one shared axis, referenced to the song’s content hash (`MediaRef`), so
  any lacing-native surface (lacing-ui’s multitrack Timeline first) renders it without
  knowing muvid exists.
- [`edl_from_annotations()`](#muvid.footage.lacing_bridge.edl_from_annotations) — the timeline-to-EDL half: a DECISION tier, after human
  edits, exports verbatim as `assemble_music_video(edl=...)` input.

Times quantize to lacing’s rational grid at `TIME_RATE` (μs): far finer than
`validate_edl`’s 1 ms tolerance and the frame grid, so annotate → edit → export →
render reproduces the same cuts.

Score arrays are inlined (a 205 s song at the current hop is ~2k floats per metric);
they move behind a ContentRef when they outgrow JSON — a body-schema major bump.

### Module Attributes

| [`TIME_RATE`](#muvid.footage.lacing_bridge.TIME_RATE)             | microseconds.                                                              |
|------------------------------------------------------------------------|----------------------------------------------------------------------------|
| [`CLIP_ALIGNMENT_SCHEMA`](#muvid.footage.lacing_bridge.CLIP_ALIGNMENT_SCHEMA) | Body-schema URIs this bridge emits (single source of truth for the names). |
| [`DECISION_TIER`](#muvid.footage.lacing_bridge.DECISION_TIER)         | Tier names.                                                                |

### Functions

| [`alignment_annotations`](#muvid.footage.lacing_bridge.alignment_annotations)(aligns, \*, ...)           | One `clip-alignment/v1` per clip, spanning the clip's coverage of the song.   |
|---------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`editor_document`](#muvid.footage.lacing_bridge.editor_document)(proj, \*[, attributed_to])       | The whole project as one lacing-native document for a multitrack editor.      |
| [`edl_annotations`](#muvid.footage.lacing_bridge.edl_annotations)(entries, \*, song_asset_id, ...) | The DECISION lane: one `music-video-edl/v1` per EDL entry, gaps included.     |
| [`edl_from_annotations`](#muvid.footage.lacing_bridge.edl_from_annotations)(annotations, \*[, ...])     | DECISION-tier annotations → the `edl=` argument, verbatim.                    |
| [`score_track_annotations`](#muvid.footage.lacing_bridge.score_track_annotations)(tensor, \*, ...)         | One `clip-score-track/v1` per (clip, metric): the whole curve as one record.  |

### muvid.footage.lacing_bridge.CLIP_ALIGNMENT_SCHEMA *= 'annot://schema/clip-alignment/v1'*

Body-schema URIs this bridge emits (single source of truth for the names).

### muvid.footage.lacing_bridge.DECISION_TIER *= 'DECISION'*

Tier names. Clip lanes are per-clip (`clip:<id>`); these are the shared ones.

### muvid.footage.lacing_bridge.TIME_RATE *= 1000000*

microseconds.

* **Type:**
  Rational-time rate for all bridge annotations

### muvid.footage.lacing_bridge.alignment_annotations(aligns, , song_asset_id, attributed_to)

One `clip-alignment/v1` per clip, spanning the clip’s coverage of the song.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)

### muvid.footage.lacing_bridge.editor_document(proj, , attributed_to='')

The whole project as one lacing-native document for a multitrack editor.

Tiers: one lane group per clip (alignment + its score sub-tracks) + the DECISION
lane. The EDL rendered into DECISION is the current default proposal; an editor
mutates that tier and exports it back through [`edl_from_annotations()`](#muvid.footage.lacing_bridge.edl_from_annotations).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.lacing_bridge.edl_annotations(entries, , song_asset_id, attributed_to)

The DECISION lane: one `music-video-edl/v1` per EDL entry, gaps included.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)

### muvid.footage.lacing_bridge.edl_from_annotations(annotations, , expected_song_asset_id=None)

DECISION-tier annotations → the `edl=` argument, verbatim.

The timeline-to-EDL half: whatever the editor did to the DECISION lane — moved,
split, retargeted, deleted — exports as plain `{song_start, song_end, clip_id}`
dicts (plus `transition`/`crop`/`crop_end`/`look`/`look_time_varying`
where the editor set one) ready for `assemble_music_video`. Sorting and
validation stay the render path’s business (`fill_gaps` + `validate_edl`);
this is a faithful read.

Annotations are untrusted editor input, so anything shaped wrong is SKIPPED rather
than crashing the export: wrong schema/tier, or (an editor could in principle attach
a `music-video-edl/v1` body to an `AnnotationRef`, whose `interval` is
optional) a reference with no interval to read a span from.

`expected_song_asset_id` is the one thing that RAISES instead (muvid#35). A
DECISION record pointing at a different song is not another record to filter past —
it is the whole export being about the wrong project (a stale clipboard, the easy
mistake in a copy-paste editor UI). Skipping it silently yields an empty or
half-empty EDL whose eventual `validate_edl` complaint names a symptom, never the
cause. The check is opt-in and evidence-based: no expected id, or a reference kind
carrying no `asset_id` at all (only `MediaRef` has one), is nothing to
contradict — it reports a WRONG song, it does not demand proof of the right one.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### muvid.footage.lacing_bridge.score_track_annotations(tensor, , song_asset_id, attributed_to)

One `clip-score-track/v1` per (clip, metric): the whole curve as one record.

Dense JAMS-style arrays on the shared grid — values normalized to [0,1], `mask`
saying where the clip actually covers the song (blank, never flat-zero, in the UI).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)
