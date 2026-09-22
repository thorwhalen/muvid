# muvid.lyricvid.manifest

The lyric-video subgenre’s manifest — muvid’s own first plugin.

Deliberately stdlib-only and free of any import from the rest of
[`muvid.lyricvid`](muvid.lyricvid.html.md#module-muvid.lyricvid): this module is what a catalogue, a UI form, an MCP tool
definition or an LLM choosing among installed subgenres reads, and none of them
should pay for pysubs2, numpy or Playwright to do it. The renderer is named as
a string and imported only when a render actually runs.

A third-party plugin should look exactly like this file.
