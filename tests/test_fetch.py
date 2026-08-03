"""Tests for muvid.mcp._fetch — the SSRF-guarded fetch EXECUTION path.

The load-bearing guarantee (module docstring): redirects are not auto-followed and every
hop's target is re-validated, so a public URL can't 302 into an internal one; plus a hard
byte cap. These drive ``fetch_bytes`` end-to-end with a fake opener (no network) so a
regression that dropped ``_NoRedirect``, skipped per-hop re-validation, or broke the byte
cap fails here rather than silently re-opening an SSRF hole. Internal redirect targets are
literal IPs, validated by the REAL ``_host_is_public`` (no DNS).
"""

from __future__ import annotations

import email.message
import io
import urllib.error

import pytest

pytest.importorskip("nw")  # keep in step with the [mcp]-extra gating

from muvid.mcp import _fetch  # noqa: E402


class _FakeResp:
    """A minimal response: chunked ``read`` + context-manager, like urllib's."""

    def __init__(self, body: bytes):
        self._buf = io.BytesIO(body)

    def read(self, n: int) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _ScriptedOpener:
    """An opener whose ``open`` replays a scripted list of steps, one per call.

    A step is ``("redirect", location)`` → raises a 302 ``HTTPError`` carrying that
    ``Location`` (the loop must re-validate + re-request it), ``("redirect_noloc", None)``
    → a 302 with no ``Location``, or ``("body", bytes)`` → returns a ``_FakeResp``.
    """

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0

    def open(self, url, timeout=None):
        self.calls += 1
        kind, payload = self.steps.pop(0)
        if kind in ("redirect", "redirect_noloc"):
            hdrs = email.message.Message()
            if kind == "redirect":
                hdrs["Location"] = payload
            raise urllib.error.HTTPError(url, 302, "Found", hdrs, None)
        return _FakeResp(payload)


@pytest.fixture
def allow_public_test(monkeypatch):
    """Let the fake host ``public.test`` validate; internal IPs use the REAL check."""
    real = _fetch._host_is_public
    monkeypatch.setattr(
        _fetch, "_host_is_public", lambda h: True if h == "public.test" else real(h)
    )


def _use_opener(monkeypatch, opener):
    monkeypatch.setattr(_fetch, "_opener", lambda: opener)


def test_redirect_into_a_private_target_is_rejected(monkeypatch, allow_public_test):
    # 200-OK-looking public URL that 302s to link-local metadata must be rejected — the
    # single most important SSRF-bypass vector.
    _use_opener(
        monkeypatch, _ScriptedOpener([("redirect", "http://169.254.169.254/latest")])
    )
    with pytest.raises(_fetch.FetchError):
        _fetch.fetch_bytes("http://public.test/song.mp3")


def test_redirect_to_loopback_is_rejected(monkeypatch, allow_public_test):
    _use_opener(monkeypatch, _ScriptedOpener([("redirect", "http://127.0.0.1/x")]))
    with pytest.raises(_fetch.FetchError):
        _fetch.fetch_bytes("http://public.test/song.mp3")


def test_too_many_redirects_is_rejected(monkeypatch, allow_public_test):
    steps = [("redirect", "http://public.test/next")] * (_fetch.MAX_REDIRECTS + 2)
    _use_opener(monkeypatch, _ScriptedOpener(steps))
    with pytest.raises(_fetch.FetchError, match="too many redirects"):
        _fetch.fetch_bytes("http://public.test/a")


def test_redirect_without_location_is_rejected(monkeypatch, allow_public_test):
    _use_opener(monkeypatch, _ScriptedOpener([("redirect_noloc", None)]))
    with pytest.raises(_fetch.FetchError, match="Location"):
        _fetch.fetch_bytes("http://public.test/a")


def test_byte_cap_is_enforced(monkeypatch, allow_public_test):
    monkeypatch.setattr(_fetch, "MAX_BYTES", 1024)
    _use_opener(monkeypatch, _ScriptedOpener([("body", b"x" * 5000)]))
    with pytest.raises(_fetch.FetchError, match="limit"):
        _fetch.fetch_bytes("http://public.test/big")


def test_happy_path_returns_bounded_bytes(monkeypatch, allow_public_test):
    _use_opener(monkeypatch, _ScriptedOpener([("body", b"hello-bytes")]))
    assert _fetch.fetch_bytes("http://public.test/ok") == b"hello-bytes"


def test_a_public_redirect_hop_is_followed_then_bodied(monkeypatch, allow_public_test):
    # A public→public redirect is allowed (each hop re-validated) and the final body wins.
    opener = _ScriptedOpener(
        [("redirect", "http://public.test/final"), ("body", b"final-body")]
    )
    _use_opener(monkeypatch, opener)
    assert _fetch.fetch_bytes("http://public.test/start") == b"final-body"
    assert opener.calls == 2  # both hops were actually requested
