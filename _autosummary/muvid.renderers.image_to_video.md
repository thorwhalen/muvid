# muvid.renderers.image_to_video

Render strategy: image_to_video.

Workflow:

1. If we have an environment anchor image, use it as the i2v seed.
   Else, generate a fresh storyboard still via `falaw.generate_image`.
2. Call `falaw.image_to_video(image, prompt, extra={duration: ...})`.
3. Trim/pad to the shot’s exact duration.

### Functions

| `render_image_to_video`(ctx, \*[, quality])   |    |
|-----------------------------------------------|----|
