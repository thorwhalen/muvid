"""Compose all rendered shots into the final music video.

Concatenates per-shot videos in timeline order, then overlays the
master song audio so any per-shot audio (lipsync output) gets mixed
under the original mix. Pure ffmpeg — no fancy crossfades in v0.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from mtv.project import MusicVideoProject


def compose(
    project: MusicVideoProject,
    *,
    out_name: str = "final.mp4",
    use_song_audio: bool = True,
) -> Path:
    """Concatenate ``shots/*/output.mp4`` and (optionally) overlay song audio.

    ``use_song_audio=True`` (default) replaces the audio track with the
    original song's audio over ``[shots[0].start_s, shots[-1].end_s]``.
    Set to False to keep each shot's own audio (useful when most shots
    are lipsync renders that already carry their slice).
    """
    spec = project.read_spec()
    if not spec.shots:
        raise RuntimeError("No shots defined; nothing to compose.")
    paths: list[Path] = []
    for sh in spec.shots:
        p = project.shot_dir(sh.id) / "output.mp4"
        if not p.exists():
            raise RuntimeError(
                f"Shot {sh.id} has no rendered output. Run mtv render first."
            )
        paths.append(p)

    out_path = project.root / "output" / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    concat_path = _ffconcat_video(paths, out_path.with_suffix(".video.mp4"))

    if not use_song_audio:
        if concat_path.resolve() != out_path.resolve():
            concat_path.replace(out_path)
        return out_path

    # Overlay original song audio over the concatenated video span.
    song = project.song_path()
    start_s = spec.shots[0].start_s
    end_s = spec.shots[-1].end_s
    duration = max(0.001, end_s - start_s)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(concat_path),
        "-ss", f"{start_s}", "-t", f"{duration}",
        "-i", str(song),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    concat_path.unlink(missing_ok=True)
    project.log_decision("compose", out_name=out_name, n_shots=len(paths))
    return out_path


def _ffconcat_video(paths: list[Path], out: Path) -> Path:
    """Concatenate mp4s using ffmpeg's concat demuxer (lossless when
    streams are compatible; falls back to a re-encode if not)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in paths:
            # ffmpeg concat demuxer requires single-quoted, escaped paths
            f.write(f"file '{str(p).replace(chr(39), chr(92) + chr(39))}'\n")
        list_path = f.name
    try:
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            str(out),
        ]
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode != 0:
            # Fall back to re-encode (handles mismatched codecs/sizes).
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                str(out),
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        Path(list_path).unlink(missing_ok=True)
    return out
