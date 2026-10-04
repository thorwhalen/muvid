# muvid.footage.music_cut

Cut footage to the music — the montage half of a music video.

A music video in muvid has always assumed its footage is a RECORDING OF THE SONG — a
concert, a dance, a lip-sync — so every clip is *synced*: placed on the song by
listening to its own soundtrack ([`muvid.footage.align`](muvid.footage.align.html.md#module-muvid.footage.align)) and shown at exactly the
moment it was filmed. That is the right answer for that footage and the wrong one for
the other common case, which is the one phone galleries sell as “memories”: a day out
filmed in short clips and photos, plus a song that was never playing. There is nothing
to sync to, so the aligner rightly cannot place those clips, and an edit built only from
synced clips is mostly black.

This module is the other answer. Footage that does not contain the song is cut TO the
music instead of synced WITH it:

* **where the cuts fall** is the montage planner’s decision
  ([`muvid.montage.plan.plan_montage()`](muvid.montage.plan.html.md#muvid.montage.plan.plan_montage)): every cut on a beat, bar or section
  boundary, denser in loud sections, with the reuse policy that lets a few clips carry a
  whole song and keeps a revisited clip from showing the same stretch twice;
* **which stretch of a video each cut shows** is decided here, by the picture: the
  stretch whose visual hits ([`muvid.footage.beats.activity_signal()`](muvid.footage.beats.html.md#muvid.footage.beats.activity_signal) — the onsets of
  picture change, camera moves included) land on the beats inside the cut, and whose
  liveliness suits the section’s loudness. That is the alignment phone “memories” do
  not do;
* **how a still moves** is a named camera look (push, pull, pan) toward the photo’s
  salient region (`burns.salient_box` when `burns` is installed).

The output is ordinary [`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry) cuts with
[`source_in`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry.source_in) set — *free* cuts — so a montage is an
edit like any other: saved, changed cut by cut, rendered by the same assembler over the
clean song. Synced and free cuts mix in one edit: [`fill_spans()`](#muvid.footage.music_cut.fill_spans) fills only the
spans it is given, which the service uses to put free cuts in the gaps a synced edit
leaves.

Pure planning: no file is written; the measurements (song analysis, picture-change
envelopes, salient boxes) come in through keyword seams with working defaults.

### Module Attributes

| [`ARCHETYPE_AUTO`](#muvid.footage.music_cut.ARCHETYPE_AUTO)   | Pick the cutting style from the song (see [`pick_archetype()`](#muvid.footage.music_cut.pick_archetype)).   |
|-------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|

### Functions

| [`choose_source_in`](#muvid.footage.music_cut.choose_source_in)(env, \*, clip_duration, length)   | Where in a video a cut of `length` seconds should start: `(source_in, fit)`.                           |
|-----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| [`fill_spans`](#muvid.footage.music_cut.fill_spans)(song_path, sources, \*[, spans, ...])   | Free cuts of `sources` covering `spans` of the song (default: all of it).                              |
| [`pick_archetype`](#muvid.footage.music_cut.pick_archetype)(analysis)                           | The cutting style for a song: a slow dissolve below `SLOW_TEMPO_BPM`, hard cuts on the beat otherwise. |

### Classes

| [`Envelope`](#muvid.footage.music_cut.Envelope)(hop_s, activity, hits[, sharpness])   | A video's picture-change signal on a regular grid of its own time.                                                                                       |
|-------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`FreeSource`](#muvid.footage.music_cut.FreeSource)(clip_id, path[, kind, duration_s])  | One picture source a montage may cut to: a video (`duration_s` its length) or a still (`kind="still"`, `duration_s=None` — it can be held for any span). |
| [`MusicCut`](#muvid.footage.music_cut.MusicCut)([entries, report])                    | The result: free cuts for the requested spans, and an account of how they were chosen (tempo, style, how well each video's picture landed on the beat).  |

### muvid.footage.music_cut.ARCHETYPE_AUTO *= 'auto'*

Pick the cutting style from the song (see [`pick_archetype()`](#muvid.footage.music_cut.pick_archetype)).

### *class* muvid.footage.music_cut.Envelope(hop_s, activity, hits, sharpness=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A video’s picture-change signal on a regular grid of its own time.

#### *classmethod* from_signals(record)

From [`muvid.footage.beats.activity_signal()`](muvid.footage.beats.html.md#muvid.footage.beats.activity_signal)’s `signals` mapping.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Envelope`](#muvid.footage.music_cut.Envelope)]

### *class* muvid.footage.music_cut.FreeSource(clip_id, path, kind='video', duration_s=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One picture source a montage may cut to: a video (`duration_s` its length) or a
still (`kind="still"`, `duration_s=None` — it can be held for any span).

### *class* muvid.footage.music_cut.MusicCut(entries=<factory>, report=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The result: free cuts for the requested spans, and an account of how they were
chosen (tempo, style, how well each video’s picture landed on the beat).

### muvid.footage.music_cut.choose_source_in(env, , clip_duration, length, beat_offsets=(), used=(), energy=0.0, lead_s=0.0, trail_s=0.0, ordinal=0)

Where in a video a cut of `length` seconds should start: `(source_in, fit)`.

Scores every in-point on the envelope’s grid (`lead_s`/`trail_s` of extra
footage kept free on either side, for a blend), by three terms:

* **hits on the beat** — the mean picture-change hit at each `beat_offsets` (beats
  inside the cut, in seconds from its start), relative to the video’s own mean hit:
  above 1 means the picture changes ON the beat more than it does anywhere;
* **liveliness matched to loudness** — `energy` in [-1, 1] (quiet to loud
  section) times how lively the stretch is relative to the whole video;
* **reuse** — the fraction of the stretch already shown by another cut, penalised.

`fit` is the hits term at the chosen point (0 when there are no inner beats or no
envelope). Without an envelope it falls back to a golden-ratio stride over the
room, the montage planner’s own blind rule, with reuse still avoided.

```pycon
>>> hits = np.zeros(100); hits[[20, 30]] = 1.0   # changes at 2.0 s and 3.0 s
>>> env = Envelope(hop_s=0.1, activity=np.full(100, 0.1), hits=hits)
>>> s, fit = choose_source_in(env, clip_duration=10.0, length=2.0, beat_offsets=[0.5, 1.5])
>>> round(s, 2), fit > 1
(1.5, True)
```

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float)]

### muvid.footage.music_cut.fill_spans(song_path, sources, \*, spans=None, song_duration=None, analysis=None, archetype='auto', cut_feel='', envelope_of=None, anchor_of=<function \_default_anchor>, canvas=(1920, 1080), fps=30.0, fit='cover', size_of=<function display_size>)

Free cuts of `sources` covering `spans` of the song (default: all of it).

Seams, each with a default that works: `analysis` (the song’s beats, bars and
sections — [`muvid.montage.analysis.analyze()`](muvid.montage.analysis.html.md#muvid.montage.analysis.analyze)), `envelope_of` (a video’s
picture-change [`Envelope`](#muvid.footage.music_cut.Envelope); `None` falls back to the planner’s blind stride),
`anchor_of` (a still’s focal point). `archetype` is a montage archetype with one
picture per slot, or `"auto"`; `cut_feel` a montage pace (`slow`, `steady`,
`driving`, `frantic`). `fit` is one of `FITS`; `size_of` reads a
picture’s displayed size (for `cover`).

Returns [`MusicCut`](#muvid.footage.music_cut.MusicCut) whose `entries` are validated-shape
[`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry) free cuts, contiguous over each span, in song
order (spans with nothing in them are simply not covered — the caller’s
`fill_gaps` makes that explicit).

* **Return type:**
  [`MusicCut`](#muvid.footage.music_cut.MusicCut)

### muvid.footage.music_cut.pick_archetype(analysis)

The cutting style for a song: a slow dissolve below `SLOW_TEMPO_BPM`, hard
cuts on the beat otherwise.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> class A: tempo_bpm = 80.0
>>> pick_archetype(A())
'ballad_dissolve'
```
