"""The :class:`Subgenre` manifest — pure data, and deliberately import-free.

A subgenre is *one more kind of video muvid knows how to make*. The manifest is
what a catalogue, a UI form, an MCP tool definition and an LLM choosing between
twelve installed subgenres all read — and none of them should have to import a
renderer to do it. So the manifest names its renderer as a **dotted string**
(``"pkg.module:function"``) which is resolved only when a render actually runs.

That one indirection is the whole design. It is what Home Assistant gets from
``manifest.json`` and Airflow from ``get_provider_info()``: listing is cheap and
cannot be broken by a plugin whose heavy dependency is missing.

This module imports nothing but the standard library, on purpose — a plugin's
manifest module is expected to do the same, so importing it costs microseconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: Bumped when the manifest shape or the renderer contract changes
#: incompatibly. A plugin declares the versions it supports via
#: :attr:`Subgenre.api_versions`; the host refuses to register a manifest that
#: does not name the running version, which turns a silent breakage at render
#: time into a loud one at listing time.
API_VERSION = "1"

#: The entry-point group third-party distributions publish to. The version is in
#: the group name (rather than only in the manifest) so that a future
#: ``muvid.subgenres.v2`` can coexist with v1 plugins on one machine instead of
#: fighting them.
ENTRY_POINT_GROUP = "muvid.subgenres.v1"


@dataclass(frozen=True, slots=True, kw_only=True)
class Example:
    """One invocation worth showing to a human or an agent.

    Agents pick better from examples than from prose — the Remotion registry's
    finding, and the reason this is a first-class field rather than something
    buried in ``description``.
    """

    description: str
    params: Mapping[str, Any] = field(default_factory=dict)
    #: Optional path, relative to the plugin package, of a short sample render.
    preview: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Subgenre:
    """A declared kind of video, and how to render one.

    Everything here is JSON-able except :attr:`render`, which is a *reference*
    to a callable rather than the callable itself. Construct one at module
    scope in a stdlib-only module and point an entry point at it.

    :param slug: stable id; also a path segment and a public contract value, so
        choose it once. Lowercase, hyphen-separated.
    :param inputs: JSON Schema for the *files and primitives* the renderer
        needs. One schema serves the UI form, CLI validation and the MCP tool
        definition, which is why it is a schema and not prose.
    :param params_schema: JSON Schema for the styling/treatment knobs.
    :param render: ``"package.module:function"``. Imported lazily, once, at
        render time. The function must satisfy :class:`~muvid.subgenres.Renderer`.
    :param api_versions: the manifest API versions this plugin is written
        against. Must include the running :data:`API_VERSION`.
    """

    slug: str
    title: str
    description: str
    render: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    params_schema: Mapping[str, Any] = field(default_factory=dict)
    produces: str = "video/mp4"
    examples: Sequence[Example] = ()
    #: Free-form tags a host may use to route "what are you making?" answers.
    intake_kinds: Sequence[str] = ()
    #: ``None`` means genuinely free. Anything else names a cost estimator, and
    #: an unknown cost must force approval rather than encode as zero — see
    #: muvid's conjunctive budget gate.
    cost_profile: str | None = None
    #: Which distribution provided this, filled in by the loader for
    #: entry-point plugins. ``None`` for in-process registrations.
    provider: str | None = None
    api_versions: Sequence[str] = (API_VERSION,)

    def __post_init__(self) -> None:
        if not self.slug or not self.slug.strip():
            raise ValueError("Subgenre.slug must be a non-empty string")
        if ":" not in self.render:
            raise ValueError(
                f"Subgenre({self.slug!r}).render must be 'module:function', "
                f"got {self.render!r} — the point of the string is that the "
                "renderer is not imported until it is used."
            )
        if API_VERSION not in tuple(self.api_versions):
            raise ValueError(
                f"Subgenre({self.slug!r}) declares api_versions="
                f"{tuple(self.api_versions)!r}, which does not include the "
                f"running API_VERSION {API_VERSION!r}."
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-able form — what a catalogue, a UI or an agent actually reads."""
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "inputs": dict(self.inputs),
            "params_schema": dict(self.params_schema),
            "produces": self.produces,
            "examples": [
                {
                    "description": e.description,
                    "params": dict(e.params),
                    "preview": e.preview,
                }
                for e in self.examples
            ],
            "intake_kinds": list(self.intake_kinds),
            "cost_profile": self.cost_profile,
            "provider": self.provider,
            "api_version": API_VERSION,
        }
