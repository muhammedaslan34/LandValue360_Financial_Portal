"""Version manifest for the LandValue360 unified platform editions."""
from __future__ import annotations

from copy import deepcopy

from landvalue360_common.versions import (
    CONTRACT_REGISTRY_VERSION,
    DEVELOPER_VERSION,
    ENGINE_VERSION,
    FORMULA_REGISTRY_VERSION,
    LANDOWNER_VERSION,
    METRIC_DICTIONARY_VERSION,
    PLATFORM_VERSION,
    POLICY_MODEL_VERSION,
    RELEASE_CHANNEL,
    REPORT_REGISTRY_VERSION,
)

GOVERNMENT_VERSION = LANDOWNER_VERSION

PLATFORM_MANIFEST = {
    "platform": {"name": "LandValue360 Platform", "version": PLATFORM_VERSION, "release_channel": RELEASE_CHANNEL},
    "developer": {"name": "LandValue360 Developer", "version": DEVELOPER_VERSION},
    "government": {
        "name": "LandValue360 Landowner",
        "functional_name": "Landowner Partnership Advisory System",
        "use_status": "non-official-advisory",
        "version": GOVERNMENT_VERSION,
    },
    "engine": {
        "name": "LandValue360 Engine",
        "version": ENGINE_VERSION,
        "formula_registry_version": FORMULA_REGISTRY_VERSION,
        "single_source_of_truth": True,
        "arithmetic": "Decimal",
        "day_count_basis": "ACT/365F",
    },
    "registries": {
        "contracts": CONTRACT_REGISTRY_VERSION,
        "metrics": METRIC_DICTIONARY_VERSION,
        "policy": POLICY_MODEL_VERSION,
        "reports": REPORT_REGISTRY_VERSION,
    },
    "claims": [
        "stable-advisory-release",
        "standards-informed",
        "evidence-backed",
        "audit-ready",
        "independently-testable",
        "subject-to-professional-and-legal-review",
        "advisory-use-only",
        "not-an-official-decision",
        "not-a-formal-valuation",
    ],
}


def platform_manifest() -> dict:
    return deepcopy(PLATFORM_MANIFEST)
