"""muvid CLI — cw dispatch over the top-level facade.

Run ``muvid --help`` after install. Every verb is the same Python
function the skill and UI call.
"""

from __future__ import annotations

import json as _json

from muvid import facade


def _print_json(obj):
    print(_json.dumps(obj, indent=2, default=str))


def init(root: str, *, title: str = "", song: str = "") -> None:
    """Create a new music video project at ROOT (optionally with a song)."""
    out = facade.init_project(root, title=title, song=song or None)
    print(out)


def transcribe(root: str, *, api_key: str = "") -> None:
    """Run ElevenLabs Scribe on the project's song; writes lyrics/transcript.json."""
    print(facade.transcribe_song(root, api_key=api_key or None))


def align(root: str, *, aligner: str = "scribe-greedy") -> None:
    """Build lyrics/alignment.annot from transcript + lyrics.md.

    --aligner: scribe-greedy (default) | user | whisperx-lite | stars.
    """
    print(facade.align_lyrics(root, aligner=aligner))


def character(
    root: str,
    name: str,
    *,
    description: str = "",
    voice_id: str = "",
    reference_audio_url: str = "",
) -> None:
    """Create or update a character card."""
    _print_json(
        facade.add_character(
            root,
            name,
            description=description,
            voice_id=voice_id,
            reference_audio_url=reference_audio_url,
        )
    )


def character_images(root: str, name: str, *paths: str) -> None:
    """Drop existing image files into characters/<name>/refs/."""
    _print_json(facade.add_character_images(root, name, list(paths)))


def character_generate(
    root: str, name: str, *, n: int = 6, quality: str = "balanced"
) -> None:
    """Generate N reference images for a character via fal."""
    _print_json(facade.generate_character_images(root, name, n=n, quality=quality))


def character_curate(
    root: str, name: str, *, k: int = 8, recipe: str = "person_mock"
) -> None:
    """Run lookbook to select K best reference images."""
    _print_json(facade.curate_character(root, name, k=k, recipe=recipe))


def character_curate_interactive(
    root: str,
    name: str,
    *,
    decisions: str,
    k: int = 8,
    recipe: str = "person_mock",
    present: int = 6,
) -> None:
    """Run an interactive curate loop driven by a pre-recorded JSON file.

    ``--decisions PATH`` points at a JSON list shaped like
    ``[{"keep": ["<image_id>"], "reject": [...], "stop": false}, ...]``.
    """
    _print_json(
        facade.curate_character_interactive(
            root,
            name,
            decisions=decisions,
            k=k,
            recipe=recipe,
            present=present,
        )
    )


def environment(
    root: str,
    name: str,
    *,
    description: str = "",
    time_of_day: str = "",
    lighting: str = "",
) -> None:
    """Create or update an environment card."""
    _print_json(
        facade.add_environment(
            root,
            name,
            description=description,
            time_of_day=time_of_day,
            lighting=lighting,
        )
    )


def environment_render(root: str, name: str, *, quality: str = "high") -> None:
    """Generate the canonical establishing image for an environment."""
    print(facade.render_environment(root, name, quality=quality))


def script(root: str) -> None:
    """Render the project's sections+shots to script/script.md."""
    print(facade.write_script(root))


def script_apply(root: str) -> None:
    """Parse script/script.md and upsert sections+shots into project.json."""
    facade.parse_script(root)
    print("ok")


def render(
    root: str,
    *,
    shot: str = "",
    quality: str = "balanced",
    force: bool = False,
    budget: float = -1.0,
    allow_unpriced: bool = False,
) -> None:
    """Render one shot (--shot ID) or all shots.

    ``--budget USD``: when ≥ 0, abort if the estimated cost exceeds this, OR if
    anything in the project could not be priced (an unpriceable item counts as
    $0 in the total, so the number alone would clear any cap). Pass ``-1`` (the
    default) to disable the gate; ``--budget=0`` is a $0 cap, not an off switch.

    ``--allow-unpriced``: proceed despite unpriceable work, after the abort has
    named it.
    """
    if shot:
        # REFUSE rather than ignore. The single-shot path has never had a cost gate
        # (`facade.render_shot` takes no budget), so silently accepting `--budget` here
        # would let a caller believe a cap applied to a render that is not capped —
        # which is worse than the gate simply not existing, and worse still now that
        # this command's own --help promises the gate.
        if budget >= 0 or allow_unpriced:
            raise SystemExit(
                "--budget/--allow-unpriced apply to the whole project and are not "
                "honoured for a single shot. Drop --shot to render (and gate) the "
                "project, or drop the budget flags to render this shot ungated."
            )
        print(facade.render_shot(root, shot, quality=quality, force=force))
    else:
        budget_arg = budget if budget >= 0 else None
        for p in facade.render(
            root,
            quality=quality,
            force=force,
            budget=budget_arg,
            allow_unpriced=allow_unpriced,
        ):
            print(p)


def estimate_cost(root: str, *, quality: str = "balanced", force: bool = False) -> None:
    """Estimate USD cost of rendering pending shots. Prints a rollup.

    ``--force`` prices what ``render --force`` would do (everything —
    the cache is bypassed, so nothing is "already rendered").
    """
    rollup = facade.estimate_render_cost(root, quality=quality, force=force)
    summary = {
        "total_amount": rollup.total_amount,
        "currency": rollup.currency,
        "by_kind": rollup.by_kind(),
        "n_skipped": len(rollup.skipped),
        # The strings, not just the count: `render`'s abort names every
        # unpriced item, and the command it points here at must not know less.
        "skipped": list(rollup.skipped),
        "lines": [
            {
                "kind": ln.kind,
                "item_id": ln.item_id,
                "model_id": ln.model_id,
                "amount": ln.amount,
                "note": ln.note,
            }
            for ln in rollup.lines
        ],
    }
    _print_json(summary)


def compose(root: str, *, out_name: str = "final.mp4", song_audio: bool = True) -> None:
    """Concatenate rendered shots and (optionally) overlay song audio."""
    print(facade.compose(root, out_name=out_name, use_song_audio=song_audio))


def status(root: str, *, json: bool = False) -> None:
    """Print a summary of the project's current state.

    Default output is human-readable. Pass ``--json`` for the
    structured shape (stages, render progress, alignment quality).
    """
    s = facade.status(root)
    if json:
        _print_json(s)
    else:
        print(facade.format_status(s))


def serve(root: str = ".", *, host: str = "127.0.0.1", port: int = 7800) -> None:
    """Launch the local web UI for managing a project."""
    from muvid.ui.app import serve as _serve

    _serve(root=root, host=host, port=port)


#: Every CLI verb, in the order they appear in ``muvid --help``. A plain list of
#: plain functions: the parser is derived from these signatures and docstrings,
#: so the command surface cannot drift from what is defined above.
COMMANDS = [
    init,
    transcribe,
    align,
    character,
    character_images,
    character_generate,
    character_curate,
    character_curate_interactive,
    environment,
    environment_render,
    script,
    script_apply,
    render,
    estimate_cost,
    compose,
    status,
    serve,
]


def main() -> int:
    """Dispatch the muvid CLI and return the exit code.

    ``cw.dispatch`` *returns* the code rather than exiting, so both entry points
    have to raise it: the console script through ``sys.exit(main())``, and
    ``python -m muvid`` through the guard below. Without that, every argument
    error would exit 0.
    """
    try:
        import cw  # type: ignore
    except ImportError as e:  # pragma: no cover - cw is a declared dependency
        raise SystemExit("muvid CLI requires `cw`. pip install cw.") from e
    return cw.dispatch(COMMANDS)


if __name__ == "__main__":
    raise SystemExit(main())
