# muvid.montage.manifest

The montage subgenre’s manifest — a beat-cut montage from a pool of stills and clips.

Deliberately stdlib-only and free of any import from the rest of
[`muvid.montage`](muvid.montage.md#module-muvid.montage): this module is what a catalogue, a UI form, an MCP tool
definition or an LLM choosing among installed subgenres reads, and none of them
should pay for numpy or ffmpeg to do it. The renderer is named as a string and
imported only when a render actually runs.

The shape copies [`muvid.lyricvid.manifest`](muvid.lyricvid.manifest.md#module-muvid.lyricvid.manifest) on purpose — that file is the
reference a third-party plugin is told to look like, and a second in-tree plugin
that drifted from it would make the reference ambiguous.

### Module Attributes

| [`SLUG`](#muvid.montage.manifest.SLUG)                | Hyphen-free because it is one word; the slug is a persisted path segment and a public contract value from the day it ships, so it is chosen once.   |
|----------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| [`MAX_MEDIA_PER_INPUT`](#muvid.montage.manifest.MAX_MEDIA_PER_INPUT) | The largest list of files one input may carry.                                                                                                      |

### muvid.montage.manifest.MAX_MEDIA_PER_INPUT *= 64*

The largest list of files one input may carry. Stated here (and enforced in
[`muvid.montage.pipeline`](muvid.montage.pipeline.md#module-muvid.montage.pipeline), which reads `MUVID_MONTAGE_MAX_MEDIA`) so a
UI can show the bound before a caller uploads 200 photos. The runtime schema
validator does not enforce `maxItems`; the pipeline REFUSES past it.

### muvid.montage.manifest.SLUG *= 'montage'*

Hyphen-free because it is one word; the slug is a persisted path segment and
a public contract value from the day it ships, so it is chosen once.
