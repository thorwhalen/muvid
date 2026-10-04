"""What an editor draws: per-camera filmstrips and the song's waveform peaks.

The studio's Edit tab (the redesign in the plan of record's *Edit tab* note, §5) draws
every camera as a strip of thumbnails on the song's timeline and the song as a
waveform. Both are derived from media the project already holds, both are expensive
to make and cheap to keep, so both are CACHED by content and parameters:

- **filmstrips** — one or more JPEG sprite sheets per clip (``cols x rows`` frames
  each, ``frame_h`` pixels tall, width from the clip's own aspect), sampled at ``fps``
  frames per second of CLIP time: frame ``i`` of a clip is its picture at
  ``i / fps`` s. Cached at ``footage/filmstrips/<clip-hash>-<params>/`` with an
  ``index.json``, so a re-call is a file read and a re-uploaded clip with other bytes
  gets new sheets. Each sheet is registered in the host's catalog (content-addressed)
  when the project is hosted, so the screen loads it by ``artifact_id``.
- **peaks** — the song's per-bucket peak absolute amplitude, mono, normalised to
  ``0..1`` over ``n`` equal buckets of the song. Cached at
  ``footage/peaks/<song-hash>-<n>.json``.

Every file is written as a NEW file (temp + rename) — the catalog hardlinks them.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

#: Default sampling of a filmstrip: frames per second of clip time, frame height in
#: pixels, frames per sheet (columns x rows), JPEG quality (ffmpeg's 2-31, lower is
#: better). 2 fps x 90 px x 10x10 puts 50 s of a clip on one ~100-200 KB sheet.
FILMSTRIP_FPS = float(os.environ.get("MUVID_FILMSTRIP_FPS", "2"))
FILMSTRIP_HEIGHT = int(os.environ.get("MUVID_FILMSTRIP_HEIGHT", "90"))
FILMSTRIP_COLS = 10
FILMSTRIP_ROWS = 10
FILMSTRIP_JPEG_Q = 5
#: The rate the song is decoded at for peaks — a waveform drawing needs no more.
PEAKS_SAMPLE_RATE = 8000
#: Bounds on ``peaks(n=)``: at least one bucket, at most one per decoded sample-ish.
PEAKS_MIN_N, PEAKS_MAX_N = 1, 200_000

#: The sheet FORMAT's version, part of the cache key: bump it when the same parameters
#: start producing different sheets, so an old cache is not served as the new one.
FILMSTRIP_FORMAT = 2
_FILMSTRIPS_DIRNAME = "filmstrips"
_PEAKS_DIRNAME = "peaks"
_INDEX_NAME = "index.json"
_HASH_PREFIX = 16  # hex characters of the content hash in a cache directory name


# -- filmstrips ---------------------------------------------------------------------


def filmstrip_key(
    clip_hash: str, *, fps: float, height: int, cols: int, rows: int
) -> str:
    """The cache directory name: the clip's content and every parameter.

    >>> filmstrip_key("ab" * 32, fps=2.0, height=90, cols=10, rows=10)
    'abababababababab-f2-h90-10x10-v2'
    """
    return (
        f"{clip_hash[:_HASH_PREFIX]}-f{fps:g}-h{height}-{cols}x{rows}"
        f"-v{FILMSTRIP_FORMAT}"
    )


def clip_filmstrip(
    fp,
    clip_id: str,
    *,
    fps: float = FILMSTRIP_FPS,
    height: int = FILMSTRIP_HEIGHT,
    cols: int = FILMSTRIP_COLS,
    rows: int = FILMSTRIP_ROWS,
) -> dict:
    """One clip's filmstrip record, generating (and caching) it when missing.

    Returns ``{duration_s, n_frames, frame_w, frame_h, sheets: [{artifact_id, cols,
    rows, first_frame, n_frames}]}``.
    """
    path = Path(fp.clip_paths()[clip_id])
    key = filmstrip_key(
        _clip_hash(fp, clip_id, path), fps=fps, height=height, cols=cols, rows=rows
    )
    folder = fp.root / _FILMSTRIPS_DIRNAME / key
    index = read_json(folder / _INDEX_NAME)
    if index is None:
        index = _make_filmstrip(
            path, folder, fps=fps, height=height, cols=cols, rows=rows
        )
    return _public_filmstrip(fp, folder, index)


def _public_filmstrip(fp, folder: Path, index: dict) -> dict:
    """The index as a caller sees it: each sheet by ``artifact_id`` (re-registered if
    the catalog lost it — a copied project, a cleared catalog)."""
    catalog = getattr(fp, "media_catalog", None)
    sheets = []
    for sheet in index["sheets"]:
        aid = sheet.get("artifact_id")
        if catalog is not None and not (aid and catalog.has(aid)):
            aid = catalog.register(folder / sheet["file"], kind="image")
        sheets.append(
            {
                "artifact_id": aid,
                "cols": sheet["cols"],
                "rows": sheet["rows"],
                "first_frame": sheet["first_frame"],
                "n_frames": sheet["n_frames"],
            }
        )
    return {
        "duration_s": index["duration_s"],
        "n_frames": index["n_frames"],
        "frame_w": index["frame_w"],
        "frame_h": index["frame_h"],
        "sheets": sheets,
    }


#: Photo file types (see ``muvid.footage.service.STILL_SUFFIXES``).
_STILL_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


def _make_filmstrip(
    clip: Path, folder: Path, *, fps: float, height: int, cols: int, rows: int
) -> dict:
    """Sample the clip, tile the frames into sheets, write ``index.json`` LAST (so a
    half-made folder is never read as a cache hit)."""
    from muvid.visualize.ffmpeg import media_duration, probe, run_ffmpeg

    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder.parent) as tmp:
        frames = Path(tmp)
        # A photo has no timeline to sample: loop it for one sample period, so its
        # strip is its one frame.
        still = clip.suffix.lower() in _STILL_SUFFIXES
        source = ["-loop", "1", "-t", f"{1.0 / fps:.4f}"] if still else []
        run_ffmpeg(
            [
                *source,
                "-i",
                str(clip),
                "-an",
                "-vf",
                f"fps={fps:g},scale=-2:{height}",
                "-q:v",
                str(FILMSTRIP_JPEG_Q),
                str(frames / "f_%06d.jpg"),
            ]
        )
        names = sorted(p.name for p in frames.glob("f_*.jpg"))
        if not names:
            raise ValueError(f"no frames could be read from {clip.name}")
        stream = probe(frames / names[0])["streams"][0]
        frame_w, frame_h = int(stream["width"]), int(stream["height"])
        per_sheet = cols * rows
        sheets = []
        for s, first in enumerate(range(0, len(names), per_sheet)):
            n = min(per_sheet, len(names) - first)
            sheet_rows = math.ceil(n / cols)
            name = f"sheet_{s:03d}.jpg"
            staged = frames / name
            _tile(frames, first, n, cols, sheet_rows, staged)
            os.replace(staged, folder / name)
            sheets.append(
                {
                    "file": name,
                    "cols": cols,
                    "rows": sheet_rows,
                    "first_frame": first,
                    "n_frames": n,
                }
            )
    index = {
        "clip": clip.name,
        "fps": fps,
        "duration_s": None if still else round(float(media_duration(clip)), 3),
        "n_frames": len(names),
        "frame_w": frame_w,
        "frame_h": frame_h,
        "sheets": sheets,
    }
    write_json(folder / _INDEX_NAME, index)
    return index


def _tile(frames: Path, first: int, n: int, cols: int, rows: int, out: Path) -> None:
    """Tile frames ``first..first+n-1`` (0-based) into one ``cols x rows`` sheet.

    The tile is emitted when it is full (``cols x rows`` frames from ``first``) or, for
    the last sheet, when the frames run out, and ``-frames:v 1`` stops at that ONE
    output. (``-frames:v`` counts OUTPUT frames: limiting it to ``n`` let every sheet be
    overwritten by the clip's last tile, so all the sheets came out identical.)
    """
    from muvid.visualize.ffmpeg import run_ffmpeg

    assert n <= cols * rows, (n, cols, rows)

    run_ffmpeg(
        [
            "-framerate",
            "1",
            "-start_number",
            str(first + 1),  # the frame files are numbered from 1
            "-i",
            str(frames / "f_%06d.jpg"),
            "-frames:v",
            "1",
            "-vf",
            f"tile={cols}x{rows}",
            "-q:v",
            str(FILMSTRIP_JPEG_Q),
            str(out),
        ]
    )


def _clip_hash(fp, clip_id: str, path: Path) -> str:
    """The clip's content hash, recorded in the manifest the first time it is needed."""
    from muvid.catalog import hash_file

    m = fp.manifest()
    for c in m.get("clips", []):
        if c.get("clip_id") == clip_id:
            if c.get("hash") and c.get("file") == path.name:
                return c["hash"]
            c["hash"] = hash_file(path)
            fp._write_manifest(m)
            return c["hash"]
    return hash_file(path)


# -- peaks --------------------------------------------------------------------------


def song_peaks(fp, *, n: int) -> dict:
    """``{duration_s, n, peaks}`` — ``n`` buckets of the song's peak |amplitude|, mono,
    normalised so the loudest bucket is 1.0 (all zeros for a silent song)."""
    song_hash = fp.song_hash()
    path = fp.root / _PEAKS_DIRNAME / f"{song_hash[:_HASH_PREFIX]}-{n}.json"
    cached = read_json(path)
    if cached is not None:
        return cached
    record = {
        "duration_s": round(float(fp.song_duration()), 3),
        "n": n,
        "peaks": _bucket_peaks(fp.song_path(), n=n),
    }
    write_json(path, record)
    return record


def _bucket_peaks(song: Path, *, n: int) -> list:
    import numpy as np

    from muvid.visualize.ffmpeg import decode_pcm

    samples = np.frombuffer(
        decode_pcm(song, sample_rate=PEAKS_SAMPLE_RATE, channels=1), dtype=np.float32
    )
    if samples.size == 0:
        return [0.0] * n
    edges = np.linspace(0, samples.size, n + 1).astype(int)
    mags = np.abs(samples)
    peaks = np.array(
        [mags[a:b].max() if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])]
    )
    top = float(peaks.max())
    if top > 0:
        peaks = peaks / top
    return [round(float(p), 4) for p in peaks]


# -- files --------------------------------------------------------------------------


def read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)
    os.replace(tmp, path)


__all__ = [
    "clip_filmstrip",
    "song_peaks",
    "filmstrip_key",
    "FILMSTRIP_FPS",
    "FILMSTRIP_HEIGHT",
    "FILMSTRIP_COLS",
    "FILMSTRIP_ROWS",
    "read_json",
    "write_json",
]
