"""muvid wires its lacing alignment store into an's lipsync pipeline.

The animation render strategy must build a ``WordTimingsLipSync`` from
the project's ``lyrics/alignment.annot`` so ``an`` doesn't re-run
whisper on the same audio. This is the SSOT enforcement from the
roadmap (I1).
"""

from __future__ import annotations

import pytest

pytest.importorskip("lacing")
pytest.importorskip("an")


def _make_alignment_store(project_root, shot_start=0.0, shot_end=10.0):
    """Write a small alignment.annot with a few words inside the shot window."""
    from lacing import SqliteStore
    from lacing.tracks.subtitle import SubtitleBuilder

    align_dir = project_root / "lyrics"
    align_dir.mkdir(parents=True, exist_ok=True)
    path = align_dir / "alignment.annot"
    if path.exists():
        path.unlink()
    store = SqliteStore(str(path))
    try:
        b = SubtitleBuilder(store, asset_id="song:audio")
        # Put words inside the shot window
        b.section("verse", shot_start, shot_end, title="verse")
        b.line(
            "hello world", shot_start + 0.5, shot_start + 2.0,
            section="verse",
            line_index=0,
            words=[
                ("hello", shot_start + 0.5, shot_start + 1.0, 0.95),
                ("world", shot_start + 1.2, shot_start + 2.0, 0.92),
            ],
        )
    finally:
        store.close()
    return path


def _make_render_ctx(project_root, shot_start=0.0, shot_end=10.0):
    """Build a minimal RenderContext pointing at the project."""
    from muvid.project import MusicVideoProject
    from muvid.renderers import RenderContext
    from muvid.schema import ShotSpec

    p = MusicVideoProject(project_root)
    shot = ShotSpec(
        id="s01",
        start_s=shot_start,
        end_s=shot_end,
        render_strategy="animation",
        characters=("alice",),
    )
    return RenderContext(
        project=p,
        shot=shot,
        shot_dir=project_root / "shots" / "s01",
        audio_slice_path=project_root / "song" / "audio.mp3",
        character_image_paths={},
        environment_image_path=None,
        lyric_lines=[],
        global_style="",
    )


def test_word_timings_for_shot_returns_relative_offsets(tmp_path):
    """Word timings are returned relative to the shot's start (slice t=0)."""
    from muvid import facade
    from muvid.renderers.animation import _word_timings_for_shot

    facade.init_project(tmp_path / "p")
    _make_alignment_store(tmp_path / "p", shot_start=10.0, shot_end=15.0)
    ctx = _make_render_ctx(tmp_path / "p", shot_start=10.0, shot_end=15.0)

    timings = _word_timings_for_shot(ctx)
    assert len(timings) == 2
    # "hello" was at song time 10.5–11.0, so relative to shot-slice it's 0.5–1.0
    assert timings[0][0] == "hello"
    assert timings[0][1] == pytest.approx(0.5)
    assert timings[0][2] == pytest.approx(1.0)
    assert timings[1][0] == "world"
    assert timings[1][1] == pytest.approx(1.2)


def test_word_timings_for_shot_empty_without_alignment_store(tmp_path):
    from muvid import facade
    from muvid.renderers.animation import _word_timings_for_shot

    facade.init_project(tmp_path / "p")
    # No alignment store written.
    ctx = _make_render_ctx(tmp_path / "p", 0.0, 10.0)
    assert list(_word_timings_for_shot(ctx)) == []


def test_make_lipsync_provider_returns_word_timings_lipsync(tmp_path):
    """When alignment exists, build a WordTimingsLipSync."""
    from muvid import facade
    from muvid.renderers.animation import _make_lipsync_provider

    facade.init_project(tmp_path / "p")
    _make_alignment_store(tmp_path / "p", shot_start=0.0, shot_end=5.0)
    ctx = _make_render_ctx(tmp_path / "p", 0.0, 5.0)

    provider = _make_lipsync_provider(ctx)
    assert provider is not None

    from an.audio import LipSyncProvider, WordTimingsLipSync

    assert isinstance(provider, WordTimingsLipSync)
    assert isinstance(provider, LipSyncProvider)
    # The provider's name embeds "muvid:lacing" so an's caches diverge
    # cleanly from whisper-based runs.
    assert "muvid:lacing" in provider.name


def test_make_lipsync_provider_returns_none_without_words(tmp_path):
    from muvid import facade
    from muvid.renderers.animation import _make_lipsync_provider

    facade.init_project(tmp_path / "p")
    # Shot is far outside any word in the alignment store.
    _make_alignment_store(tmp_path / "p", shot_start=0.0, shot_end=5.0)
    ctx = _make_render_ctx(tmp_path / "p", 100.0, 105.0)

    assert _make_lipsync_provider(ctx) is None


def test_lipsync_provider_does_not_re_transcribe(tmp_path):
    """Sanity: the provider's align() works without ever calling whisper.

    We exercise the provider with a fabricated AudioClip; if it tried
    to load faster-whisper it would either crash or produce different
    timings. We just check the visemes are derived from our injected
    word timings.
    """
    from an.audio import StaticWordTimings, WordTimingsLipSync
    from an.audio.tts import AudioClip

    timings = [("hello", 0.5, 1.0), ("world", 1.2, 2.0)]
    provider = WordTimingsLipSync(StaticWordTimings(timings, label="x"))
    track = provider.align(AudioClip(duration=2.0, transcript=""), transcript="")
    assert track.duration == 2.0
    # m+a maps to A; the "world" timeframe should produce some non-rest
    # visemes.
    non_rest = [v for v in track.visemes if v.code != "X"]
    assert non_rest, "should emit non-rest visemes for both words"
