"""Regression test for muvid#85 — POST endpoints 422ing on their JSON body.

``muvid/ui/app.py`` has ``from __future__ import annotations`` at module
level. Under PEP 563 a parameter annotation is a *string*, resolved by
FastAPI against the handler function's module globals. Every request model
used to be declared *inside* ``create_app``, so the name did not exist at
module scope and FastAPI silently fell back to treating the parameter as a
query scalar — every POST endpoint returned 422 and its handler body never
ran. The fix moves the request models to module level; this test drives
every affected POST endpoint with its documented JSON body and asserts the
handler actually executes (not a 422).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="the UI is behind the `ui` extra")
pytest.importorskip("httpx", reason="fastapi's TestClient needs httpx")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from muvid.project import MusicVideoProject
    from muvid import facade
    from muvid.ui.app import create_app

    root = tmp_path / "proj"
    MusicVideoProject.init(root, title="test project")

    monkeypatch.setattr(facade, "transcribe_song", lambda root, **kw: "lyrics.md")
    monkeypatch.setattr(facade, "align_lyrics", lambda root, **kw: {"aligned": True})
    monkeypatch.setattr(
        facade, "add_character", lambda root, name, **kw: {"name": name}
    )
    monkeypatch.setattr(
        facade, "generate_character_images", lambda root, name, **kw: ["a.png"]
    )
    monkeypatch.setattr(
        facade, "curate_character", lambda root, name, **kw: ["a.png"]
    )
    monkeypatch.setattr(
        facade, "add_environment", lambda root, name, **kw: {"name": name}
    )
    monkeypatch.setattr(facade, "parse_script", lambda root: None)
    monkeypatch.setattr(facade, "write_script", lambda root: "script.md")
    monkeypatch.setattr(
        facade, "render_shot", lambda root, shot_id, **kw: f"{shot_id}.mp4"
    )
    monkeypatch.setattr(facade, "render", lambda root, **kw: ["out.mp4"])
    monkeypatch.setattr(facade, "compose", lambda root, **kw: "final.mp4")

    return TestClient(create_app(root))


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/transcribe", {}),
        ("/api/align", {}),
        ("/api/character", {"name": "Alice"}),
        ("/api/character/generate", {"name": "Alice"}),
        ("/api/character/curate", {"name": "Alice"}),
        ("/api/environment", {"name": "cafe"}),
        ("/api/script", {"content": "# hi"}),
        ("/api/render", {}),
        ("/api/compose", {}),
    ],
)
def test_post_endpoint_accepts_its_json_body(client, path, body):
    """Every documented POST body reaches the handler, not a 422 on `req`."""
    resp = client.post(path, json=body)
    assert resp.status_code != 422, resp.json()
    assert resp.status_code == 200, resp.json()
