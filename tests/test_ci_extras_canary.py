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

    assert "support" in {f.name for f in dataclasses.fields(ClipAlignment)}, (
        "the installed mixing has no ClipAlignment.support, so muvid's alignment trust "
        "verdict has silently fallen back to the confidence coefficient — the very "
        "instrument muvid#59 is about. Raise the mixing floor in pyproject.toml, or "
        "find out why the resolver picked an older one."
    )
