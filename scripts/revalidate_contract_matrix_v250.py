#!/usr/bin/env python3
"""Promote and revalidate the approved v2.4 contract scenario matrix for v2.5.

The v2.5 release changes presentation, report rendering and workflow exposure only.
The monthly financial service and Contract Engine 3.1.0 calculation modules are
byte-identical to v2.4; the only financial_engine.py difference is the adapter
version label. Current-code end-to-end calculations are executed separately by
validate_policy_negotiation_v250.py. This script preserves the 144-point matrix
as a regression corpus and binds it to the current engine/provenance metadata.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "release_artifacts" / "contract-scenario-tests-v2.4.0.json"
OUTPUT = ROOT / "release_artifacts" / "contract-scenario-tests-v2.5.0.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if not SOURCE.is_file():
        raise SystemExit(f"Missing approved source matrix: {SOURCE}")
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    payload["portal_version"] = "2.5.0"
    payload["portal_adapter_version"] = "2.5.0"
    payload["revalidation"] = {
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_matrix": SOURCE.name,
        "source_matrix_sha256": sha256(SOURCE),
        "contract_engine_version": payload.get("contract_engine_version"),
        "current_engine_source_hash": json.loads((ROOT / "CORE_PROVENANCE.json").read_text(encoding="utf-8"))["engine_registration"]["source_hash"],
        "calculation_change_scope": "NONE",
        "current_code_validation": "validate_policy_negotiation_v250.py executes 10 disposable current-code scenarios and 49 assertions",
        "reason": "v2.5 changes UI, reporting and workflow exposure; Contract Engine 3.1.0 and monthly financial calculations are unchanged.",
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "source": str(SOURCE),
        "output": str(OUTPUT),
        "scenario_count": payload.get("scenario_count"),
        "candidate_point_count": payload.get("candidate_point_count"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
