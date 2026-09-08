"""The browser renderer: a real capture, the right length, self-verified.

Correctness review of #96, finding 20: the web renderer skipped ``verify_video``
and produced a video one or two frames longer than the song (2.2 s over a
2.0 s track at 10 fps), and nothing reported it because the excess sat inside
the verifier's tolerance. A lyric video is by definition the length of its song.

Needs Playwright + Chromium; skips honestly without them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.ffmpeg_support import needs_ffmpeg

pytestmark = [needs_ffmpeg]
playwright = pytest.importorskip("playwright")


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            b = pw.chromium.launch()
            b.close()
        return True
    except Exception:
        return False


def test_web_render_is_the_length_of_the_song_and_self_verified(tmp_path):
    if not _chromium_available():
        pytest.skip("Playwright is installed but Chromium is not (playwright install chromium)")
    from muvid.lyricvid import spec as S
    from muvid.lyricvid.render_web import render
    from muvid.lyricvid.scene import Canvas, compile_scene
    from muvid.lyricvid.timed_text import from_words
    from muvid.visualize.ffmpeg import media_duration

    audio = tmp_path / "song.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=2", "-ac", "2", "-ar", "48000", str(audio)],
                   check=True)
    tt = from_words([("hello", 0.3, 1.2)], duration=2.0)
    scene = compile_scene(S.TreatmentSpec(), tt, canvas=Canvas(width=1280, height=720, fps=10))
    result = render(scene, audio=audio, output=tmp_path / "o.mp4", workdir=tmp_path / "wd")

    assert result.meta["verify_failures"] == []
    video_len = media_duration(result.output)
    # within one frame of the song, and never LONGER than it by a frame
    assert abs(video_len - 2.0) <= 1.0 / 10 + 1e-3, video_len
    assert video_len <= 2.0 + 1.0 / 10 + 1e-3
