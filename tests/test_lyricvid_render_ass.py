"""The ASS renderer's BURN-IN path — document → libass → mp4 (muvid#97).

The document half (``scene_to_ass``) is covered by doctests. This is the other
half, the one #97 was filed about: it had never run anywhere, not because it was
skipped but because no test existed. It needs an ffmpeg built with libass, so it
guards with ``needs_ffmpeg_filter("subtitles")`` — and ``test_ci_extras_canary``
asserts that filter is present in CI, so a runner image that loses libass turns
this from "silently skipped" into a loud failure.

The assertion that matters is the frame check: a frame sampled while a cue is
on screen must contain ink, and a frame before any cue must not. ``verify_video``
passing on a black video would prove nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.ffmpeg_support import needs_ffmpeg, needs_ffmpeg_filter

pytestmark = [needs_ffmpeg, needs_ffmpeg_filter("subtitles")]


def _sine(path: Path, seconds: float = 3.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"sine=frequency=220:duration={seconds}", "-ac", "2", "-ar", "48000", str(path)],
        check=True,
    )
    return path


def _mean_luma(video: Path, at_s: float) -> float:
    """Mean grey level of one frame, 0..255, via a raw gray dump."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at_s), "-i", str(video), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout
    return sum(raw) / max(1, len(raw))


def test_ass_burn_in_produces_ink_at_cue_time_and_none_before(tmp_path):
    from muvid.lyricvid import spec as S
    from muvid.lyricvid.render_ass import render
    from muvid.lyricvid.scene import Canvas, compile_scene
    from muvid.lyricvid.timed_text import from_words
    from muvid.visualize import verify_video

    audio = _sine(tmp_path / "song.wav")
    # one big word from 1.0 s to 2.5 s; nothing before 1.0 s
    tt = from_words([("HELLO", 1.0, 2.5)], duration=3.0)
    treatment = S.TreatmentSpec(
        direction=S.Direction(palette=S.Palette(bg="#000000", fg="#ffffff")),
        scenes=(S.Scene(archetype="one_word_centred", motion="cut",
                        timing=S.Timing(quantize_to="word", attack_s=0.0)),),
    )
    # 720p: verify_video applies YouTube's floor, and a burn-in test that had to
    # special-case that check would be testing less than the real path does.
    scene = compile_scene(treatment, tt, canvas=Canvas(width=1280, height=720, fps=24))

    out = tmp_path / "out.mp4"
    result = render(scene, audio=audio, output=out, workdir=tmp_path / "wd")

    assert result.output == out and out.exists()
    ass = result.artifacts.get("ass")
    assert ass is not None and Path(ass).exists(), "the .ass side artifact is a deliverable"
    assert "HELLO" in Path(ass).read_text(encoding="utf-8")

    checks = verify_video(out, audio=audio)
    failures = [c for c in checks if not c.ok] if hasattr(checks[0], "ok") else []
    assert not failures, failures

    dark = _mean_luma(out, 0.4)   # before the cue
    lit = _mean_luma(out, 1.7)    # mid-cue
    assert dark < 4.0, f"pre-cue frame is not black: mean luma {dark:.1f}"
    assert lit > dark + 3.0, (
        f"the cue left no ink on the frame: luma before={dark:.1f} during={lit:.1f}. "
        "libass ran but drew nothing — check the .ass Style/Dialogue or font fallback."
    )


def test_ass_burn_in_records_a_font_substitution_honestly(tmp_path):
    """Asking for a font the system lacks must not fail — and must not lie."""
    from muvid.lyricvid import spec as S
    from muvid.lyricvid.render_ass import render
    from muvid.lyricvid.scene import Canvas, compile_scene
    from muvid.lyricvid.timed_text import from_words

    audio = _sine(tmp_path / "song.wav", 1.5)
    tt = from_words([("x", 0.2, 1.0)], duration=1.5)
    treatment = S.TreatmentSpec(
        direction=S.Direction(typography=S.Typography(family="No Such Font 9000")),
    )
    scene = compile_scene(treatment, tt, canvas=Canvas(width=320, height=180, fps=12))
    result = render(scene, audio=audio, output=tmp_path / "o.mp4", workdir=tmp_path / "wd")
    assert result.meta.get("font_requested") == "No Such Font 9000"
    assert result.meta.get("font") and result.meta["font"] != "No Such Font 9000"
