"""Render dispatch — turn a single ShotSpec into ``shots/<id>/output.mp4``.

Each render strategy is a small function ``render_<strategy>(project,
shot, *, audio_slice_path, ctx) -> Path``. The dispatcher resolves
shared dependencies (audio slice, lyric lines that fall in the shot,
character anchor image, environment anchor image) once and passes them
in.

Caching: each shot output's name is content-derived. If
``shots/<id>/output.mp4`` exists and the recorded ``shot.json`` hash
matches the current ShotSpec, we skip.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from muvid.project import MusicVideoProject
from muvid.renderers._errors import RendererUnavailable
from muvid.schema import ShotSpec


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderContext:
    """Shared resolved inputs for rendering a shot."""

    project: MusicVideoProject
    shot: ShotSpec
    shot_dir: Path
    audio_slice_path: Path
    character_image_paths: dict[str, Path]
    environment_image_path: Path | None
    lyric_lines: list[Any]  # muvid.align.LineAlignment
    global_style: str = ""


def billed_video_seconds(duration_s: float) -> int:
    """The seconds a video-generation call is BILLED for a shot of this length.

    The fal video models take an integer duration with a floor of one second —
    ``max(1, round())`` is the exact expression both video renderers send — so
    an estimate must price THIS number, never the raw float: a 0.4 s shot is
    billed one second, and a zero-duration shot priced at $0.00 with
    ``has_unknown_costs`` false is the muvid#52 defect one level down (the
    estimate paraphrasing the renderer's arithmetic instead of calling it).
    """
    return max(1, int(round(float(duration_s or 0.0))))


def shot_is_rendered(
    project: MusicVideoProject,
    shot: ShotSpec,
    global_style: str,
    *,
    force: bool = False,
) -> bool:
    """Whether :func:`render_shot` would return the cached output untouched.

    THE definition of "already rendered" — there is exactly one, and both the
    renderer and ``muvid.cost.estimate_render_cost`` call it. The estimate used
    to apply a weaker proxy (``output.mp4`` exists), so an edited-but-rendered
    shot (file present, hash stale) and a ``--force`` run were both priced at
    $0.00 and then billed (muvid#52) — the same defect family as "unknown reads
    as free" (muvid#47), with "pending reads as done" as the variant. Two
    predicates that must agree is the shape that produced it; this function is
    the agreement.
    """
    if force:
        return False
    shot_dir = project.shot_dir(shot.id)
    out_path = shot_dir / "output.mp4"
    hash_path = shot_dir / "output.hash"
    return (
        out_path.exists()
        and hash_path.exists()
        and hash_path.read_text().strip() == _shot_hash(shot, global_style)
    )


def render_shot(
    project: MusicVideoProject,
    shot_id: str,
    *,
    quality: str = "balanced",
    force: bool = False,
) -> Path:
    """Render a single shot. Returns the path to the produced mp4.

    Skipped (returns the existing path) if a previously-rendered output
    matches the current shot definition's hash, unless ``force=True`` —
    the :func:`shot_is_rendered` predicate, which the cost estimate shares.
    """
    spec = project.read_spec()
    shot = spec.shot(shot_id)
    shot_dir = project.shot_dir(shot.id)
    shot_dir.mkdir(parents=True, exist_ok=True)
    out_path = shot_dir / "output.mp4"
    hash_path = shot_dir / "output.hash"
    current_hash = _shot_hash(shot, spec.global_style)

    if shot_is_rendered(project, shot, spec.global_style, force=force):
        return out_path

    ctx = _build_context(project, shot, spec.global_style)
    strategy = shot.render_strategy
    _render = _load_strategy(strategy)

    rendered = strategy
    fallback_reason: str | None = None
    try:
        produced = _render(ctx, quality=quality)
    except RendererUnavailable as e:
        # The strategy's engine is not installed, so it never ran. Degrading is
        # correct — `an` is declared in no extra by design — but the DECISION
        # belongs here rather than inside the strategy, because this is where
        # the provenance line is written. muvid#46: the renderer used to
        # degrade privately and the journal recorded `strategy="animation"` for
        # a shot that came out a freeze frame, so the record could not be used
        # to find the affected shots afterwards.
        # Exactly ONE level of fallback, and that is structural rather than
        # checked: the degrade below runs INSIDE this handler, so a fallback
        # that is itself unavailable propagates instead of being caught again.
        # An earlier draft guarded `e.fallback == strategy` "in case it loops";
        # mutation-testing showed the guard could not fail, because there is no
        # loop to prevent. A guard that cannot go red is a comment that claims
        # more than the code does.
        rendered = e.fallback
        fallback_reason = str(e)
        warnings.warn(
            f"shot {shot.id!r} asked for the {strategy!r} strategy; rendering "
            f"it as {rendered!r} instead. {fallback_reason}",
            RuntimeWarning,
            stacklevel=2,
        )
        produced = _load_strategy(rendered)(ctx, quality=quality)

    if produced.resolve() != out_path.resolve():
        shutil.copy2(produced, out_path)

    # The cache entry exists only for a render that did what was asked. A
    # degraded output is PROVISIONAL: `_shot_hash` is computed from the shot
    # alone, so recording it would make the still satisfy this shot forever —
    # the moment the user installs `an`, `render_shot` would keep returning the
    # freeze frame without ever retrying. Skipping the write costs a re-render
    # per run (an ffmpeg still-mux; `storyboard.png` is already cached by
    # `still.py`, so no second image is generated and nothing is re-billed) and
    # buys a warning that repeats until the cause is fixed, instead of one that
    # can be missed once and never seen again.
    if fallback_reason is None:
        hash_path.write_text(current_hash)
    else:
        # DECLINING to write is not enough, and the difference is a real path:
        # `--force` bypasses the cache check above, so a shot that rendered
        # successfully once (hash written) and is then re-rendered after `an`
        # disappears would overwrite output.mp4 with the freeze frame while
        # leaving the OLD hash — which still matches, because the shot did not
        # change. The next run without `--force` would then serve that freeze
        # frame from cache forever, and the invariant one line up would be a
        # sentence rather than a fact. Actively invalidating makes it
        # unconditional.
        hash_path.unlink(missing_ok=True)

    decision: dict[str, Any] = {
        "shot_id": shot.id,
        # What was ACTUALLY rendered, never what was asked for.
        "strategy": rendered,
        "duration_s": shot.duration_s,
        "quality": quality,
    }
    if rendered != strategy:
        # Present if and ONLY if a fallback happened, which makes "find every
        # degraded shot" a grep for one key rather than a join against the spec.
        decision["requested_strategy"] = strategy
        decision["fallback_reason"] = fallback_reason
    project.log_decision("render_shot", **decision)
    return out_path


def render_all(
    project: MusicVideoProject, *, quality: str = "balanced", force: bool = False
) -> list[Path]:
    spec = project.read_spec()
    return [
        render_shot(project, sh.id, quality=quality, force=force) for sh in spec.shots
    ]


# --- internals ------------------------------------------------------------

#: strategy name -> the module path and function that implements it.
#:
#: A table rather than an if/elif chain because the fallback path needs to
#: resolve a strategy BY NAME (a `RendererUnavailable` carries the name it wants
#: to degrade to), and two chains that must agree is one chain too many.
#: Imports stay lazy and inside the loader: every strategy module does
#: ``from muvid.renderers import RenderContext``, so importing one at this
#: module's top level closes a cycle, and `still`/`image_to_video`/
#: `text_to_video` reach `falaw` — which `import muvid.renderers` must not pay
#: for.
_STRATEGIES: dict[str, tuple[str, str]] = {
    "lipsync": ("muvid.renderers.lipsync", "render_lipsync"),
    "image_to_video": ("muvid.renderers.image_to_video", "render_image_to_video"),
    "text_to_video": ("muvid.renderers.text_to_video", "render_text_to_video"),
    "still": ("muvid.renderers.still", "render_still"),
    "animation": ("muvid.renderers.animation", "render_animation"),
}


def _load_strategy(strategy: str):
    """Resolve a strategy name to its render function, importing it lazily."""
    try:
        module_name, func_name = _STRATEGIES[strategy]
    except KeyError:
        raise ValueError(
            f"Unknown render_strategy: {strategy!r} (known: {sorted(_STRATEGIES)})"
        ) from None
    from importlib import import_module

    return getattr(import_module(module_name), func_name)


def _shot_hash(shot: ShotSpec, global_style: str) -> str:
    payload = json.dumps(
        {"shot": _shot_dict(shot), "style": global_style},
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _shot_dict(shot: ShotSpec) -> dict:
    from dataclasses import asdict

    d = asdict(shot)
    d["characters"] = list(d["characters"])
    return d


def _build_context(
    project: MusicVideoProject, shot: ShotSpec, global_style: str
) -> RenderContext:
    from muvid.characters import get_character_anchor_image
    from muvid.environments import get_environment_anchor_image

    shot_dir = project.shot_dir(shot.id)
    audio_slice = _ensure_audio_slice(project, shot)

    char_imgs: dict[str, Path] = {}
    for name in shot.characters:
        try:
            char_imgs[name] = get_character_anchor_image(project, name)
        except FileNotFoundError:
            # render strategies that don't need an image will tolerate missing
            pass

    env_img: Path | None = None
    if shot.environment:
        env_img = get_environment_anchor_image(project, shot.environment)

    lines = _lyric_lines_for_shot(project, shot)

    return RenderContext(
        project=project,
        shot=shot,
        shot_dir=shot_dir,
        audio_slice_path=audio_slice,
        character_image_paths=char_imgs,
        environment_image_path=env_img,
        lyric_lines=lines,
        global_style=global_style,
    )


def _ensure_audio_slice(project: MusicVideoProject, shot: ShotSpec) -> Path:
    """Extract the song's audio over [start_s, end_s] for this shot."""
    from mixing.audio import Audio

    out = project.shot_dir(shot.id) / "audio.wav"
    if out.exists():
        return out
    song = project.song_path()
    audio = Audio(str(song))
    seg = audio[shot.start_s : shot.end_s]
    seg.save(str(out))
    return out


def _lyric_lines_for_shot(project: MusicVideoProject, shot: ShotSpec) -> list:
    """Read the alignment store and return lyric lines that fall in the shot."""
    align_path = project.root / "lyrics" / "alignment.annot"
    if not align_path.exists():
        return []
    try:
        from lacing import SqliteStore
        from lacing.tracks.subtitle import SubtitleTrack
    except Exception:
        return []
    # migrate=True: muvid OWNS this file — align.py writes it, at a path muvid
    # chooses inside its own project folder — so upgrading it on open is muvid
    # maintaining its own artifact, not rewriting a user's document. lacing
    # keeps migration opt-in precisely so a library never does that silently
    # (lacing#15); this is the application making the call it is entitled to
    # make, the same one nw and reelee made. Without it, a v1 .annot written
    # before lacing 0.0.31 raises SchemaMismatchError here and the render dies.
    # The v1->v2 step is a stamp, and lacing's runner re-reads the version under
    # the write lock, so concurrent opens skip rather than double-apply.
    store = SqliteStore(str(align_path), migrate=True)
    try:
        track = SubtitleTrack(store, asset_id=None)
        return [
            {
                "text": ann.body.get("text", ""),
                "start_s": ann.reference.interval.start.to_seconds(),
                "end_s": ann.reference.interval.end.to_seconds(),
                "line_index": ann.body.get("line_index"),
                "section": ann.body.get("section"),
            }
            for ann in track.lines_in(shot.start_s, shot.end_s)
        ]
    finally:
        store.close()
