# Montage mode in reelee-studio — a UX recommendation

2026-10-04 · for the owner and the session building the backend v1 · grounded in `tt/reelee-web/src/studio/screens/{intake,music-gallery,music-footage,music-edit,music-shared}.tsx` and `t/muvid/muvid/footage/{service,strategy,align}.py` (read-only).

## The one-paragraph verdict

Keep it inside **Music video**. Do not add a category, do not add an up-front question, do not add a toggle. The tool already measures the one fact that matters — "is the song audible in this video?" — per clip, and today it measures it correctly (all eight of the design partner's clips came back `reliable=false`) and then throws the answer away on the way to the cut. The fix is to let that verdict decide *how each clip meets the song*: a clip with the song in it is placed where it fits (what exists today); a clip without it is cut to the music (the new montage path). Say so in plain words on each clip's row and once at the top, and give a one-click way to overrule it per clip. That is the whole v1 UI: three strings changed, one new line of copy, one two-way control per lane, photos allowed in the drop zone.

## 1. Inside Music video, not a new category

The argument for a separate "Memories" / "Montage" category is discoverability and marketing pull — Apple's *Memories*, GoPro's *Quik*, CapCut's *templates* have taught people that "my clips + a song → a video" is a thing with a name. The argument is real but it points at the gallery's **subtitle**, not at a second card. Here is why a category is the wrong seam:

- **Users cannot answer the question a category asks.** "Is the song in your footage?" is exactly what the design partner did not know they were being asked. They had a song and some videos; the product said "music video"; they used it. A second card forces the choice before upload, when the user has the least information and we have none. After upload, we have the answer in seconds.
- **Mixed projects are the common case, not the edge.** A wedding: three phone clips of the first dance (song audible) plus the cake, the kids, the drive home (no song). A split category makes that project impossible in either half. One project with per-clip roles makes it natural.
- **Every planned extension applies to both.** Multiple songs, best-matching song, emotion matching, section-aware pacing — none of these cares whether a clip was synced or free. Two categories means building each twice or leaving one behind.
- **The result is the same object.** The owner has decided the montage produces "a normal edit in the same Edit tab." If the output is one thing, the input should not pretend to be two.
- **Mental model check against the apps named.** Memories, Quik and CapCut users expect: pick media, pick song, get a cut, tweak. None of them asks "is this a music video or a slideshow?" — they infer from the media. Our aligner is a sharper version of that inference. Hiding it behind a mode picker would make us the only tool in the set that asks.

What to change for discoverability: the gallery heading stays **Music videos**; its subtitle currently reads "Bring the song and your videos of it — a party, a wedding, a gig. Reelee listens to each one, finds where it fits, and cuts between them on the beat." That sentence is the bug the design partner hit in prose form: *videos of it*. Replace with: **"Bring a song and your videos and photos. Where the song is in a video, Reelee lines it up; where it isn't, it cuts the pictures to the music."** The empty state ("No music videos yet. Start one, then drop in the song and your videos.") becomes "...the song and your videos and photos."

The intake screen's "A montage set to music" feel button (`intake.tsx:242`) should keep pointing at this same genre when intake is wired. It is already the right affordance: it is about *feel*, and feel is what the montage path delivers. Do not make it select a different kind.

If, after a few months, analytics show people hunting for "slideshow" or "memories", add a **label** per project card ("Music video" / "Cut to the music" / "Both") — `GalleryCard`'s sub line already varies by genre — not a category.

## 2. No toggle, no up-front question; the system decides per clip, the person overrules per clip

**Default behaviour.** "Find where each video fits" runs as today. Each clip comes back with the aligner's verdict. The cut then treats clips in two ways:

- `reliable=true` or placed by hand → **has the song in it**: placed on the song, cut as today.
- `reliable=false` → **cut to the music**: not placed; used freely by the montage path, segments chosen so motion lands on beats.
- Photos → always cut to the music, with a pan/zoom move.

**What is asked up front.** Nothing. The "A new music video" modal stays title + shape. Any question before upload is one the user cannot answer and we soon can.

**Where the override lives.** On each clip's lane in the Footage tab, in `ClipLane`'s head row, next to the place words: a two-choice control (the `Choice` chip pair already used for "Its shape"): **Has the song in it** · **Doesn't have the song**. Choosing "Doesn't have the song" clears the offset and sets a declared role; choosing "Has the song in it" on a clip the aligner could not hear invites a hand placement (the existing "Starts at (seconds)" field) and otherwise keeps the aligner's offset. The declared role must survive a re-run of listening exactly as declared offsets do today (`source === 'declared'`), and must be forgettable with the existing "Forget where I placed this" affordance, reworded "Forget what I said about this".

**One shortcut for the all-b-roll case.** When every heard clip came back unsure, the summary line (section 4) is already a confident whole-project statement, so no project-level toggle is needed. When *some* are unsure, offer a single link in the summary: "None of them has the song, actually" — which declares all unsure clips free in one click. Do not offer the reverse ("all of them have it") as one click: that is the dangerous direction (section 7).

**What not to call it.** Not "mode", not "alignment", not "sync". The user-facing noun is the fact itself: whether the song is in the video. Internally the role can be `synced | free`; that word never reaches a screen.

## 3. Copy — the exact words

All strings are in the person's register the screens already use (short, concrete, no nouns of ours). Where an existing string is replaced, the file and current text are named.

**Gallery** (`music-gallery.tsx:63-64`) — subtitle and empty state as in section 1.

**Footage tab, drop zones** (`music-footage.tsx`, titles come from `OpSpec` in `muvid/footage/service.py:2944-2951`):

- `set_song` title **The song** — unchanged. Sub-line unchanged.
- `add_clip` title "Add a video" → **Add videos and photos**. Section heading "Your videos" → **Your videos and photos**. Drop sub-line: "Drop them here — videos and photos, as many as you have. Up to 8 videos; photos don't count." (the 8 is `CLIP` cap; see section 5).
- `UPLOAD_ACCEPT.add_clip`: `'video/*,image/*'`.

**Footage tab, the listen button** (`service.py:2953`, "Find where each video fits"): rename to **Listen to the videos**. Hint: "Finds the song in each video and places it. A video without the song in it is cut to the music instead." Busy label: "Listening…". The current name promises that every video fits somewhere, which is the promise that broke.

**Lane badges** (`placeWords`, `music-footage.tsx:158-163`) — one line per state, attention colour only where the person may want to act:

- no verdict yet: "Not placed on the song yet" (unchanged).
- hand-placed: "Placed by hand · starts at 0:40" (unchanged shape).
- heard, reliable: **"Has the song in it · starts at 1:02"** (replaces "Found by listening").
- heard, unsure: **"Couldn't hear the song in it — so it's cut to the music."** (attention colour; replaces "Found by listening — not sure"). The lane track shows no bar on the ruler; in its place, where "Not placed yet" sits today, a muted **"Cut to the music"** chip, visually distinct from "Not placed yet" (a dashed outline is enough).
- declared free: **"Doesn't have the song — cut to the music (you said so)"**.
- photo: **"Photo — moves slowly to the music"**.

Say *couldn't hear*, never *doesn't have*, for the aligner's verdict. We report what we heard; only the person asserts what is there.

**The summary line** (new, under the "Your videos and photos" heading, after a listen finishes) — section 4 has the three variants.

**Edit tab** (`music-edit.tsx`):

- `!canCut` message, line 131: "Place your videos on the song first (the Footage tab), then Reelee can cut it." → **"Add the song and some videos or photos first (the Footage tab)."** `canCut` becomes `song && (placed > 0 || free > 0 || photos > 0)`.
- "Cut it for me" hint, line 287: "Cut the whole song on the beat, and save it as a new edit" → **"Cut the song on the beat — videos with the song in them where they fit, everything else to the music — and save it as a new edit."**
- The "How to cut" chips (`STRATEGY_WORDS`, `music-shared.tsx:185-190`) are about choosing *between* synced cameras. Show them only when at least two clips have the song in them. Otherwise show one sentence in their place: **"These videos don't have the song in them, so they're cut to the music: a new picture on the beat, photos slowly moving."**
- After the cut lands, a status sentence (the `fitSentence` pattern, line 303): **"Cut to the music: 31 pictures across 2:00 of the song, from 5 videos and 12 photos."** Plus the span note from section 7 when the song was trimmed.
- `editLabel` for an edit made this way: **"Cut to the music 1"** rather than "Cut 1", so the chip row tells the two apart.
- "Fit the moves to the beat" sub-line ("Changes a cut only where the dancers clearly move on the beat.") — fine as is; the montage path already lands motion on beats, so the button should be hidden or say "Already on the beat" for an all-free edit.

**Watch tab** — nothing.

## 4. How the detection is communicated when it fires

Three rules. **Once, at the top; always, per clip; never as a modal or a toast.** The summary is derived from the clips' current state, so it is always true (it does not go stale if the person changes a lane). It is a sentence in the "Your videos and photos" section, rendered by a small component (call it `HeardLine`), and it has three variants:

- **All have the song** (today's happy path): "The song is in all 8 videos." Muted; nothing to do.
- **None has the song** (the design partner's case): **"I couldn't hear the song in any of these 8 videos, so I'll cut them to the music instead — a new picture on the beat, with the song clean underneath. If one of them does have the song, say so on its row."**
- **Mixed**: **"The song is in 3 of these videos — they're placed where they fit. I couldn't hear it in the other 5, so they'll be cut to the music. Wrong about one? Say so on its row."** Followed by the one-click link: "None of them has the song, actually."

Per-clip is the authority; the whole-project line is the digest. Mixed projects need no extra concept: the cut interleaves — the three synced clips own the stretches they cover (as today), and the free clips and photos fill the rest, on the beat. The Edit tab's timeline shows the result as one edit; a tiny "to the music" marker on free segments is a nice-to-have, not v1.

Photos are never "heard" and never appear in these counts; "12 photos" appears only in the cut's result sentence.

## 5. Upload: photos alongside videos

- **One drop zone**, not two. The person has a folder from a day out; they drop it. Splitting "videos" from "photos" makes them sort by file type, which is our job.
- **Caps.** Videos keep the genre's 8 (the lane colours and the alignment cost both assume it). Photos: **up to 40** in v1 — Memories-scale, enough for a 4-minute song at 3–6 s a picture. Say the cap before sending, as the drop zone already does for size ("Up to 8 videos and 40 photos"). Over-cap files get the existing `too-big` row pattern: "That's more than 40 photos — the first 40 went in."
- **Ordering.** Default to **the day as it happened**: capture time from EXIF / QuickTime creation metadata, falling back to upload order where a file has none, and say which: "In the order they were taken" or "In the order you added them" as a muted line under the photo strip. This is what Memories and Quik do and what people expect; a shuffled day feels wrong before it feels creative. Reordering by hand is deferred.
- **Where photos show.** Not as lanes (a lane is a position on the song, and a photo has none). A thumbnail strip **"Photos · 12"** below the lanes, each with Remove. Reuse `Picture` from the gallery.
- **Accepted types.** `image/*` plus HEIC (iPhone default) — if the backend cannot decode HEIC, refuse at upload with a plain reason, never silently drop.

## 6. Minimum v1 change set, in priority order

Smallest set that turns the design partner's "weird results" into a compelling result:

1. **Backend honours the verdict in "Cut it for me"** (`propose_edit`): `reliable=false` clips go to the montage path; photos too. No UI needed for this to fix the design partner's project; everything below is making it legible.
2. **`placeWords` rewrite** (`music-footage.tsx:158-163`) — the five badge states in section 3, plus the "Cut to the music" chip in the lane track where "Not placed yet" sits. Smallest possible change that stops the unsure state reading as a half-failure.
3. **`HeardLine`** (new, ~30 lines, in `music-footage.tsx` under the section heading) — the three sentences in section 4.
4. **Per-lane role control** — the `Choice` pair "Has the song in it / Doesn't have the song" in `ClipLane`, dispatching a new op (`set_role` or reuse `clear_offset` + a declared flag). Includes the one-click "None of them has the song, actually" in the mixed `HeardLine`.
5. **Photos in the drop zone** — `UPLOAD_ACCEPT`, the drop-zone title and sub-line, the photo strip, and the cap text.
6. **Edit tab strings** — `canCut`, the `!canCut` sentence, the hint, hiding "How to cut" for all-free, the result sentence, `editLabel`.
7. **Gallery subtitle and empty state**; rename "Find where each video fits" to "Listen to the videos".

Items 1–3 alone are a shippable fix. 4 is what makes it safe (section 7). 5 is what makes it the Memories use case. 6–7 are polish that costs minutes.

**Defer** (each is a real idea with a real cost, none needed to be compelling):

- A pace control ("a new picture every bar / every two bars / let the music decide"); v1 picks from the song's sections.
- Reordering photos and videos by hand; "shuffle".
- A per-segment "to the music" marker on the timeline.
- Per-card "Cut to the music" label in the gallery.
- Wiring the intake "feel" button.
- Running "Listen" automatically inside "Cut it for me" when it hasn't run — one-button-to-result is the right eventual shape, but it changes the job model; keep the two buttons in v1 and let `canCut` guide.
- Multiple songs, best-matching song, emotion matching — the owner's own list; none changes the per-clip-role model above, which is the point of choosing it.

## 7. Risks, and what the design does about each

**A performance video whose alignment is merely weak is silently treated as b-roll.** This is the one that bites a wedding video: the first dance, filmed from the back of the room, muffled, comes back `reliable=false` and gets chopped to the beat instead of played in sync. Mitigations, in the order they matter: (a) the badge says *couldn't hear*, in attention colour, with the override beside it — the person who filmed it knows the song was playing and can say so in one click; (b) the `HeardLine` names the count so a "5 of 8 unsure" on a concert shoot reads as a prompt to check, not a result; (c) the one-click bulk action only goes in the safe direction (declare all free), never "all have the song"; (d) when the person marks a clip "Has the song in it" and the aligner has an offset it did not vouch for, offer that offset as the default in the "Starts at" field with "Reelee's best guess — check it against the player" rather than placing it silently. The reverse risk — b-roll that genuinely has the song faintly in it (a shop playing the same track) — is rare and benign: it gets placed, and it sounds right because it is right.

**Footage shorter than the song.** The design partner had 114 s of footage for a 264 s song. A montage either repeats shots or trims the song; left undecided it produces exactly the black gaps they saw. Default: **trim the song to the footage, ending at a section boundary** (the existing `set_span` op), and say so in the result sentence: "The song is trimmed to 2:00 because there's 1:54 of footage. [Use the whole song — pictures will repeat]". Never pad with black; never repeat without saying so.

**An unsure clip disappearing from the ruler reads as a failed upload.** Today an unsure clip still draws a bar; tomorrow it draws none. The "Cut to the music" chip in the track, distinct from "Not placed yet", is what prevents "where did my video go?". Test this with the design partner specifically.

**Hand-declared roles lost on re-listen.** Declared offsets survive a re-run today (`source === 'declared'`); declared roles must follow the same rule or the person's correction evaporates the next time they add a clip (the remove-clip confirm already promises "places set by hand stay").

**Photos without capture dates.** Screenshots and WhatsApp forwards have none; mixing dated and undated media under "in the order they were taken" is misleading. State the fallback, and when any photo lacks a date, say "In the order you added them" for the whole set.

**The strategy chips in an all-free project.** "The surest video at each moment" is meaningless when nothing is placed and will look broken. Hide them (section 3); do not leave them to confuse.

**Wording drift between the lane, the summary and the result.** Three places say the same fact; they must use the same two phrases — *has the song in it* / *cut to the music* — and no synonyms. Put them in `music-shared.tsx` beside `STRATEGY_WORDS` and import from there.
