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
