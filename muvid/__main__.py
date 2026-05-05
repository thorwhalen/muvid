"""muvid CLI — argh dispatch over the top-level facade.

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
) -> None:
    """Render one shot (--shot ID) or all shots.

    ``--budget USD``: when ≥ 0, abort if the estimated cost exceeds
    this. Pass ``-1`` (the default) to disable the gate.
    """
    if shot:
        print(facade.render_shot(root, shot, quality=quality, force=force))
    else:
        budget_arg = budget if budget >= 0 else None
        for p in facade.render(root, quality=quality, force=force, budget=budget_arg):
            print(p)


def estimate_cost(root: str, *, quality: str = "balanced") -> None:
    """Estimate USD cost of rendering pending shots. Prints a rollup."""
    rollup = facade.estimate_render_cost(root, quality=quality)
    summary = {
        "total_amount": rollup.total_amount,
        "currency": rollup.currency,
        "by_kind": rollup.by_kind(),
        "n_skipped": len(rollup.skipped),
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


def main() -> None:
    try:
        import argh  # type: ignore
    except ImportError as e:
        raise SystemExit("muvid CLI requires `argh`. pip install argh.") from e
    argh.dispatch_commands(
        [
            init,
            transcribe,
            align,
            character,
            character_images,
            character_generate,
            character_curate,
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
    )


if __name__ == "__main__":
    main()
