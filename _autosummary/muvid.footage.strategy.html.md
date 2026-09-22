# muvid.footage.strategy

The pluggable `SelectionStrategy` registry — alignments → an EDL.

The user-extensible strategy pattern behind full-auto assembly: a strategy turns a set of
[`FootageAlignment`](muvid.footage.edl.html.md#muvid.footage.edl.FootageAlignment)s (which clip covers which span of the
song, with a confidence) into an EDL (which clip to SHOW for each span). Strategies emit
only `{song_start, song_end, clip_id}` — the `clip_in` sign convention lives in
[`muvid.footage.edl.derive_cuts()`](muvid.footage.edl.html.md#muvid.footage.edl.derive_cuts) (SSOT), so a third-party strategy can’t desync the
cut. Every strategy’s output still passes [`validate_edl()`](muvid.footage.edl.html.md#muvid.footage.edl.validate_edl) before
any cutting, so a strategy that leaves a gap fails loudly with the exact uncovered span.

\*\*A strategy sees `reliable` the way it already sees `overlaps`: as a preference\*\*
(muvid#88). Every built-in ranks a vouched clip above an unvouched one for the same span
via `_prefer_vouched()`, and falls back to an unvouched clip only where nothing else
covers the span — so one badly-aligned clip in a shoot costs the spans only it covered,
not the whole edit. The strategies still decide nothing about trust: they never drop a
source, and the verdict they read (`FootageAlignment.reliable`) is
[`vouches_for()`](muvid.footage.edl.html.md#muvid.footage.edl.vouches_for)’s. A third-party strategy that ranks clips itself
should carry the same preference; `weighted` does it as a reward penalty rather than a
special case (see [`muvid.footage.select_score`](muvid.footage.select_score.html.md#module-muvid.footage.select_score)).

Registry idiom mirrors `mixing.audio.segmentation` (a `strategy: str | callable` param

+ a `_STRATEGIES` dict + [`resolve_strategy()`](#muvid.footage.strategy.resolve_strategy)) — the federation’s established shape.

Register your own with [`register_selection_strategy()`](#muvid.footage.strategy.register_selection_strategy).

### Module Attributes

| [`SelectionStrategy`](#muvid.footage.strategy.SelectionStrategy)   | alignments + song duration → an EDL (built-ins ignore song_duration but it is passed so a strategy MAY reason about the full timeline).   |
|----------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|
| [`DEFAULT_STRATEGY`](#muvid.footage.strategy.DEFAULT_STRATEGY)    | The default auto-strategy when none is chosen.                                                                                            |

### Functions

| [`best_confidence`](#muvid.footage.strategy.best_confidence)(alignments, song_duration)       | For each covered span, show the highest-confidence clip (ties: longest coverage).                                            |
|---------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| [`fewest_cuts`](#muvid.footage.strategy.fewest_cuts)(alignments, song_duration)           | Stay on the current clip as long as it covers; only switch when it runs out (then to the clip extending furthest).           |
| [`list_strategies`](#muvid.footage.strategy.list_strategies)()                                | All strategy slugs (eager + lazy), sorted.                                                                                   |
| [`longest_take`](#muvid.footage.strategy.longest_take)(alignments, song_duration)          | Prefer the clip that keeps rolling longest — pick the one whose coverage extends furthest forward (ties: higher confidence). |
| [`register_lazy_strategy`](#muvid.footage.strategy.register_lazy_strategy)(slug, target_ref)         | Register a strategy by a `"module:func"` reference, imported only on first use.                                              |
| [`register_selection_strategy`](#muvid.footage.strategy.register_selection_strategy)(slug, fn)            | Register a selection strategy under `slug` (returns it, for inline use).                                                     |
| [`resolve_strategy`](#muvid.footage.strategy.resolve_strategy)(strategy)                       | Resolve a strategy name OR a bare callable to a [`SelectionStrategy`](#muvid.footage.strategy.SelectionStrategy).          |
| [`select_edl`](#muvid.footage.strategy.select_edl)(strategy, alignments, ...[, context]) | Run `strategy` (name or callable) to produce an EDL from `alignments`.                                                       |

### muvid.footage.strategy.DEFAULT_STRATEGY *= 'best_confidence'*

The default auto-strategy when none is chosen.

### muvid.footage.strategy.SelectionStrategy

alignments + song duration → an EDL (built-ins ignore song_duration but it
is passed so a strategy MAY reason about the full timeline).

**Score-driven opt-in (progressive disclosure).** A strategy that needs the score tensor

+ beats declares a keyword-only `context` parameter (or `**kwargs`); [`select_edl()`](#muvid.footage.strategy.select_edl)
  then passes a `SelectionContext` (defined in [`muvid.footage.select_score`](muvid.footage.select_score.html.md#module-muvid.footage.select_score)) as
  `context=`. The built-ins take only `(alignments, song_duration)` and never see it —
  the exact same dispatch idiom as `nw.jobs._call_dispatch`. The magic parameter name is
  literally `context`.

* **Type:**
  A strategy

alias of `Callable`[[…], `list[EdlEntry]`]

### muvid.footage.strategy.best_confidence(alignments, song_duration)

For each covered span, show the highest-confidence clip (ties: longest coverage).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry)]

### muvid.footage.strategy.fewest_cuts(alignments, song_duration)

Stay on the current clip as long as it covers; only switch when it runs out
(then to the clip extending furthest). Minimizes the number of cuts.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry)]

### muvid.footage.strategy.list_strategies()

All strategy slugs (eager + lazy), sorted. Lazy slugs are NOT imported to list them.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.footage.strategy.longest_take(alignments, song_duration)

Prefer the clip that keeps rolling longest — pick the one whose coverage extends
furthest forward (ties: higher confidence). Yields long, continuous takes.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry)]

### muvid.footage.strategy.register_lazy_strategy(slug, target_ref)

Register a strategy by a `"module:func"` reference, imported only on first use.

Lets a heavy strategy (numpy DP, cv2, …) be *listed* and *named* without importing its
module at registration time — the import happens in [`resolve_strategy()`](#muvid.footage.strategy.resolve_strategy).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### muvid.footage.strategy.register_selection_strategy(slug, fn)

Register a selection strategy under `slug` (returns it, for inline use).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry)]]

### muvid.footage.strategy.resolve_strategy(strategy)

Resolve a strategy name OR a bare callable to a [`SelectionStrategy`](#muvid.footage.strategy.SelectionStrategy).

A lazy slug is imported here (and cached into the eager table) on first resolution.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry)]]

### muvid.footage.strategy.select_edl(strategy, alignments, song_duration, , context=None)

Run `strategy` (name or callable) to produce an EDL from `alignments`.

`context` (a `SelectionContext`) is passed to score-driven strategies that declare it;
the alignment-only built-ins ignore it. See [`SelectionStrategy`](#muvid.footage.strategy.SelectionStrategy).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`EdlEntry`](muvid.footage.edl.html.md#muvid.footage.edl.EdlEntry)]
