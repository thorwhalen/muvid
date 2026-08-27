"""Typed errors the render strategies raise.

Its own module, and deliberately importing nothing. ``animation.py`` does
``from muvid.renderers import RenderContext``, and the dispatcher imports each
strategy module *lazily, inside* :func:`muvid.renderers.render_shot` — which is
the only reason that cycle does not already exist. Declaring these errors in
``muvid/renderers/__init__.py`` would be fine; re-exporting them from there
would not, because a strategy module importing the package root back is exactly
the loop the lazy imports avoid. A leaf module both sides can import is the
shape with no edge cases.

The two errors below name a distinction that muvid#46 was filed because
``animation.py`` did not make: **an engine that never ran is not an engine that
ran and refused.** They are different facts about the render, they deserve
different responses, and collapsing them is how a shot authored as ``animation``
came out as a freeze frame that read as a deliberate creative choice.
"""

from __future__ import annotations


class RendererUnavailable(RuntimeError):
    """A strategy's engine is not installed, so the strategy never ran.

    Not a failure: a *capability* report. ``an`` is deliberately declared in no
    muvid extra and carries no version floor (the import in
    :mod:`muvid.renderers.animation` is soft by design), so a machine without it
    is an expected, supported machine — degrading to ``still`` there is the
    intended behaviour, not damage control.

    What muvid#46 changed is who decides. The strategy reports the fact; the
    **dispatcher** owns the fallback, because the dispatcher is where the
    provenance line is written and a fallback nobody journalled is a shot you
    cannot find afterwards.

    ``fallback`` names the strategy to degrade to, so the dispatcher needs no
    per-strategy knowledge to honour it.
    """

    def __init__(self, message: str, *, strategy: str, fallback: str = "still") -> None:
        super().__init__(message)
        self.strategy = strategy
        self.fallback = fallback


class AnimationRenderError(RuntimeError):
    """``an`` ran, and reported that it would not render this scene.

    Raised, never swallowed, for the reason muvid#38 removed the same shape from
    ``trim_video_to_duration``: a sibling package's failure that returns a
    plausible artifact instead of surfacing is a bug whose only symptom is a
    wrong output nobody re-measures. ``an`` states its refusal as *data*
    (``OrchestratorReport.success is False``) rather than an exception, so the
    swallow here was one ``if`` — and it discarded the exact findings that name
    the cause.

    The message is built by :func:`muvid.renderers.animation._format_an_failure`
    and always carries something actionable: ``an``'s own error string, every
    error-severity validation and verification finding, and the path to the
    synthesized ``scene.md`` that produced them.
    """
