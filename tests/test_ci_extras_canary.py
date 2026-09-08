"""CI canary: the deps the footage tests importorskip on MUST be present in CI.

``tests/test_footage.py`` (and the scoring/mcp suites) guard themselves with
``pytest.importorskip`` so a local checkout without the ``mcp`` extra still runs the rest
of the suite. In CI that same guard is a trap: when the workflow's installed extras drift,
the whole footage path silently skips and its coverage drops to 0% while the run stays
green (muvid#24 B5 — align.py and footage_tools.py sat at 0% for months).

This file has no skip guard. Outside CI it passes vacuously; inside CI (``$CI`` is set by
GitHub Actions) a missing dep is a FAILURE, so the extras drift is loud instead of silent.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

#: Modules the footage / mcp test files skip on, and the extra that provides each.
_REQUIRED_IN_CI = [
    ("nw", "mcp"),
    ("fastmcp", "mcp"),
    ("mixing", "(base dependency)"),
]


@pytest.mark.parametrize("module,extra", _REQUIRED_IN_CI)
def test_footage_test_deps_are_installed_in_ci(module, extra):
    if not os.environ.get("CI"):
        pytest.skip("canary only bites in CI — locally, missing extras are legitimate")
    assert importlib.util.find_spec(module) is not None, (
        f"{module!r} is not installed in CI, so every test that importorskips it is "
        f"silently skipped and its subject drops to 0% coverage. Install the {extra} "
        f"extra in .github/workflows/ci.yml (and keep [tool.wads.ci.install] extras in "
        f"pyproject.toml in agreement) — see muvid#24 B5."
    )


def test_the_installed_mixing_reports_alignment_support_in_ci():
    """The declared ``mixing`` floor must actually deliver ``support`` (muvid#59).

    A version floor is a claim about what pip resolves, not a fact about what is
    imported — and muvid's trust verdict degrades *silently* when the claim is wrong:
    ``_as_alignment`` reads ``support`` by name and treats its absence as "not
    measured", so an older ``mixing`` does not crash, it just quietly puts every
    alignment back on the confidence coefficient that muvid#59 was filed about. That is
    the same shape as the extras drift above — green, and measuring nothing.

    Capability, not version string: what muvid needs is the attribute, and asking for
    the attribute cannot be satisfied by a version that merely claims to have it.
    """
    if not os.environ.get("CI"):
        pytest.skip("canary only bites in CI — a local checkout may lag the floor")
    import dataclasses

    from mixing.audio import ClipAlignment

    fields = {f.name for f in dataclasses.fields(ClipAlignment)}
    assert "margin" in fields, (
        "the installed mixing has no ClipAlignment.margin, so muvid's gate loses its "
        "SEPARATOR — and `vouches_for` refuses every voted clip rather than degrading "
        "quietly, which is the right failure but a total outage. Raise the mixing floor "
        "in pyproject.toml, or find out why the resolver picked an older one."
    )
    assert "support" in fields, (
        "the installed mixing has no ClipAlignment.support, so muvid's alignment trust "
        "verdict has silently fallen back to the confidence coefficient — the very "
        "instrument muvid#59 is about. Raise the mixing floor in pyproject.toml, or "
        "find out why the resolver picked an older one."
    )


#: ffmpeg FILTERS the suite guards on with ``needs_ffmpeg_filter``, and what needs each.
#: Same trap as the extras above: a runner image whose ffmpeg lacks libass makes the
#: lyric-video ASS burn-in tests skip, and the run stays green while the default
#: renderer's mp4 path is measured by nothing (muvid#97). Ubuntu's ``ffmpeg`` package
#: depends on libass9 and libfreetype, so in CI these are a hard requirement.
_FILTERS_REQUIRED_IN_CI = [
    ("subtitles", "the lyric-video ASS renderer (libass)"),
    ("drawtext", "visualizer titles (libfreetype)"),
    ("xfade", "footage transitions"),
]


@pytest.mark.parametrize("filter_name,needed_for", _FILTERS_REQUIRED_IN_CI)
def test_ffmpeg_filters_are_present_in_ci(filter_name, needed_for):
    if not os.environ.get("CI"):
        pytest.skip("canary only bites in CI — a local slim ffmpeg is legitimate")
    import platform
    import shutil

    if platform.system() == "Windows":
        # Deliberate, and recorded in pyproject's [tool.wads.ops.ffmpeg]: the
        # Windows runner gets no ffmpeg (the installer crashes on its cp1252
        # console) and every ffmpeg-backed test skips there by design. A canary
        # that fires on a policy is not a canary.
        pytest.skip("ffmpeg is intentionally not installed on the Windows runner")
    assert shutil.which("ffmpeg"), (
        "CI's runner has no ffmpeg at all, so every ffmpeg-backed test is silently "
        "skipped. The wads install step ([tool.wads.ops.ffmpeg]) should have put one "
        "on PATH — check .github/workflows/ci.yml."
    )
    from muvid.visualize.ffmpeg import has_filter

    assert has_filter(filter_name), (
        f"CI's ffmpeg has no {filter_name!r} filter, so every test that guards on it "
        f"({needed_for}) is silently skipped and that path is measured by nothing. "
        "Fix the installer in [tool.wads.ops.ffmpeg] (pyproject.toml) — the check is "
        "capability-based precisely so a slim binary triggers a reinstall."
    )
