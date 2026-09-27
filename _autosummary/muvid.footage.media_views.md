# muvid.footage.media_views

What an editor draws: per-camera filmstrips and the song’s waveform peaks.

The studio’s Edit tab (the redesign in the plan of record’s *Edit tab* note, §5) draws
every camera as a strip of thumbnails on the song’s timeline and the song as a
waveform. Both are derived from media the project already holds, both are expensive
to make and cheap to keep, so both are CACHED by content and parameters:

- **filmstrips** — one or more JPEG sprite sheets per clip (`cols x rows` frames
  each, `frame_h` pixels tall, width from the clip’s own aspect), sampled at `fps`
  frames per second of CLIP time: frame `i` of a clip is its picture at
  `i / fps` s. Cached at `footage/filmstrips/<clip-hash>-<params>/` with an
  `index.json`, so a re-call is a file read and a re-uploaded clip with other bytes
  gets new sheets. Each sheet is registered in the host’s catalog (content-addressed)
  when the project is hosted, so the screen loads it by `artifact_id`.
- **peaks** — the song’s per-bucket peak absolute amplitude, mono, normalised to
  `0..1` over `n` equal buckets of the song. Cached at
  `footage/peaks/<song-hash>-<n>.json`.

Every file is written as a NEW file (temp + rename) — the catalog hardlinks them.

### Module Attributes

| [`FILMSTRIP_FPS`](#muvid.footage.media_views.FILMSTRIP_FPS)   | frames per second of clip time, frame height in pixels, frames per sheet (columns x rows), JPEG quality (ffmpeg's 2-31, lower is better).   |
|------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|

### Functions

| [`clip_filmstrip`](#muvid.footage.media_views.clip_filmstrip)(fp, clip_id, \*[, fps, ...])    | One clip's filmstrip record, generating (and caching) it when missing.                                                                                                                  |
|-------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`song_peaks`](#muvid.footage.media_views.song_peaks)(fp, \*, n)                          | `{duration_s, n, peaks}` — `n` buckets of the song's peak <br/><br/>```<br/>|amplitude|<br/>```<br/><br/>, mono, normalised so the loudest bucket is 1.0 (all zeros for a silent song). |
| [`filmstrip_key`](#muvid.footage.media_views.filmstrip_key)(clip_hash, \*, fps, height, ...) | The cache directory name: the clip's content and every parameter.                                                                                                                       |

### muvid.footage.media_views.FILMSTRIP_FPS *= 2.0*

frames per second of clip time, frame height in
pixels, frames per sheet (columns x rows), JPEG quality (ffmpeg’s 2-31, lower is
better). 2 fps x 90 px x 10x10 puts 50 s of a clip on one ~100-200 KB sheet.

* **Type:**
  Default sampling of a filmstrip

### muvid.footage.media_views.clip_filmstrip(fp, clip_id, , fps=2.0, height=90, cols=10, rows=10)

One clip’s filmstrip record, generating (and caching) it when missing.

Returns `{duration_s, n_frames, frame_w, frame_h, sheets: [{artifact_id, cols,
rows, first_frame, n_frames}]}`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### muvid.footage.media_views.filmstrip_key(clip_hash, , fps, height, cols, rows)

The cache directory name: the clip’s content and every parameter.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> filmstrip_key("ab" * 32, fps=2.0, height=90, cols=10, rows=10)
'abababababababab-f2-h90-10x10-v2'
```

### muvid.footage.media_views.song_peaks(fp, , n)

`{duration_s, n, peaks}` — `n` buckets of the song’s peak 

```
|amplitude|
```

, mono,
normalised so the loudest bucket is 1.0 (all zeros for a silent song).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
