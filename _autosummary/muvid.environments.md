# muvid.environments

Environment cards + canonical establishing image generation.

### Functions

| [`add_environment`](#muvid.environments.add_environment)(project, name, \*[, ...])        | Create or update an environment card.                                |
|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| [`get_environment_anchor_image`](#muvid.environments.get_environment_anchor_image)(project, name)      | Return the canonical environment image, or None if not yet rendered. |
| [`render_environment`](#muvid.environments.render_environment)(project, name, \*[, quality]) | Generate the canonical establishing image for the environment.       |

### muvid.environments.add_environment(project, name, , description='', time_of_day='', lighting='')

Create or update an environment card.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.environments.get_environment_anchor_image(project, name)

Return the canonical environment image, or None if not yet rendered.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.environments.render_environment(project, name, , quality='high')

Generate the canonical establishing image for the environment.

Saves to `environments/<name>/establishing.png` and stores the
relative path on the card as `reference_image_path`.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
