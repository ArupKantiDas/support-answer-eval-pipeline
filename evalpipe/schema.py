"""Schemas + a small dependency-free validator.

The same schema object is sent to the Claude API as a structured-output format
and used locally to validate whatever comes back (including mock output). Using
one definition for both means the contract can only drift in one place.

`jsonschema` is used when it happens to be installed; otherwise the built-in
validator below covers the subset of JSON Schema these documents use.
"""

from __future__ import annotations

VERDICTS = ["pass", "warning", "fail"]
RISK_LEVELS = ["low", "medium", "high"]
STATUSES = ["pass", "review", "fail"]

LLM_EVALUATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "case_id": {"type": "string"},
        "policy_adherence": {"type": "string", "enum": VERDICTS},
        "customer_helpfulness": {"type": "string", "enum": VERDICTS},
        "risk_level": {"type": "string", "enum": RISK_LEVELS},
        "reasoning": {"type": "array", "items": {"type": "string"}},
        "policy_violations": {"type": "array", "items": {"type": "string"}},
        "recommended_fix": {"type": "string"},
    },
    "required": [
        "case_id",
        "policy_adherence",
        "customer_helpfulness",
        "risk_level",
        "reasoning",
        "policy_violations",
        "recommended_fix",
    ],
    "additionalProperties": False,
}

CASE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "case_id": {"type": "string"},
        "user_message": {"type": "string"},
        "assistant_response": {"type": "string"},
        "policy_context": {
            "type": "object",
            "properties": {
                "allowed_actions": {"type": "array", "items": {"type": "string"}},
                "disallowed_actions": {"type": "array", "items": {"type": "string"}},
                "required_points": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["allowed_actions", "disallowed_actions", "required_points"],
            "additionalProperties": True,
        },
    },
    "required": ["case_id", "user_message", "assistant_response", "policy_context"],
    "additionalProperties": True,
}

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


class SchemaError(ValueError):
    """Raised when a document does not satisfy its schema."""


def _validate(node, schema: dict, path: str, errors: list[str]) -> None:
    expected = schema.get("type")
    if expected:
        py_type = _TYPES[expected]
        if expected == "number" and isinstance(node, bool):
            errors.append(f"{path}: expected number, got boolean")
            return
        if expected == "integer" and isinstance(node, bool):
            errors.append(f"{path}: expected integer, got boolean")
            return
        if not isinstance(node, py_type):
            errors.append(f"{path}: expected {expected}, got {type(node).__name__}")
            return

    if "enum" in schema and node not in schema["enum"]:
        errors.append(f"{path}: {node!r} not in {schema['enum']}")

    if expected == "object":
        for key in schema.get("required", []):
            if key not in node:
                errors.append(f"{path}: missing required property '{key}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in node:
                if key not in props:
                    errors.append(f"{path}: unexpected property '{key}'")
        for key, subschema in props.items():
            if key in node:
                _validate(node[key], subschema, f"{path}.{key}", errors)

    if expected == "array" and "items" in schema:
        for i, item in enumerate(node):
            _validate(item, schema["items"], f"{path}[{i}]", errors)


def validate(document, schema: dict, label: str = "document") -> None:
    """Raise `SchemaError` listing every problem found. No-op when valid."""
    try:  # prefer the real library when available
        import jsonschema  # type: ignore

        jsonschema.validate(instance=document, schema=schema)
        return
    except ImportError:
        pass
    except Exception as exc:  # jsonschema.ValidationError and friends
        raise SchemaError(f"{label}: {exc}") from exc

    errors: list[str] = []
    _validate(document, schema, label, errors)
    if errors:
        raise SchemaError("; ".join(errors))
