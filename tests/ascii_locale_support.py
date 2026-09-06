"""Run a snippet in a child interpreter whose preferred text codec is ASCII.

``lyrics.md`` and ``script.md`` are the canonical, editable form of the user's
song text, so they are non-ASCII by nature. Python resolves the codec for an
unqualified ``Path.read_text`` / ``Path.write_text`` / ``open`` from the
*process* locale, which cannot be changed reliably once the interpreter is
running — hence a child process rather than a fixture that monkeypatches
something.

``LC_ALL=C`` on its own is not enough on a modern CPython: PEP 538 would coerce
it back to a UTF-8 locale and PEP 540 would turn UTF-8 mode on anyway, so both
are switched off explicitly. The resulting environment is what a Docker image
with no locale installed, a cron job, a systemd unit, or a minimal CI container
actually hands the process.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


#: Locale settings that pin the child interpreter's text codec to ASCII.
ASCII_LOCALE_ENV = {
    "LC_ALL": "C",
    "LANG": "C",
    "PYTHONCOERCECLOCALE": "0",  # PEP 538: don't coerce C -> C.UTF-8
    "PYTHONUTF8": "0",  # PEP 540: don't enable UTF-8 mode
}


def run_under_ascii_locale(source: str, *args: str) -> subprocess.CompletedProcess:
    """Run ``source`` (as ``python -c``) with an ASCII preferred encoding.

    ``args`` become ``sys.argv[1:]`` in the child. The completed process is
    returned so a test can assert on the exit status *and* on what the child
    printed — print results as ASCII-safe JSON, since the child's stdout is
    subject to the same ASCII codec as its files.
    """
    return subprocess.run(
        [sys.executable, "-c", source, *args],
        env={**os.environ, **ASCII_LOCALE_ENV},
        capture_output=True,
        text=True,
    )


def _ascii_locale_is_reachable() -> bool:
    """Whether :data:`ASCII_LOCALE_ENV` really yields an ASCII codec here.

    Asked of a live child rather than inferred from ``sys.platform``: Windows
    resolves the codec from its ANSI code page and ignores ``LC_ALL``, and a
    future CPython could change how the C locale is handled. Probing keeps the
    guard honest either way.
    """
    probe = run_under_ascii_locale(
        "import codecs, locale, sys; "
        "sys.exit(0 if codecs.lookup(locale.getpreferredencoding(False)).name "
        "== 'ascii' else 1)"
    )
    return probe.returncode == 0


#: Skip a test that needs the ASCII locale on a platform that cannot reach it.
needs_ascii_locale = pytest.mark.skipif(
    not _ascii_locale_is_reachable(),
    reason="this platform does not select an ASCII codec under LC_ALL=C",
)
