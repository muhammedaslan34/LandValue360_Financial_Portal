"""Canonical JSON, hashing, and scenario merge utilities."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from typing import Any


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def json_merge_patch(target: Any, patch: Any) -> Any:
    """Apply RFC 7396-style JSON Merge Patch semantics.

    A non-object patch replaces the target. Object members with a null value
    delete the corresponding key. This is used to derive scenarios without
    mutating the approved project-version snapshot.
    """

    if not isinstance(patch, dict):
        return deepcopy(patch)
    result: dict[str, Any] = deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = json_merge_patch(result.get(key), value)
        else:
            result[key] = deepcopy(value)
    return result


def sanitize_audit_value(value: Any) -> Any:
    """Remove credentials and token material before persisting audit detail."""

    sensitive = {"password", "password_hash", "token", "token_hash", "secret"}
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in sensitive else sanitize_audit_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_audit_value(item) for item in value]
    return value
