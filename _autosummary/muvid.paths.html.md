# muvid.paths

The ONE place muvid decides where its data lives.

Every muvid part that persists anything answers *where* from here: the footage genre’s
stateful projects (`muvid/footage/workspace.py`), the visualizer’s per-user render
buckets (`muvid/mcp/workspace.py`), and — new with this module — the local, single-user
project folders the part-3 pipeline and the part-4 subgenres work in.

Default root `~/.local/share/muvid`, overridable with `$MUVID_DATA_HOME`. Per the
app-data-lifecycle rule this is the user-data dir and **never** the app/deploy tree: a
deploy’s `rsync --delete` treats anything it did not build as drift and erases it.

**Why this module exists at all, rather than a third copy of a four-line function.**
`data_home` and [`safe_component()`](#muvid.paths.safe_component) were duplicated verbatim in the two workspace
modules, and the part-3 surfaces had *no* copy — `root` is a required positional on
every part-3 CLI verb \*\*except `serve``**, and on
:class:`muvid.project.MusicVideoProject`, so nothing computed a default and the only
written-down answer lived in prose: ``.claude/skills/muvid/SKILL.md` and the `README`
both said `~/muvid/<song-stem>`. That was followed literally, which put 36 MB of a real
project — two generated song takes, three rendered videos, the poem sources — in
`~/muvid/il-pleut`: an app-named directory under `$HOME` that no deploy, backup or
tool owns. A default stated in prose drifts from the code and cannot be tested; one
computed by a function can be both. So the fix is not a better sentence, it is
[`project_root()`](#muvid.paths.project_root) plus a `muvid project-root` verb that the skill and the README
*call* instead of a path they *state*.

**\`\`serve\`\` is the exception, and it is the one that actually wrote.** `serve(root=".")`
defaults to the cwd, which is a fine convention for “the project I am standing in” — but
the UI has no create-a-project action and used to `mkdir -p` its way into whatever
directory it was pointed at, so `muvid serve` in any folder plus one save in the browser
scaffolded `script/` and `.muvid/` there. The fix is in `muvid/ui/app.py`, which now
refuses a root that is not a project, rather than here: the default is *right*, and it was
the absent precondition that was wrong. Do not restate the old “required on every verb”
claim — it was in this docstring, in `facade` and in `CLAUDE.md`, and it was false in
exactly the place it mattered.

A person choosing to keep their own working files in `~/Downloads` is a different thing
and not this module’s business — the rule is about where a *tool* writes by default.

### Module Attributes

| [`DATA_HOME_ENV_VAR`](#muvid.paths.DATA_HOME_ENV_VAR)   | Env var overriding the muvid data root.                                            |
|----------------------------------------------------------------------|------------------------------------------------------------------------------------|
| [`PROJECTS_KIND`](#muvid.paths.PROJECTS_KIND)       | The `{kind}` subfolder local (single-user, non-connector) project folders live in. |

### Functions

| [`data_home`](#muvid.paths.data_home)()                      | The muvid data root: `$MUVID_DATA_HOME` or `~/.local/share/muvid`.             |
|-----------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`project_root`](#muvid.paths.project_root)(name, \*[, kind])   | Where a local muvid project named `name` belongs: `{data_home}/{kind}/{name}`. |
| [`safe_component`](#muvid.paths.safe_component)(value, \*, label) | A single, traversal-safe path component (no `/`, `\`, `..`, or empties).       |

### muvid.paths.DATA_HOME_ENV_VAR *= 'MUVID_DATA_HOME'*

Env var overriding the muvid data root. ONE knob for the root, never one per kind —
N env vars where one belongs is the anti-pattern the app-data-lifecycle rule names.

### muvid.paths.PROJECTS_KIND *= 'projects'*

The `{kind}` subfolder local (single-user, non-connector) project folders live in.
Data never goes at the root itself: leaving the root open is what let the footage
genre add `music_video/` and the visualizer `visualizer/` without a migration.

**Deliberately not the part-3 genre slug issue #4 will pick** (recommended there:
`music-video-ai`), and deliberately kept despite reading like
`music_video/projects/{email}/` one level down. Two reasons. The connector layout is
per-email and per-`project_id` — `{root}/{slug}/projects/{email}/{id}` — while this
is a local, single-user, per-*name* folder the CLI creates; they are different shapes,
so #4 landing does not imply these folders move, and naming this after a slug that is
still only *recommended* would pre-commit to it. The apparent collision is
disambiguated by depth: `projects/` at depth 1 is local, `projects/` under a genre
slug is the connector’s. If #4 decides otherwise, this is a one-word change plus a
migration of whatever exists by then — which is why it is a named constant.

### muvid.paths.data_home()

The muvid data root: `$MUVID_DATA_HOME` or `~/.local/share/muvid`.

The override is `expanduser`-ed and **required to be absolute**, because this path
is now printed by `muvid project-root` for a caller to interpolate. Without the
first, `MUVID_DATA_HOME=~/store` yields a *literal* `~` directory, so the printed
string and what [`muvid.project.MusicVideoProject`](muvid.project.html.md#muvid.project.MusicVideoProject) resolves it to are two
different places. Without the second, a relative override roots the whole data store
at the cwd — which, run from an app directory, is the very failure this module exists
to prevent, reached through its own knob. A misconfigured root is refused loudly
rather than read as something plausible.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### muvid.paths.project_root(name, , kind='projects')

Where a local muvid project named `name` belongs: `{data_home}/{kind}/{name}`.

Returns the path without creating it — `muvid init` (or a subgenre’s own renderer)
owns creation, and a resolver that made directories could not be used to *ask* where
a project would go.

`kind` is the seam for a surface that wants its own subtree rather than sharing
`projects/`; the connector-facing genres already use their own (`music_video/`,
`visualizer/`) through the workspace classes that own those layouts.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### muvid.paths.safe_component(value, , label)

A single, traversal-safe path component (no `/`, `\`, `..`, or empties).

Every caller-supplied name reaching the data root goes through this. On the footage
MCP surface the names arrive from a remote OAuth caller, so there it is a trust
boundary rather than a tidiness check; [`project_root()`](#muvid.paths.project_root) is CLI-only and not on
that surface. Behaviour is unchanged from the two copies this replaced — notably
there is no length cap and no unicode normalisation, both pre-existing.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
