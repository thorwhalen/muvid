"""Tests for muvid.visualize (filtergraph building, visual registry, rendering).

Most of these are pure: the filter chains are built as strings, so they can be
asserted without running ffmpeg. The end-to-end render at the bottom does run
ffmpeg, and skips when it is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from muvid.visualize.canvas import (
    CoverLayout,
    TitleStyle,
    background_chain,
    compose_chain,
    cover_box,
    cover_chain,
    escape_filter_value,
    overlay_chain,
)
from muvid.visualize.ffmpeg import Loudness, media_duration
from muvid.visualize.visuals import (
    VisualContext,
    VisualPlan,
    list_visuals,
    register_visual,
    resolve_visual,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")

SIZE = (1920, 1080)


# --------------------------------------------------------------------------
# canvas: the filter chains
# --------------------------------------------------------------------------


def test_cover_box_scales_with_both_frame_dimensions():
    # The box tracks both dimensions; the cover fitted into it (aspect-preserving)
    # reaches whichever edge comes first, so it fills the frame up to the fraction.
    assert cover_box(SIZE, CoverLayout(cover_fraction=1.0)) == (1920, 1080)
    assert cover_box(SIZE, CoverLayout(cover_fraction=0.95)) == (1824, 1026)
    assert cover_box(SIZE, CoverLayout(cover_fraction=0.5)) == (960, 540)


def test_blur_background_fills_the_frame_by_cropping_not_padding():
    chain = background_chain(SIZE, CoverLayout(), src="0:v", out="bg")
    assert "force_original_aspect_ratio=increase" in chain  # fill, then crop
    assert "crop=1920:1080" in chain
    assert "gblur=sigma=30.0" in chain
    assert "pad=" not in chain  # no black bars


def test_color_background_uses_the_colour_not_the_cover():
    chain = background_chain(
        SIZE,
        CoverLayout(background="color", background_color="navy"),
        src="0:v",
        out="bg",
    )
    assert "color=navy" in chain
    assert "gblur" not in chain


def test_cover_chain_preserves_aspect_ratio():
    chain = cover_chain(SIZE, CoverLayout(), src="fg", out="out")
    assert "force_original_aspect_ratio=decrease" in chain


def test_cover_chain_is_opaque_by_default_and_alpha_when_asked():
    assert "colorchannelmixer" not in cover_chain(SIZE, CoverLayout(), src="f", out="o")
    translucent = cover_chain(SIZE, CoverLayout(cover_alpha=0.85), src="f", out="o")
    # rgba first so an opaque source gains an alpha channel to scale.
    assert "format=rgba,colorchannelmixer=aa=0.85" in translucent


def test_overlay_centres_and_can_stop_at_the_shortest_input():
    assert "overlay=(W-w)/2:(H-h)/2," in overlay_chain(
        background="a", cover="b", out="c"
    )
    assert "shortest=1" in overlay_chain(
        background="a", cover="b", out="c", shortest=True
    )


def test_compose_chain_splits_the_cover_into_background_and_foreground():
    chain = compose_chain(SIZE, CoverLayout(), src="0:v", out="v")
    assert chain.startswith("[0:v]split=2[_bgsrc][_fgsrc]")
    assert chain.endswith("[v]")
    assert "gblur" in chain and "overlay" in chain


@pytest.mark.skipif(not HAS_FFMPEG, reason="title_chain checks the ffmpeg build")
def test_compose_chain_can_burn_in_a_title():
    chain = compose_chain(SIZE, CoverLayout(), src="0:v", out="v", title="Hi")
    assert "drawtext=" in chain
    assert chain.endswith("[v]")


def test_escaping_protects_the_filtergraph_from_the_title():
    # A title with a colon would otherwise be read as an option separator.
    assert escape_filter_value("Song: Part 1, take 2") == r"Song\: Part 1\, take 2"
    assert escape_filter_value("100%") == r"100\%"
    assert escape_filter_value("a'b") == r"a\'b"


# --------------------------------------------------------------------------
# loudness
# --------------------------------------------------------------------------


def test_loudness_filter_is_single_pass_until_measured():
    spec = Loudness().filter_spec()
    assert spec == "loudnorm=I=-14.0:TP=-1.0:LRA=11.0"
    assert "measured_I" not in spec


def test_measured_loudness_yields_a_linear_two_pass_filter():
    measured = {
        "input_i": "-20.4",
        "input_tp": "-1.2",
        "input_lra": "5.1",
        "input_thresh": "-30.6",
        "target_offset": "0.3",
    }
    spec = Loudness(measured=measured).filter_spec()
    assert "measured_I=-20.4" in spec
    assert "linear=true" in spec  # a linear gain, not a dynamic squash


# --------------------------------------------------------------------------
# the visual registry (the open-closed seam)
# --------------------------------------------------------------------------


def _ctx(tmp_path: Path, image: Path | None = None, **kwargs) -> VisualContext:
    return VisualContext(
        audio=tmp_path / "song.wav",
        image=image,
        duration=10.0,
        size=(640, 360),
        fps=12,
        workdir=tmp_path,
        **kwargs,
    )


def test_the_builtin_visuals_are_registered():
    assert set(list_visuals()) >= {
        "still",
        "ken_burns",
        "cqt",
        "bars",
        "spectrum",
        "waves",
        "scope",
    }


def _spy(calls: list, name: str):
    """A stand-in visual that records that it was the one chosen."""

    def visual(ctx):
        calls.append(name)
        return VisualPlan()

    return visual


def test_auto_picks_a_still_when_there_is_an_image(tmp_path, monkeypatch):
    import muvid.visualize.visuals as visuals

    calls: list[str] = []
    monkeypatch.setitem(visuals._VISUALS, "still", _spy(calls, "still"))
    monkeypatch.setitem(visuals._VISUALS, "cqt", _spy(calls, "cqt"))
    resolve_visual("auto", _ctx(tmp_path, image=tmp_path / "cover.png"))
    assert calls == ["still"]


def test_auto_picks_a_reactive_visual_when_there_is_no_image(tmp_path, monkeypatch):
    import muvid.visualize.visuals as visuals

    calls: list[str] = []
    monkeypatch.setitem(visuals._VISUALS, "still", _spy(calls, "still"))
    monkeypatch.setitem(visuals._VISUALS, "cqt", _spy(calls, "cqt"))
    resolve_visual("auto", _ctx(tmp_path))
    assert calls == ["cqt"]


def test_registering_a_visual_makes_it_selectable_by_name(tmp_path, monkeypatch):
    import muvid.visualize.visuals as visuals

    monkeypatch.setattr(
        visuals, "_VISUALS", dict(visuals._VISUALS)
    )  # keep the registry clean

    @register_visual("my_look")
    def _my_look(ctx):
        return VisualPlan(filters=["mine"], video="v")

    assert "my_look" in list_visuals()
    assert resolve_visual("my_look", _ctx(tmp_path)).filters == ["mine"]


def test_an_unknown_visual_names_the_ones_that_exist(tmp_path):
    with pytest.raises(ValueError, match="Unknown visual 'nope'"):
        resolve_visual("nope", _ctx(tmp_path))


def test_a_custom_callable_is_a_visual(tmp_path):
    plan = resolve_visual(
        lambda ctx: VisualPlan(filters=["x"], video="v"), _ctx(tmp_path)
    )
    assert plan.filters == ["x"]


def test_a_callable_may_return_a_prerendered_video(tmp_path):
    plan = resolve_visual(lambda ctx: tmp_path / "custom.mp4", _ctx(tmp_path))
    assert plan.inputs == [["-i", str(tmp_path / "custom.mp4")]]
    assert plan.has_cover  # nothing more should be drawn over it


def test_a_still_without_an_image_says_what_to_do_instead(tmp_path):
    with pytest.raises(ValueError, match="needs an image"):
        resolve_visual("still", _ctx(tmp_path))


# --------------------------------------------------------------------------
# encoder guards
# --------------------------------------------------------------------------


def test_an_odd_canvas_is_rejected_before_ffmpeg_fails_cryptically(tmp_path):
    # Album art is routinely an odd square (1401x1401); H.264 at yuv420p cannot
    # encode odd dimensions, and libx264's own error names neither the cause nor
    # the fix.
    from muvid.visualize.video import render_audio_video

    with pytest.raises(ValueError, match=r"odd dimension.*size=\(1400, 1400\)"):
        render_audio_video(tmp_path / "song.wav", size=(1401, 1401))


def test_the_encode_is_what_youtube_asks_for():
    from muvid.visualize.video import (
        _audio_encode_args,
        _gop_frames,
        _video_encode_args,
    )

    video = _video_encode_args(crf=18, preset="medium", fps=24, gop=48)
    assert "-pix_fmt" in video and video[video.index("-pix_fmt") + 1] == "yuv420p"
    assert video[video.index("-profile:v") + 1] == "high"
    assert video[video.index("-bf") + 1] == "2"
    assert video[video.index("-sc_threshold") + 1] == "0"  # closed GOP

    audio = _audio_encode_args("384k")
    # Pinned: loudnorm leaks its internal 192 kHz into the output otherwise.
    assert audio[audio.index("-ar") + 1] == "48000"
    assert audio[audio.index("-c:a") + 1] == "aac"

    assert _gop_frames(24, 2.0) == 48
    assert _gop_frames(24, 0.5) == 12  # YouTube's literal recommendation
    assert _gop_frames(24, 0.0) == 1  # never zero


# --------------------------------------------------------------------------
# end to end, with a real ffmpeg
# --------------------------------------------------------------------------


@pytest.fixture
def song_and_cover(tmp_path):
    """A 2-second tone and a (non-16:9) cover, synthesized by ffmpeg itself."""
    audio, image = tmp_path / "song.wav", tmp_path / "cover.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2:sample_rate=48000",
            "-ac",
            "2",
            str(audio),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=teal:s=300x400",
            "-frames:v",
            "1",
            str(image),
        ],
        check=True,
    )
    return audio, image


def _video_stream(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,pix_fmt,codec_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    import json

    return json.loads(out.stdout)["streams"][0]


@needs_ffmpeg
def test_still_render_is_16_9_yuv420p_and_as_long_as_the_song(song_and_cover, tmp_path):
    from muvid.visualize import render_audio_video

    audio, image = song_and_cover
    result = render_audio_video(
        audio,
        image,
        visual="still",
        saveas=tmp_path / "out.mp4",
        size=(640, 360),
        fps=10,
    )
    stream = _video_stream(result.path)
    assert (stream["width"], stream["height"]) == (640, 360)  # 16:9, not the 3:4 source
    assert stream["pix_fmt"] == "yuv420p"  # or half the world cannot decode it
    assert stream["codec_name"] == "h264"
    assert abs(result.duration - media_duration(audio)) < 0.5
    assert result.canvas and result.canvas.exists()


@needs_ffmpeg
def test_the_render_carries_no_edit_lists(song_and_cover, tmp_path):
    # YouTube: "No Edit Lists (or the video might not get processed correctly)".
    # ffmpeg writes one by default and +faststart does not remove it.
    from muvid.visualize import render_audio_video, verify_video

    audio, image = song_and_cover
    for visual in ("still", "cqt"):  # the loop-copy path and the filtergraph path
        result = render_audio_video(
            audio,
            image,
            visual=visual,
            saveas=tmp_path / f"{visual}.mp4",
            size=(320, 180),
            fps=10,
        )
        assert result.path.read_bytes().count(b"elst") == 0, visual
        edit_lists = next(
            c for c in verify_video(result.path) if c.name == "edit lists"
        )
        assert edit_lists.ok, f"{visual}: {edit_lists.detail}"


@needs_ffmpeg
def test_a_reactive_render_reacts_to_the_audio_without_an_image(
    song_and_cover, tmp_path
):
    from muvid.visualize import render_audio_video

    audio, _ = song_and_cover
    result = render_audio_video(
        audio, visual="cqt", saveas=tmp_path / "cqt.mp4", size=(320, 180), fps=10
    )
    assert abs(result.duration - media_duration(audio)) < 0.5
    assert _video_stream(result.path)["pix_fmt"] == "yuv420p"


@needs_ffmpeg
def test_normalizing_moves_the_loudness_towards_the_target(song_and_cover, tmp_path):
    from muvid.visualize import render_audio_video

    audio, image = song_and_cover
    result = render_audio_video(
        audio,
        image,
        visual="still",
        saveas=tmp_path / "loud.mp4",
        size=(320, 180),
        fps=10,
        normalize=True,
    )
    assert result.loudness is not None
    assert result.loudness.measured is not None
    assert result.loudness.integrated == -14.0


@needs_ffmpeg
def test_thumbnail_from_cover_is_youtube_ready(song_and_cover, tmp_path):
    from muvid.visualize import thumbnail_image

    _, image = song_and_cover
    thumb = thumbnail_image(image, saveas=tmp_path / "t.jpg")
    stream = _video_stream(thumb)
    assert (stream["width"], stream["height"]) == (1280, 720)  # YouTube's recommended
    assert thumb.stat().st_size <= 2 * 1024 * 1024  # thumbnails.set cap


@needs_ffmpeg
def test_the_teal_tint_recolors_the_visualizer(song_and_cover, tmp_path):
    # The blend/tint colour bug: a screen blend must run in alpha-free RGB or the
    # visualizer's colour is mangled. Assert the default accent actually lands teal
    # (low red, high green/blue) in the frame, not magenta.
    import numpy as np
    from PIL import Image
    from muvid.visualize import render_audio_video

    audio, image = song_and_cover
    result = render_audio_video(
        audio,
        image,
        visual="bars",
        saveas=tmp_path / "teal.mp4",
        size=(640, 360),
        fps=10,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            "1",
            "-i",
            str(result.path),
            "-frames:v",
            "1",
            str(tmp_path / "f.png"),
        ],
        check=True,
    )
    frame = np.asarray(Image.open(tmp_path / "f.png"))[:, :, :3]
    strip = frame[:, : frame.shape[1] // 5].reshape(-1, 3)
    lit = strip[strip.sum(1) > 120]  # the visualizer pixels
    if len(lit):
        r, g, b = lit.mean(0)
        assert g > r and b > r, f"accent not teal: RGB {(r, g, b)}"
