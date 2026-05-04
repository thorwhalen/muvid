"""Project facade — folder layout, persistence, and a small ``dol``-backed mall.

A ``MusicVideoProject`` is just a directory with a ``project.json``. Everything
else (lyrics, characters, environments, shots) lives in a predictable
sub-folder so external tools (``lookbook``, ``lacing``, ``mixing``, ``an``)
can address slices of it directly.

The mall is a ``MutableMapping`` view over the same folders: useful when
called from a notebook or agent that wants to write a single character card
without learning the full schema.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Optional

from muvid.schema import (
    ProjectSpec,
    SongInfo,
    SectionSpec,
    ShotSpec,
    CharacterRef,
    EnvironmentRef,
)


PROJECT_FILE = "project.json"
SONG_DIR = "song"
LYRICS_DIR = "lyrics"
CHARACTERS_DIR = "characters"
ENVIRONMENTS_DIR = "environments"
SCRIPT_DIR = "script"
SHOTS_DIR = "shots"
OUTPUT_DIR = "output"
HIDDEN_DIR = ".muvid"


class MusicVideoProject:
    """Filesystem-backed music video project.

    All write methods touch the disk immediately; readers always re-read
    the SSOT (no in-memory cache) so external edits are picked up.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    # --- lifecycle -------------------------------------------------------

    @classmethod
    def init(
        cls,
        root: str | Path,
        *,
        title: str = "",
        song_path: Optional[str | Path] = None,
        copy_song: bool = True,
        exist_ok: bool = False,
    ) -> "MusicVideoProject":
        """Create a fresh project directory.

        If ``song_path`` is given, the audio is copied (or moved if
        ``copy_song=False``) into ``song/``, probed for duration, and
        registered in ``project.json``.
        """
        root = Path(root).expanduser().resolve()
        if root.exists() and not exist_ok and any(root.iterdir()):
            raise FileExistsError(f"{root} already exists and is not empty")
        for sub in (
            SONG_DIR,
            LYRICS_DIR,
            CHARACTERS_DIR,
            ENVIRONMENTS_DIR,
            SCRIPT_DIR,
            SHOTS_DIR,
            OUTPUT_DIR,
            HIDDEN_DIR,
        ):
            (root / sub).mkdir(parents=True, exist_ok=True)
        proj = cls(root)
        spec = ProjectSpec(title=title or root.name)
        proj.write_spec(spec)
        if song_path is not None:
            proj.set_song(song_path, copy=copy_song)
        return proj

    # --- spec read/write -------------------------------------------------

    @property
    def project_file(self) -> Path:
        return self.root / PROJECT_FILE

    def read_spec(self) -> ProjectSpec:
        if not self.project_file.exists():
            raise FileNotFoundError(
                f"No {PROJECT_FILE} in {self.root}. Did you run `muvid init` here?"
            )
        with self.project_file.open() as f:
            data = json.load(f)
        return ProjectSpec.from_dict(data)

    def write_spec(self, spec: ProjectSpec) -> None:
        with self.project_file.open("w") as f:
            json.dump(spec.to_dict(), f, indent=2, sort_keys=False)
            f.write("\n")

    def update_spec(self, **changes: Any) -> ProjectSpec:
        """Read, replace, write. Returns the new spec."""
        spec = self.read_spec().with_(**changes)
        self.write_spec(spec)
        return spec

    # --- song ------------------------------------------------------------

    def set_song(self, source: str | Path, *, copy: bool = True) -> SongInfo:
        """Register an audio file as this project's song.

        The file is copied (or moved) to ``song/``, probed for duration
        with ffprobe, and recorded in ``project.json``.
        """
        source = Path(source).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        target = self.root / SONG_DIR / source.name
        if target.resolve() != source:
            if copy:
                shutil.copy2(source, target)
            else:
                shutil.move(str(source), target)
        info = _probe_audio(target)
        rel = target.relative_to(self.root).as_posix()
        song = SongInfo(
            audio_path=rel,
            duration_s=info["duration_s"],
            sample_rate=int(info.get("sample_rate", 0) or 0),
            bitrate=int(info.get("bitrate", 0) or 0),
        )
        self.update_spec(song=song)
        # Sidecar info file for inspection.
        with (self.root / SONG_DIR / "audio.info.json").open("w") as f:
            json.dump(info, f, indent=2)
        return song

    def song_path(self) -> Path:
        spec = self.read_spec()
        if spec.song is None:
            raise RuntimeError("No song registered. Call set_song(...) first.")
        return self.root / spec.song.audio_path

    # --- characters ------------------------------------------------------

    def character_dir(self, name: str) -> Path:
        return self.root / CHARACTERS_DIR / name

    def add_character(self, name: str, *, description: str = "") -> CharacterRef:
        d = self.character_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "refs").mkdir(exist_ok=True)
        (d / "selected").mkdir(exist_ok=True)
        card = {"name": name, "description": description, "voice": None}
        with (d / "card.json").open("w") as f:
            json.dump(card, f, indent=2)
        spec = self.read_spec()
        if name not in {c.name for c in spec.characters}:
            self.write_spec(
                spec.with_(
                    characters=spec.characters
                    + (CharacterRef(name=name, description=description),)
                )
            )
        return CharacterRef(name=name, description=description)

    def read_character_card(self, name: str) -> dict[str, Any]:
        with (self.character_dir(name) / "card.json").open() as f:
            return json.load(f)

    def write_character_card(self, name: str, card: dict[str, Any]) -> None:
        with (self.character_dir(name) / "card.json").open("w") as f:
            json.dump(card, f, indent=2)

    # --- environments ----------------------------------------------------

    def environment_dir(self, name: str) -> Path:
        return self.root / ENVIRONMENTS_DIR / name

    def add_environment(self, name: str, *, description: str = "") -> EnvironmentRef:
        d = self.environment_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        card = {"name": name, "description": description}
        with (d / "card.json").open("w") as f:
            json.dump(card, f, indent=2)
        spec = self.read_spec()
        if name not in {e.name for e in spec.environments}:
            self.write_spec(
                spec.with_(
                    environments=spec.environments
                    + (EnvironmentRef(name=name, description=description),)
                )
            )
        return EnvironmentRef(name=name, description=description)

    def read_environment_card(self, name: str) -> dict[str, Any]:
        with (self.environment_dir(name) / "card.json").open() as f:
            return json.load(f)

    def write_environment_card(self, name: str, card: dict[str, Any]) -> None:
        with (self.environment_dir(name) / "card.json").open("w") as f:
            json.dump(card, f, indent=2)

    # --- shots -----------------------------------------------------------

    def shot_dir(self, shot_id: str) -> Path:
        return self.root / SHOTS_DIR / shot_id

    def upsert_shot(self, shot: ShotSpec) -> ShotSpec:
        d = self.shot_dir(shot.id)
        d.mkdir(parents=True, exist_ok=True)
        with (d / "shot.json").open("w") as f:
            json.dump({**_dataclass_to_dict(shot)}, f, indent=2)
        spec = self.read_spec()
        existing = {s.id: s for s in spec.shots}
        existing[shot.id] = shot
        ordered = tuple(sorted(existing.values(), key=lambda s: s.start_s))
        self.write_spec(spec.with_(shots=ordered))
        return shot

    def upsert_section(self, section: SectionSpec) -> SectionSpec:
        spec = self.read_spec()
        existing = {s.id: s for s in spec.sections}
        existing[section.id] = section
        ordered = tuple(sorted(existing.values(), key=lambda s: s.start_s))
        self.write_spec(spec.with_(sections=ordered))
        return section

    # --- decisions log ---------------------------------------------------

    def log_decision(self, kind: str, **payload: Any) -> None:
        """Append a one-line JSON entry to ``.muvid/decisions.jsonl``."""
        log = self.root / HIDDEN_DIR / "decisions.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            entry = {"kind": kind, **payload}
            f.write(json.dumps(entry, default=str) + "\n")


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Like ``asdict`` but converts tuples → lists for JSON friendliness."""
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj):
        d = asdict(obj)
    else:
        d = dict(obj)
    return _tuple_to_list(d)


def _tuple_to_list(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _tuple_to_list(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_tuple_to_list(v) for v in x]
    return x


# --- ffprobe wrapper ------------------------------------------------------


def _probe_audio(path: Path) -> dict[str, Any]:
    """Probe duration / sample_rate / bitrate via ffprobe.

    Falls back to ``{duration_s: 0.0}`` if ffprobe is missing rather than
    raising — the user can still hand-edit ``project.json``.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,bit_rate:stream=sample_rate,codec_type",
        "-of",
        "json",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {
            "duration_s": 0.0,
            "sample_rate": 0,
            "bitrate": 0,
            "warning": "ffprobe not available; please set duration manually",
        }
    data = json.loads(out)
    fmt = data.get("format", {})
    audio_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        {},
    )
    return {
        "duration_s": float(fmt.get("duration", 0.0) or 0.0),
        "bitrate": int(fmt.get("bit_rate", 0) or 0),
        "sample_rate": int(audio_stream.get("sample_rate", 0) or 0),
    }
