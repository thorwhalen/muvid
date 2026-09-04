"""Tests for muvid.genre — the ``music-visualizer`` nw.Genre registration + factory.

Pure (nw only, no ffmpeg): registration, the available-genre-is-ready invariant, the
Templates, and the project-factory create/rollback contract a host connector drives via
``nw.create_genre_project`` (thorwhalen/muvid#3).
"""

from __future__ import annotations

import pytest

nw = pytest.importorskip("nw")

import muvid.genre as genre_mod  # noqa: E402 — after the nw importorskip


def test_genre_is_registered_and_available_and_ready():
    genre = nw.get_genre("music-visualizer")
    assert genre.status == "available"
    # The load-bearing invariant: an available genre must be ready. muvid's visuals are
    # muvid-internal ffmpeg strategies (NOT nw.renderers), so the genre carries them as
    # Templates only — setting strategy_names would make is_ready() False.
    assert genre.is_ready() is True
    assert nw.describe_genre("music-visualizer")["ready"] is True
    assert genre.transform_names == ()
    assert genre.strategy_names == ()
    assert genre.projection_entrypoint is None
    assert genre.cost_profile is None  # free


def test_templates_are_the_exposed_visuals_and_exclude_ken_burns():
    genre = nw.get_genre("music-visualizer")
    slugs = genre.list_templates()
    assert slugs == ["still", "cqt", "bars", "spectrum", "waves", "scope"]
    assert "ken_burns" not in slugs  # the slow Pillow path is not exposed by the genre
    # each Template carries its visual slug as opaque params
    assert genre.template("cqt").params == {"visual": "cqt"}
    assert genre.defaults == {"visual": "auto"}


def test_resolve_genre_returns_the_visual_params_envelope():
    env = nw.resolve_genre("music-visualizer", "waves")
    assert env["genre"] == "music-visualizer"
    assert env["template"] == "waves"
    assert env["params"] == {"visual": "waves"}


def test_factory_creates_a_bucket_and_create_genre_project_round_trips(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    info = nw.create_genre_project(
        "music-visualizer", "user@example.com", "proj-A", template="cqt"
    )
    assert info["project_id"] == "proj-A"
    assert info["template"] == "cqt"
    assert info["visual"] == "cqt"
    # the bucket exists under the caller's own subtree, and exposes .root for rollback
    from muvid.mcp.workspace import VisualizerWorkspace

    proj = VisualizerWorkspace.for_email("user@example.com").open_project("proj-A")
    assert proj.root.exists()
    assert (proj.root / "manifest.json").exists()
    assert proj.manifest()["title"] == "proj-A"


def test_factory_rollback_target_is_the_bucket_root(tmp_path, monkeypatch):
    # nw._rollback_project deletes project.root on a failed seed; the factory's returned
    # project must expose it. (No initializer is registered, so rollback never fires in
    # practice — but the contract must hold.)
    monkeypatch.setenv("MUVID_DATA_HOME", str(tmp_path))
    proj = genre_mod._music_visualizer_project_factory(
        "u@x.com", "p", title="p", template="still", params={"visual": "still"}
    )["project"]
    assert proj.root.exists()
    import shutil

    shutil.rmtree(proj.root, ignore_errors=True)  # what rollback would do
    assert not proj.root.exists()
