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

import pathlib
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


def test_the_two_workspaces_delegate_to_the_one_implementation(monkeypatch):
    """Not "agree today" — they must actually DELEGATE, so a third copy cannot appear.

    Identity (``data_root is data_home``) was the first version and is blunter, but both
    are thin forwarders rather than aliases now, so the names keep ``__module__`` for
    generated docs and for this repo's drift-test idiom. Delegation is the property that
    matters and it survives that change: redirect the SSOT and both forwarders follow.
    A module that had grown its own copy would not.
    """
    # Each module does `from muvid.paths import data_home`, i.e. binds by VALUE, so the
    # patch goes on the module's own binding. Patching `paths.data_home` would leave
    # both forwarders pointing at the original and pass for the wrong reason.
    for module in (footage_ws, mcp_ws):
        monkeypatch.setattr(module, "data_home", lambda: Path("/sentinel"))
    assert footage_ws.data_root() == Path("/sentinel")
    assert mcp_ws.data_root() == Path("/sentinel")
    # safe_component is imported by value in both, so identity is the right check there.
    assert footage_ws.safe_component is paths.safe_component
    assert mcp_ws._safe_component is paths.safe_component
    assert footage_ws.DATA_HOME_ENV_VAR == mcp_ws.DATA_HOME_ENV_VAR == "MUVID_DATA_HOME"


def test_there_is_exactly_one_implementation_of_the_default_root():
    """The duplication itself, pinned: only ``muvid/paths.py`` may spell the path out.

    The two workspace modules each carried a verbatim copy. A fourth copy elsewhere would
    pass every behavioural test above while re-creating exactly the drift that let the
    part-3 surfaces end up with no default at all.
    """
    import muvid

    pkg = Path(muvid.__file__).parent
    spellers = sorted(
        f.relative_to(pkg).as_posix()
        for f in pkg.rglob("*.py")
        if '".local"' in f.read_text(encoding="utf-8")
    )
    assert spellers == ["paths.py"], f"the data root is spelled out in {spellers}"


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


#: Every prose site that teaches a reader or an agent where a project goes. The README is
#: the one that matters most and was missed on the first pass: ``pyproject.toml`` declares
#: it as the ``readme``, so it ships to PyPI as the long_description, while the skill is
#: repo-only and reaches no wheel. The fix landed in the unpublished document first.
_PROSE_SITES = (
    ("README.md", True),
    (".claude/skills/muvid/SKILL.md", False),
)

#: The paragraph in SKILL.md that cites the old path as a cautionary tale is allowed to
#: name it; nothing else is. Keyed on a phrase from that paragraph.
_CAUTIONARY_MARKER = "This line used to say"


def _repo_root():
    return pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relpath,shipped", _PROSE_SITES)
def test_no_prose_site_teaches_an_app_dir_under_home(relpath, shipped):
    """The PROPERTY, not one byte-string — the first version of this guard was a string.

    It asserted ``"Default to\n   `~/muvid/" not in text``, which catches exactly that
    byte sequence including its three-space continuation indent. Measured against five
    plausible reintroductions — the same sentence un-wrapped onto one line, reordered,
    re-indented to 2 or 4 spaces, or reworded to "Put the project in `~/muvid/...`" — it
    passed all five. A guard defeated by reflowing a paragraph is not a guard.

    So: ``~/muvid/`` may not appear at all, except inside the one paragraph that cites it
    as the cautionary example. Anything else is the instruction coming back.
    """
    path = _repo_root() / relpath
    if not path.is_file():  # pragma: no cover - not shipped in a wheel
        pytest.skip(f"{relpath} not present in this checkout")
    text = path.read_text(encoding="utf-8")

    # The exemption is the ONE line carrying the marker, not "anything after it".
    # Measured: an earlier version of this guard allowed every line at or below the
    # marker's index, and all five of the rewordings below passed simply by being
    # appended further down the file. A region-based exemption is a hole the size of the
    # rest of the document; a line-based one is the size of the sentence it is for.
    offenders = [
        ln.strip()
        for ln in text.splitlines()
        if "~/muvid/" in ln and _CAUTIONARY_MARKER not in ln
    ]
    assert not offenders, (
        f"{relpath} teaches an app dir under $HOME again: {offenders}. "
        "Point at `muvid project-root` instead of writing the path."
    )


@pytest.mark.parametrize("relpath,shipped", _PROSE_SITES)
def test_every_prose_site_points_at_the_resolver(relpath, shipped):
    """Absence is half the property: the instruction has to say what to do INSTEAD.

    The first version paired its string check with ``"muvid project-root" in text``, which
    the cautionary paragraph alone satisfies — so deleting the instruction and keeping the
    explanation stayed green. This requires the command in an assignment a reader can
    copy, which the explanation prose does not contain.
    """
    path = _repo_root() / relpath
    if not path.is_file():  # pragma: no cover
        pytest.skip(f"{relpath} not present in this checkout")
    text = path.read_text(encoding="utf-8")
    assert '"$(muvid project-root' in text, (
        f"{relpath} must tell the reader to ASK for the root, in a copyable form"
    )


def test_the_readme_is_the_shipped_prose_site():
    """Pin WHY the README is in the table above, so a packaging change cannot quietly
    move the published document out from under the guard."""
    pyproject = (_repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    assert 'readme = "README.md"' in pyproject


def test_project_root_verb_is_wired_into_the_cli():
    """A resolver nothing can reach is a resolver the skill cannot call."""
    from muvid.__main__ import COMMANDS, project_root as verb

    assert verb in COMMANDS


def test_importing_muvid_paths_pulls_no_extra():
    """``muvid.paths`` is on every import-safe path, so it stays stdlib-only."""
    # The claim is "stdlib-only", so the check is stdlib-only — not a closed list of
    # heavy names. A denylist of ('numpy','cv2','fastmcp','moviepy','nw') was the first
    # version and would have stayed green if `paths.py` grew an `import requests`, which
    # is the shape the claim is actually about. Everything third-party is reported.
    code = (
        "import sys, pathlib, sysconfig;"
        "before=set(sys.modules);"
        "import muvid.paths;"
        "stdlib=pathlib.Path(sysconfig.get_paths()['stdlib']).resolve();"
        "new=[n for n in set(sys.modules)-before"
        " if not n.startswith('muvid')"
        " and getattr(sys.modules[n],'__file__',None)"
        " and stdlib not in pathlib.Path(sys.modules[n].__file__).resolve().parents];"
        "print(sorted(new))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", f"muvid.paths pulled non-stdlib: {out.stdout}"


# --- the override knob, which is itself a way into the bug being fixed -------------


def test_override_is_expanduser_ed(monkeypatch):
    """``MUVID_DATA_HOME=~/store`` must not make a literal ``~`` directory.

    Without this, ``muvid project-root`` printed ``~/store/projects/x`` while
    ``MusicVideoProject`` resolved the same string to ``$HOME/store/projects/x`` — two
    different directories, and the skill tells agents to shell-interpolate the printed
    one. `ls "$root"` and `muvid init "$root"` would disagree.
    """
    monkeypatch.setenv(paths.DATA_HOME_ENV_VAR, "~/muvid_store_probe")
    assert paths.data_home() == Path.home() / "muvid_store_probe"
    assert "~" not in str(paths.project_root("song"))


@pytest.mark.parametrize("relative", ["mydata", "./mydata", "a/b"])
def test_relative_override_is_refused(relative, monkeypatch):
    """A relative data root follows the process around — and lands in an app dir.

    ``MUVID_DATA_HOME=mydata`` rooted the whole store at the cwd, so running from the
    app directory wrote data into it: the exact failure this module exists to prevent,
    reached through the module's own knob. Refused loudly rather than read as plausible.
    """
    monkeypatch.setenv(paths.DATA_HOME_ENV_VAR, relative)
    with pytest.raises(ValueError, match="absolute"):
        paths.data_home()


def test_every_default_root_is_absolute(monkeypatch):
    """``default_project_root``'s docstring promises an absolute path. Hold it to that."""
    monkeypatch.delenv(paths.DATA_HOME_ENV_VAR, raising=False)
    assert Path(facade.default_project_root("s")).is_absolute()
    monkeypatch.setenv(paths.DATA_HOME_ENV_VAR, "~/muvid_store_probe")
    assert Path(facade.default_project_root("s")).is_absolute()


# --- the one part-3 verb whose root is NOT a required positional -------------------


def test_the_ui_refuses_a_directory_that_is_not_a_project(tmp_path, monkeypatch):
    """``muvid serve`` defaults ``root`` to the cwd, and the UI used to grow a project.

    Measured before the guard: ``create_app(".")`` in an empty directory, then one
    ``POST /api/script``, left ``script/script.md`` and ``.muvid/decisions.jsonl`` there
    — 200, reported as success. Neither path is gitignored, so from a repo root that is
    untracked muvid data inside an app directory. ``muvid init`` is the verb that
    creates, and it takes an explicit root.
    """
    pytest.importorskip("fastapi", reason="the UI is behind the `ui` extra")
    from muvid.ui.app import create_app

    empty = tmp_path / "not_a_project"
    empty.mkdir()
    monkeypatch.chdir(empty)
    with pytest.raises(RuntimeError, match="no muvid project"):
        create_app(".")
    # and it refused without leaving anything behind
    assert list(empty.iterdir()) == []


def test_the_ui_still_accepts_a_real_project(tmp_path):
    """The guard must not break the legitimate case."""
    pytest.importorskip("fastapi", reason="the UI is behind the `ui` extra")
    from muvid.project import MusicVideoProject
    from muvid.ui.app import create_app

    root = tmp_path / "proj"
    MusicVideoProject.init(root, title="probe")
    assert create_app(root) is not None


def test_facade_verb_is_exported_like_its_siblings():
    """Every other facade verb is reachable as ``muvid.<name>``; this one too."""
    import muvid

    assert muvid._LAZY["default_project_root"] == "muvid.facade"
    assert callable(muvid.default_project_root)
