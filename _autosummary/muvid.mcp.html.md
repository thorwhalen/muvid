# muvid.mcp

muvid MCP server — the `music-visualizer` tool surface for a remote connector.

Optional subpackage (extra `muvid[mcp]`: `fastmcp` + `py2mcp` + `nw`).
`import muvid` never imports this. The connector references the tools by name via
[`TOOL_REFS`](#muvid.mcp.TOOL_REFS); [`register_tools()`](#muvid.mcp.register_tools) aggregates them onto a host FastMCP server
(the unified reelee AV connector, thorwhalen/muvid#3), and [`build_server()`](#muvid.mcp.build_server)
assembles a standalone server for local (stdio) testing.

Almost every tool is **free**. The one exception is
`propose_lyric_treatments_ai` (`COSTED_TOOLS`), which calls an LLM to author a
lyric-video treatment — muvid’s first tool that spends money. It is a separate tool from
the free heuristic proposer because a host meters by tool NAME and cannot see an
argument, so a “free unless you pass a flag” tool is unmeterable by construction.
Its cost is reported as UNKNOWN rather than zero: muvid’s budget gate is conjunctive and
an unpriced call must force approval (muvid#47).

### Module Attributes

| [`TOOL_NAMES`](#muvid.mcp.TOOL_NAMES)   | All tools this package exposes (all free).                                      |
|---------------------------------------------------------------|---------------------------------------------------------------------------------|
| [`FREE_TOOLS`](#muvid.mcp.FREE_TOOLS)   | Alias — muvid has no costed tools.                                              |
| [`TOOL_REFS`](#muvid.mcp.TOOL_REFS)    | Bare tool name → its `module:function` reference (tools live in three modules). |

### Functions

| [`register_tools`](#muvid.mcp.register_tools)(server, \*[, prefix, include, ...])   | Register muvid's MCP tools onto an EXISTING FastMCP `server` — the aggregation seam for a host connector (the unified reelee connector, muvid#3).   |
|-------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| [`build_server`](#muvid.mcp.build_server)(\*[, name, instructions, auth, ...])    | Assemble a standalone FastMCP server exposing muvid's tools (stdio dev/testing).                                                                    |
| [`current_email`](#muvid.mcp.current_email)()                                      | The caller's identity for the in-flight tool call (raises if unauthenticated).                                                                      |
| [`token_email`](#muvid.mcp.token_email)()                                        | The verified caller's email from the OAuth token (`email` claim, else `sub`).                                                                       |
| [`use_email`](#muvid.mcp.use_email)(email)                                     | Bind the caller identity for the duration of the block (local/stdio/testing).                                                                       |
| [`data_root`](#muvid.mcp.data_root)()                                          | The muvid data root: `$MUVID_DATA_HOME` or `~/.local/share/muvid`.                                                                                  |

### Classes

| [`VisualizerWorkspace`](#muvid.mcp.VisualizerWorkspace)(email, root)   | A single caller's private visualizer area, addressed by `email`.   |
|-------------------------------------------------------------------------------------|--------------------------------------------------------------------|

### muvid.mcp.FREE_TOOLS *= ['list_visuals', 'list_projects', 'project_status', 'render_visualizer', 'set_song', 'add_footage', 'add_footage_folder', 'align_footage', 'propose_edit', 'footage_timeline', 'assemble_music_video', 'footage_status', 'list_strategies', 'footage_editor_document', 'footage_edl_from_annotations', 'beat_grid', 'list_music_video_projects', 'remove_footage', 'score_footage', 'footage_score_status', 'footage_scores', 'list_archetypes', 'analyze_song_lyrics', 'propose_lyric_treatments', 'validate_lyric_treatment', 'render_lyric_video', 'list_subgenres', 'render_subgenre']*

Alias — muvid has no costed tools.

### muvid.mcp.TOOL_NAMES *= ['list_visuals', 'list_projects', 'project_status', 'render_visualizer', 'set_song', 'add_footage', 'add_footage_folder', 'align_footage', 'propose_edit', 'footage_timeline', 'assemble_music_video', 'footage_status', 'list_strategies', 'footage_editor_document', 'footage_edl_from_annotations', 'beat_grid', 'list_music_video_projects', 'remove_footage', 'score_footage', 'footage_score_status', 'footage_scores', 'list_archetypes', 'analyze_song_lyrics', 'propose_lyric_treatments', 'propose_lyric_treatments_ai', 'validate_lyric_treatment', 'render_lyric_video', 'list_subgenres', 'render_subgenre']*

All tools this package exposes (all free). Bare names; a host may prefix them.

### muvid.mcp.TOOL_REFS *= {'add_footage': 'muvid.mcp.footage_tools:add_footage', 'add_footage_folder': 'muvid.mcp.footage_tools:add_footage_folder', 'align_footage': 'muvid.mcp.footage_tools:align_footage', 'analyze_song_lyrics': 'muvid.mcp.lyricvid_tools:analyze_song_lyrics', 'assemble_music_video': 'muvid.mcp.footage_tools:assemble_music_video', 'beat_grid': 'muvid.mcp.footage_tools:beat_grid', 'footage_editor_document': 'muvid.mcp.footage_tools:footage_editor_document', 'footage_edl_from_annotations': 'muvid.mcp.footage_tools:footage_edl_from_annotations', 'footage_score_status': 'muvid.mcp.scoring_tools:footage_score_status', 'footage_scores': 'muvid.mcp.scoring_tools:footage_scores', 'footage_status': 'muvid.mcp.footage_tools:footage_status', 'footage_timeline': 'muvid.mcp.footage_tools:footage_timeline', 'list_archetypes': 'muvid.mcp.lyricvid_tools:list_archetypes', 'list_music_video_projects': 'muvid.mcp.footage_tools:list_music_video_projects', 'list_projects': 'muvid.mcp.tools:list_projects', 'list_strategies': 'muvid.mcp.footage_tools:list_strategies', 'list_subgenres': 'muvid.mcp.subgenre_tools:list_subgenres', 'list_visuals': 'muvid.mcp.tools:list_visuals', 'project_status': 'muvid.mcp.tools:project_status', 'propose_edit': 'muvid.mcp.footage_tools:propose_edit', 'propose_lyric_treatments': 'muvid.mcp.lyricvid_tools:propose_lyric_treatments', 'propose_lyric_treatments_ai': 'muvid.mcp.lyricvid_tools:propose_lyric_treatments_ai', 'remove_footage': 'muvid.mcp.footage_tools:remove_footage', 'render_lyric_video': 'muvid.mcp.lyricvid_tools:render_lyric_video', 'render_subgenre': 'muvid.mcp.subgenre_tools:render_subgenre', 'render_visualizer': 'muvid.mcp.tools:render_visualizer', 'score_footage': 'muvid.mcp.scoring_tools:score_footage', 'set_song': 'muvid.mcp.footage_tools:set_song', 'validate_lyric_treatment': 'muvid.mcp.lyricvid_tools:validate_lyric_treatment'}*

Bare tool name → its `module:function` reference (tools live in three modules).

### *class* muvid.mcp.VisualizerWorkspace(email, root)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A single caller’s private visualizer area, addressed by `email`.

`email` and every `project_id` are validated as single path components, so a
caller can never escape their own subtree.

#### create_project(project_id, , title='', force=False)

Create (and return) a new visualizer bucket under this user.

* **Return type:**
  [`VisualizerProject`](muvid.mcp.workspace.html.md#muvid.mcp.workspace.VisualizerProject)

#### list_projects()

This user’s buckets: `[{project_id, title}]` (newest-modified first).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### open_project(project_id)

Open an existing visualizer bucket (raises if it doesn’t exist).

* **Return type:**
  [`VisualizerProject`](muvid.mcp.workspace.html.md#muvid.mcp.workspace.VisualizerProject)

### muvid.mcp.build_server(, name='muvid', instructions="muvid music-visualizer — turn a song into a 16:9, YouTube-ready visualizer video (+ thumbnail), deterministically and for free (ffmpeg only, no AI, no keys).\\n\\nWorkflow:\\n1. Create a project: create_project(genre='music-visualizer', template='<look>'). See list_visuals() for the looks (still / cqt / bars / spectrum / waves / scope) or use 'auto'.\\n2. Render: render_visualizer(project_id, audio='<https URL to the song>', cover='<https URL to the cover>', visual='<look>'). Audio/cover are direct media URLs, fetched server-side; the 'still' look needs a cover.\\n3. Inspect: project_status(project_id) lists your renders (paths + the platform-check verdict).\\n\\nAll tools are free. Renders are stored server-side in your project bucket.", auth=None, middleware=None)

Assemble a standalone FastMCP server exposing muvid’s tools (stdio dev/testing).

The hosted path aggregates onto the reelee connector via [`register_tools()`](#muvid.mcp.register_tools);
this is the convenience builder for `muvid.mcp` on its own.

### muvid.mcp.current_email()

The caller’s identity for the in-flight tool call (raises if unauthenticated).

Resolves from the explicit [`use_email()`](#muvid.mcp.use_email) override when present (local/stdio/
tests), else the verified OAuth token — so tools work under any host middleware, and
an unauthenticated call is failed closed.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.mcp.data_root()

The muvid data root: `$MUVID_DATA_HOME` or `~/.local/share/muvid`.

Public API (`muvid.mcp` re-exports it), so the name stays though the body moved.
A forwarder, not an alias, for the introspection reason given on the footage twin.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### muvid.mcp.register_tools(server, , prefix='', include=None, exclude=None)

Register muvid’s MCP tools onto an EXISTING FastMCP `server` — the aggregation
seam for a host connector (the unified reelee connector, muvid#3).

Mirrors `braidio.mcp.register_tools` / `falaw.bridges.mcp.register_tools`.
`prefix` namespaces the tool names (e.g. `"muvid_"`) to avoid collisions;
`include` / `exclude` (sets of bare names) select a subset. Returns the
registered (prefixed) names. The host installs identity/metering separately —
muvid’s tools resolve the caller via [`current_email()`](#muvid.mcp.current_email), which works under any
host middleware.

### muvid.mcp.token_email()

The verified caller’s email from the OAuth token (`email` claim, else `sub`).

Lowercased, or `None` when there is no request/token context — deliberately no
fallback, so a caller is failed closed rather than handed a shared identity.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.mcp.use_email(email)

Bind the caller identity for the duration of the block (local/stdio/testing).

### Modules

| [`footage_tools`](muvid.mcp.footage_tools.html.md#module-muvid.mcp.footage_tools)   | MCP tools for the footage-aligned `music_video` genre (thorwhalen/reelee#229).        |
|-------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| [`identity`](muvid.mcp.identity.html.md#module-muvid.mcp.identity)             | Caller identity for the muvid MCP tools — resolved from the OAuth token, fail-closed. |
| [`scoring_tools`](muvid.mcp.scoring_tools.html.md#module-muvid.mcp.scoring_tools)   | MCP tools for the footage SCORING layer (thorwhalen/muvid#13).                        |
| [`workspace`](muvid.mcp.workspace.html.md#module-muvid.mcp.workspace)           | Per-user output bucket for the muvid `music-visualizer` MCP genre.                    |
