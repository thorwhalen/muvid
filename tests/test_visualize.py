"""Tests for muvid.visualize (filtergraph building, visual registry, rendering).

Most of these are pure: the filter chains are built as strings, so they can be
asserted without running ffmpeg. The end-to-end render at the bottom does run
ffmpeg, and skips when it — or the particular filter a test exercises — is not
available (see :mod:`tests.ffmpeg_support`).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.ffmpeg_support import needs_ffmpeg, needs_ffmpeg_filter

from muvid.visualize.canvas import (
    CoverLayout,
    TitleStyle,
    background_chain,
    brightness_saturation_lut,
    compose_chain,
    cover_box,
    cover_chain,
    escape_filter_value,
    lut_filter,
    overlay_chain,
)
from muvid.visualize.ffmpeg import Loudness, media_duration
from muvid.visualize.reactive import (
    DEFAULT_FLASH_LABEL,
    FLASH_BRIGHTNESS,
    FLASH_COMPONENTS,
    FLASH_FILTERS,
    FLASH_SATURATION,
    _write_flash_script,
    flash_filter,
    onset_envelope,
)
from muvid.visualize.visuals import (
    REACTIVE_BG_DIM,
    REACTIVE_BG_SATURATION,
    VisualContext,
    VisualPlan,
    list_visuals,
    register_visual,
    resolve_visual,
)

SIZE = (1920, 1080)


def _ffmpeg_accepts(source: str, *graph_args: str) -> None:
    """Push one frame of the lavfi ``source`` through ``graph_args``.

    ffmpeg is the only real judge of filtergraph escaping, and for a *path* an
    under-escaped value is always loud — a parse error ("No option name near …")
    or an unopenable file — so ``check=True`` *is* the assertion here.

    It is not a sufficient assertion for a *text* value: an unclosed quote is a
    silent wrong render, not an error. Compare pixels for those — see
    :func:`_rendered_frame`.
    """
    # -frames:v is a per-output option, so it has to come *before* the output
    # URL that ``graph_args`` ends with; trailing it there would silently apply
    # to nothing and render the whole clip.
    base = f"ffmpeg -y -loglevel error -f lavfi -i {source} -frames:v 1".split()
    subprocess.run([*base, *graph_args], check=True)


def _rendered_frame(chain: str, saveas) -> bytes:
    """Render one PNG frame of ``chain`` over black and return its bytes.

    PNG is lossless and the render is deterministic, so two frames comparing
    equal byte for byte means ffmpeg drew the same picture — which is the only
    way to catch an escaping bug that ffmpeg accepts without complaint.
    """
    _ffmpeg_accepts(
        f"color=c=black:s={SIZE[0]}x{SIZE[1]}:d=0.2",
        "-filter_complex",
        chain,
        "-map",
        "[v]",
        str(saveas),
    )
    return Path(saveas).read_bytes()


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


# title_chain requires drawtext, so guard on drawtext — not on ffmpeg being installed.
@needs_ffmpeg_filter("drawtext")
def test_compose_chain_can_burn_in_a_title():
    chain = compose_chain(SIZE, CoverLayout(), src="0:v", out="v", title="Hi")
    assert "drawtext=" in chain
    assert chain.endswith("[v]")


def test_escaping_survives_both_of_ffmpegs_unescaping_passes():
    # ffmpeg unescapes a filter option value TWICE — once parsing the graph,
    # then again splitting that filter's argument string into options — so a
    # single backslash is consumed by the first pass and never reaches the
    # second. ':' and "'" are special to both parsers and so need two layers.
    assert escape_filter_value("Song: Part 1, take 2") == r"Song\\: Part 1\, take 2"
    assert escape_filter_value("a'b") == r"a\\\'b"
    assert escape_filter_value("a\\b") == "a" + "\\" * 4 + "b"

    # ',', ';' and '[]' are graph punctuation only: the option parser does not
    # touch them, so one layer is exactly right.
    assert escape_filter_value("a,b;c[d]e") == r"a\,b\;c\[d\]e"

    # '%' is special to neither parser — '\%' is unescaped straight back to '%',
    # so escaping it was always a no-op.
    assert escape_filter_value("100%") == "100%"


@needs_ffmpeg_filter("drawtext")
@pytest.mark.parametrize("title", ["Song: Part 1", "Take 2, live", "Bob's Song"])
def test_a_punctuated_title_survives_a_real_ffmpeg_round_trip(tmp_path, title):
    # "ffmpeg exited 0" is NOT enough to prove a *text* value was escaped right.
    # A ':' under-escapes into a syntax error, which is loud — but an under-
    # escaped "'" opens a quoted section the option parser never closes, which
    # swallows the text silently and still exits 0. So compare the pixels
    # against the same drawtext handed the title through `textfile=`, which
    # involves no filtergraph escaping at all and is therefore ground truth for
    # "what ffmpeg actually drew". (Skips where this build has no drawtext.)
    chain = compose_chain(SIZE, CoverLayout(), src="0:v", out="v", title=title)

    truth_file = tmp_path / "title.txt"
    truth_file.write_text(title)
    truth_chain = chain.replace(
        f"text={escape_filter_value(title)}", f"textfile={truth_file}"
    )
    # Guard the rewrite: if title_chain ever stops emitting `text=<escaped>`,
    # this would compare the chain against itself and pass for any escaper.
    assert truth_chain != chain, "the escaped title is not where this test expects it"

    assert _rendered_frame(chain, tmp_path / "escaped.png") == _rendered_frame(
        truth_chain, tmp_path / "truth.png"
    )


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


@needs_ffmpeg_filter("showcqt")  # one of the two visuals below is showcqt
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


@needs_ffmpeg_filter("showcqt")
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


@needs_ffmpeg_filter("showfreqs")  # the 'bars' visual
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


# --------------------------------------------------------------------------
# the beat-reactive flash (muvid.visualize.reactive)
# --------------------------------------------------------------------------

#: The synthesized click track: 20 ms of 1 kHz once a second, silence between.
#: Its onset envelope is knowable up front, which is what makes it assertable.
CLICK_PERIOD_S = 1.0
CLICK_SECONDS = 4
CLICK_FPS = 10

#: One ``sendcmd`` line per (frame, LUT component), e.g.
#: ``1.000 [enter] lutyuv@flash y 'clip(val+63.75,0,255)';``
#: The expression is single-quoted because it contains ``,``, which is
#: ``sendcmd``'s own "next command" separator.
SENDCMD_LINE = re.compile(
    r"^(?P<t>\d+\.\d{3}) \[enter\] (?P<target>\S+) "
    r"(?P<comp>[yuv]) '(?P<expr>[^']*)';$"
)


@pytest.fixture
def click_track(tmp_path):
    """Four one-second-apart clicks, synthesized by ffmpeg into ``tmp_path``."""
    audio = tmp_path / "clicks.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc=exprs=0.9*sin(2*PI*1000*t)*lt(mod(t\\,{CLICK_PERIOD_S})\\,0.02)"
            f":d={CLICK_SECONDS}:s=44100",
            "-ac",
            "1",
            str(audio),
        ],
        check=True,
    )
    return audio


@needs_ffmpeg
def test_the_onset_envelope_peaks_on_the_clicks_and_decays_between_them(click_track):
    env = onset_envelope(click_track, fps=CLICK_FPS, duration=float(CLICK_SECONDS))
    assert len(env) == CLICK_SECONDS * CLICK_FPS
    assert all(0.0 <= v <= 1.0 for v in env), "the envelope must be normalized"

    # A click at t=N lands on frame fps*N. (t=0 is not detected: the envelope
    # measures the *rise* in loudness, and the track opens on its first click,
    # so there is no quiet frame before it to rise from.)
    for beat in range(1, CLICK_SECONDS):
        click = beat * CLICK_FPS
        # +/-1 frame: resampling a click out of digital silence leaves a little
        # pre-ringing, and against a -90 dB floor even that reads as an onset,
        # so the flash may start one frame early.
        assert max(env[click - 1 : click + 2]) > 0.5, f"beat {beat} did not flash"
        # Well before the click, the previous beat has long since faded out.
        assert env[click - CLICK_FPS // 2] < 0.05, f"beat {beat} lit up early"
        # And after it, the pulse fades — smoothly, not in one step.
        trail = env[click + 1 : click + CLICK_FPS - 1]
        assert trail == sorted(trail, reverse=True), f"beat {beat} trail: {trail}"
        assert trail[-1] < 0.05, f"beat {beat} never went dark: {trail[-1]}"


@needs_ffmpeg
def test_a_faster_decay_makes_the_flash_shorter(click_track):
    """``decay`` is the afterglow knob, not decoration — it must move the trail."""
    kwargs = dict(fps=CLICK_FPS, duration=float(CLICK_SECONDS))
    snappy = onset_envelope(click_track, decay=0.1, **kwargs)
    lingering = onset_envelope(click_track, decay=0.9, **kwargs)
    frame_after_a_beat = CLICK_FPS + 2
    assert snappy[frame_after_a_beat] < lingering[frame_after_a_beat]
    assert sum(snappy) < sum(lingering)


def test_the_sendcmd_script_is_well_formed(tmp_path):
    envelope = [0.0, 0.5, 1.0]
    script = _write_flash_script(
        envelope,
        tmp_path / "f.cmd",
        fps=CLICK_FPS,
        target="lutyuv@flash",
        brightness=FLASH_BRIGHTNESS,
        saturation=FLASH_SATURATION,
    )
    lines = script.read_text().splitlines()
    # One command per LUT component per frame — a table is addressed per plane,
    # where `eq` took a single `brightness` and a single `saturation`.
    assert len(lines) == len(FLASH_COMPONENTS) * len(envelope)

    matched = [SENDCMD_LINE.match(line) for line in lines]
    assert all(matched), [ln for ln, m in zip(lines, matched) if not m]
    assert {m["target"] for m in matched} == {"lutyuv@flash"}

    times = [float(m["t"]) for m in matched]
    assert times == sorted(times)  # sendcmd needs its commands in time order
    assert times[0] == 0.0
    assert times[-1] == (len(envelope) - 1) / CLICK_FPS

    # Each frame's three expressions must be the LUT for the pulse strength that
    # frame carries: at rest a no-op, at full pulse the configured peaks. The
    # arithmetic inside them is pinned against `eq` itself, in pixels, by
    # test_the_lut_reproduces_the_gpl_eq_it_replaced.
    per_frame = [
        matched[i * len(FLASH_COMPONENTS) : (i + 1) * len(FLASH_COMPONENTS)]
        for i in range(len(envelope))
    ]
    for pulse, frame in zip(envelope, per_frame):
        expected = brightness_saturation_lut(
            brightness=FLASH_BRIGHTNESS * pulse,
            saturation=1 + FLASH_SATURATION * pulse,
        )
        assert [m["comp"] for m in frame] == list(FLASH_COMPONENTS)
        assert {m["comp"]: m["expr"] for m in frame} == expected

    # The one that must be EXACT rather than close: at rest the flash may not
    # touch a single pixel, or every unflashed frame of every render moves.
    assert {m["comp"]: m["expr"] for m in matched[: len(FLASH_COMPONENTS)]} == (
        brightness_saturation_lut()
    )


#: Workdir names covering every character that is special to one or both of
#: ffmpeg's parsers, plus two that are special to neither (the control cases).
#: The flash writes its ``sendcmd`` script *into* the workdir and then names
#: that path inside a filtergraph, so the directory's name is attacker-grade
#: input to the escaper.
AWKWARD_WORKDIR_NAMES = [
    "plain",
    "has space",
    "has'quote",
    "has:colon",
    "has,comma",
    "has;semi",
    "has[bracket]",
]


@needs_ffmpeg_filter(*FLASH_FILTERS)
@pytest.mark.parametrize("dirname", AWKWARD_WORKDIR_NAMES)
def test_the_flash_fragment_renders_from_a_workdir_that_needs_escaping(
    click_track, tmp_path, dirname
):
    workdir = tmp_path / dirname
    workdir.mkdir()
    fragment = flash_filter(
        click_track, fps=CLICK_FPS, duration=float(CLICK_SECONDS), workdir=workdir
    )
    # It is appended to a visual's chain, so it must open with the separator.
    assert fragment.startswith(",sendcmd=f=")
    # The filter the script drives, sitting at its identity table until a command
    # arrives. `lutyuv` needs no `eval=frame`: rebuilding the table IS the update.
    assert f",lutyuv@{DEFAULT_FLASH_LABEL}=" in fragment
    assert (
        lut_filter(brightness_saturation_lut(), label=DEFAULT_FLASH_LABEL) in fragment
    )

    script = workdir / f"{DEFAULT_FLASH_LABEL}.cmd"
    assert script.exists()
    # The path goes into a filtergraph, so it must be escaped, not raw.
    assert escape_filter_value(str(script)) in fragment

    # ...and ffmpeg is the only judge of whether that escaping is *correct*.
    # Under-escaped, this is a filtergraph syntax error ("No option name near
    # ...") or a sendcmd that cannot open the file it was handed.
    _ffmpeg_accepts(
        f"color=c=black:s=64x64:d={CLICK_SECONDS}",
        "-vf",
        f"null{fragment}",
        "-f",
        "null",
        "-",
    )


@needs_ffmpeg
def test_an_undecodable_track_yields_no_flash_rather_than_an_error(tmp_path):
    # The flash is a garnish; a track ffmpeg cannot read should cost the render
    # its flash, not fail it.
    junk = tmp_path / "not-really-a.wav"
    junk.write_bytes(b"this is not audio")
    assert onset_envelope(junk, fps=CLICK_FPS) == []
    assert flash_filter(junk, fps=CLICK_FPS, duration=1.0, workdir=tmp_path) == ""


def test_a_build_without_sendcmd_drops_the_flash_instead_of_breaking_the_graph(
    tmp_path, monkeypatch
):
    import muvid.visualize.reactive as reactive

    monkeypatch.setattr(reactive, "has_filter", lambda name: name != "sendcmd")
    fragment = reactive.flash_filter(
        tmp_path / "song.wav", fps=CLICK_FPS, duration=1.0, workdir=tmp_path
    )
    assert fragment == ""


@needs_ffmpeg_filter("showspectrum", *FLASH_FILTERS)
def test_the_spectrum_visual_flashes_last_in_the_chain_and_can_be_switched_off(
    click_track, tmp_path
):
    ctx = VisualContext(
        audio=click_track,
        image=None,
        duration=float(CLICK_SECONDS),
        size=(320, 180),
        fps=CLICK_FPS,
        workdir=tmp_path,
    )
    chain = resolve_visual("spectrum", ctx).filters[0]
    assert "sendcmd=f=" in chain and f"lutyuv@{DEFAULT_FLASH_LABEL}=" in chain
    # The flash must come *after* the recolour, so it modulates the colours the
    # frame actually shows rather than ones that are about to be replaced.
    assert chain.index("colorchannelmixer") < chain.index("sendcmd")

    off = resolve_visual("spectrum", replace(ctx, options={"flash": False}))
    assert "sendcmd" not in off.filters[0]


@needs_ffmpeg_filter("showspectrum", *FLASH_FILTERS)
def test_the_flashing_spectrum_renders_and_changes_the_picture(click_track, tmp_path):
    # ffmpeg is the real judge of a sendcmd script: a malformed one fails the
    # render outright. And the flash has to *do* something, so the same source
    # rendered with and without it must not come out identical.
    from muvid.visualize import render_audio_video

    renders = {}
    for name, options in (("on", {}), ("off", {"flash": False})):
        renders[name] = render_audio_video(
            click_track,
            visual="spectrum",
            saveas=tmp_path / f"flash-{name}.mp4",
            size=(320, 180),
            fps=CLICK_FPS,
            options=options,
        )
    assert _video_stream(renders["on"].path)["pix_fmt"] == "yuv420p"
    assert abs(renders["on"].duration - media_duration(click_track)) < 0.5
    # Digest rather than raw bytes: identical mp4s would otherwise dump two
    # multi-kilobyte blobs into the failure report.
    digests = {
        k: hashlib.sha256(r.path.read_bytes()).hexdigest() for k, r in renders.items()
    }
    assert digests["on"] != digests["off"], "the flash changed nothing in the picture"


# --------------------------------------------------------------------------
# The GPL `eq` is gone; the substitution is measured, not asserted (muvid#69)
# --------------------------------------------------------------------------
#
# `eq` is compiled into ffmpeg only under `--enable-gpl`, so muvid needed a GPL
# build for two chains that are arithmetic on three planes: the darkened blurred
# background, and the beat flash. Both are `lutyuv` now.
#
# A string assertion cannot check a substitution — it only checks that somebody
# typed the new name. So render both filters over the same source and compare
# DECODED planes. The residual is not zero, and the reason matters: `vf_eq` runs
# an integer fast path that quantises `brightness` to 1/100 and carries a
# rounding offset, while these expressions are the exact arithmetic `eq`'s own
# documentation describes. Measured luma offsets (input value -> output value),
# ffmpeg 8.1:
#
#     brightness   exact (b*255)   eq     lutyuv
#       -0.25         -63.75      -65      -64
#       -0.30         -76.50      -80      -77
#       -0.55        -140.25     -144     -141
#       +0.1875       +47.81      +45      +47
#
# So where the two disagree, `lutyuv` is the correct side. `EQ_LUT_TOLERANCE_LSB`
# bounds that disagreement; it is a measurement, and tightening it below the
# measured worst case would fail on `eq`'s error, not on ours.
#
# This test is the one place that still WANTS the GPL filter — it is the
# reference being retired — so it skips on the LGPL build the change exists to
# serve.

#: Worst per-plane |eq - lutyuv| over the shipped constants, measured. ~1% of
#: full scale: invisible in the picture, and in `eq`'s favour nowhere.
EQ_LUT_TOLERANCE_LSB = 3


def _planes(vf, *, size=(160, 90), frames=1):
    """Decode `vf` over a fixed synthetic source into stacked (Y, U, V) planes.

    yuv444p so chroma is compared at full resolution — a subsampled read would
    average away exactly the chroma error this test exists to bound.
    """
    w, h = size
    out = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s={w}x{h}:r=10:d={frames / 10}",
            "-vf",
            vf,
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv444p",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(out, np.uint8).astype(int).reshape(-1, 3, h, w)


def _max_plane_diff(eq_filter, lut_exprs):
    a = _planes(eq_filter)
    b = _planes(lut_filter(lut_exprs))
    assert a.shape == b.shape
    return int(np.abs(a - b).max())


@needs_ffmpeg
def test_the_script_addresses_the_filter_the_fragment_declares(click_track, tmp_path):
    """A sendcmd command aimed at a filter that is not in the graph is SILENT.

    Measured on ffmpeg 8.1: a script targeting `nosuch@flash` renders the whole
    clip, exits 0, and logs nothing at `-loglevel warning`. The flash just never
    happens. So the label the script writes and the label the filter carries have
    to be the same string by construction, and this is the test that says so.
    """
    fragment = flash_filter(
        click_track, fps=CLICK_FPS, duration=float(CLICK_SECONDS), workdir=tmp_path
    )
    assert fragment
    declared = {
        p.split("=")[0]
        for p in re.split(r"(?<!\\),", fragment)
        if p and "@" in p.split("=")[0]
    }
    script = (tmp_path / f"{DEFAULT_FLASH_LABEL}.cmd").read_text().splitlines()
    targeted = {SENDCMD_LINE.match(line)["target"] for line in script}
    assert targeted == declared, (
        f"the script drives {sorted(targeted)} but the fragment declares "
        f"{sorted(declared)}; ffmpeg will render this happily and never flash."
    )


@needs_ffmpeg_filter("eq", "lutyuv")
@pytest.mark.parametrize(
    "dim,saturation",
    [
        (CoverLayout().dim, CoverLayout().saturation),  # the still-cover default
        (REACTIVE_BG_DIM, REACTIVE_BG_SATURATION),  # every reactive visual
        (0.55, REACTIVE_BG_SATURATION),  # `scope`'s darker frame
    ],
)
def test_the_background_lut_reproduces_the_gpl_eq_it_replaced(dim, saturation):
    """`background_chain`'s darken/desaturate, at the constants that ship."""
    worst = _max_plane_diff(
        f"eq=brightness=-{dim}:saturation={saturation}",
        brightness_saturation_lut(brightness=-dim, saturation=saturation),
    )
    assert worst <= EQ_LUT_TOLERANCE_LSB, (
        f"dim={dim} saturation={saturation}: the lutyuv background is {worst} LSB "
        f"from the eq it replaced (bound {EQ_LUT_TOLERANCE_LSB}). That is a look "
        "change, not a substitution."
    )


@needs_ffmpeg_filter("eq", "lutyuv")
@pytest.mark.parametrize("pulse", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_the_flash_lut_reproduces_the_gpl_eq_it_replaced(pulse):
    """The flash, across its whole envelope — including the two endpoints."""
    brightness, saturation = FLASH_BRIGHTNESS * pulse, 1 + FLASH_SATURATION * pulse
    worst = _max_plane_diff(
        f"eq=brightness={brightness}:saturation={saturation}",
        brightness_saturation_lut(brightness=brightness, saturation=saturation),
    )
    assert worst <= EQ_LUT_TOLERANCE_LSB, (
        f"pulse={pulse}: the lutyuv flash is {worst} LSB from the eq it replaced "
        f"(bound {EQ_LUT_TOLERANCE_LSB})."
    )


@needs_ffmpeg
def test_the_flash_probe_names_the_filters_the_fragment_actually_uses(
    click_track, tmp_path
):
    """`FLASH_FILTERS` is a probe, and a stale probe fails in silence.

    `flash_filter` returns `""` when a build lacks one of these, so naming a
    filter the chain no longer contains means an LGPL build — the build muvid#69
    exists to serve — passes nothing and renders with no flash, with no error
    anywhere. It stayed `("sendcmd", "eq")` for exactly as long as nothing
    derived it from the fragment, so derive it.
    """
    fragment = flash_filter(
        click_track, fps=CLICK_FPS, duration=float(CLICK_SECONDS), workdir=tmp_path
    )
    assert fragment, "no fragment to inspect — this build cannot flash at all"
    # Split on the commas the filtergraph parser would split on: the escaped ones
    # inside the script path and the LUT expressions are not separators.
    parts = [p for p in re.split(r"(?<!\\),", fragment) if p]
    used = {p.split("=")[0].split("@")[0] for p in parts}
    assert used == set(FLASH_FILTERS), (
        f"the flash fragment uses {sorted(used)} but FLASH_FILTERS probes for "
        f"{sorted(FLASH_FILTERS)}. A build missing a filter this does not probe "
        "for fails the render; one missing a filter it no longer uses loses the "
        "flash for no reason."
    )


@needs_ffmpeg_filter("eq", "lutyuv")
def test_the_chroma_pivot_is_127_5_and_not_128():
    """128 is the intuitive pivot and it is the wrong one.

    `vf_eq` scales saturation in 0-1 about 0.5, which is 127.5 in eight bits.
    The difference never exceeds `0.5 * |1 - saturation|`, so across muvid's
    range it is well under one LSB and the tolerance test above cannot see it —
    it shows up in the MEAN, which is why this one measures that instead. Left
    unpinned, "128 is obviously the middle" is a one-character edit nothing
    would have caught.
    """
    dim, saturation = CoverLayout().dim, CoverLayout().saturation
    reference = _planes(f"eq=brightness=-{dim}:saturation={saturation}")

    def mean_chroma_error(exprs):
        got = _planes(lut_filter({k: v for k, v in exprs.items() if k in "uv"}))
        return float(np.abs(reference[:, 1:] - got[:, 1:]).mean())

    # The SHIPPED expressions, not a re-derivation of them — a test that built
    # both pivots by hand would pin the decision while letting the constant in
    # the code be anything at all.
    ours = mean_chroma_error(
        brightness_saturation_lut(brightness=-dim, saturation=saturation)
    )
    naive_expr = f"clip((val-128)*{saturation:g}+128,0,255)"
    naive = mean_chroma_error({"u": naive_expr, "v": naive_expr})
    assert ours < naive, (
        f"the shipped chroma pivot gives mean error {ours:.3f} against eq, and "
        f"pivoting about 128 gives {naive:.3f}. 127.5 is supposed to be the "
        "better one — check _CHROMA_PIVOT."
    )


def test_the_canvas_doctests_actually_run():
    """`testpaths` is `tests/` and nothing passes `--doctest-modules`, so a `>>>`
    under `muvid/` is prose that no run has ever executed. The LUT builders'
    examples state exact filter strings — the kind of claim that goes stale in
    silence — so collect that one module's doctests here.
    """
    import doctest

    from muvid.visualize import canvas

    result = doctest.testmod(
        canvas, optionflags=doctest.NORMALIZE_WHITESPACE | doctest.ELLIPSIS
    )
    assert result.attempted, "no doctests found in canvas.py — did they move?"
    assert result.failed == 0


@needs_ffmpeg_filter("lutyuv")
def test_the_flash_at_rest_is_bit_exact_and_not_merely_close():
    """The tolerance above may not cover the resting state.

    Most frames of most renders carry no pulse, so `brightness=0, saturation=1`
    has to be the identity to the byte — a 1-LSB "no-op" would tint every quiet
    frame of every video muvid makes.
    """
    untouched = _planes("null")
    at_rest = _planes(lut_filter(brightness_saturation_lut()))
    assert np.array_equal(untouched, at_rest), (
        "the flash's resting LUT is not the identity; every unflashed frame moves"
    )


# --------------------------------------------------------------------------
# The flash is on by default, and the skill has to say so (muvid#5)
# --------------------------------------------------------------------------
#
# The spectrum visual pulses on every onset unless you turn it off, and for
# months no markdown in this repo mentioned it: `grep -ri flash` over every .md
# returned nothing. So an agent reading the skill could neither discover the
# effect nor disable it, and the text that would have said so lived only on a
# branch that has since been deleted. Prose drifts silently — a call-site sweep
# cannot see it — which is why the camera-phrase table in
# `tests/test_animation_camera.py` is pinned the same way.

VISUALIZE_SKILL = (
    Path(__file__).parents[1] / ".claude" / "skills" / "muvid-visualize" / "SKILL.md"
)


def _flash_options_the_code_reads() -> set[str]:
    """Every ``ctx.options.get("flash*")`` key in the visuals module, by AST.

    Parsed rather than grepped so a renamed or added knob is found by structure,
    and read from the SOURCE so this cannot pass by importing a stale module.
    """
    import ast

    tree = ast.parse(
        (Path(__file__).parents[1] / "muvid" / "visualize" / "visuals.py").read_text()
    )
    keys = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("flash")
        ):
            keys.add(node.args[0].value)
    return keys


def test_the_skill_names_every_flash_option_the_code_actually_reads():
    text = VISUALIZE_SKILL.read_text()
    missing = sorted(k for k in _flash_options_the_code_reads() if f"`{k}`" not in text)
    assert not missing, (
        f"{VISUALIZE_SKILL.name} does not name the flash options {missing}; an agent "
        "reading it cannot know they exist. Document them, or remove them from the code."
    )


def test_the_skill_does_not_advertise_an_option_the_code_ignores():
    """`flash_decay` is the trap: a real constant that is NOT a forwarded option.

    ``FLASH_DECAY`` is ``flash_filter``'s keyword default and ``spectrum_visual``
    never passes an option to it, so ``options={"flash_decay": 0.9}`` is silently
    ignored. A doc that offered it would be worse than one that said nothing.
    """
    read = _flash_options_the_code_reads()
    assert "flash_decay" not in read, (
        "`flash_decay` is now a real option — the skill's 'there is no flash_decay "
        "option' paragraph is false and must be updated."
    )
    text = VISUALIZE_SKILL.read_text()
    assert "no `flash_decay` option" in text


def test_the_documented_flash_defaults_are_the_code_s_defaults():
    from muvid.visualize.reactive import FLASH_BRIGHTNESS, FLASH_DECAY, FLASH_SATURATION

    text = VISUALIZE_SKILL.read_text()
    for name, value in (
        ("flash_brightness", FLASH_BRIGHTNESS),
        ("flash_saturation", FLASH_SATURATION),
    ):
        assert f"`{value}`" in text, (
            f"the skill does not state {name}'s default of {value}"
        )
    assert f"`FLASH_DECAY = {FLASH_DECAY}`" in text


def test_the_flash_is_still_on_by_default():
    """The fact the whole doc section exists to convey."""
    import inspect

    from muvid.visualize import visuals

    src = inspect.getsource(visuals.spectrum_visual)
    assert 'ctx.options.get("flash", True)' in src, (
        "the spectrum flash is no longer on by default; the skill says it is."
    )
