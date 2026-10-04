---
name: muvid-montage
description: >-
  Cut a person's own videos and photos to a song — the "memories" kind of music video, where the footage does NOT contain the song (a day out, a trip, a party filmed in clips, a folder of photos) and the audio is the song itself. Use when someone has a song plus footage and photos and wants them "put to music", "cut to the beat", "made into a memories video", a slideshow to music, a montage, a recap; when a music video came out mostly black or oddly placed because the clips have no song in their sound; or when deciding whether a clip should be synced to the song or cut to the music. Covers the studio flow, the Python and MCP calls, how each clip's role is decided (and overridden), photos with Ken Burns moves, the pace knob, how the picture is aligned to the beat, and the research behind it (methods, libraries and licences, apps and market, UX).
---

# muvid-montage — cut footage to the music

A music video in muvid has two halves, and every clip belongs to one of them:

| The clip | Its role | How it is used |
|---|---|---|
| A recording **of** the song (a concert, a dance, a lip-sync) | `synced` | placed where it was filmed: listening (`align`) finds its offset on the song |
| Anything else — a day out, b-roll, a photo | `to_the_music` | **cut to the music**: free cuts on the song's beats and bars, each video at the stretch whose picture changes land on the beat, each photo with a slow pan or zoom |

The audio of the result is always the clean song. Both halves mix in one edit: synced clips own the stretches they cover, everything else fills the rest.

## How a clip's role is decided

`muvid.footage.service._role_of` is the one rule:

1. a photo → `to_the_music`, always;
2. the person said so (`set_has_song`, `"yes"` or `"no"`) → that, and it survives listening again;
3. a video nobody has listened to → `not_listened`, and `propose_edit` **refuses** until it is (`align`) or declared. Nothing is guessed: a concert cut to the music is a desynced concert, the muvid#59 failure by another road;
4. otherwise listening decides (`service.heard_the_song`): a video the aligner **vouches for** (`edl.vouches_for`) is `synced`; one it cannot place but where enough of the clip agreed on SOME offset and that offset beat every rival (support ≥ `HEARD_SUPPORT`, 0.4, and margin > 0, on a vote of at least `MIN_HEARD_WINDOWS`, 4, windows) is also `synced` — the song is plainly there, its place is ambiguous (muvid#59) — and is set aside until placed by hand; anything else ("couldn't hear the song in it") is `to_the_music`.

`HEARD_SUPPORT` is a first cut, not a calibration: b-roll with no song in it scored at most 0.33 with a positive margin (a 6 s clip reached 0.5 at margin 0.0, and a 6 s pure-noise clip 0.5 at +0.167 on a 3-window vote: short clips vote too coarsely to count), and a bar-multiple alias of a real recording 0.487 at margin +0.115. Its independent second opinion (landmark fingerprints) is [mixing#55](https://github.com/thorwhalen/mixing/issues/55). When a synced clip is set aside and there is footage to cut to the music, its span is filled from it (`service._set_aside_unvouched`); with none, the muvid#59 refusal stands exactly as before.

## Doing it

**In the studio** (reelee-studio → Music video): drop the song, then videos and photos into "Add a video or photo". Press "Listen to the videos": each row then says "Has the song in it", "Heard the song in it, but couldn't tell where" or "Couldn't hear the song in it — so it's cut to the music", and each row has a "Has the song in it / Doesn't have the song" choice. Then go to Edit, "Cut it for me", and "Make the video". "Cut it for me" waits until every video has been listened to or declared; a project of photos only needs no listening.

**In Python** (the same functions the studio and the MCP connector call):

```python
from muvid.footage import service
service.set_song(fp, path="song.mp3")
service.add_clip(fp, path="walk.mp4")          # a video
service.add_clip(fp, path="abbey.jpg")         # a photo: stored upright, kind="still"
service.align(fp)                              # every video: listened to, or declared below
service.set_has_song(fp, clip_id="…", has_song="no")   # overrule listening, per clip
out = service.propose_edit(fp, pace="steady")  # slow | steady | driving | frantic
out["music"]       # what the cut to the music did: style, n_cuts, uses, on_beat per video
service.render(fp, edit_id=out["edit_id"])
```

**Without a project** — just plan free cuts over a song: `muvid.footage.music_cut.fill_spans(song_path, [FreeSource(...)], spans=..., envelope_of=..., canvas=...)`. It is pure: no file is written, and the result is ordinary `EdlEntry` cuts.

## What happens inside (so you can change it in the right place)

- **The edit model.** A *free cut* is an `EdlEntry` with `source_in` set: it shows its clip from that second of the clip's own time, whatever its alignment. `edl.placed_as(e, a)` turns it into the placement it implies (`offset = song_start − source_in`, vouched by construction), so every containment, blend and render check applies unchanged, and the trust gate never refuses a free cut. An unplaced clip or photo has a never-persisted `edl.unplaced` record carrying its duration (`STILL_DURATION_S` for a photo).
- **Where the cuts fall.** `muvid.montage.plan.plan_montage` (the montage subgenre's planner): every cut on a beat, bar or section boundary, denser in loud sections, with the reuse policy that lets a few clips carry a whole song. The style is picked from the tempo (`music_cut.pick_archetype`: slow dissolves under 92 BPM, hard cuts on the beat above). `grid` is excluded because an edit holds one picture per cut.
- **Which stretch of a video.** `music_cut.choose_source_in` scores every in-point by: picture-change hits on the beats inside the cut (relative to the clip's own mean), liveliness matched to the section's loudness, sharpness (so a whip-pan blur loses), and reuse of stretches already shown. The signal is `footage.beats.activity_signal`: frame differences with the camera move included, because in b-roll a pan arriving IS the event. It is measured once per clip (about 0.7 s for a 34 s phone clip) and cached under `beats/`.
- **Photos.** Named camera looks (`slow_push`, `pan_right`, `slow_pull`, `pan_left`, `punch_in`) at zoom 1.12, anchored on `burns.salient_box` (the photo's detailed region; the centre without `burns`).
- **Framing.** Free cuts are `cover`-cropped to the canvas, centred on the subject for photos (`music_cut.cover_crop`), computed on the size the picture is SHOWN at: a phone video stored 640×360 with a −90° rotation is portrait (`music_cut.display_size`).
- **Render.** The same assembler as synced cuts. A photo is a looped still input (`assemble._source_input`).
- **Speed.** "Cut it for me" is a synchronous op, so nothing heavy happens there: the song's analysis is measured at `set_song` and kept (`service.song_analysis`, keyed on the song hash), each video's hash and picture signal at `add_clip`, and probes and photo anchors are memoised per file. Photos are stored upright as JPEG; HEIC needs `pillow-heif`, and without it is refused with a plain reason.

## Traps

- **A short video cannot hold a long slot.** When it runs out, the rest of the slot goes to the least-used source that can hold it (`_take_over`), never to its neighbours; a blend with no footage either side is dropped (`_feasible_blends`).
- **Footage shorter than the song** is reused: each stretch at most once before repeats, with different stretches on each return. v1 fills the whole song rather than trimming it. Trimming to a section boundary is the UX recommendation, deferred to [reelee-web#402](https://github.com/thorwhalen/reelee-web/issues/402); `set_span` does it by hand today.
- **Energy-derived sections are coarse.** A 4-minute rock song can come back as one long "chorus", so pacing barely changes. Better structure is [muvid#133](https://github.com/thorwhalen/muvid/issues/133).
- **The studio's Edit preview cannot show a photo cut** (it plays `<video>` elements); the render is correct ([reelee-web#402](https://github.com/thorwhalen/reelee-web/issues/402)).
- **Do not hand-roll a montage with `muvid.montage.render_montage`** for a studio project: that subgenre renders straight to a file with no editable edit. The footage path above produces a named, editable edit.

## Extensions (filed, not built)

More visual channels [muvid#132](https://github.com/thorwhalen/muvid/issues/132) · more music features [muvid#133](https://github.com/thorwhalen/muvid/issues/133) · emotion matching [muvid#134](https://github.com/thorwhalen/muvid/issues/134) · choose the best song of several [muvid#135](https://github.com/thorwhalen/muvid/issues/135) · several songs with transitions [muvid#136](https://github.com/thorwhalen/muvid/issues/136) · capture-time order [muvid#137](https://github.com/thorwhalen/muvid/issues/137) · fingerprint detector [mixing#55](https://github.com/thorwhalen/mixing/issues/55) · studio photo strip, preview, pace control [reelee-web#402](https://github.com/thorwhalen/reelee-web/issues/402) · the timeline-editor document [muvid#138](https://github.com/thorwhalen/muvid/issues/138).

## Neighbours in the fleet

`tituli` for any text on screen (a title card, credits, captions; composite onto the finished video, never onto the stills). `arioso` to generate a song when the person has none (licence-clean for publishing). `burns` for Ken Burns motion specs and saliency. `mixing.audio.beat_grid` for the beat. `yb` to publish.

## References

Vancouver-referenced research, 2026-10-04:

- `references/methods_and_prior_art.md`: automatic music-video generation since Foote 2002, Audeosynth, visual beats, DP formulations, and detecting that a clip does not contain the song.
- `references/libraries_and_licences.md`: what to use now, later, or never (licences: madmom/Essentia/aubio/Panako/InsightFace are out).
- `references/apps_and_market.md`: Memories, Google Photos, Quik, CapCut and others; who aligns to what; the market and the gap; licensing a personal song.
- `references/ux_recommendation.md`: why this lives inside Music video with no up-front toggle, the exact copy, and the risks.
