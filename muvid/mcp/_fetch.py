"""SSRF-hardened fetch of a caller-supplied media URL (server-side, shared prod box).

The ``music-visualizer`` tools fetch a user-given audio (and optional cover) URL on the
server, so an unguarded fetch is a server-side request forgery hole: a URL could point
at localhost, cloud metadata (169.254.169.254), or a private/link-local address. This
module is the guard — a deliberately minimal, dependency-free mirror of braidio's
``braidio.mcp._docs`` (which muvid must not import — wrong dependency direction). It is
no weaker than that guard:

- **scheme** must be ``http``/``https`` on ports {80, 443};
- the **host must resolve to a public address** (:pyattr:`ipaddress.is_global`), which
  rejects loopback/private/link-local/reserved/carrier-grade-NAT, IPv4-mapped IPv6 too;
- **redirects are NOT auto-followed** — every hop's ``Location`` is re-validated, so a
  public URL can't 302 into an internal one;
- the whole fetch is bounded by a wall-clock deadline, a redirect cap, and a total-bytes
  cap (chunked read), so a slow-loris or redirect loop can't pin a worker.

Because audio is legitimately tens of MB, the byte cap is larger than a document cap and
env-tunable. A per-*duration* cap (a long song is cheap to download but expensive to
render) is enforced separately, on the render path, via ``muvid.visualize.media_duration``.

A shared, zero-dep safe-fetch util (folding braidio's and this one together) is a filed
follow-up; until then each connector keeps its own guard rather than sharing a weaker one.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

#: Total-bytes cap for a media fetch (env ``MUVID_FETCH_MAX_BYTES``; default 60 MB —
#: audio is legitimately large, matching braidio's audio-download cap intent).
MAX_BYTES = int(os.environ.get("MUVID_FETCH_MAX_BYTES", str(60 * 1024 * 1024)))
#: Redirect-hop cap (each hop is re-validated).
MAX_REDIRECTS = 5
#: Wall-clock deadline for the whole fetch, in seconds (env ``MUVID_FETCH_TIMEOUT_S``).
TOTAL_TIMEOUT_S = float(os.environ.get("MUVID_FETCH_TIMEOUT_S", "60"))
#: Chunk size for the bounded read.
_CHUNK = 64 * 1024
_ALLOWED_SCHEMES = ("http", "https")
_ALLOWED_PORTS = (80, 443)


class FetchError(ValueError):
    """A media fetch was rejected (unsafe target) or failed (network/size/timeout)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never auto-follow a redirect — the caller re-validates every hop by hand."""

    def redirect_request(self, *args, **kwargs):  # noqa: D401, ARG002
        return None


def _host_is_public(host: str) -> bool:
    """True iff EVERY address ``host`` resolves to is globally routable."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False
        # Canonicalize IPv4-mapped IPv6 (::ffff:127.0.0.1) before the is_global check.
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:  # rejects private/loopback/link-local/reserved/CGNAT
            return False
    return True


def _validate_target(uri: str) -> None:
    """Raise :class:`FetchError` unless ``uri`` is a safe http(s) public target."""
    parts = urlparse(uri)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise FetchError(f"unsupported URL scheme {parts.scheme!r} (http/https only)")
    host = parts.hostname
    if not host:
        raise FetchError(f"URL has no host: {uri!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in _ALLOWED_PORTS:
        raise FetchError(f"port {port} not allowed (80/443 only)")
    if not _host_is_public(host):
        raise FetchError(f"host {host!r} does not resolve to a public address")


def _read_bounded(resp, deadline: float) -> bytes:
    """Read the body in chunks, enforcing the byte cap and the wall-clock deadline."""
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_BYTES:
        if time.monotonic() > deadline:
            raise FetchError("fetch exceeded the time budget")
        chunk = resp.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        chunks.append(chunk)
    data = b"".join(chunks)
    if len(data) > MAX_BYTES:
        raise FetchError(f"resource exceeds the {MAX_BYTES}-byte limit")
    return data


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect)


def _open_following_redirects(uri: str, deadline: float):
    """Open ``uri``, re-validating scheme/host/port at EVERY hop; return the response.

    Redirects are not auto-followed (``_NoRedirect``); each ``Location`` is re-validated,
    so a public URL can't 302 into an internal one. The caller consumes + closes the body.
    """
    current = uri
    opener = _opener()
    for _ in range(MAX_REDIRECTS + 1):
        _validate_target(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError("fetch exceeded the time budget")
        try:
            return opener.open(current, timeout=remaining)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):  # surfaced by _NoRedirect
                loc = e.headers.get("Location")
                if not loc:
                    raise FetchError("redirect without a Location") from e
                current = urllib.parse.urljoin(current, loc)
                continue  # re-validate the redirect target on the next iteration
            raise FetchError(f"fetch failed: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise FetchError(f"fetch failed: {e}") from e
    raise FetchError(f"too many redirects (>{MAX_REDIRECTS})")


def fetch_bytes(uri: str) -> bytes:
    """Fetch ``uri``'s body as bytes (SSRF-guarded, byte/time-capped, in-memory).

    For SMALL resources (audio, images) — buffers the whole body in RAM. Use
    :func:`fetch_to_file_streaming` for large media (video) so a big file is written
    straight to disk rather than materialized (~2x) in memory.
    """
    deadline = time.monotonic() + TOTAL_TIMEOUT_S
    with _open_following_redirects(uri, deadline) as resp:
        return _read_bounded(resp, deadline)


def fetch_to_file(uri: str, dest: Path) -> Path:
    """Fetch a SMALL ``uri`` to ``dest`` (in-memory then written). Returns ``dest``."""
    data = fetch_bytes(uri)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def fetch_to_file_streaming(uri: str, dest: Path, *, max_bytes: int) -> Path:
    """Stream a (large) ``uri`` straight to ``dest``, chunk by chunk (SSRF/size/time-bound).

    For video footage: the body is written to disk as it arrives and the running total is
    checked against ``max_bytes`` — never buffered whole in RAM. A partial file is removed
    on any failure. Returns ``dest``.
    """
    deadline = time.monotonic() + TOTAL_TIMEOUT_S
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with _open_following_redirects(uri, deadline) as resp, open(dest, "wb") as f:
            while True:
                if time.monotonic() > deadline:
                    raise FetchError("fetch exceeded the time budget")
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError(f"resource exceeds the {max_bytes}-byte limit")
                f.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest
