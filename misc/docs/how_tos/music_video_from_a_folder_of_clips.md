# Make a music video from a folder of phone clips

**The situation this is for.** Several people filmed the same performance on their own
phones. You have the clean song as one file, and the clips in a shared cloud folder. You
want a cut of the song with the camera moving between them.

**What you get.** A set of *proposed edits* you can compare, and a rendered video from
whichever you pick. The edits are the durable artifact; the video is a by-product you can
regenerate at any time, so trying a different look costs an encode, not a re-analysis.

Everything runs on the server. Nothing large transits your machine.

---

## The shape of it

```
set_song ──► add_footage_folder ──► align_footage ──┬──► propose_edit ──► assemble_music_video
  (once)         (once)                 (once)      │      (cheap, repeat freely)
                                                    └──► score_footage ──► propose_edit(strategy='weighted')
                                                          (slow, once)      (cheap, repeat freely)
```

The split that matters: **everything left of `propose_edit` is paid once; everything right
of it is cheap.** Alignment and scoring are the expensive passes and are persisted.
Proposing an edit reads them and takes under a second, so you can generate a dozen options
and render only the one you want.

---

## 1. Create the project

```
create_project(folder_name="we_ll_see", genre="music_video", template="landscape")
```

`template` is the canvas: `landscape` (16:9), `portrait` (9:16), or `square`. Pick it now —
it is fixed for the project's life, and re-rendering in another shape currently means a new
project (muvid#21).

## 2. Set the song

```
muvid_set_song(project_id, url="<share link or direct URL>")
```

A **share link is fine** — a Google Drive `/view` link, a Dropbox `?dl=0` link. It is
normalised to a direct download before fetching.

The song is two things at once: the reference every clip is aligned to, *and* the audio the
final video uses. Clip audio is never used.

> **If this fails with "got an HTML page… anyone-with-the-link"**, the file is not publicly
> shared. Every anonymous URL form for a private Drive file returns a sign-in page with
> `HTTP 200`, so no amount of link-rewriting fixes it — change the sharing setting. This is
> the single most common failure, and the error says so rather than letting a web page get
> stored as your song.

## 3. Add the footage

```
muvid_add_footage_folder(project_id, url="<folder share link>")
```

A shoot is a folder, not a file. One folder link downloads as a single archive server-side
and expands into one clip per media file.

**Read the `skipped` list.** Members are skipped for real reasons — not a video, over the
per-clip size cap, past the project's clip cap — and each is named with which. A shorter
list than you expected is information, not noise.

For a single clip, use `muvid_add_footage(project_id, url=...)`. Passing a *folder* link
there is refused by name rather than half-working.

## 4. Align

```
muvid_align_footage(project_id)
```

Each clip's audio is matched against the song, giving an offset, a confidence, and the span
of the song it covers.

**Read the offsets before the confidences.** Devices recording one performance land at
nearly the same offset, so the cluster is the evidence:

```
bc2287e1  off= -2.41  conf=0.216   ┐
c4e9d6f3  off= -2.75  conf=0.182   │ five clips within 1.6 s of each other
9ccb560e  off= -1.20  conf=0.479   │ -> these are the same take
a7f2b9de  off= -2.34  conf=0.080   │
f18c0d61  off= -2.41  conf=0.240   ┘
bcc2d2af  off=+76.65  conf=0.010   <- 79 s from the median: a different take
```

`offset_consensus` in the response reports exactly this. It identifies the odd clip that no
per-clip score does: notice that four clips in the good cluster score *below* the same
threshold that the outlier fails.

**Confidence is guidance, not a verdict.** Nothing is dropped for a low score — every clip
stays usable. A low number means "look at this one", not "this is unusable". Treat
`low_confidence` as a list to review, not a list of rejects.

## 5. Propose edits — the part you repeat

```
muvid_propose_edit(project_id, strategy="best_confidence")
```

Returns the EDL an assembly *would* use, without rendering. Try several:

| strategy | what it does |
|---|---|
| `best_confidence` | at each moment, the clip that matched the song best |
| `fewest_cuts` | stay on one camera as long as possible |
| `longest_take` | prefer whichever clip keeps rolling longest |
| `weighted` | score-driven, beat-snapped — needs step 6 first |

Each response carries a **coverage report**:

- `uncovered` — spans of the song with no footage, named with **start and end times**, not
  an aggregate percentage. These are the parts you may need to reshoot or accept a gap in.
- `weak_segments` — spans that made the cut using footage whose alignment is doubtful. These
  are the compromises in the edit, so you can decide whether to keep them.

Save the ones you like. An EDL is a plain list of `{song_start, song_end, clip_id}` and it
is returned at full precision, so it feeds back verbatim.

## 6. Optional: score the footage for a real edit

The alignment-only strategies cut only where coverage changes, so with five clips covering
the whole song you get one or two cuts — technically a music video, visually a locked-off
camera. For an edit that actually moves:

```
muvid_score_footage(project_id)          # slow: decodes every clip. Poll:
muvid_footage_score_status(project_id)   # until status == "succeeded"
```

Then the score-driven selector, which cuts on the beat and balances shot quality against
how often it switches camera:

```
muvid_propose_edit(project_id, strategy="weighted")
muvid_propose_edit(project_id, strategy="weighted", preset="energetic")
muvid_propose_edit(project_id, strategy="weighted", config={"l_min_s": 0.8, "l_max_s": 3.0})
```

Re-weighting is **cheap** — it re-selects from the same scores without re-scoring. This is
where the options come from.

## 7. Render

```
muvid_assemble_music_video(project_id, edl=<the EDL you chose>)
```

Each cut is trimmed at its aligned in-point, scaled and padded onto the canvas (never
stretched), and concatenated over the clean song audio.

Omit `edl` and pass `strategy=` instead to select and render in one step — fine for a first
look, but you lose the chance to read the coverage report before paying for the encode.

---

## Reusing this shape elsewhere

The pattern generalises past music videos, to anything that aligns many recordings to one
reference and picks among them:

1. **One reference, many observations.** Everything is expressed in *reference time* (here,
   song time). Each source carries a mapping back to its own timeline. Get this wrong and
   every later stage inherits the error.
2. **Expensive analysis is persisted and separately addressable.** Alignment and scores are
   computed once and stored. Nothing downstream recomputes them.
3. **Selection is separated from rendering.** The list of decisions is a first-class artifact
   you can read, diff, hand-edit, and re-render. If the only way to see a decision is to pay
   for the output, you cannot compare options — and comparing options is the actual work.
4. **Scores guide; they never silently decide.** Surface sub-scores. Never drop the user's
   material because a number fell below a constant — say the number, say the threshold, say
   which metric produced it, and let them choose.
5. **Report what is missing, in the units of the problem.** "84% covered" is not actionable.
   "no footage for 41.2–58.7 s" is.
6. **Ingest is the boundary where lying happens.** A share link answers `HTTP 200` with a web
   page. Normalise the link, then check the *bytes*, and fail with the cause. Never let a
   sign-in page become an asset.

---

## Things that will bite you

- **A private Drive link looks like success.** `HTTP 200`, `text/html`, ~900 KB. Only sniffing
  the bytes catches it.
- **A folder link is one URI for N files.** Dropbox serves it as a ZIP; Drive offers no
  download URL for a folder at all and needs the API.
- **Confidence is not comparable across ingestion routes** — decoding a `.mov` directly
  scores ~2× lower than reading ffmpeg-extracted WAV of the same audio (mixing#25). Don't
  port a threshold between projects until that is fixed.
- **The clip cap is 8 and the per-clip cap 400 MB** by default
  (`MUVID_FOOTAGE_MAX_CLIPS`, `MUVID_FOOTAGE_MAX_BYTES`). A bigger shoot needs them raised.
- **Mixed frame rates are normal.** iPhones produce 30/1 and 359/12 in the same folder.
- **The rendered file has no download URL yet** (muvid#24) — it lands on the server.
