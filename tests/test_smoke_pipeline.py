"""End-to-end smoke fixture for the muvid pipeline.

Exercises every render strategy with deterministic stubs for fal/an.
This is the safety net that lets the ecosystem (lacing, falaw, an,
lookbook, mixing) evolve without silently breaking muvid: any change
that perturbs a seam (data shape, function name, return type) makes
this test fail fast.

Strategy:
- ffmpeg's lavfi generates synthetic audio + solid-colour mp4 stubs
- muvid.lyrics's call into mixing.transcript is monkeypatched
- falaw.generate_image is monkeypatched
- each render_<strategy> is replaced with a stub that produces a
  solid-colour mp4 of the right duration
- everything else (project IO, schema, dispatch, alignment, compose)
  runs for real
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("lacing")
pytest.importorskip("falaw")


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _has_ffmpeg(), reason="needs ffmpeg/ffprobe on PATH"
)


# --- ffmpeg helpers --------------------------------------------------------


def _make_solid_mp4(out: Path, duration_s: float, color: str = "navy") -> None:
    """Solid-color mp4 with silent audio track, ``duration_s`` long."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=24:d={duration_s}",
        "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "32k",
        "-shortest",
        "-t", f"{duration_s}",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _make_synthetic_song(out: Path, duration_s: float = 12.0) -> None:
    """Sine-wave mp3 of fixed duration."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration_s}",
        "-c:a", "libmp3lame", "-b:a", "64k",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _make_solid_png(out: Path, color: str = "red") -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s=64x64",
        "-frames:v", "1",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    return float(subprocess.check_output(cmd).decode().strip())


# --- canned content --------------------------------------------------------

CANNED_LYRICS = """[verse]
hello hello hello
goodbye goodbye goodbye
"""

CANNED_TRANSCRIPT = {
    "language_code": "eng",
    "language_probability": 1.0,
    "text": "hello hello hello goodbye goodbye goodbye",
    "words": [
        {"text": "hello", "type": "word", "start": 0.5, "end": 1.0, "confidence": 0.95},
        {"text": "hello", "type": "word", "start": 1.0, "end": 1.5, "confidence": 0.93},
        {"text": "hello", "type": "word", "start": 1.5, "end": 2.0, "confidence": 0.92},
        {"text": "goodbye", "type": "word", "start": 6.0, "end": 6.5, "confidence": 0.91},
        {"text": "goodbye", "type": "word", "start": 6.5, "end": 7.0, "confidence": 0.90},
        {"text": "goodbye", "type": "word", "start": 7.0, "end": 7.5, "confidence": 0.89},
    ],
}


# --- stubs -----------------------------------------------------------------


class _FakeAsset:
    def __init__(self, source: Path):
        self._source = source

    def download(self, to: str) -> str:
        shutil.copy2(self._source, to)
        return to


class _FakeResult:
    def __init__(self, asset: _FakeAsset):
        self.first = asset
        self.assets = (asset,)


def _make_render_stub(strategy_name: str):
    palette = {
        "still": "red",
        "lipsync": "blue",
        "image_to_video": "green",
        "text_to_video": "purple",
        "animation": "orange",
    }
    color = palette[strategy_name]

    def _render(ctx, *, quality: str = "balanced") -> Path:
        out = ctx.shot_dir / "output.mp4"
        _make_solid_mp4(out, max(0.5, ctx.shot.duration_s), color=color)
        return out

    return _render


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def stub_image(tmp_path):
    p = tmp_path / "stub.png"
    _make_solid_png(p)
    return p


@pytest.fixture
def patched_seams(monkeypatch, stub_image):
    """Patch every external surface muvid touches (transcribe, fal, render).

    Note: ``muvid.__init__`` re-exports ``render`` from the facade, which
    shadows the ``muvid.renderers`` subpackage on the parent package object.
    We side-step that by importing the render strategy submodules
    directly and patching attributes on them.
    """
    import mixing.transcript as _mt
    import falaw
    import muvid.renderers.still as _still
    import muvid.renderers.lipsync as _lipsync
    import muvid.renderers.image_to_video as _i2v
    import muvid.renderers.text_to_video as _t2v
    import muvid.renderers.animation as _anim

    monkeypatch.setattr(_mt, "transcribe", lambda *a, **kw: CANNED_TRANSCRIPT)

    def _fake_generate_image(*a, **kw):
        return _FakeResult(_FakeAsset(stub_image))

    monkeypatch.setattr(falaw, "generate_image", _fake_generate_image)

    monkeypatch.setattr(_still, "render_still", _make_render_stub("still"))
    monkeypatch.setattr(_lipsync, "render_lipsync", _make_render_stub("lipsync"))
    monkeypatch.setattr(
        _i2v, "render_image_to_video", _make_render_stub("image_to_video")
    )
    monkeypatch.setattr(
        _t2v, "render_text_to_video", _make_render_stub("text_to_video")
    )
    monkeypatch.setattr(
        _anim, "render_animation", _make_render_stub("animation")
    )


# --- the test --------------------------------------------------------------


def test_full_pipeline(tmp_path, patched_seams, stub_image):
    """Init → transcribe → align → cast → script → render(×5) → compose.

    The pipeline must finish with a final.mp4 whose duration covers
    the spanned shot range.
    """
    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.schema import ShotSpec

    project_root = tmp_path / "smoke_project"
    song_path = tmp_path / "song.mp3"
    _make_synthetic_song(song_path, duration_s=12.0)

    # 1. init
    facade.init_project(project_root, title="smoke", song=song_path)
    p = MusicVideoProject(project_root)
    spec = p.read_spec()
    assert spec.song is not None
    assert spec.song.duration_s == pytest.approx(12.0, abs=0.5)

    # 2. transcribe (stubbed)
    facade.transcribe_song(project_root)
    assert (project_root / "lyrics" / "transcript.json").exists()

    # 3. user-canonical lyrics, then align
    (project_root / "lyrics" / "lyrics.md").write_text(CANNED_LYRICS)
    facade.align_lyrics(project_root)
    assert (project_root / "lyrics" / "alignment.annot").exists()

    # 4. character with a stub png
    facade.add_character(project_root, "alice", description="lead singer")
    facade.add_character_images(project_root, "alice", [str(stub_image)])

    # 5. environment + render (falaw.generate_image stubbed)
    facade.add_environment(project_root, "park", description="open green field")
    facade.render_environment(project_root, "park")
    assert (project_root / "environments" / "park" / "establishing.png").exists()

    # 6. five shots — one per render strategy
    shots = [
        ShotSpec(
            id="s01", start_s=0.0, end_s=2.0, render_strategy="still",
            environment="park",
        ),
        ShotSpec(
            id="s02", start_s=2.0, end_s=4.0, render_strategy="lipsync",
            environment="park", characters=("alice",),
        ),
        ShotSpec(
            id="s03", start_s=4.0, end_s=6.0,
            render_strategy="image_to_video", environment="park",
        ),
        ShotSpec(
            id="s04", start_s=6.0, end_s=8.0,
            render_strategy="text_to_video",
        ),
        ShotSpec(
            id="s05", start_s=8.0, end_s=10.0, render_strategy="animation",
            environment="park", characters=("alice",),
        ),
    ]
    for sh in shots:
        p.upsert_shot(sh)

    # 7. render all
    outputs = facade.render(project_root)
    assert len(outputs) == 5
    for path in outputs:
        path = Path(path)
        assert path.exists()
        assert path.stat().st_size > 0

    # 8. compose
    final = Path(facade.compose(project_root))
    assert final.exists()
    duration = _ffprobe_duration(final)
    # Final should span the rendered shots: shots[0].start to shots[-1].end = 10s.
    assert 9.0 < duration < 11.0


def test_render_caching_skips_rerender(tmp_path, patched_seams, stub_image):
    """Re-running render() should not regenerate already-rendered shots."""
    from muvid import facade
    from muvid.project import MusicVideoProject
    from muvid.schema import ShotSpec

    project_root = tmp_path / "cache_project"
    song_path = tmp_path / "song.mp3"
    _make_synthetic_song(song_path, duration_s=6.0)
    facade.init_project(project_root, title="cache", song=song_path)
    p = MusicVideoProject(project_root)
    p.upsert_shot(
        ShotSpec(id="s01", start_s=0.0, end_s=2.0, render_strategy="still")
    )
    out1 = Path(facade.render(project_root)[0])
    mtime1 = out1.stat().st_mtime_ns

    out2 = Path(facade.render(project_root)[0])
    mtime2 = out2.stat().st_mtime_ns
    assert mtime1 == mtime2, "second render should be a no-op (cached by hash)"
