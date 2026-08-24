"""Narrow compatibility boundary for non-persisted analytical previews."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def preview_valuation_policy_snapshot() -> dict[str, Any]:
    """Return a complete, disclosed policy snapshot rather than a rate fallback."""

    from ..web_defaults import default_valuation_policy_snapshot

    snapshot = deepcopy(default_valuation_policy_snapshot())
    snapshot["preview_only_unversioned_selection"] = True
    return snapshot
