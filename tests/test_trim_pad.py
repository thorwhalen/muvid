"""Tests for muvid.renderers._common.trim_video_to_duration padding."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from muvid.renderers._common import trim_video_to_duration


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _make_test_video(path: Path, *, duration_s: float = 2.0) -> None:
    """Build a tiny silent mp4 using ffmpeg's testsrc + anullsrc."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration_s}:size=320x240:rate=24",
        "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _video_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not on PATH")
def test_pad_extends_short_video_to_target(tmp_path):
    """A 2-second clip padded to 5 seconds should end up ~5 seconds long."""
    src = tmp_path / "short.mp4"
    dst = tmp_path / "padded.mp4"
    _make_test_video(src, duration_s=2.0)

    out = trim_video_to_duration(src, target_s=5.0, dst=dst)
    assert out == dst
    assert dst.exists()
    duration = _video_duration(dst)
    # tpad rounds to frame; allow ±100ms tolerance.
    assert 4.9 <= duration <= 5.1, f"expected ~5.0s, got {duration}"


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not on PATH")
def test_cut_shortens_long_video_to_target(tmp_path):
    """A 5-second clip cut to 2 seconds should be ~2 seconds long."""
    src = tmp_path / "long.mp4"
    dst = tmp_path / "cut.mp4"
    _make_test_video(src, duration_s=5.0)

    out = trim_video_to_duration(src, target_s=2.0, dst=dst)
    assert out == dst
    duration = _video_duration(dst)
    assert 1.9 <= duration <= 2.1, f"expected ~2.0s, got {duration}"


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not on PATH")
def test_within_tolerance_copies_unchanged(tmp_path):
    """Source within 50ms of target should be a straight copy."""
    src = tmp_path / "right.mp4"
    dst = tmp_path / "out.mp4"
    _make_test_video(src, duration_s=3.0)

    out = trim_video_to_duration(src, target_s=3.0, dst=dst)
    assert out == dst
    assert dst.exists()
    # Should be byte-identical (we copy via shutil) when within tolerance.
    assert dst.read_bytes() == src.read_bytes()
