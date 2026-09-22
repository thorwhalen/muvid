# muvid.renderers.still

Render strategy: still — a single image held for the shot duration.

Cheapest possible render. Uses ffmpeg to loop the image and mux the
shot’s audio slice onto it. If we have an environment anchor, we use
it; otherwise we generate a still via `falaw.generate_image`.

### Functions

| `render_still`(ctx, \*[, quality])   |    |
|--------------------------------------|----|
