"""Pin ``muvid``'s command-line grammar against what argh produced before the cw migration.

The golden in ``tests/cli_goldens/muvid.json`` was recorded by running the real
``muvid`` entry point while it still dispatched through ``argh`` (0.31.3, via
``argh.dispatch_commands`` with the default name-mapping policy). Every vector's
exit code and normalised ``usage:`` line is asserted here.

What is deliberately *not* asserted: full ``--help`` bodies and argparse's error
text. CPython rewrites both between versions (3.12 changed the "invalid choice"
quoting and the option column) and CI spans 3.10 and 3.12. The stronger check --
byte-identical stdout, stderr and exit code across all 34 vectors, run through
both ``python -m muvid`` and the ``muvid`` console script -- was done at
migration time and is recorded in the pull request.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from muvid.__main__ import COMMANDS

GOLDEN = json.loads(
    (Path(__file__).parent / "cli_goldens" / "muvid.json").read_text(encoding="utf-8")
)
CASES = GOLDEN["cases"]


def _usage(text: str) -> str:
    """The first ``usage:`` block, whitespace-collapsed, prog neutralised."""
    match = re.search(r"^usage: (.*?)(?=\n\S|\n\n|\Z)", text, re.S | re.M)
    if not match:
        return ""
    return re.sub(r"^PROGNAME", "PROG", " ".join(match.group(1).split()))


#: Every recorded vector either prints help or fails to parse, so each one
#: returns promptly. The timeout is a guard against a *grammar regression*: if
#: ``serve``'s ``root`` ever became a positional again, ``serve
#: positional-not-allowed`` would stop being an argparse error and would start
#: the web UI, and the suite would hang forever instead of failing.
RUN_TIMEOUT = 60


def _run(argv, cwd=None):
    """Run the CLI in a subprocess with a pinned prog, as a shell would see it."""
    code = (
        "import sys; sys.argv[0] = 'PROGNAME';"
        "from muvid.__main__ import main; sys.exit(main())"
    )
    return subprocess.run(
        [sys.executable, "-c", code, *argv],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, "COLUMNS": "80"},
        timeout=RUN_TIMEOUT,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: " ".join(c["argv"]) or "(no args)")
def test_grammar_matches_the_argh_recording(case, tmp_path):
    """Every recorded vector still exits the same way with the same usage line."""
    proc = _run(case["argv"], cwd=tmp_path)
    assert proc.returncode == case["rc"], (
        f"{case['argv']} exited {proc.returncode}, expected {case['rc']}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert _usage(proc.stdout or proc.stderr) == case["usage"]


def test_no_argument_invocation_prints_usage_to_stdout_and_exits_zero(tmp_path):
    """argh's no-argument behaviour, which plain argparse does NOT reproduce.

    A bare argparse parser with a required subparser exits 2 to stderr. argh
    printed usage to stdout and exited 0, and ``muvid`` has always done that.
    """
    proc = _run([], cwd=tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.startswith("usage:")
    assert proc.stderr == ""


def test_argument_errors_exit_non_zero(tmp_path):
    """``main()`` must RETURN the exit code and both entry points must raise it.

    ``cw.dispatch`` returns the code where ``argh.dispatch_commands`` exited by
    itself. Forgetting the wiring makes every argument error exit 0 -- which no
    other test in this suite would notice.
    """
    assert _run(["no-such-command"], cwd=tmp_path).returncode == 2
    assert _run(["init"], cwd=tmp_path).returncode == 2


def test_a_command_that_raises_SystemExit_still_exits_one(tmp_path):
    """``render --shot`` refuses the budget flags; that refusal must stay an error.

    This is the only vector whose non-zero code comes from the command *body*
    rather than from argparse, so it is the one that proves a ``SystemExit``
    raised inside a command still reaches the shell intact.
    """
    proc = _run(["render", ".", "--shot", "s1", "--budget", "1"], cwd=tmp_path)
    assert proc.returncode == 1
    assert "apply to the whole project" in proc.stderr


def test_variadic_positional_survives(tmp_path):
    """``character-images ROOT NAME [paths ...]`` -- a VAR_POSITIONAL parameter.

    ``character_images(root, name, *paths)`` must keep accepting zero or more
    trailing paths as positionals rather than becoming an option.
    """
    proc = _run(["character-images", "--help"], cwd=tmp_path)
    assert proc.returncode == 0
    assert "[paths ...]" in " ".join(proc.stdout.split())


def test_serve_root_is_an_option_not_a_positional(tmp_path):
    """``serve(root=".")`` is a defaulted positional; argh's DEFAULT policy made it
    an option (``-r ROOT``), and cw's ARGH convention must agree.

    This is the discriminator that shows ``muvid`` runs on the default
    name-mapping policy -- unlike sibling repos that explicitly selected
    ``BY_NAME_IF_KWONLY``, where the same signature stays positional. If a future
    edit adds a convention override to ``main()``, this test goes red.
    """
    usage = " ".join(_run(["serve", "--help"], cwd=tmp_path).stdout.split())
    assert "[-r ROOT]" in usage
    assert "[root]" not in usage
    # And the consequence, which is why this matters more than a usage string:
    # with `root` positional, this invocation would START THE WEB SERVER instead
    # of being rejected.
    assert _run(["serve", "positional-not-allowed"], cwd=tmp_path).returncode == 2


def test_every_command_is_reachable_on_the_command_line(tmp_path):
    """The parser is built from ``COMMANDS``, so it cannot drift from it."""
    top = _run(["--help"], cwd=tmp_path).stdout
    for func in COMMANDS:
        name = func.__name__.replace("_", "-")
        assert re.search(rf"[{{,]{re.escape(name)}[,}}]", " ".join(top.split())), (
            f"{name} is missing from the top-level command list"
        )


def test_the_cli_no_longer_imports_argh():
    """muvid no longer depends on argh; nothing in the CLI path may import it."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, muvid.__main__; sys.exit(1 if 'argh' in sys.modules else 0)",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, "importing muvid.__main__ pulled in argh"
