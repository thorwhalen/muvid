# muvid.footage.explain

Why each cut is where it is — the post-mortem of an edit, in plain words.

A person watching an edit asks “why did it cut THERE?”, “why this clip?”, “why is this
one slightly off the beat?”. This module answers from the edit itself and the same
measurements the cutting used — the song’s beat grid and sections, each video’s picture
signal — so the answer is about the edit as it stands now (a cut a person changed is
explained as changed), and the screen, the assistant and a reader all get one record.

Each cut gets a few sentences in a fixed order — *where and on what* → \*how the picture
changes\* → *what picture and why that stretch* → *what is worth a look* — plus the
facts behind them (song times, the beat it lands on, the fade’s real start) and
`flags` for the ones worth a look: `straddles_the_beat`, `off_the_beat`,
`long_hold`, `repeated_stretch`. Every clock time in a sentence is also listed in
`times` with its second, so a screen can turn it into a link that seeks the player.

Plain nouns only (beat, bar, fade, picture, photo, clip); clock times, never floats.
The explanation says what the system did by default when that is the reason — the
post-mortem is only useful if it admits its defaults.

Pure: the measurements come in as arguments ([`explain()`](#muvid.footage.explain.explain)); the service builds
them (`service.explain_edit`).

### Module Attributes

| [`FLAG_WORDS`](#muvid.footage.explain.FLAG_WORDS)   | What each flag says, for a chip.   |
|---------------------------------------------------------------|------------------------------------|

### Functions

| [`explain`](#muvid.footage.explain.explain)(entries, \*, analysis, song_duration, ...)   | The post-mortem of an edit: `{summary, times, cuts: [...]}`.   |
|-------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| [`clock`](#muvid.footage.explain.clock)(t)                                             | `72.41` -> `"1:12.4"` — how a time is said.                    |

### muvid.footage.explain.FLAG_WORDS *= {'long_hold': 'long hold', 'off_the_beat': 'not on a beat', 'repeated_stretch': 'shown before', 'straddles_the_beat': 'starts before the beat'}*

What each flag says, for a chip.

### muvid.footage.explain.clock(t)

`72.41` -> `"1:12.4"` — how a time is said.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> clock(72.41), clock(5.0)
('1:12.4', '0:05.0')
```

### muvid.footage.explain.explain(entries, \*, analysis, song_duration, clips, alignments={}, envelope_of=<function <lambda>>, selection={}, grid_note=None, window=None, scope=None)

The post-mortem of an edit: `{summary, times, cuts: [...]}`.

`entries` are the edit’s validated [`EdlEntry`](muvid.footage.edl.md#muvid.footage.edl.EdlEntry) cuts;
`analysis` the song’s (extended) beat grid and sections; `clips` maps a clip
id to `{name, kind ('video'|'still'), role}`; `alignments` the clip
placements (for synced cuts); `envelope_of(clip_id)` a video’s picture signal
([`muvid.footage.music_cut.Envelope`](muvid.footage.music_cut.md#muvid.footage.music_cut.Envelope)) or `None`; `selection` how the
edit was made (`pace`, `style`, `strategy`). `window` (song seconds)
keeps only the cuts that overlap it; the summary is of the whole edit — or of
`scope`, the part of the song a trimmed edit covers (its `span`), so the
black outside it is not counted as shots.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
