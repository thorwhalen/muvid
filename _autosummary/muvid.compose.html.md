# muvid.compose

Compose all rendered shots into the final music video.

Concatenates per-shot videos in timeline order, then overlays the
master song audio so any per-shot audio (lipsync output) gets mixed
under the original mix. Pure ffmpeg — no fancy crossfades in v0.

### Functions

| [`compose`](#muvid.compose.compose)(project, \*[, out_name, use_song_audio])   | Concatenate `shots/*/output.mp4` and (optionally) overlay song audio.   |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|

### muvid.compose.compose(project, , out_name='final.mp4', use_song_audio=True)

Concatenate `shots/*/output.mp4` and (optionally) overlay song audio.

`use_song_audio=True` (default) replaces the audio track with the
original song’s audio over `[shots[0].start_s, shots[-1].end_s]`.
Set to False to keep each shot’s own audio (useful when most shots
are lipsync renders that already carry their slice).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
