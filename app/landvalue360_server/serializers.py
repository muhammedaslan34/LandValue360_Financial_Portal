"""Stable API serialization helpers for ORM records."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def record_dict(record: object, fields: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        value = getattr(record, field)
        result[field] = iso(value) if isinstance(value, datetime) else value
    return result
