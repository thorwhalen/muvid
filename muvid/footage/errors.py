"""The one refusal type of the footage operations (:mod:`muvid.footage.service`).

An operation that will not do what it was asked — no song yet, an unknown clip, an
edit that does not validate, an alignment nobody vouches for — raises
:class:`FootageError` with a message that names the next action. Every surface turns it
into its own refusal: the MCP tools into a fastmcp ``ToolError``, a host's HTTP route
into a ``422``. The operations never import a transport's error type, which is what
lets one function serve every surface.

It is a :class:`ValueError` because a refusal *is* a bad value from the caller's side,
and so that code already catching ``ValueError`` around the footage layer keeps
working.
"""

from __future__ import annotations


class FootageError(ValueError):
    """An operation refused — the message says why and what to do next."""


__all__ = ["FootageError"]
