"""The refusal and cancellation types of the footage operations (:mod:`muvid.footage.service`).

An operation that will not do what it was asked — no song yet, an unknown clip, an
edit that does not validate, an alignment nobody vouches for — raises
:class:`FootageError` with a message that names the next action. Every surface turns it
into its own refusal: the MCP tools into a fastmcp ``ToolError``, a host's HTTP route
into a ``422``. The operations never import a transport's error type, which is what
lets one function serve every surface.

An operation a host asked to stop (its ``should_cancel`` said so) raises
:class:`FootageCancelled` — not a refusal and not a failure.

**How a host tells them apart.** nw defines the contract a host reads:
``nw.GenreOpRefused`` (a deliberate refusal; everything else out of an op is a bug) and
``nw.GenreOpCancelled``. These two derive from them *when nw is importable* — the only
case in which anything serves the ops generically — and otherwise from ``ValueError``
and ``Exception``, because muvid's core does not depend on nw (it is the ``mcp``
extra's). Either way ``FootageError`` is a ``ValueError``, so code already catching that
keeps working.
"""

from __future__ import annotations

try:  # nw is optional for muvid's core; the host contract lives there when it is here
    from nw import GenreOpCancelled as _Cancelled
    from nw import GenreOpRefused as _Refused
except ImportError:  # no nw (or one older than the mcp extra's floor): plain bases
    _Refused, _Cancelled = ValueError, Exception


class FootageError(_Refused):
    """An operation refused — the message says why and what to do next."""


class FootageCancelled(_Cancelled):
    """An operation stopped between steps because its host asked it to."""


__all__ = ["FootageError", "FootageCancelled"]
