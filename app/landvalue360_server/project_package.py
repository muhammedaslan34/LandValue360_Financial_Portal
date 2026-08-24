"""Portable single-project packages for migration between LandValue360 releases.

A ``.lv360`` file is a deterministic ZIP container with project metadata,
immutable input versions, scenarios and reference results. Imported versions
are always created as drafts and must be recalculated under a target policy.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import stat
from typing import Any
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import __version__ as APPLICATION_VERSION
from .context import AuthContext
from .enums import ProjectKind
from .errors import ConflictError
from .json_tools import canonical_json, sha256_json
from .models import CalculationRun, Project, ProjectVersion, Scenario
from .services.projects import create_project, create_project_version, create_scenario
from .services.tenant import get_project, tenant_clause

PROJECT_PACKAGE_FORMAT = "LANDVALUE360_PROJECT_PACKAGE"
PROJECT_PACKAGE_VERSION = "2.1.1"
SUPPORTED_PROJECT_PACKAGE_VERSIONS = {"0.25.0", "0.26.0", "0.26.1", "1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.3.2", "2.0.0", "2.1.1"}
ALLOWED_ROOT_FILES = {"manifest.json", "project.json", "versions.json", "scenarios.json"}

LEGACY_FIELD_RENAMES = {
    "recovery_method": "advance_recovery_method",
    "recovery_priority": "advance_recovery_priority",
    "valuation_policy_version_id": "valuation_policy_pack_version_id",
}


def migrate_legacy_fields(value: Any, *, path: str = "$", changes: list[dict[str, Any]] | None = None) -> tuple[Any, list[dict[str, Any]]]:
    """Rename short-lived legacy fields without silently changing values.

    The returned impact rows are persisted in the import response. Existing
    canonical values win when both names are present and that conflict is made
    explicit for the operator.
    """

    impact = changes if changes is not None else []
    if isinstance(value, list):
        return [migrate_legacy_fields(item, path=f"{path}[{index}]", changes=impact)[0] for index, item in enumerate(value)], impact
    if not isinstance(value, dict):
        return deepcopy(value), impact
    migrated: dict[str, Any] = {}
    for key, item in value.items():
        target = LEGACY_FIELD_RENAMES.get(str(key), str(key))
        if target != key and target in value:
            impact.append({
                "path": path,
                "old_field": str(key),
                "new_field": target,
                "old_value": deepcopy(item),
                "new_value": deepcopy(value[target]),
                "reason": "Both legacy and canonical fields were supplied; the canonical value was retained.",
                "result_impact": "Potential input conflict; recalculation and operator review required.",
            })
            continue
        migrated_item, _ = migrate_legacy_fields(item, path=f"{path}.{target}", changes=impact)
        if target in migrated and target != key:
            impact.append({
                "path": path,
                "old_field": str(key),
                "new_field": target,
                "old_value": deepcopy(item),
                "new_value": deepcopy(migrated[target]),
                "reason": "Both legacy and canonical fields were supplied; the canonical value was retained.",
                "result_impact": "Potential input conflict; recalculation and operator review required.",
            })
            continue
        migrated[target] = migrated_item
        if target != key:
            impact.append({
                "path": path,
                "old_field": str(key),
                "new_field": target,
                "old_value": deepcopy(item),
                "new_value": deepcopy(migrated_item),
                "reason": "Field renamed to the v2.1.1 canonical contract.",
                "result_impact": "Alias-only migration; value preserved. Recalculation remains mandatory under the selected v2.1.1 policies.",
            })
    return migrated, impact



def _json_depth(value: Any, *, limit: int, _depth: int = 0) -> int:
    if _depth > limit:
        raise ConflictError("PROJECT_PACKAGE_JSON_TOO_DEEP", f"Project package JSON exceeds the maximum nesting depth of {limit}.")
    if isinstance(value, dict):
        return max([_depth] + [_json_depth(item, limit=limit, _depth=_depth + 1) for item in value.values()])
    if isinstance(value, list):
        return max([_depth] + [_json_depth(item, limit=limit, _depth=_depth + 1) for item in value])
    return _depth


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        return False
    if any(part in {"", ".", ".."} for part in path.parts):
        return False
    if len(path.parts) == 1:
        return name in ALLOWED_ROOT_FILES
    return len(path.parts) == 2 and path.parts[0] == "reference-results" and path.suffix == ".json"


def _is_symlink(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def _validate_archive_safety(
    archive: ZipFile,
    *,
    compressed_payload_bytes: int,
    max_payload_bytes: int,
    max_uncompressed_bytes: int,
    max_entries: int,
    max_compression_ratio: int,
) -> None:
    if compressed_payload_bytes > max_payload_bytes:
        raise ConflictError("PROJECT_PACKAGE_TOO_LARGE", f"Project package exceeds the maximum upload size of {max_payload_bytes} bytes.")
    infos = archive.infolist()
    if not infos or len(infos) > max_entries:
        raise ConflictError("PROJECT_PACKAGE_ENTRY_LIMIT", f"Project package must contain between 1 and {max_entries} entries.")
    seen: set[str] = set()
    total_uncompressed = 0
    total_compressed = 0
    for info in infos:
        if info.filename in seen:
            raise ConflictError("PROJECT_PACKAGE_DUPLICATE_ENTRY", f"Duplicate project-package entry: {info.filename}")
        seen.add(info.filename)
        if info.is_dir() or _is_symlink(info) or not _safe_archive_name(info.filename):
            raise ConflictError("PROJECT_PACKAGE_UNSAFE_ENTRY", f"Unsafe or unsupported project-package entry: {info.filename}")
        if info.flag_bits & 0x1:
            raise ConflictError("PROJECT_PACKAGE_ENCRYPTED", "Encrypted project packages are not supported.")
        total_uncompressed += int(info.file_size)
        total_compressed += max(1, int(info.compress_size))
        if info.file_size > max_uncompressed_bytes:
            raise ConflictError("PROJECT_PACKAGE_ENTRY_TOO_LARGE", f"Project-package entry is too large: {info.filename}")
        if info.file_size / max(1, info.compress_size) > max_compression_ratio:
            raise ConflictError("PROJECT_PACKAGE_COMPRESSION_RATIO", f"Suspicious compression ratio detected in {info.filename}.")
    if total_uncompressed > max_uncompressed_bytes:
        raise ConflictError("PROJECT_PACKAGE_UNCOMPRESSED_TOO_LARGE", f"Project package expands beyond {max_uncompressed_bytes} bytes.")
    if total_uncompressed / max(1, total_compressed) > max_compression_ratio:
        raise ConflictError("PROJECT_PACKAGE_COMPRESSION_RATIO", "Project package compression ratio exceeds the permitted limit.")
    missing = ALLOWED_ROOT_FILES - seen
    if missing:
        raise ConflictError("PROJECT_PACKAGE_MISSING_FILE", f"Project package is missing required files: {', '.join(sorted(missing))}.")

def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value is not None else None)


def _json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def _read_json(archive: ZipFile, name: str) -> Any:
    try:
        return json.loads(archive.read(name).decode("utf-8"))
    except KeyError as exc:
        raise ConflictError("PROJECT_PACKAGE_MISSING_FILE", f"Project package is missing {name}.") from exc
    except Exception as exc:
        raise ConflictError("PROJECT_PACKAGE_INVALID_JSON", f"Invalid JSON in {name}.") from exc


def export_project_package(
    session: Session,
    *,
    context: AuthContext,
    project_id: str,
    include_reference_results: bool = True,
) -> bytes:
    project = get_project(session, context, project_id)
    versions = list(session.scalars(
        select(ProjectVersion)
        .where(ProjectVersion.project_id == project.id, *tenant_clause(ProjectVersion, context))
        .order_by(ProjectVersion.version_number)
    ).all())
    version_ids = [item.id for item in versions]
    scenarios = list(session.scalars(
        select(Scenario)
        .where(Scenario.project_version_id.in_(version_ids) if version_ids else False)
        .order_by(Scenario.project_version_id, Scenario.code)
    ).all()) if version_ids else []

    latest_runs: dict[str, CalculationRun] = {}
    if include_reference_results and version_ids:
        runs = list(session.scalars(
            select(CalculationRun)
            .where(CalculationRun.project_version_id.in_(version_ids), *tenant_clause(CalculationRun, context))
            .order_by(CalculationRun.created_at.desc())
        ).all())
        for run in runs:
            latest_runs.setdefault(run.project_version_id, run)

    project_payload = {
        "name": project.name,
        "code": project.code,
        "description": project.description,
        "project_kind": project.project_kind,
        "status": project.status,
        "source_project_id": project.id,
        "created_at": _iso(project.created_at),
        "updated_at": _iso(project.updated_at),
    }
    version_payloads = []
    for item in versions:
        version_payloads.append({
            "source_version_id": item.id,
            "version_number": item.version_number,
            "source_status": item.status,
            "label": item.label,
            "notes": item.notes,
            "input_snapshot": deepcopy(item.input_snapshot),
            "input_hash": item.input_hash,
            "source_input_schema": item.source_input_schema,
            "source_input_snapshot": deepcopy(item.source_input_snapshot),
            "source_input_hash": item.source_input_hash,
            "supersedes_source_version_id": item.supersedes_version_id,
            "approved_at": _iso(item.approved_at),
            "created_at": _iso(item.created_at),
            "updated_at": _iso(item.updated_at),
        })
    scenario_payloads = [{
        "source_scenario_id": item.id,
        "source_version_id": item.project_version_id,
        "name": item.name,
        "code": item.code,
        "description": item.description,
        "source_status": item.status,
        "override_snapshot": deepcopy(item.override_snapshot),
        "override_hash": item.override_hash,
    } for item in scenarios]

    files: dict[str, bytes] = {
        "project.json": _json_bytes(project_payload),
        "versions.json": _json_bytes(version_payloads),
        "scenarios.json": _json_bytes(scenario_payloads),
    }
    reference_index = []
    for version_id, run in latest_runs.items():
        filename = f"reference-results/{version_id}.json"
        payload = {
            "source_run_id": run.id,
            "source_version_id": version_id,
            "status": run.status,
            "mode": run.mode,
            "case_id": run.case_id,
            "application_version": run.application_version,
            "calculation_model_version": run.calculation_model_version,
            "input_hash": run.input_hash,
            "output_hash": run.output_hash,
            "created_at": _iso(run.created_at),
            "completed_at": _iso(run.completed_at),
            "output_snapshot": deepcopy(run.output_snapshot),
        }
        files[filename] = _json_bytes(payload)
        reference_index.append({"source_version_id": version_id, "file": filename, "output_hash": run.output_hash})

    manifest = {
        "format": PROJECT_PACKAGE_FORMAT,
        "format_version": PROJECT_PACKAGE_VERSION,
        "source_platform_version": APPLICATION_VERSION,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "project": {"name": project.name, "code": project.code, "project_kind": project.project_kind},
        "counts": {"versions": len(version_payloads), "scenarios": len(scenario_payloads), "reference_results": len(reference_index)},
        "reference_results": reference_index,
        "files": {name: {"sha256": sha256_json(json.loads(data.decode("utf-8"))), "bytes": len(data)} for name, data in files.items()},
        "import_rule": "Input versions and scenarios are imported as new draft records. Reference results are retained for audit only and must be recalculated under a target policy.",
    }
    files["manifest.json"] = _json_bytes(manifest)

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    return buffer.getvalue()


def _unique_project_code(session: Session, context: AuthContext, preferred: str) -> str:
    base = "-".join(str(preferred or "IMPORTED").strip().upper().replace("_", "-").split())[:60] or "IMPORTED"
    candidate = base
    counter = 2
    while session.scalar(select(Project.id).where(*tenant_clause(Project, context), Project.code == candidate)):
        suffix = f"-IMP{counter}"
        candidate = f"{base[:80-len(suffix)]}{suffix}"
        counter += 1
    return candidate


def _is_client_portal_package(manifest: dict[str, Any]) -> bool:
    source = str(manifest.get("source_platform_version") or "").strip().lower()
    return source.startswith("client-portal-")


def _portal_project_kind(project_payload: dict[str, Any], manifest: dict[str, Any], impact: list[dict[str, Any]]) -> str:
    source_kind = str(project_payload.get("project_kind") or "").strip().upper()
    allowed = {item.value for item in ProjectKind}
    if _is_client_portal_package(manifest):
        # Portal 1.0.1 exports SHARED natively. Older portal builds exported
        # LANDOWNER, which is not an internal ProjectKind and would disappear
        # from the Developer workspace if stored unchanged.
        if source_kind not in allowed or source_kind == "LANDOWNER":
            impact.append({
                "path": "$.project.project_kind",
                "old_field": "project_kind",
                "new_field": "project_kind",
                "old_value": source_kind or None,
                "new_value": ProjectKind.SHARED.value,
                "reason": "Client Portal submissions enter the internal shared Developer workflow.",
                "result_impact": "Semantic compatibility mapping only. The imported project remains a draft and must be reviewed and recalculated.",
            })
            return ProjectKind.SHARED.value
        return source_kind
    if source_kind not in allowed:
        raise ConflictError("PROJECT_PACKAGE_KIND_UNSUPPORTED", f"Unsupported project kind: {source_kind or 'EMPTY'}")
    return source_kind


def _adapt_client_portal_snapshot(
    value: dict[str, Any],
    *,
    source_input_schema: str | None,
    path: str,
    impact: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Adapt the Client Portal 1.0 submission shape to the v2.1.1 Developer contract.

    The portal intentionally stores area-allocation fields on each commercial
    product row.  The internal Developer application separates those fields
    into ``planning_products`` and ``products``.  Without this adapter the
    browser fills ``planning_products`` from its default template and silently
    replaces the imported commercial rows because the product IDs do not
    match.  The source snapshot remains untouched in ``source_input_snapshot``
    for audit/provenance.
    """

    snapshot = deepcopy(value or {})
    portal_schema = str(source_input_schema or "").strip().lower().startswith("portal-submission-")
    portal_marker = isinstance(snapshot.get("portal_submission"), dict)
    is_portal = _is_client_portal_package(manifest) or portal_schema or portal_marker
    if not is_portal:
        return snapshot

    audit = snapshot.setdefault("integration_audit", {})
    audit["client_portal"] = {
        "source_platform_version": manifest.get("source_platform_version"),
        "package_format_version": manifest.get("format_version"),
        "imported_as_draft": True,
        "requires_analyst_completion": True,
        "results_reused": False,
    }

    products = snapshot.get("products")
    if not isinstance(products, list) or not products:
        return snapshot
    if isinstance(snapshot.get("planning_products"), list) and snapshot.get("planning_products"):
        # Portal 1.0.1 already emits the native split contract. Keep every
        # financial input unchanged and add provenance only.
        return snapshot

    planning_products: list[dict[str, Any]] = []
    commercial_products: list[dict[str, Any]] = []
    planning_fields = {
        "area_method", "gfa_allocation_share", "efficiency", "is_sellable",
        "unit_count", "average_net_unit_area_sqm", "direct_gfa_sqm",
        "direct_sellable_area_sqm",
    }
    for index, raw in enumerate(products):
        if not isinstance(raw, dict):
            raise ConflictError("PROJECT_PACKAGE_SCHEMA_INVALID", f"{path}.products[{index}] must be an object.")
        product = deepcopy(raw)
        product_id = str(product.get("product_id") or f"PORTAL-PRODUCT-{index + 1}").strip()
        name = str(product.get("name") or product_id).strip()
        planning_row: dict[str, Any] = {
            "product_id": product_id,
            "name": name,
            "area_method": str(product.get("area_method") or "GFA_ALLOCATION").upper(),
            "is_sellable": bool(product.get("is_sellable", True)),
            "efficiency": str(product.get("efficiency") if product.get("efficiency") not in (None, "") else "0"),
            "gfa_allocation_share": str(product.get("gfa_allocation_share") if product.get("gfa_allocation_share") not in (None, "") else "0"),
        }
        for field in ("unit_count", "average_net_unit_area_sqm", "direct_gfa_sqm", "direct_sellable_area_sqm"):
            if product.get(field) not in (None, ""):
                planning_row[field] = deepcopy(product[field])
        planning_products.append(planning_row)

        for field in planning_fields:
            product.pop(field, None)
        product["product_id"] = product_id
        product["name"] = name
        product.setdefault("buyer_incentive_net_sales_deduction_fraction", "1")
        product.setdefault("refund_net_sales_deduction_fraction", "1")
        product.setdefault("eligible_profit_share_revenue_fraction", "1")
        product.setdefault("construction_developer_responsibility_share", "1")
        product.setdefault("construction_government_responsibility_share", "0")
        product.setdefault("construction_developer_economic_share", "1")
        product.setdefault("construction_government_economic_share", "0")
        commercial_products.append(product)

    snapshot["planning_products"] = planning_products
    snapshot["products"] = commercial_products
    impact.append({
        "path": path,
        "old_field": "products[*].area_method/gfa_allocation_share/efficiency/is_sellable",
        "new_field": "planning_products",
        "old_value": "Client Portal combined product rows",
        "new_value": f"{len(planning_products)} planning rows linked to {len(commercial_products)} commercial rows",
        "reason": "LandValue360 Developer 2.1.1 separates planning allocation from commercial product assumptions.",
        "result_impact": "Portal values and product IDs are preserved; no financial result is reused and analyst review/recalculation remains mandatory.",
    })
    return snapshot


def import_project_package(
    session: Session,
    *,
    context: AuthContext,
    payload: bytes,
    name_override: str | None = None,
    code_override: str | None = None,
    max_payload_bytes: int = 25 * 1024 * 1024,
    max_uncompressed_bytes: int = 100 * 1024 * 1024,
    max_entries: int = 250,
    max_compression_ratio: int = 100,
    max_json_depth: int = 80,
) -> dict[str, Any]:
    try:
        archive = ZipFile(BytesIO(payload), "r")
    except (BadZipFile, ValueError) as exc:
        raise ConflictError("PROJECT_PACKAGE_INVALID", "The uploaded file is not a valid LandValue360 project package.") from exc
    with archive:
        _validate_archive_safety(
            archive,
            compressed_payload_bytes=len(payload),
            max_payload_bytes=max_payload_bytes,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_entries=max_entries,
            max_compression_ratio=max_compression_ratio,
        )
        manifest = _read_json(archive, "manifest.json")
        if manifest.get("format") != PROJECT_PACKAGE_FORMAT:
            raise ConflictError("PROJECT_PACKAGE_FORMAT_UNSUPPORTED", "The uploaded file is not a LandValue360 project package.")
        if str(manifest.get("format_version")) not in SUPPORTED_PROJECT_PACKAGE_VERSIONS:
            raise ConflictError("PROJECT_PACKAGE_VERSION_UNSUPPORTED", f"Unsupported project package version: {manifest.get('format_version')}")
        project_payload = _read_json(archive, "project.json")
        versions = _read_json(archive, "versions.json")
        scenarios = _read_json(archive, "scenarios.json")
        if not isinstance(project_payload, dict) or not isinstance(versions, list) or not isinstance(scenarios, list):
            raise ConflictError("PROJECT_PACKAGE_SCHEMA_INVALID", "Project package JSON has an invalid top-level structure.")
        for value in (manifest, project_payload, versions, scenarios):
            _json_depth(value, limit=max_json_depth)
        if len(versions) > max_entries or len(scenarios) > max_entries * 10:
            raise ConflictError("PROJECT_PACKAGE_RECORD_LIMIT", "Project package contains too many versions or scenarios.")
        manifest_files = manifest.get("files") or {}
        if not isinstance(manifest_files, dict):
            raise ConflictError("PROJECT_PACKAGE_MANIFEST_INVALID", "Project package manifest files index is invalid.")
        archive_names = {item.filename for item in archive.infolist()}
        expected_names = set(manifest_files) | {"manifest.json"}
        if archive_names != expected_names:
            unexpected = sorted(archive_names - expected_names)
            missing = sorted(expected_names - archive_names)
            raise ConflictError("PROJECT_PACKAGE_MANIFEST_MISMATCH", f"Archive/manifest mismatch. Unexpected={unexpected}; missing={missing}.")
        for name, metadata in manifest_files.items():
            if not isinstance(metadata, dict):
                raise ConflictError("PROJECT_PACKAGE_MANIFEST_INVALID", f"Invalid manifest metadata for {name}.")
            raw = archive.read(name)
            if len(raw) != int(metadata.get("bytes") or -1):
                raise ConflictError("PROJECT_PACKAGE_SIZE_MISMATCH", f"Project package size check failed for {name}.")
            try:
                value = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                raise ConflictError("PROJECT_PACKAGE_INVALID_JSON", f"Invalid JSON in {name}.") from exc
            _json_depth(value, limit=max_json_depth)
            if sha256_json(value) != metadata.get("sha256"):
                raise ConflictError("PROJECT_PACKAGE_HASH_MISMATCH", f"Project package integrity check failed for {name}.")

    migration_impact: list[dict[str, Any]] = []
    migrated_versions: list[dict[str, Any]] = []
    for index, source in enumerate(versions):
        migrated, _ = migrate_legacy_fields(source, path=f"$.versions[{index}]", changes=migration_impact)
        migrated_versions.append(migrated)
    migrated_scenarios: list[dict[str, Any]] = []
    for index, source in enumerate(scenarios):
        migrated, _ = migrate_legacy_fields(source, path=f"$.scenarios[{index}]", changes=migration_impact)
        migrated_scenarios.append(migrated)
    versions = migrated_versions
    scenarios = migrated_scenarios

    project_name = (name_override or project_payload.get("name") or "Imported project").strip()
    project_code = _unique_project_code(session, context, code_override or project_payload.get("code") or "IMPORTED")
    project_kind = _portal_project_kind(project_payload, manifest, migration_impact)
    project = create_project(
        session,
        context=context,
        name=project_name,
        code=project_code,
        description=(project_payload.get("description") or "") + f"\nImported from LandValue360 {manifest.get('source_platform_version')} package.",
        portfolio_id=None,
        project_kind=project_kind,
    )

    version_map: dict[str, str] = {}
    created_versions: list[ProjectVersion] = []
    for source in sorted(versions, key=lambda row: int(row.get("version_number") or 0)):
        old_id = str(source.get("source_version_id") or "")
        supersedes = version_map.get(str(source.get("supersedes_source_version_id") or ""))
        notes = source.get("notes") or ""
        source_status = source.get("source_status") or "UNKNOWN"
        source_input_schema = source.get("source_input_schema") or f"lv360-package-{manifest.get('format_version')}"
        imported_snapshot = _adapt_client_portal_snapshot(
            deepcopy(source.get("input_snapshot") or {}),
            source_input_schema=source_input_schema,
            path=f"$.versions[{len(created_versions)}].input_snapshot",
            impact=migration_impact,
            manifest=manifest,
        )
        version = create_project_version(
            session,
            context=context,
            project_id=project.id,
            input_snapshot=imported_snapshot,
            label=source.get("label") or f"Imported source v{source.get('version_number')}",
            notes=(notes + f"\nImported source status: {source_status}. Recalculation and approval are required.").strip(),
            supersedes_version_id=supersedes,
            source_input_schema=source_input_schema,
            source_input_snapshot=deepcopy(source.get("source_input_snapshot") or source.get("input_snapshot") or {}),
            source_input_hash=source.get("source_input_hash") or source.get("input_hash"),
        )
        if old_id:
            version_map[old_id] = version.id
        created_versions.append(version)

    scenario_count = 0
    for source in scenarios:
        target_version_id = version_map.get(str(source.get("source_version_id") or ""))
        if not target_version_id:
            continue
        create_scenario(
            session,
            context=context,
            version_id=target_version_id,
            name=str(source.get("name") or "Imported scenario"),
            code=str(source.get("code") or f"SCENARIO-{scenario_count + 1}"),
            description=source.get("description"),
            override_snapshot=deepcopy(source.get("override_snapshot") or {}),
        )
        scenario_count += 1

    return {
        "project_id": project.id,
        "project_name": project.name,
        "project_code": project.code,
        "latest_version_id": created_versions[-1].id if created_versions else None,
        "version_count": len(created_versions),
        "scenario_count": scenario_count,
        "source_platform_version": manifest.get("source_platform_version"),
        "package_format_version": manifest.get("format_version"),
        "migration_impact_report": {
            "source_platform_version": manifest.get("source_platform_version"),
            "target_platform_version": APPLICATION_VERSION,
            "field_changes": migration_impact,
            "field_change_count": len(migration_impact),
            "reference_results_reused": False,
            "recalculation_required": True,
            "reason": "Imported results are audit references only; v2.1.1 recalculates with the selected project and valuation policy versions.",
        },
        "message": "Project inputs and scenarios were imported as drafts. Client Portal projects require analyst completion and recalculation under published target policies before reliance.",
    }
