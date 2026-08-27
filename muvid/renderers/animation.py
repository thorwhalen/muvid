"""Render strategy: animation — handoff to the ``an`` package.

We synthesize a minimal ``an`` scene for this shot's interval: each
lyric line becomes a dialogue beat for the singing character; the shot's
environment becomes the entity backdrop.

Lipsync alignment: muvid already owns the SSOT for word timings (the
``lacing`` alignment store written by ``muvid align``). We build a
:class:`an.audio.WordTimingsLipSync` from those timings and pass it
into ``an.orchestrate`` so ``an`` does NOT re-transcribe the same
audio with whisper. Falls back to ``an``'s default lipsync provider
when no alignment store exists yet (e.g. user skipped ``muvid align``).

Camera: muvid and ``an`` do not share a camera vocabulary and must not
pretend to. :attr:`muvid.schema.ShotSpec.camera` is free prose a director
writes into the script (``**camera**: slow push-in``); ``an``'s
``camera.move`` is a closed set of named moves, and a name outside it is a
hard refusal at both validate and compile — deliberately, because a camera
move that silently no-ops is the failure it exists to prevent. So the prose
is TRANSLATED here, at the boundary, and never passed through
(muvid#44: this module emitted ``move: static``, which ``an`` has never
implemented, so every animation render failed validate and fell back).

Failure handling: an engine that never ran is not an engine that ran and
refused, and muvid#46 was filed because this module collapsed the two. ``an``
states a refusal as *data* (``OrchestratorReport.success is False``), so the old
handling was one ``if`` that discarded ``report.error``, ``report.validation``
and ``report.verifications`` and returned a still image under the shot's own
filename. The output was wrong rather than absent (a freeze frame reads as a
creative choice), the provenance line recorded the REQUESTED strategy so the
affected shots could not be found afterwards, and ``still`` can reach
``falaw.generate_image`` — a silent degradation that bills, under a gate that
``cost.py`` had already told the shot was free. Now: a missing ``an`` raises
:class:`~muvid.renderers._errors.RendererUnavailable` and the DISPATCHER
degrades and journals it, because the dispatcher is where the provenance line is
written; an ``an`` that refuses raises
:class:`~muvid.renderers._errors.AnimationRenderError` carrying every finding.

That closed set is not fixed, and muvid declares no ``an`` floor — ``an`` is
in no extra, the import below is soft, and a user may have any version.
``hold``/``push_in``/``pull_out``/``zoom_in``/``zoom_out`` have always been
there; the four TRANSLATING moves (``pan_left``, ``pan_right``, ``tilt_up``,
``tilt_down``) arrived with an#109 and shipped in ``an`` 0.1.65 — on 0.1.64
and below, emitting one of them is muvid#44 again. So
:func:`an_camera_move` checks the move against the vocabulary the INSTALLED
``an`` reports (``an.ir.camera.CAMERA_MOVES``) and degrades to ``hold`` with
a warning rather than emitting a move that will be refused. That is the floor,
enforced at the only place that can see which ``an`` is actually there.
"""

from __future__ import annotations

import warnings
from collections.abc import Collection
from pathlib import Path
from typing import Sequence

from muvid.renderers import RenderContext
from muvid.renderers._errors import AnimationRenderError, RendererUnavailable

#: ``an``'s spelling of "the camera does not move", and what a direction this
#: table cannot name resolves to. Not a guess dressed as a move: an
#: uninterpretable direction is a reason to leave the camera alone, not to
#: invent a push-in the director did not ask for. It is also what this module
#: effectively meant by the invalid ``static`` it used to emit.
DFLT_AN_CAMERA_MOVE = "hold"

#: muvid prose -> ``an`` move name — the WHOLE recognised vocabulary, including
#: the several ways a director spells "don't move". A direction that matches
#: nothing here is not silently translated: :func:`an_camera_move` warns, so a
#: dropped direction is visible in the run rather than only in the render.
#:
#: Matching is by WORD, never by substring, and the move that occurs EARLIEST in
#: the direction wins — the director's first-written move, not this table's
#: declaration order. Ties (two phrases starting at the same word) go to the
#: longer phrase, so ``push into`` beats ``push in`` on specificity rather than
#: on which line happens to come first. Declaration order is the last
#: tie-break and decides nothing today.
#:
#: The values are ``an``'s vocabulary and nothing else. They are pinned in CI
#: against :mod:`tests.test_animation_camera`'s recorded snapshot of
#: ``an.ir.camera.CAMERA_MOVES``, and — on a machine that has ``an`` — against
#: the live set. A hand-copied vocabulary is exactly how ``static`` survived
#: here after ``an`` tightened the rule.
#:
#: ``an`` distinguishes a push (1.0->1.25) from a zoom (1.0->1.5), so the prose
#: does too: a "push" is the gentler move, "zoom" the emphatic one.
AN_CAMERA_MOVE_PHRASES: tuple[tuple[str, str], ...] = (
    ("push in", "push_in"),
    ("push into", "push_in"),
    ("dolly in", "push_in"),
    ("zoom in", "zoom_in"),
    ("pull out", "pull_out"),
    ("pull back", "pull_out"),
    ("dolly out", "pull_out"),
    ("zoom out", "zoom_out"),
    ("pan left", "pan_left"),
    ("pan right", "pan_right"),
    ("tilt up", "tilt_up"),
    ("tilt down", "tilt_down"),
    ("crane up", "tilt_up"),
    ("crane down", "tilt_down"),
    # How a director spells "the camera does not move". These are matches, not
    # fallbacks: the fallback warns, and "static" must not.
    ("static", "hold"),
    ("hold", "hold"),
    ("locked", "hold"),
    ("locked off", "hold"),
    ("lock off", "hold"),
    ("no movement", "hold"),
)

#: Punctuation that ends a clause. Kept as a boundary rather than flattened to
#: a space because a clause is how far back a negator reaches: "no clouds, push
#: in" asks for a push, "static, no push-in" does not.
_CAMERA_CLAUSE_SEPARATORS = ",.;:!?()[]{}/|\n\u2014\u2013"

#: Punctuation INSIDE a word — "push-in" and "push in" are the same direction.
_CAMERA_WORD_SEPARATORS = "-_"

#: Deleted outright rather than split on, so "don't" stays one word ("dont")
#: and stays recognisable as a negator.
_CAMERA_DROPPED_CHARS = "'\u2019\"\u201c\u201d"

#: A word that, earlier in the same clause, means the director is REFUSING the
#: move rather than asking for it. Without this, "static, no push-in" renders a
#: push-in — the one move the direction explicitly forbids.
_CAMERA_NEGATORS = frozenset({"no", "not", "dont", "never", "without", "avoid"})


def _camera_clauses(direction: str) -> list[list[str]]:
    """Normalize a direction into clauses of lowercase words.

    >>> _camera_clauses("Static, no PUSH-IN")
    [['static'], ['no', 'push', 'in']]
    """
    text = (direction or "").lower()
    for ch in _CAMERA_DROPPED_CHARS:
        text = text.replace(ch, "")
    for ch in _CAMERA_CLAUSE_SEPARATORS:
        text = text.replace(ch, "\x00")
    for ch in _CAMERA_WORD_SEPARATORS:
        text = text.replace(ch, " ")
    return [clause.split() for clause in text.split("\x00")]


def _camera_phrase_hits(direction: str):
    """Every phrase occurrence, as ``(position, -length, order, move, negated)``.

    ``position`` is the index of the phrase's first word counted across the
    whole direction, so sorting these tuples yields the director's first-written
    move, longest (most specific) phrase first on a tie. ``negated`` is True when
    a negator precedes the phrase in its own clause; those are reported rather
    than dropped so the caller can tell "the director named no move" (warn) from
    "the director refused one" (obey, silently).
    """
    clauses = _camera_clauses(direction)
    offsets, running = [], 0
    for clause in clauses:
        offsets.append(running)
        running += len(clause)
    for order, (phrase, move) in enumerate(AN_CAMERA_MOVE_PHRASES):
        words = phrase.split()
        for clause, offset in zip(clauses, offsets):
            for i in range(len(clause) - len(words) + 1):
                if clause[i : i + len(words)] != words:
                    continue
                negated = bool(_CAMERA_NEGATORS.intersection(clause[:i]))
                yield (offset + i, -len(words), order, move, negated)


#: Sentinel for "the installed `an` has not been consulted yet" — distinct
#: from `None`, which is the real answer "`an` is not importable here".
_UNREAD = object()
_AN_CAMERA_MOVES_CACHE: object = _UNREAD


def _installed_an_camera_moves() -> frozenset[str] | None:
    """The move names the INSTALLED ``an`` implements, or ``None`` if absent.

    ``an`` is a soft dependency with no declared floor (see the module
    docstring), so the vocabulary muvid may emit is whatever the user happens
    to have. Read once and cached — the answer cannot change within a process.
    """
    global _AN_CAMERA_MOVES_CACHE
    if _AN_CAMERA_MOVES_CACHE is _UNREAD:
        try:
            from an.ir.camera import CAMERA_MOVES
        except Exception:  # pragma: no cover - depends on the environment
            _AN_CAMERA_MOVES_CACHE = None
        else:
            _AN_CAMERA_MOVES_CACHE = frozenset(CAMERA_MOVES)
    return _AN_CAMERA_MOVES_CACHE


def an_camera_move(
    direction: str,
    *,
    known_moves: Collection[str] | None = None,
) -> str:
    """Translate a muvid camera direction into an ``an`` ``camera.move`` name.

    Never returns a name the ``an`` in front of it cannot honour. Two
    independent ways that can happen, and each one WARNS rather than passing
    silently — a dropped camera direction is the failure muvid#44 was:

    - the direction names no move this table knows ("handheld, drifting"), or
    - it names one the *installed* ``an`` does not implement (``pan_left`` is
      an#109, released in ``an`` 0.1.65; on 0.1.60 it is a hard refusal at
      validate and at compile).

    ``known_moves`` is the vocabulary to check against; ``None`` means "ask the
    installed ``an``", and when ``an`` is absent nothing is narrowed.

    >>> an_camera_move("slow push-in")
    'push_in'
    >>> an_camera_move("static")
    'hold'
    >>> an_camera_move("")
    'hold'
    >>> an_camera_move("Pan Left across the room")
    'pan_left'

    The director's first-written move wins, not this table's order, and a
    negated move is refused rather than obeyed:

    >>> an_camera_move("pan left, then push in")
    'pan_left'
    >>> an_camera_move("static, no push-in")
    'hold'

    An `an` that cannot do the move gets the no-op instead of a refusal:

    >>> an_camera_move("pan left", known_moves={"hold", "push_in"})
    'hold'

    A direction naming nothing is not silently dropped:

    >>> import warnings
    >>> with warnings.catch_warnings(record=True) as caught:
    ...     warnings.simplefilter("always")
    ...     an_camera_move("handheld, drifting")
    'hold'
    >>> "handheld, drifting" in str(caught[0].message)
    True
    """
    all_hits = sorted(_camera_phrase_hits(direction))
    hits = [h for h in all_hits if not h[-1]]
    if not hits:
        # An explicitly REFUSED move ("no push-in") is honoured, not warned
        # about: the director named a move and said not to make it.
        if (direction or "").strip() and not all_hits:
            warnings.warn(
                f"camera direction {direction!r} names no move `an` implements; "
                f"rendering the shot with {DFLT_AN_CAMERA_MOVE!r} (no camera "
                "move). Recognised phrases: "
                f"{sorted({p for p, _ in AN_CAMERA_MOVE_PHRASES})}.",
                stacklevel=2,
            )
        return DFLT_AN_CAMERA_MOVE
    move = hits[0][3]
    if known_moves is None:
        known_moves = _installed_an_camera_moves()
    if known_moves is not None and move not in known_moves:
        warnings.warn(
            f"camera direction {direction!r} translates to {move!r}, which the "
            f"installed `an` does not implement (it has: {sorted(known_moves)}); "
            f"rendering the shot with {DFLT_AN_CAMERA_MOVE!r} instead. "
            "`pan_left`/`pan_right`/`tilt_up`/`tilt_down` need `an` >= 0.1.65 "
            "(an#109).",
            stacklevel=2,
        )
        return DFLT_AN_CAMERA_MOVE
    return move


def _finding_lines(findings, *, source: str) -> list[str]:
    """Render ``an``'s error-severity findings as one diagnostic line each.

    Handles BOTH of ``an``'s finding types, which are different dataclasses in
    different modules: :class:`an.ir.validate.ValidationFinding` (severity,
    ir_path, description) and :class:`an.verify._base.Finding`, which adds
    ``suggested_fix``. Read by name with ``getattr`` rather than imported,
    because this module must keep working with an ``an`` it does not pin — and
    because the formatter runs on the failure path, where raising a second
    exception while explaining the first is the worst possible outcome.
    """
    out: list[str] = []
    for f in findings or ():
        if getattr(f, "severity", None) != "error":
            continue
        line = (
            f"  [{source}] {getattr(f, 'ir_path', '?')}: {getattr(f, 'description', f)}"
        )
        fix = getattr(f, "suggested_fix", None)
        if fix:
            line += f"\n      fix: {fix}"
        out.append(line)
    return out


def _format_an_failure(report, *, scene_dir: Path, shot_id: str) -> str:
    """Turn ``an``'s failure report into a message that names the cause.

    **No single field is populated in every failure shape**, which is why this
    reads three. ``an.orchestrate`` has five ways to return ``success=False``
    and they disagree about where the diagnosis lives:

    ===========================  ==========  ==============  =================
    shape                        ``error``   ``validation``  findings in
    ===========================  ==========  ==============  =================
    validation crashed           set         **None**        --
    validation did not pass      set         set             ``validation``
    pre-render verifier error    **None**    set             ``verifications``
    render failed                set         set             --
    post-render verifier error   **None**    set             ``verifications``
    ===========================  ==========  ==============  =================

    So a formatter that reads ``report.error`` alone reports an empty string for
    two of the five — including every :class:`an.verify.layout.LayoutLintVerifier`
    refusal, which is the one muvid can actually provoke by synthesizing a bad
    ``scene.md``. Reading only ``validation`` misses the other three.

    The last resort is deliberate rather than defensive: if ``an`` says it failed
    but carries no error, no failed validation and no error-severity finding, the
    message says exactly that and dumps the report. An unexplained failure must
    still be a *loud* failure — silence is the bug this function exists to end.
    """
    lines: list[str] = []
    error = getattr(report, "error", None)
    if error:
        lines.append(f"  {error}")
    lines += _finding_lines(
        getattr(getattr(report, "validation", None), "findings", ()), source="validate"
    )
    for vr in getattr(report, "verifications", ()) or ():
        lines += _finding_lines(getattr(vr, "findings", ()), source="verify")
    if not lines:
        lines.append(
            "  `an` reported success=False but carried no error, no failed "
            "validation and no error-severity finding. This is either an `an` "
            "bug or a report shape muvid does not know how to read; the raw "
            f"report is: {report!r}"
        )
    return (
        f"`an` refused to render shot {shot_id!r}:\n"
        + "\n".join(lines)
        + f"\nThe scene muvid synthesized is at {scene_dir / 'scene.md'} — it is "
        "left on disk precisely so this is diagnosable."
    )


def render_animation(ctx: RenderContext, *, quality: str = "balanced") -> Path:
    """Synthesize a tiny ``an`` scene for this shot and orchestrate it.

    Raises :class:`~muvid.renderers._errors.RendererUnavailable` when ``an``
    is not installed — the dispatcher answers that by rendering a ``still`` and
    journalling that it did. Raises
    :class:`~muvid.renderers._errors.AnimationRenderError` when ``an`` IS
    installed and refuses the scene, because that is a bug in what muvid
    synthesized and a still image is a wrong answer, not a lesser one (muvid#46).
    """
    try:
        from an.orchestrate import orchestrate
    except ImportError as e:
        # ImportError only, never bare `Exception`. The narrower catch is
        # already the house style two functions below in
        # `_make_lipsync_provider`; the broad one here meant an `an` that is
        # INSTALLED BUT BROKEN — a bad transitive dep, a syntax error in a
        # submodule — was indistinguishable from an absent one, and degraded
        # just as quietly. A non-import failure now propagates its own
        # traceback. The original message is carried through, so "not
        # installed" and "installed, but its native deps are not" read
        # differently in the warning the dispatcher emits.
        raise RendererUnavailable(
            f"`an` is not usable, so shot {ctx.shot.id!r} cannot be animated: "
            f"{e}. `an` is deliberately declared in no muvid extra and carries "
            "no version floor, so this is a supported state; `pip install an` "
            "to render animation shots.",
            strategy="animation",
            fallback="still",
        ) from e

    scene_dir = ctx.shot_dir / "an_scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    md = _build_an_scene_md(ctx)
    (scene_dir / "scene.md").write_text(md)

    lipsync = _make_lipsync_provider(ctx)
    orchestrate_kwargs = {"lipsync": lipsync} if lipsync is not None else {}
    report = orchestrate(str(scene_dir), **orchestrate_kwargs)
    if not getattr(report, "success", False):
        # NOT a fallback. `an` states a refusal as data rather than an
        # exception (`OrchestratorReport.success is False`), so the swallow
        # this replaces was a single `if` that discarded `report.error`,
        # `report.validation` and `report.verifications` and returned a freeze
        # frame under the shot's own filename. Three things made that worse
        # than a missing output: the still reads as a creative choice, the
        # provenance line recorded the REQUESTED strategy so the affected shots
        # could not be found afterwards, and `still` can reach
        # `falaw.generate_image` — a silent degradation that bills.
        raise AnimationRenderError(
            _format_an_failure(report, scene_dir=scene_dir, shot_id=ctx.shot.id)
        )
    out = ctx.shot_dir / "output.mp4"
    src = Path(report.output_path)
    if src != out:
        import shutil

        shutil.copy2(src, out)
    return out


def _make_lipsync_provider(ctx: RenderContext):
    """Build a ``WordTimingsLipSync`` from this project's alignment store.

    Returns ``None`` if either:

    - ``an.audio.WordTimingsLipSync`` is not available (older ``an``), or
    - the project has no ``lyrics/alignment.annot`` yet, or
    - no words fall in this shot's window.

    In those cases the caller skips the override and ``an`` falls back to
    its default offline lipsync.
    """
    try:
        from an.audio import StaticWordTimings, WordTimingsLipSync
    except ImportError:
        return None

    timings = _word_timings_for_shot(ctx)
    if not timings:
        return None

    provider = StaticWordTimings(timings, label="muvid:lacing")
    return WordTimingsLipSync(provider)


def _word_timings_for_shot(
    ctx: RenderContext,
) -> Sequence[tuple[str, float, float]]:
    """Read this shot's word timings, relative to the slice's t=0.

    Thin wrapper over :func:`muvid.contracts.word_timings_for_window`
    + :func:`muvid.contracts.shifted_word_timings`. Kept here as a
    private name so the existing tests in this module's neighbourhood
    don't need to know about ``muvid.contracts``.
    """
    from muvid.contracts import shifted_word_timings, word_timings_for_window

    absolute = word_timings_for_window(
        ctx.project,
        ctx.shot.start_s,
        ctx.shot.end_s,
    )
    return shifted_word_timings(absolute, offset_s=ctx.shot.start_s)


def _build_an_scene_md(ctx: RenderContext) -> str:
    duration = max(1.0, ctx.shot.duration_s)
    camera_move = an_camera_move(ctx.shot.camera)
    lyrics = ctx.lyric_lines or [
        {
            "text": ctx.shot.description or "...",
            "start_s": ctx.shot.start_s,
            "end_s": ctx.shot.end_s,
        }
    ]
    speaker = ctx.shot.characters[0] if ctx.shot.characters else "narrator"
    chars_block = ""
    if ctx.shot.characters:
        chars_block = "\n".join(
            f"- {{ kind: character, id: {c}, store: characters, ref: {c}-v1 }}"
            for c in ctx.shot.characters
        )
    env_block = ""
    if ctx.shot.environment:
        env_block = (
            f"- {{ kind: environment, id: {ctx.shot.environment}, "
            f"store: environments, ref: {ctx.shot.environment} }}"
        )
    entities = "\n".join(b for b in (env_block, chars_block) if b)
    # An EMPTY ```yaml entities``` block does not parse — `an`'s markdown reader
    # fails on it with a bare `yaml.scanner.ScannerError`, which `orchestrate`
    # swallows into `success=False` and this renderer turns into a silent
    # fallback to `still`. Both `env` and `chars` are optional in a muvid script,
    # so that shot is writable today. `an`'s own serializer omits the block when
    # there are no entities; so do we.
    entities_block = f"\n```yaml entities\n{entities}\n```\n" if entities else ""
    dialogue = "\n".join(f"{speaker}: {L['text']}" for L in lyrics)
    return f"""# {ctx.shot.id}

```yaml meta
title: {ctx.shot.id}
duration: {duration}
fps: 24
resolution: {{ width: 640, height: 360 }}
```

## Shot {ctx.shot.id} (cutout)

```yaml shot
duration: {duration}
camera: {{ move: {camera_move} }}
```
{entities_block}
```dialogue
{dialogue}
```
"""
