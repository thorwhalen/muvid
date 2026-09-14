"""The data root is one function, and no muvid default ever points inside ``$HOME/muvid``.

The bug these guard: ``root`` is a required positional on every part-3 CLI verb and on
``MusicVideoProject``, so no code answered "where does a new project go" — the only
written-down answer was prose in ``.claude/skills/muvid/SKILL.md`` saying
``~/muvid/<song-stem>``, which is an app-named directory under ``$HOME``. A real project
landed there. The assertions below are deliberately about the PROPERTY (nothing defaults
into an app dir) and about the SSOT (one implementation, not three), because a test that
only pinned the happy-path string would stay green while a fourth copy of the function
drifted.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from muvid import facade, paths
from muvid.footage import workspace as footage_ws
from muvid.mcp import workspace as mcp_ws


def test_data_home_default_is_the_xdg_data_dir(monkeypatch):
    monkeypatch.delenv(paths.DATA_HOME_ENV_VAR, raising=False)
    assert paths.data_home() == Path.home() / ".local" / "share" / "muvid"


def test_data_home_honours_the_one_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_HOME_ENV_VAR, str(tmp_path))
    assert paths.data_home() == tmp_path
    # And every surface sees it, because they are the same function object.
    assert footage_ws.data_root() == tmp_path
    assert mcp_ws.data_root() == tmp_path


def test_the_two_workspaces_share_the_one_implementation():
    """Not "agree today" — literally the same object, so a third copy cannot appear."""
    assert footage_ws.data_root is paths.data_home
    assert mcp_ws.data_root is paths.data_home
    assert footage_ws.safe_component is paths.safe_component
    assert mcp_ws._safe_component is paths.safe_component
    assert footage_ws.DATA_HOME_ENV_VAR == mcp_ws.DATA_HOME_ENV_VAR == "MUVID_DATA_HOME"


def test_project_root_lands_under_a_kind_subfolder_not_the_root(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_HOME_ENV_VAR, str(tmp_path))
    root = paths.project_root("il-pleut")
    assert root == tmp_path / "projects" / "il-pleut"
    # The data root itself stays open for future kinds (music_video/, visualizer/, ...).
    assert root.parent != tmp_path


def test_project_root_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_HOME_ENV_VAR, str(tmp_path))
    root = paths.project_root("unmade")
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", ["", "   ", ".", "..", "a/b", "a\\b", "x\x00y"])
def test_project_root_refuses_a_traversing_name(bad, monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_HOME_ENV_VAR, str(tmp_path))
    with pytest.raises(ValueError):
        paths.project_root(bad)


def test_no_default_root_is_an_app_dir_under_home(monkeypatch):
    """The regression, stated as the property rather than as one bad string.

    ``~/muvid/...`` is what the skill used to say. Any default that resolves directly
    under ``$HOME`` is the same mistake regardless of the name it uses.
    """
    monkeypatch.delenv(paths.DATA_HOME_ENV_VAR, raising=False)
    home = Path.home()
    for candidate in (
        paths.data_home(),
        paths.project_root("some-song"),
        Path(facade.default_project_root("some-song")),
        footage_ws.data_root(),
        mcp_ws.data_root(),
    ):
        assert candidate.parent != home, f"{candidate} sits directly in $HOME"
        assert home / "muvid" not in candidate.parents
        assert candidate.is_relative_to(home / ".local" / "share" / "muvid")


def test_facade_and_paths_agree(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_HOME_ENV_VAR, str(tmp_path))
    assert facade.default_project_root("s") == str(paths.project_root("s"))


def test_the_skill_no_longer_hardcodes_an_app_dir_default():
    """The root cause was prose, so the prose is pinned too.

    The skill is what an agent reads and follows literally; a code-only fix would leave
    the sentence that actually produced ``~/muvid/il-pleut`` in place.
    """
    skill = (
        Path(__file__).resolve().parents[1]
        / ".claude"
        / "skills"
        / "muvid"
        / "SKILL.md"
    )
    if not skill.is_file():  # pragma: no cover - not shipped in a wheel
        pytest.skip("skill not present in this checkout")
    text = skill.read_text(encoding="utf-8")
    # The old instruction, as a path an agent would copy. The cautionary mention of it
    # in the replacement prose is quoted with a trailing `<song-stem>`, so match the
    # instruction form: a backticked path used as the default.
    assert "Default to\n   `~/muvid/" not in text
    assert "muvid project-root" in text


def test_project_root_verb_is_wired_into_the_cli():
    """A resolver nothing can reach is a resolver the skill cannot call."""
    from muvid.__main__ import COMMANDS, project_root as verb

    assert verb in COMMANDS


def test_importing_muvid_paths_pulls_no_extra():
    """``muvid.paths`` is on every import-safe path, so it stays stdlib-only."""
    code = (
        "import muvid.paths, sys;"
        "heavy=[m for m in ('numpy','cv2','fastmcp','moviepy','nw') if m in sys.modules];"
        "print(heavy)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", out.stdout
