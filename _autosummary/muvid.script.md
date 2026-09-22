# muvid.script

Script (screenplay) markdown ↔ ShotSpec list.

The script is a human-friendly markdown anchored to the song timeline.
Each `###` header introduces a shot with a header line of the form:

```default
### <id> | <start>-<end> | <strategy>
```

Then optional `**key**: value` lines (`env`, `chars`, `camera`,
`framing`) followed by a free-form prose description block. Sections
are introduced by `## [<label>] <start> → <end>` headers but are
optional (the project-level sections list is the SSOT for those).

The parse is intentionally lenient — we ignore anything we don’t
understand — so the agent can write the file and the user can edit it
without learning a strict grammar.

### Functions

| [`parse_and_apply`](#muvid.script.parse_and_apply)(project, \*[, path])   | Parse `script/script.md` and upsert any sections/shots it defines.   |
|-----------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| [`parse_script`](#muvid.script.parse_script)(md)                       | Parse a script markdown string into (sections, shots).               |
| [`render_script`](#muvid.script.render_script)(sections, shots)         | Inverse of `parse_script`.                                           |
| [`write_script`](#muvid.script.write_script)(project)                  | Render the project's current sections+shots to `script/script.md`.   |

### muvid.script.parse_and_apply(project, , path=None)

Parse `script/script.md` and upsert any sections/shots it defines.

Existing sections/shots not present in the script are left alone.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.script.parse_script(md)

Parse a script markdown string into (sections, shots).

Sections are returned only when explicitly headed with `## [label]`;
otherwise it’s an empty list and you should rely on the project’s
section list separately.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`SectionSpec`](muvid.schema.md#muvid.schema.SectionSpec)], [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ShotSpec`](muvid.schema.md#muvid.schema.ShotSpec)]]

### muvid.script.render_script(sections, shots)

Inverse of `parse_script`. Writes the canonical markdown form.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.script.write_script(project)

Render the project’s current sections+shots to `script/script.md`.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
