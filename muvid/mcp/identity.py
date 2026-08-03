"""Caller identity for the muvid MCP tools — resolved from the OAuth token, fail-closed.

A hosted connector is multi-user: every tool must place work under the *verified*
caller, never a shared/ambient identity. muvid's tools resolve the caller via
:func:`current_email`, which reads the fastmcp request's access token — so they work
under ANY host middleware, including the shared ``enlace_metering.MeteringMiddleware``
the unified reelee connector installs (thorwhalen/reelee#232), which keys its own
context var. There is **no fallback** beyond the token: an unauthenticated call is
failed closed. Mirrors ``braidio.mcp.metering.token_email``/``current_email``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

#: An explicit caller identity for contexts with no OAuth request token — local/stdio
#: use and tests. Set it with :func:`use_email`. The hosted connector never sets this
#: (the token is the SSOT there), so it can't mask a real caller.
_CURRENT_EMAIL: ContextVar[Optional[str]] = ContextVar(
    "muvid_current_email", default=None
)


@contextmanager
def use_email(email: str):
    """Bind the caller identity for the duration of the block (local/stdio/testing)."""
    token = _CURRENT_EMAIL.set(email)
    try:
        yield
    finally:
        _CURRENT_EMAIL.reset(token)


def token_email() -> Optional[str]:
    """The verified caller's email from the OAuth token (``email`` claim, else ``sub``).

    Lowercased, or ``None`` when there is no request/token context — deliberately no
    fallback, so a caller is failed closed rather than handed a shared identity.
    """
    try:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
    except Exception:  # noqa: BLE001 — no active request/token context
        return None
    if token is None:
        return None
    claims = getattr(token, "claims", None) or {}
    ident = claims.get("email") or claims.get("sub")
    return str(ident).lower() if ident else None


def current_email() -> str:
    """The caller's identity for the in-flight tool call (raises if unauthenticated).

    Resolves from the explicit :func:`use_email` override when present (local/stdio/
    tests), else the verified OAuth token — so tools work under any host middleware, and
    an unauthenticated call is failed closed.
    """
    email = _CURRENT_EMAIL.get() or token_email()
    if not email:
        from fastmcp.exceptions import ToolError

        raise ToolError("no caller identity (authentication required)")
    return email
