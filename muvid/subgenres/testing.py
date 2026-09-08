"""A conformance kit for subgenre plugin authors.

Ship a contract and people will implement it slightly wrong; ship a contract
*and a test that proves conformance* and they won't. pytest does this with
``pytester``, Datasette with ``datasette.utils.testing`` — this is the small
version.

In your plugin's test suite::

    from muvid.subgenres.testing import check_subgenre_conformance
    from my_package.subgenre import MY_THING

    def test_conformance(tmp_path):
        report = check_subgenre_conformance(MY_THING, workdir=tmp_path)
        assert report.ok, report.summary()

Pass ``render=False`` to check only the manifest — useful in a fast unit suite
where you don't want to pay for a real render.

The module also provides :func:`echo_renderer`, a real (if trivial) renderer
used by muvid's own tests and by this module's doctests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from muvid.subgenres._contract import RenderRequest, RenderResult
from muvid.subgenres._manifest import API_VERSION, Subgenre


def echo_renderer(request: RenderRequest) -> RenderResult:
    """A renderer that writes its request as JSON. Not a stub — it is a real,
    total implementation of the contract, which is what makes it usable as the
    fixture every conformance check runs against.
    """
    request.output.parent.mkdir(parents=True, exist_ok=True)
    request.output.write_text(
        json.dumps(
            {
                "subgenre": request.subgenre,
                "inputs": dict(request.inputs),
                "params": dict(request.params),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return RenderResult(output=request.output, meta={"renderer": "echo"})


@dataclass
class ConformanceReport:
    """What :func:`check_subgenre_conformance` found."""

    slug: str
    passed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        lines = [
            f"subgenre {self.slug!r}: "
            f"{len(self.passed)} passed, {len(self.failures)} failed, "
            f"{len(self.skipped)} skipped"
        ]
        lines += [f"  FAIL  {f}" for f in self.failures]
        lines += [f"  skip  {s}" for s in self.skipped]
        return "\n".join(lines)


def _check_json_schema(name: str, schema: Any, report: ConformanceReport) -> None:
    if not schema:
        report.skipped.append(f"{name}: no schema declared")
        return
    if not isinstance(schema, dict) or schema.get("type") != "object":
        report.failures.append(
            f"{name} must be a JSON Schema object (got type="
            f"{schema.get('type') if isinstance(schema, dict) else type(schema).__name__})"
        )
        return
    try:
        json.dumps(schema)
    except TypeError as exc:
        report.failures.append(f"{name} is not JSON-serialisable: {exc}")
        return
    report.passed.append(f"{name} is a JSON-able object schema")


def check_subgenre_conformance(
    subgenre: Subgenre,
    *,
    workdir: Path | str,
    render: bool = True,
    inputs: dict | None = None,
    params: dict | None = None,
) -> ConformanceReport:
    """Check a manifest against the contract, and optionally run one render.

    >>> import tempfile
    >>> sg = Subgenre(slug='conformance-demo', title='Demo',
    ...               description='A demo.',
    ...               render='muvid.subgenres.testing:echo_renderer')
    >>> with tempfile.TemporaryDirectory() as d:
    ...     rep = check_subgenre_conformance(sg, workdir=d)
    >>> rep.ok
    True
    """
    workdir = Path(workdir)
    report = ConformanceReport(slug=subgenre.slug)

    # --- manifest ---------------------------------------------------------
    if subgenre.slug != subgenre.slug.strip().lower():
        report.failures.append("slug must be lowercase and unpadded")
    elif " " in subgenre.slug:
        report.failures.append("slug must not contain spaces")
    else:
        report.passed.append("slug is well-formed")

    if not subgenre.description.strip():
        report.failures.append("description is empty — it is what an agent chooses on")
    else:
        report.passed.append("description is non-empty")

    if API_VERSION not in tuple(subgenre.api_versions):
        report.failures.append(
            f"api_versions {tuple(subgenre.api_versions)} omits running "
            f"API_VERSION {API_VERSION!r}"
        )
    else:
        report.passed.append(f"declares API_VERSION {API_VERSION}")

    _check_json_schema("inputs", subgenre.inputs, report)
    _check_json_schema("params_schema", subgenre.params_schema, report)

    try:
        json.dumps(subgenre.to_dict())
        report.passed.append("to_dict() is JSON-serialisable")
    except TypeError as exc:
        report.failures.append(f"to_dict() is not JSON-serialisable: {exc}")

    if not subgenre.examples:
        report.skipped.append(
            "no examples — agents pick better from examples than from prose"
        )
    else:
        report.passed.append(f"{len(subgenre.examples)} example(s)")

    # --- the renderer resolves, WITHOUT being imported during listing -----
    module_name, _, func_name = subgenre.render.partition(":")
    if not module_name or not func_name:
        report.failures.append(f"render={subgenre.render!r} is not 'module:function'")
        return report

    try:
        from importlib import import_module

        fn = getattr(import_module(module_name), func_name)
    except Exception as exc:
        report.failures.append(
            f"render target does not resolve: {type(exc).__name__}: {exc}"
        )
        return report
    if not callable(fn):
        report.failures.append(f"render target {subgenre.render!r} is not callable")
        return report
    report.passed.append("render target resolves to a callable")

    # --- one real render --------------------------------------------------
    if not render:
        report.skipped.append("render not attempted (render=False)")
        return report

    wd = workdir / "conformance"
    wd.mkdir(parents=True, exist_ok=True)
    out = wd / "out.bin"
    try:
        result = fn(
            RenderRequest(
                subgenre=subgenre.slug,
                inputs=inputs or {},
                params=params or {},
                workdir=wd,
                output=out,
            )
        )
    except Exception as exc:
        report.failures.append(f"render raised {type(exc).__name__}: {exc}")
        return report

    if not isinstance(result, RenderResult):
        report.failures.append(
            f"render returned {type(result).__name__}, expected RenderResult"
        )
        return report
    report.passed.append("render returned a RenderResult")

    if result.output != out:
        report.failures.append(
            f"render wrote to {result.output}, but was asked for {out}"
        )
    elif not out.exists():
        report.failures.append("render reported success but produced no file")
    else:
        report.passed.append("render produced the requested output path")

    try:
        json.dumps(result.to_dict())
        report.passed.append("RenderResult.to_dict() is JSON-serialisable")
    except TypeError as exc:
        report.failures.append(
            f"RenderResult.to_dict() is not JSON-serialisable: {exc}"
        )

    return report
