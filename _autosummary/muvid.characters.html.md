# muvid.characters

Character cards + reference image curation via lookbook.

A character lives at `characters/<name>/` inside a project. The card
(`card.json`) holds the human-meaningful description, the reference
image URL/path used as the lipsync anchor, and the optional voice spec.

Reference images go through three states:

- `refs/` — raw image dump (anything the user collected, plus
  optional `generate_references` outputs).
- `selected/` — lookbook’s curated subset, ready for LoRA training
  or just for picking the canonical face.
- `card.reference_image_url` — a single chosen anchor (the best
  selected image) used by every downstream render.

### Functions

| [`add_character`](#muvid.characters.add_character)(project, name, \*[, ...])           | Create or update a character card.                                                                                                                                                                                    |
|----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`add_reference_images`](#muvid.characters.add_reference_images)(project, name, images, \*)   | Drop user-provided images into `characters/<name>/refs/`.                                                                                                                                                             |
| [`curate_references`](#muvid.characters.curate_references)(project, name, \*[, k, recipe]) | Run lookbook on `refs/` and copy the selected images to `selected/`.                                                                                                                                                  |
| [`curate_references_interactive`](#muvid.characters.curate_references_interactive)(project, name, ...) | Same as [`curate_references()`](#muvid.characters.curate_references) but routes through `lookbook.curate_interactive()` so the user (or a scripted decision callable) drives keep/reject decisions per round. |
| [`generate_reference_images`](#muvid.characters.generate_reference_images)(project, name, \*)      | Generate raw reference images via `falaw.generate_image`.                                                                                                                                                             |
| [`get_character_anchor_image`](#muvid.characters.get_character_anchor_image)(project, name)         | Return the canonical anchor image path for a character.                                                                                                                                                               |

### muvid.characters.add_character(project, name, , description='', voice_id='', reference_audio_url='', voice_style='')

Create or update a character card. Idempotent.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### muvid.characters.add_reference_images(project, name, images, , copy=True)

Drop user-provided images into `characters/<name>/refs/`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

### muvid.characters.curate_references(project, name, , k=8, recipe='person_mock')

Run lookbook on `refs/` and copy the selected images to `selected/`.

Default recipe is `person_mock` so this works without the heavy ML
dependencies; pass `recipe="person"` once those are installed.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

### muvid.characters.curate_references_interactive(project, name, , on_decision, k=8, recipe='person_mock', present=6, max_rounds=20)

Same as [`curate_references()`](#muvid.characters.curate_references) but routes through
`lookbook.curate_interactive()` so the user (or a scripted
decision callable) drives keep/reject decisions per round.

`on_decision` is forwarded directly; see lookbook for the API.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

### muvid.characters.generate_reference_images(project, name, , n=6, style_variants=(), quality='balanced')

Generate raw reference images via `falaw.generate_image`.

The character’s `description` is used as the prompt; each variant
(e.g. “front portrait”, “three-quarter”, “wide shot”) is appended.
Output goes to `characters/<name>/refs/`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

### muvid.characters.get_character_anchor_image(project, name)

Return the canonical anchor image path for a character.

Resolves in order: `card.reference_image_path` (curated),
first file in `selected/`, first file in `refs/`.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
