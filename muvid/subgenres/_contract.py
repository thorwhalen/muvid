"""What a subgenre renderer is handed, and what it must give back.

A :class:`typing.Protocol` rather than a base class: a plugin author writes a
plain function and never imports a muvid class to inherit from, and muvid can
type-check the shape without either side depending on the other at runtime.

The request carries **paths and primitives only**. That is the load-bearing
decision in this module. Passing a rich host object (Sphinx's ``app``, MkDocs'
``config``, Datasette's ``datasette``) is the well-documented way to end up
unable to change your own schema for years, because every plugin has reached
into it. A plugin that needs more than this asks muvid through a narrow
accessor function, and that request becomes a visible, versionable API change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderRequest:
    """Everything a subgenre renderer gets.

    :param subgenre: the slug being rendered, so one function can serve several.
    :param inputs: the caller's files and primitives, validated against the
        manifest's ``inputs`` schema before it gets here.
    :param params: the styling/treatment knobs, validated against
        ``params_schema``.
    :param workdir: a directory the renderer may write intermediates into. It
        exists, and it belongs to this render — nothing else writes there.
    :param output: where the finished artifact must be written.
    """

    subgenre: str
    inputs: Mapping[str, Any]
    params: Mapping[str, Any] = field(default_factory=dict)
    workdir: Path
    output: Path


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderResult:
    """What a renderer gives back.

    :param output: the finished artifact. Must be the request's ``output``.
    :param duration_s: length of the produced media, when it has one.
    :param artifacts: named side products worth keeping — a ``.ass`` subtitle
        file, a thumbnail, the resolved treatment spec. These are how a
        subgenre stays inspectable instead of producing an opaque file.
    :param meta: anything else worth recording; must be JSON-able.
    """

    output: Path
    duration_s: float | None = None
    artifacts: Mapping[str, Path] = field(default_factory=dict)
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "duration_s": self.duration_s,
            "artifacts": {k: str(v) for k, v in self.artifacts.items()},
            "meta": dict(self.meta),
        }


@runtime_checkable
class Renderer(Protocol):
    """``(RenderRequest) -> RenderResult``, and nothing else."""

    def __call__(self, request: RenderRequest) -> RenderResult: ...
