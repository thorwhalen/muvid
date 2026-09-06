"""The FastAPI UI's text I/O — the browser is UTF-8, so the server must be too.

Two of the UI's read sites move text from disk to the browser: ``GET /`` serves
the static page, and ``GET /api/script`` feeds the in-browser editor for
``script.md``. Both went through an unqualified ``read_text``, so their codec
came from the *process* locale. A serve under ``LC_ALL=C`` — a container with
no locale, a systemd unit — could not even render its own front page, since
``index.html`` carries em dashes and ellipses.

The locale cannot be changed once the interpreter is running, so this runs in a
child under :mod:`tests.ascii_locale_support`.
"""

from __future__ import annotations

import json

import pytest

from tests.ascii_locale_support import needs_ascii_locale, run_under_ascii_locale


pytest.importorskip("fastapi", reason="the UI is behind the `ui` extra")


#: What a user has in the browser's script editor.
NON_ASCII_SCRIPT = (
    "## [verso 1] 0.00 → 12.50\n\n"
    "### s01 | 0.00-12.50 | image_to_video\n"
    "Plano medio de Renée en el café — «mira a cámara».\n"
)

_SERVE = """
import json, sys
from fastapi.testclient import TestClient
from muvid.ui.app import create_app

client = TestClient(create_app(sys.argv[1]))
index = client.get("/")
assert index.status_code == 200, index.status_code
script = client.get("/api/script")
assert script.status_code == 200, script.status_code
# ensure_ascii keeps stdout readable under the ASCII codec too.
print(json.dumps(script.json()["content"]))
"""


@needs_ascii_locale
def test_ui_reads_utf8_under_ascii_locale(tmp_path):
    """The front page renders and the script editor loads under ``LC_ALL=C``."""
    pytest.importorskip("httpx", reason="fastapi's TestClient needs httpx")
    from muvid.project import MusicVideoProject

    root = tmp_path / "proj"
    MusicVideoProject.init(root, title="prueba")
    script_md = root / "script" / "script.md"
    script_md.parent.mkdir(parents=True, exist_ok=True)
    script_md.write_text(NON_ASCII_SCRIPT, encoding="utf-8")

    proc = run_under_ascii_locale(_SERVE, str(root))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == NON_ASCII_SCRIPT
