# muvid.renderers.lipsync

Render strategy: lipsync.

For shots where a character is “singing on screen”. We need a still
image of the character (the curated anchor) plus the audio slice. Calls
`falaw.animate_face` (image+audio → talking video).

If multiple characters are present, we pick the first one and warn —
multi-character lipsync isn’t in v0.

### Functions

| `render_lipsync`(ctx, \*[, quality])   |    |
|----------------------------------------|----|
