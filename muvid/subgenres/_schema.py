"""A small JSON-Schema validator for subgenre manifests — stdlib only, on purpose.

A manifest declares ``inputs`` and ``params_schema`` as JSON Schema so that one
artefact can serve a UI form, CLI validation and an MCP tool definition. Until
this module existed the schema was *declared* and *documented as validated* and
validated nowhere — a request with ``{'audoi': 'typo.wav'}`` rendered without
complaint, and the conformance kit passed its own contradictions.

This is deliberately not the full ``jsonschema`` package: manifests use a small,
stable subset (``type``, ``required``, ``properties``, ``additionalProperties``,
``enum``, ``minimum`` / ``maximum``, ``items``), and adding a dependency to the
import-safe path for that would be the wrong trade. Anything outside the subset
is ignored rather than refused, so a manifest may carry richer schema for a UI
without breaking runtime validation — but the docstring on :func:`validate`
says exactly which keywords are enforced, so nobody mistakes "passed" for
"fully valid".
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["validate", "SchemaError", "ENFORCED_KEYWORDS"]

#: The JSON-Schema keywords this validator actually enforces. Everything else
#: in a manifest schema is documentation as far as the runtime is concerned.
ENFORCED_KEYWORDS = (
    "type", "required", "properties", "additionalProperties",
    "enum", "minimum", "maximum", "items",
)

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


class SchemaError(ValueError):
    """The value does not satisfy the schema. ``errors`` lists every problem."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _type_ok(value: Any, typ: str | list[str]) -> bool:
    names = [typ] if isinstance(typ, str) else list(typ)
    for name in names:
        accepted = _TYPES.get(name)
        if accepted is None:
            continue
        if name in {"integer", "number"} and isinstance(value, bool):
            continue  # bool is an int in Python, not in JSON Schema
        if isinstance(value, accepted):
            return True
    return False


def _check(value: Any, schema: Mapping[str, Any], path: str, errors: list[str]) -> None:
    typ = schema.get("type")
    if typ is not None and not _type_ok(value, typ):
        errors.append(f"{path or '$'}: expected {typ}, got {type(value).__name__}")
        return  # further checks assume the type
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path or '$'}: {value!r} is not one of {list(schema['enum'])}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path or '$'}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path or '$'}: {value} > maximum {schema['maximum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {}) or {}
        for key in schema.get("required", ()) or ():
            if key not in value:
                errors.append(f"{path or '$'}: missing required {key!r}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(
                        f"{path or '$'}: unexpected {key!r} (allowed: {sorted(props)})"
                    )
        for key, sub in props.items():
            if key in value and isinstance(sub, Mapping):
                _check(value[key], sub, f"{path}.{key}" if path else key, errors)
    if isinstance(value, (list, tuple)) and isinstance(schema.get("items"), Mapping):
        for i, item in enumerate(value):
            _check(item, schema["items"], f"{path or '$'}[{i}]", errors)


def validate(value: Any, schema: Mapping[str, Any] | None, *, where: str = "") -> list[str]:
    """Return every problem found; an empty list means it passed.

    Enforces exactly :data:`ENFORCED_KEYWORDS`. A missing or empty schema
    passes everything — an undeclared schema is a manifest author's choice,
    not a runtime failure.

    >>> validate({'audio': 'a.wav'}, {'type': 'object', 'required': ['audio']})
    []
    >>> validate({'audoi': 'a.wav'},
    ...          {'type': 'object', 'required': ['audio'],
    ...           'properties': {'audio': {'type': 'string'}},
    ...           'additionalProperties': False})
    ["$: missing required 'audio'", "$: unexpected 'audoi' (allowed: ['audio'])"]
    >>> validate({'fps': 'thirty'}, {'type': 'object',
    ...           'properties': {'fps': {'type': 'integer', 'minimum': 1}}})
    ['fps: expected integer, got str']
    >>> validate(True, {'type': 'integer'})
    ['$: expected integer, got bool']
    """
    if not schema:
        return []
    errors: list[str] = []
    _check(value, schema, where, errors)
    return errors
