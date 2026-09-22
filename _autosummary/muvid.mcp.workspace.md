# muvid.mcp.workspace

Per-user output bucket for the muvid `music-visualizer` MCP genre.

A remote MCP connector is stateless and multi-user, so each caller must be isolated
and address work by `project_id` (never a path). The visualizer is itself stateless
— a render is a pure function of (audio, cover, visual) — so a “project” here is just a
**lightweight bucket** where a caller’s renders land, not a full nw/muvid project (no
graph, no asset store). That keeps the `music-visualizer` genre the cheapest possible
2nd genre (thorwhalen/muvid#3); a richer per-user asset library (content-addressed
uploads shared across genres) is a deliberate follow-up.

Layout (default root `~/.local/share/muvid`; override via `MUVID_DATA_HOME`):

- `{root}/visualizer/projects/{email}/{project_id}/manifest.json` — the bucket
- `{root}/visualizer/projects/{email}/{project_id}/renders/{render_id}/` — one render
  (its `.mp4`, optional `thumbnail.jpg`, and a `meta.json` sidecar)

Per the app-data-lifecycle rule this lives in the user-data dir, **never** inside the
app/deploy tree (a deploy’s `rsync --delete` would erase it).

### Module Attributes

| [`DATA_HOME_ENV_VAR`](#muvid.mcp.workspace.DATA_HOME_ENV_VAR)   | Env var overriding the muvid data root (where per-user visualizer buckets live).   |
|----------------------------------------------------------------------|------------------------------------------------------------------------------------|

### Functions

| [`data_root`](#muvid.mcp.workspace.data_root)()   | The muvid data root: `$MUVID_DATA_HOME` or `~/.local/share/muvid`.   |
|----------------------------------------------------------------|----------------------------------------------------------------------|

### Classes

| [`VisualizerProject`](#muvid.mcp.workspace.VisualizerProject)(email, project_id, root)   | One caller's visualizer bucket — a folder its renders land in.   |
|-----------------------------------------------------------------------------------------------|------------------------------------------------------------------|
| [`VisualizerWorkspace`](#muvid.mcp.workspace.VisualizerWorkspace)(email, root)             | A single caller's private visualizer area, addressed by `email`. |

### muvid.mcp.workspace.DATA_HOME_ENV_VAR *= 'MUVID_DATA_HOME'*

Env var overriding the muvid data root (where per-user visualizer buckets live).
Re-exported from [`muvid.paths`](muvid.paths.md#module-muvid.paths), the SSOT — this module used to carry its own
verbatim copy of both, beside a second copy in `muvid/footage/workspace.py`.

### *class* muvid.mcp.workspace.VisualizerProject(email, project_id, root)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One caller’s visualizer bucket — a folder its renders land in.

`root` is exposed so `nw.create_genre_project`’s all-or-nothing rollback
(`nw.genres._rollback_project` removes `project.root`) reverts a half-created
bucket. Kept storage-only: no nw graph, no asset library.

#### list_renders()

This bucket’s renders (newest-first), from each render’s `meta.json`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### new_render_dir(render_id)

Create + return a fresh directory for one render (traversal-checked id).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### *class* muvid.mcp.workspace.VisualizerWorkspace(email, root)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A single caller’s private visualizer area, addressed by `email`.

`email` and every `project_id` are validated as single path components, so a
caller can never escape their own subtree.

#### create_project(project_id, , title='', force=False)

Create (and return) a new visualizer bucket under this user.

* **Return type:**
  [`VisualizerProject`](#muvid.mcp.workspace.VisualizerProject)

#### list_projects()

This user’s buckets: `[{project_id, title}]` (newest-modified first).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### open_project(project_id)

Open an existing visualizer bucket (raises if it doesn’t exist).

* **Return type:**
  [`VisualizerProject`](#muvid.mcp.workspace.VisualizerProject)

### muvid.mcp.workspace.data_root()

The muvid data root: `$MUVID_DATA_HOME` or `~/.local/share/muvid`.

Public API (`muvid.mcp` re-exports it), so the name stays though the body moved.
A forwarder, not an alias, for the introspection reason given on the footage twin.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
