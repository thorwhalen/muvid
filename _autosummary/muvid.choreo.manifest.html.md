# muvid.choreo.manifest

The choreo subgenre’s manifest — visual music, declared without importing it.

Deliberately stdlib-only and free of any import from the rest of
[`muvid.choreo`](muvid.choreo.html.md#module-muvid.choreo): this module is what a catalogue, a UI form, an MCP tool
definition or an LLM choosing among installed subgenres reads, and none of them
should pay for numpy or an ffmpeg probe to do it. The renderer is named as a
string and imported only when a render actually runs — the same shape as
[`muvid.lyricvid.manifest`](muvid.lyricvid.manifest.html.md#module-muvid.lyricvid.manifest), which is the reference plugin.

What `choreo` is: event-driven visual music in the Fischinger / McLaren /
Gondry lineage. Audio in, nothing else. Discrete musical **events** (onsets, per
frequency band) become **objects** with persistence on screen, and **sections**
become different scene arrangements. It is choreographed and structural, not a
spectrum readout — objects appear ON events and then live — which is what makes
it a different thing from [`muvid.visualize`](muvid.visualize.html.md#module-muvid.visualize).

### Module Attributes

| [`ARCHETYPE_NAMES`](#muvid.choreo.manifest.ARCHETYPE_NAMES)   | The archetype names, restated here (rather than imported from `spec`) so the manifest module stays free of every other choreo module.   |
|--------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|
| [`BEAT_SOURCES`](#muvid.choreo.manifest.BEAT_SOURCES)      | The beat-grid sources `analysis` knows.                                                                                                 |

### muvid.choreo.manifest.ARCHETYPE_NAMES *= ('fischinger', 'star_guitar', 'mclaren', 'swarm')*

The archetype names, restated here (rather than imported from `spec`) so
the manifest module stays free of every other choreo module. A test pins
this tuple against `spec.ARCHETYPES` and `scene.ARCHETYPE_FNS` so the
three cannot drift.

### muvid.choreo.manifest.BEAT_SOURCES *= ('auto', 'mixing', 'numpy')*

The beat-grid sources `analysis` knows. `auto` tries `mixing` (librosa,
in the `scoring` extra) and falls back to the built-in numpy estimate only
when librosa is absent — a *missing* dependency, never a failing one.
