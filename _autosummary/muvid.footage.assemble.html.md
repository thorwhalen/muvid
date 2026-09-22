# muvid.footage.assemble

Assemble validated cuts into a music video, in BOUNDED memory.

The previous shape — one ffmpeg `-filter_complex` pass with one input per cut — held a
decoder context and inter-filter queues for EVERY cut at once, so peak RSS grew roughly
linearly with cut count: a real 30-cut weighted edit needed >2.3 GB and was OOM-killed on
the 3.7 GB production box, while 15 cuts only just fit (muvid#21/#24). Score-driven edits
routinely produce 30-70 cuts, so that shape caps exactly the edits the scoring layer
exists to make.

Now three bounded stages, memory O(1) in cut count:

1. **One intermediate per PART** — a single-input ffmpeg run per cut (input-side `-ss`,
   so a late cut no longer decodes the whole head of its clip), scaled+padded onto the
   fixed canvas at a constant `fps`. A gap entry (`clip_path == ""` — a span of the
   song with no footage) renders as black from a `color` source, which is what makes
   partial-coverage edits renderable at all. Each intermediate is cut to an EXACT frame
   count derived from the shared song-time grid (`round(end*fps) - round(start*fps)`);
   sub-frame cuts (0 grid frames) are dropped, an exhausted source clones its last frame
   up to the count (`tpad`), and a cut whose source yields no frames at all falls back
   to black — so total frames equals `round(song_duration*fps)` by construction,
   whatever the cut count, source rates, or a clip whose audio outlives its video.

   A boundary carrying a [`Transition`](muvid.footage.edl.html.md#muvid.footage.edl.Transition) contributes a fourth
   kind of part: a **two-input** `xfade`, sitting between the two solos it blends,
   each of which is correspondingly shortened (see `_part_plan()`). TWO is the
   number that matters — the bounded-memory guarantee is O(1) in CUT count, and a
   constant number of decoders per invocation keeps it. Reaching for one filtergraph
   over all parts is exactly how the OOM below comes back. The counts still telescope:
   the `pre`/`post` terms cancel against the transition length, so the total is
   `round(song_duration*fps)` with or without transitions.
2. **Concat by stream copy** (concat demuxer) — no re-encode, no filtergraph.
3. **Mux the clean song** in the same final pass. When the master already IS the delivery
   contract (aac, 48 kHz stereo), its packets are stream-copied bit-identically; anything
   else is encoded to the contract (aac 192k, 48 kHz stereo — verify_video’s audio check
   must be a property of the renderer, not of which song the user brought: muvid#24 B3).

ffmpeg auto-applies each clip’s rotation metadata on decode (default `-autorotate 1`),
so a display-matrix portrait clip lands upright and pillarboxed, never stretched.

A cut may also carry a **look** — a compiled `looks` filter fragment
([`muvid.footage.look`](muvid.footage.look.html.md#module-muvid.footage.look)) spliced into the per-part chain by `_part_filter()`.
It is the federation seam and it is deliberately the cheapest possible one: a `-vf`
fragment adds no `-i`, so it cannot move the invariant above. That is enforced rather
than trusted, and by an ALLOWLIST rather than by refusals —
`_validate_look()` accepts only the filters
[`LOOK_FILTERS`](muvid.footage.edl.html.md#muvid.footage.edl.LOOK_FILTERS) names, and refuses a fragment that names a
container input, that is more than one linear chain, or that is not lexically closed.
The allowlist is what closes `movie=`/`amovie=` (a second container opened from
*inside* the fragment, which is the invariant leaving by the back door) and, because
`assemble_music_video` is a live per-caller MCP tool whose `edl` argument is
free-form, the filters that write the host’s disk (`metadata=…:file=`,
`deshake=filename=`, `sendcmd`, `signature`). The allowlist is a
vocabulary and bounds no PARAMETER, so the frame size a look asks for is bounded
separately against the delivery canvas (muvid#75) — `scale=8000:8000` peaks at
328 MB from a 64x48 source against 19 MB for a look that stays at canvas size,
and `pad`/`zoompan` reach the same magnitude. \*\*Nor is a size bound a bound on
the OPTIONS that set one\*\*: `pad`’s `aspect` and `scale`’s
`force_original_aspect_ratio` both move the frame while declaring no dimension
a bound can read, and on the production canvas both are larger than the case the
size bound refuses (590 MB and 941 MB against 403 MB). So the four filters that
can change the output geometry are allowlisted per OPTION and per positional
slot — see `_LOOK_GEOMETRY_FILTERS`.

A cut whose look is **time-varying** — a punch-in, a pan, anything reading the
filter clock — additionally makes `_part_plan()` **warn** when the cut borders
a transition: the blend is a separate invocation whose inputs are
input-side-seeked, so the clock restarts and the move plays again from the start
(muvid#73). The EDL says which kind a look is
([`look_time_varying`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry.look_time_varying)) because the fragment is a
bare string muvid did not author; rebasing it would mean rewriting an arbitrary
ffmpeg expression, which is what `looks`’ rule 27 refuses.

\*\*Both of `_part_plan()`’s findings go to two places\*\*, and the second half
was missing until now: a [`AssemblyWarning`](#muvid.footage.assemble.AssemblyWarning) on stderr, *and* the `on_note`
sink the caller’s reply is built from. `assemble_music_video` is a live
per-caller MCP tool and its caller has no stderr, so a warning that reached only
stderr was a hitch the caller was billed for and never told about — the silent
no-op this module refuses everywhere else. See `_emit()`.

Deliberately NOT moviepy (its `write_videofile` runs in-process and would escape the
`$MUVID_FFMPEG_TIMEOUT_S` worker guard). Every stage runs through
[`muvid.visualize.ffmpeg.run_ffmpeg()`](muvid.visualize.ffmpeg.html.md#muvid.visualize.ffmpeg.run_ffmpeg); note the guard is **per invocation** now, so a
render’s wall-clock bound is `(parts + 1) * MUVID_FFMPEG_TIMEOUT_S` — the env var bounds
a hang, not the total render. Parts equal cuts for an untransitioned edit and approach
`2 * cuts` when every boundary is transitioned.

### Module Attributes

| [`DEFAULT_FPS`](#muvid.footage.assemble.DEFAULT_FPS)    | Default output frame rate for the assembled video.   |
|-----------------------------------------------------------------|------------------------------------------------------|
| [`DEFAULT_CANVAS`](#muvid.footage.assemble.DEFAULT_CANVAS) | 9 1080p) when the caller/genre gives none.           |

### Functions

| [`assemble_music_video`](#muvid.footage.assemble.assemble_music_video)(cuts, song_path, ...[, ...])   | Render `cuts` (a validated, contiguous, gap-filled EDL) into `out_path`.   |
|------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|

### Exceptions

| [`AssemblyWarning`](#muvid.footage.assemble.AssemblyWarning)   | A render-plan finding the caller should see — not an error, not silence.   |
|--------------------------------------------------------------------|----------------------------------------------------------------------------|

### *exception* muvid.footage.assemble.AssemblyWarning

Bases: [`RuntimeWarning`](https://docs.python.org/3/builtins/exceptions.html#RuntimeWarning)

A render-plan finding the caller should see — not an error, not silence.

A `RuntimeWarning` subclass, so every existing `pytest.warns(RuntimeWarning)`
and every stderr reader keeps working. It exists so the *reply* half can pick
these out: `warnings.warn` reaches a developer’s stderr and nothing else, and
[`assemble_music_video()`](muvid.mcp.footage_tools.html.md#muvid.mcp.footage_tools.assemble_music_video) is a live per-caller MCP
tool whose caller has no stderr. A warning the caller cannot see is the silent
no-op this module refuses everywhere else — so [`assemble_music_video()`](#muvid.footage.assemble.assemble_music_video)
takes an `on_note` sink and the tool returns what it collects.

A distinct class rather than a stderr filter or a `catch_warnings` block:
`catch_warnings` mutates process-global state and the connector serves
concurrent callers, so one render could swallow or steal another’s warnings.

### muvid.footage.assemble.DEFAULT_CANVAS *= (1920, 1080)*

9 1080p) when the caller/genre gives none.

* **Type:**
  Default canvas (16

### muvid.footage.assemble.DEFAULT_FPS *= 30*

Default output frame rate for the assembled video.

### muvid.footage.assemble.assemble_music_video(cuts, song_path, out_path, , canvas=(1920, 1080), fps=30, crf=20, preset='veryfast', on_note=None)

Render `cuts` (a validated, contiguous, gap-filled EDL) into `out_path`.

Bounded stages: one single-input ffmpeg run per cut (gaps render black), a stream-copy
concat, and a final mux of the clean song for `[cuts[0].song_start,
cuts[-1].song_end]` — which, for EDLs produced by `fill_gaps`, is the whole song.
Returns `out_path`.

* **Parameters:**
  **on_note** – optional `str -> None` sink for the render-plan findings
  `_part_plan()` raises (a transition that rounds to zero frames; a
  time-varying look on a blended boundary — muvid#73). They are ALWAYS
  raised as [`AssemblyWarning`](#muvid.footage.assemble.AssemblyWarning) as well; this is the additional
  path, and the only one a remote caller can see. `assemble_music_video`
  is a live per-caller MCP tool, so a finding that reaches only the
  server’s stderr is a hitch the caller is billed for and never told
  about. A callback rather than a changed return type, because the
  return type is a public contract and because `catch_warnings`
  mutates process-global state that concurrent renders would share.
* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
