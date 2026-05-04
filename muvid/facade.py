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
from muvid import lyrics as _lyrics
from muvid import script as _script
from muvid.project import MusicVideoProject
from muvid.render import render_all as _render_all, render_shot as _render_shot
from muvid.schema import SectionSpec, ShotSpec


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


def align_lyrics(root: str | Path) -> str:
    """Build ``lyrics/alignment.annot`` from transcript + lyrics.md.

    Returns the path to the alignment store.
    """
    p = MusicVideoProject(root)
    transcript = _lyrics.read_transcript(p.root / "lyrics" / "transcript.json")
    doc = _lyrics.read_lyrics_md(p.root / "lyrics" / "lyrics.md")
    spec = p.read_spec()
    duration = spec.song.duration_s if spec.song else 0.0
    alignment = _align_mod.align_lyrics(doc, transcript, duration_s=duration)
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
    p.log_decision("align_lyrics", n_lines=len(alignment.lines), n_words=len(alignment.words))
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
        p, name, description=description,
        voice_id=voice_id, reference_audio_url=reference_audio_url,
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
        p, name, description=description,
        time_of_day=time_of_day, lighting=lighting,
    )


def render_environment(root: str | Path, name: str, *, quality: str = "high") -> str:
    p = MusicVideoProject(root)
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
    return str(_render_shot(p, shot_id, quality=quality, force=force))


def render(root: str | Path, *, quality: str = "balanced", force: bool = False) -> list[str]:
    p = MusicVideoProject(root)
    return [str(x) for x in _render_all(p, quality=quality, force=force)]


def compose(
    root: str | Path, *, out_name: str = "final.mp4", use_song_audio: bool = True
) -> str:
    p = MusicVideoProject(root)
    return str(_compose.compose(p, out_name=out_name, use_song_audio=use_song_audio))


def status(root: str | Path) -> dict:
    """Return a summary dict of the project's current state.

    Useful for the skill / UI to show the user where they are in the
    pipeline. No side effects.
    """
    p = MusicVideoProject(root)
    spec = p.read_spec()
    return {
        "root": str(p.root),
        "title": spec.title,
        "song": (
            None
            if spec.song is None
            else {"path": spec.song.audio_path, "duration_s": spec.song.duration_s}
        ),
        "n_characters": len(spec.characters),
        "n_environments": len(spec.environments),
        "n_sections": len(spec.sections),
        "n_shots": len(spec.shots),
        "has_transcript": (p.root / "lyrics" / "transcript.json").exists(),
        "has_lyrics_md": (p.root / "lyrics" / "lyrics.md").exists(),
        "has_alignment": (p.root / "lyrics" / "alignment.annot").exists(),
        "n_rendered": sum(
            1
            for sh in spec.shots
            if (p.shot_dir(sh.id) / "output.mp4").exists()
        ),
        "has_final": (p.root / "output" / "final.mp4").exists(),
    }


def _slugify(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"
