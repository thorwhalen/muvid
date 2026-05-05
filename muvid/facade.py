"""Top-level facade — the verbs the CLI / skill / UI all call.

These are the same functions, just packaged so each one takes a
project root path (string) instead of a ``MusicVideoProject``. They
are deliberately thin: each one resolves the project, then delegates
to the underlying module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence

from muvid import align as _align_mod
from muvid import characters as _chars
from muvid import compose as _compose
from muvid import environments as _envs
from muvid import events as _events
from muvid import lyrics as _lyrics
from muvid import script as _script
from muvid.project import MusicVideoProject
from muvid.renderers import render_all as _render_all, render_shot as _render_shot
from muvid.schema import SectionSpec, ShotSpec


def _fal_events_log(project: MusicVideoProject) -> Path:
    """Path to the per-project fal events JSONL."""
    return project.root / ".muvid" / "fal_events.jsonl"


def init_project(
    root: str | Path,
    *,
    title: str = "",
    song: Optional[str | Path] = None,
) -> str:
    """Create a new music video project. Returns the absolute root path."""
    p = MusicVideoProject.init(root, title=title, song_path=song)
    return str(p.root)


def transcribe_song(root: str | Path, *, api_key: str | None = None) -> str:
    """Run ElevenLabs Scribe on the project's song.

    Writes the raw response to ``lyrics/transcript.json`` and a draft
    ``lyrics/lyrics.md`` with auto-detected line breaks. The user is
    expected to edit ``lyrics.md`` to fix mishears and add real section
    tags. Returns the path to the lyrics markdown.
    """
    p = MusicVideoProject(root)
    transcript = _lyrics.transcribe(
        p.song_path(),
        api_key=api_key,
        out_path=p.root / "lyrics" / "transcript.json",
    )
    doc = _lyrics.lyrics_from_transcript(transcript)
    lyrics_md = p.root / "lyrics" / "lyrics.md"
    if not lyrics_md.exists():
        _lyrics.write_lyrics_md(lyrics_md, doc)
    p.log_decision("transcribe_song", n_lines=len(doc.lines))
    return str(lyrics_md)


def align_lyrics(
    root: str | Path,
    *,
    aligner: str = "scribe-greedy",
    **aligner_kwargs,
) -> str:
    """Build ``lyrics/alignment.annot`` from transcript + lyrics.md.

    ``aligner`` selects the alignment strategy (see
    :func:`muvid.align.list_aligners`); extra kwargs are forwarded to
    the chosen aligner. Defaults to ``scribe-greedy``.

    Returns the path to the alignment store.
    """
    p = MusicVideoProject(root)
    transcript_path = p.root / "lyrics" / "transcript.json"
    transcript = (
        _lyrics.read_transcript(transcript_path)
        if transcript_path.exists()
        else {"words": []}
    )
    doc = _lyrics.read_lyrics_md(p.root / "lyrics" / "lyrics.md")
    spec = p.read_spec()
    duration = spec.song.duration_s if spec.song else 0.0
    alignment = _align_mod.align_lyrics(
        doc, transcript, duration_s=duration, aligner=aligner, **aligner_kwargs,
    )
    out = p.root / "lyrics" / "alignment.annot"
    _align_mod.write_alignment_store(alignment, path=out)
    # Sync the parsed sections into the project SSOT so the script can
    # reference them by id.
    for s in alignment.sections:
        if s.start_s is None or s.end_s is None:
            continue
        p.upsert_section(
            SectionSpec(
                id=_slugify(s.label),
                start_s=s.start_s,
                end_s=s.end_s,
                label=s.label,
            )
        )
    p.log_decision(
        "align_lyrics", n_lines=len(alignment.lines), n_words=len(alignment.words)
    )
    return str(out)


def add_character(
    root: str | Path,
    name: str,
    *,
    description: str = "",
    voice_id: str = "",
    reference_audio_url: str = "",
) -> dict:
    p = MusicVideoProject(root)
    return _chars.add_character(
        p,
        name,
        description=description,
        voice_id=voice_id,
        reference_audio_url=reference_audio_url,
    )


def add_character_images(
    root: str | Path, name: str, paths: Sequence[str]
) -> list[str]:
    p = MusicVideoProject(root)
    out = _chars.add_reference_images(p, name, paths)
    return [str(x) for x in out]


def generate_character_images(
    root: str | Path, name: str, *, n: int = 6, quality: str = "balanced"
) -> list[str]:
    p = MusicVideoProject(root)
    out = _chars.generate_reference_images(p, name, n=n, quality=quality)
    return [str(x) for x in out]


def curate_character(
    root: str | Path, name: str, *, k: int = 8, recipe: str = "person_mock"
) -> list[str]:
    p = MusicVideoProject(root)
    out = _chars.curate_references(p, name, k=k, recipe=recipe)
    return [str(x) for x in out]


def curate_character_interactive(
    root: str | Path,
    name: str,
    *,
    decisions: str | Path | list,
    k: int = 8,
    recipe: str = "person_mock",
    present: int = 6,
    max_rounds: int = 20,
) -> list[str]:
    """Interactive curate driven by a pre-recorded decisions JSON.

    ``decisions`` is either a path to a JSON file or an in-memory list,
    each element shaped like ``{"keep": [<image_id>], "reject": [...],
    "stop": false}``. The decisions are applied in order, one per
    round. Useful for skill-driven flows: the agent shows the user the
    candidates, collects their answers, writes a JSON, and re-runs.
    """
    import json as _json

    from lookbook import InteractiveDecision

    if isinstance(decisions, (str, Path)):
        records = _json.loads(Path(decisions).read_text())
    else:
        records = list(decisions)

    decision_objs = [
        InteractiveDecision(
            keep=tuple(r.get("keep", ())),
            reject=tuple(r.get("reject", ())),
            stop=bool(r.get("stop", False)),
        )
        for r in records
    ]
    p = MusicVideoProject(root)
    out = _chars.curate_references_interactive(
        p, name,
        on_decision=decision_objs,
        k=k, recipe=recipe, present=present, max_rounds=max_rounds,
    )
    return [str(x) for x in out]


def add_environment(
    root: str | Path,
    name: str,
    *,
    description: str = "",
    time_of_day: str = "",
    lighting: str = "",
) -> dict:
    p = MusicVideoProject(root)
    return _envs.add_environment(
        p,
        name,
        description=description,
        time_of_day=time_of_day,
        lighting=lighting,
    )


def render_environment(root: str | Path, name: str, *, quality: str = "high") -> str:
    p = MusicVideoProject(root)
    with _events.log_fal_events_to(_fal_events_log(p)):
        return str(_envs.render_environment(p, name, quality=quality))


def write_script(root: str | Path) -> str:
    p = MusicVideoProject(root)
    return str(_script.write_script(p))


def parse_script(root: str | Path) -> None:
    p = MusicVideoProject(root)
    _script.parse_and_apply(p)


def render_shot(
    root: str | Path, shot_id: str, *, quality: str = "balanced", force: bool = False
) -> str:
    p = MusicVideoProject(root)
    with _events.log_fal_events_to(_fal_events_log(p)):
        return str(_render_shot(p, shot_id, quality=quality, force=force))


def render(
    root: str | Path,
    *,
    quality: str = "balanced",
    force: bool = False,
    budget: float | None = None,
) -> list[str]:
    """Render every shot. Returns the produced mp4 paths.

    ``budget`` (USD): when set, refuses to start if
    :func:`estimate_render_cost` exceeds it. Pass ``None`` to skip the
    gate entirely.
    """
    p = MusicVideoProject(root)
    if budget is not None:
        rollup = estimate_render_cost(root, quality=quality)
        if rollup.total_amount > budget:
            raise RuntimeError(
                f"render aborted: estimated cost ${rollup.total_amount:.2f} "
                f"{rollup.currency} exceeds budget ${budget:.2f}. "
                f"Pass --budget=0 to disable, or raise the cap."
            )
    with _events.log_fal_events_to(_fal_events_log(p)):
        return [str(x) for x in _render_all(p, quality=quality, force=force)]


def estimate_render_cost(
    root: str | Path, *, quality: str = "balanced"
):
    """Return a :class:`muvid.cost.CostRollup` for the project's pending shots."""
    from muvid.cost import estimate_render_cost as _estimate

    p = MusicVideoProject(root)
    return _estimate(p, quality=quality)


def compose(
    root: str | Path, *, out_name: str = "final.mp4", use_song_audio: bool = True
) -> str:
    p = MusicVideoProject(root)
    return str(_compose.compose(p, out_name=out_name, use_song_audio=use_song_audio))


def status(root: str | Path) -> dict:
    """Return a summary dict of the project's current state.

    Useful for the skill / UI to show the user where they are in the
    pipeline. No side effects.

    Returns a structured shape with stage progression, per-shot render
    status, and (when an alignment store exists) a word-confidence
    histogram. Stable enough to be programmatic; pass through
    :func:`format_status` for human-readable text.
    """
    p = MusicVideoProject(root)
    spec = p.read_spec()

    transcript_path = p.root / "lyrics" / "transcript.json"
    lyrics_path = p.root / "lyrics" / "lyrics.md"
    alignment_path = p.root / "lyrics" / "alignment.annot"
    final_path = p.root / "output" / "final.mp4"

    shot_states = []
    for sh in spec.shots:
        out = p.shot_dir(sh.id) / "output.mp4"
        shot_states.append(
            {
                "id": sh.id,
                "strategy": sh.render_strategy,
                "duration_s": sh.duration_s,
                "rendered": out.exists(),
            }
        )
    n_rendered = sum(1 for s in shot_states if s["rendered"])

    char_states = []
    for c in spec.characters:
        cdir = p.character_dir(c.name)
        refs = list((cdir / "refs").iterdir()) if (cdir / "refs").exists() else []
        sel = (
            list((cdir / "selected").iterdir())
            if (cdir / "selected").exists()
            else []
        )
        char_states.append(
            {
                "name": c.name,
                "n_refs": len(refs),
                "n_selected": len(sel),
                "has_anchor": (
                    "reference_image_path" in p.read_character_card(c.name)
                ),
            }
        )

    env_states = []
    for e in spec.environments:
        env_states.append(
            {
                "name": e.name,
                "rendered": (p.environment_dir(e.name) / "establishing.png").exists(),
            }
        )

    return {
        "root": str(p.root),
        "title": spec.title,
        "song": (
            None
            if spec.song is None
            else {"path": spec.song.audio_path, "duration_s": spec.song.duration_s}
        ),
        "stages": {
            "init": p.project_file.exists(),
            "transcribe": transcript_path.exists(),
            "lyrics_md": lyrics_path.exists(),
            "align": alignment_path.exists(),
            "characters": char_states,
            "environments": env_states,
            "script": bool(spec.shots),
            "render": {
                "total": len(spec.shots),
                "done": n_rendered,
                "pending": len(spec.shots) - n_rendered,
                "shots": shot_states,
            },
            "compose": final_path.exists(),
        },
        "alignment": _alignment_stats(alignment_path),
        # Backwards-compat counters:
        "n_characters": len(spec.characters),
        "n_environments": len(spec.environments),
        "n_sections": len(spec.sections),
        "n_shots": len(spec.shots),
        "has_transcript": transcript_path.exists(),
        "has_lyrics_md": lyrics_path.exists(),
        "has_alignment": alignment_path.exists(),
        "n_rendered": n_rendered,
        "has_final": final_path.exists(),
        "recent_fal_events": _events.read_recent_fal_events(
            _fal_events_log(p), limit=10
        ),
        "estimated_render_cost": _try_estimate_cost(p),
    }


def _try_estimate_cost(project: MusicVideoProject) -> dict | None:
    """Return ``{total_amount, currency, by_kind, n_skipped}`` or None."""
    try:
        from muvid.cost import estimate_render_cost as _estimate
    except ImportError:
        return None
    try:
        rollup = _estimate(project)
    except Exception:
        return None
    return {
        "total_amount": rollup.total_amount,
        "currency": rollup.currency,
        "by_kind": rollup.by_kind(),
        "n_skipped": len(rollup.skipped),
    }


def format_status(status_dict: dict) -> str:
    """Human-readable rendering of :func:`status`'s output.

    Single-screen-ish: title, song, stage checkmarks, render progress
    bar, alignment quality summary. No colour (we don't pull in a TTY
    library).
    """
    parts: list[str] = []
    parts.append(f"# {status_dict.get('title') or status_dict['root']}")
    parts.append(f"  root: {status_dict['root']}")
    song = status_dict.get("song")
    if song:
        parts.append(
            f"  song: {song['path']} ({song['duration_s']:.1f}s)"
        )
    else:
        parts.append("  song: (not set)")

    stages = status_dict.get("stages", {})
    parts.append("")
    parts.append("Stages:")
    for label, key in [
        ("init",       "init"),
        ("transcribe", "transcribe"),
        ("lyrics.md",  "lyrics_md"),
        ("align",      "align"),
        ("script",     "script"),
        ("compose",    "compose"),
    ]:
        ok = "✓" if stages.get(key) else " "
        parts.append(f"  [{ok}] {label}")

    chars = stages.get("characters", [])
    if chars:
        parts.append("")
        parts.append("Characters:")
        for c in chars:
            anchor = "anchor" if c["has_anchor"] else "no-anchor"
            parts.append(
                f"  - {c['name']}: {c['n_refs']} refs, "
                f"{c['n_selected']} selected, {anchor}"
            )

    envs = stages.get("environments", [])
    if envs:
        parts.append("")
        parts.append("Environments:")
        for e in envs:
            ok = "rendered" if e["rendered"] else "pending"
            parts.append(f"  - {e['name']}: {ok}")

    render = stages.get("render", {})
    if render.get("total"):
        parts.append("")
        bar_w = 24
        done_w = (
            int(round(bar_w * render["done"] / render["total"]))
            if render["total"]
            else 0
        )
        bar = "█" * done_w + "░" * (bar_w - done_w)
        parts.append(
            f"Render: {bar} {render['done']}/{render['total']}"
        )

    al = status_dict.get("alignment", {})
    if al:
        hist = al.get("confidence_histogram", {})
        parts.append(
            f"Alignment: {al.get('n_lines', 0)} lines, "
            f"{al.get('n_words', 0)} words "
            f"(high={hist.get('high', 0)}, med={hist.get('medium', 0)}, "
            f"low={hist.get('low', 0)})"
        )

    cost = status_dict.get("estimated_render_cost") or {}
    if cost:
        skipped_note = (
            f" ({cost['n_skipped']} unpriced)"
            if cost.get("n_skipped")
            else ""
        )
        parts.append(
            f"Estimated remaining render cost: "
            f"~${cost.get('total_amount', 0.0):.2f} "
            f"{cost.get('currency', 'USD')}{skipped_note}"
        )
    return "\n".join(parts)


def _alignment_stats(alignment_path: Path) -> dict | None:
    """Return n_lines, n_words, and a confidence histogram. ``None`` if
    no alignment store yet."""
    if not alignment_path.exists():
        return None
    try:
        from lacing import SqliteStore
        from lacing.tracks.subtitle import SubtitleTrack
    except Exception:
        return None
    store = SqliteStore(str(alignment_path))
    try:
        track = SubtitleTrack(store, asset_id=None)
        lines = track.all_lines()
        words = track.all_words()
        # Confidence buckets: high ≥0.85, low <0.5, else medium.
        hist = {"high": 0, "medium": 0, "low": 0}
        for w in words:
            c = w.body.get("confidence")
            if c is None:
                continue
            if c >= 0.85:
                hist["high"] += 1
            elif c < 0.5:
                hist["low"] += 1
            else:
                hist["medium"] += 1
        return {
            "n_lines": len(lines),
            "n_words": len(words),
            "confidence_histogram": hist,
        }
    finally:
        store.close()


def _slugify(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"
