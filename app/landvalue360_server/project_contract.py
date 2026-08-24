"""Versioned Excel contract for editable project-input exchange.

The contract is intentionally simple and auditable.  It stores one scalar per
row with an explicit JSON path and data type.  Excel is only a transport and
review surface; the authoritative project version remains the JSON snapshot in
LandValue360.  Import always creates a new draft version and never mutates an
approved version.
"""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import re
from typing import Any, Iterable
from defusedxml import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .reporting import Cell, build_xlsx

PROJECT_CONTRACT_VERSION = "0.10.0"
MAX_WORKBOOK_BYTES = 8 * 1024 * 1024
MAX_CONTRACT_ROWS = 25_000
MAX_PATH_LENGTH = 500

_MAGIC = "LANDVALUE360_PROJECT_INPUT_CONTRACT"
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


class ProjectContractError(ValueError):
    """Raised when an imported workbook does not satisfy the project contract."""


def _scalar_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _walk(value: Any, path: str = "") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        yield path, "object", ""
        for key in sorted(value):
            child = f"{path}.{key}" if path else str(key)
            yield from _walk(value[key], child)
    elif isinstance(value, list):
        yield path, "array", ""
        for index, item in enumerate(value):
            child = f"{path}.{index}" if path else str(index)
            yield from _walk(item, child)
    else:
        yield path, _scalar_type(value), "" if value is None else value


def _section(path: str) -> str:
    root = path.split(".", 1)[0] if path else "root"
    labels = {
        "planning": "Planning",
        "planning_products": "Planning products",
        "products": "Commercial products",
        "costs": "Costs",
        "funding": "Funding",
        "finance_model": "Finance structure",
        "partnership": "Partnership",
        "negotiation_studio": "Negotiation",
        "valuation_context": "Valuation context",
    }
    return labels.get(root, "Project basics")


def project_contract_xlsx(*, snapshot: dict[str, Any], project_id: str, project_name: str, version_id: str, version_number: int) -> bytes:
    """Return a versioned XLSX transport for a project snapshot."""

    instructions = [
        [Cell("LandValue360 Enterprise — Project Input Contract", style=1)],
        [Cell("Contract version"), Cell(PROJECT_CONTRACT_VERSION)],
        [Cell("Project"), Cell(project_name)],
        [Cell("Project version"), Cell(f"v{version_number}")],
        [Cell("Purpose"), Cell("Edit scalar values in the Project Contract sheet, then import the workbook to create a new draft version.")],
        [Cell("Rules"), Cell("Do not change Path or Type. Do not insert/delete array rows in this release. Use the web application for structural changes.")],
        [Cell("Identity"), Cell("Project ID and name are re-bound to the target project during import.")],
        [Cell("Governance"), Cell("Import never overwrites the source version. A new auditable draft version is created.")],
    ]
    rows: list[list[Cell]] = [
        [
            Cell("Path", style=1),
            Cell("Type", style=1),
            Cell("Value", style=1),
            Cell("Section", style=1),
            Cell("Editable", style=1),
            Cell("Notes", style=1),
        ],
        [Cell("__meta__.magic"), Cell("string"), Cell(_MAGIC), Cell("Metadata"), Cell("NO"), Cell("Do not edit")],
        [Cell("__meta__.contract_version"), Cell("string"), Cell(PROJECT_CONTRACT_VERSION), Cell("Metadata"), Cell("NO"), Cell("Do not edit")],
        [Cell("__meta__.project_id"), Cell("string"), Cell(project_id), Cell("Metadata"), Cell("NO"), Cell("Source reference")],
        [Cell("__meta__.project_version_id"), Cell("string"), Cell(version_id), Cell("Metadata"), Cell("NO"), Cell("Source reference")],
        [Cell("__meta__.project_version_number"), Cell("integer"), Cell(version_number, number=True), Cell("Metadata"), Cell("NO"), Cell("Source reference")],
    ]
    for path, kind, value in _walk(snapshot):
        if not path:
            continue
        editable = "NO" if path in {"project_id", "project_name"} or kind in {"object", "array"} else "YES"
        notes = "Container row — do not edit" if kind in {"object", "array"} else ""
        rows.append([Cell(path), Cell(kind), Cell(value), Cell(_section(path)), Cell(editable), Cell(notes)])
    return build_xlsx(
        [
            ("Instructions", instructions, [30, 90]),
            ("Project Contract", rows, [48, 14, 32, 24, 12, 48]),
        ]
    )


def _column_index(reference: str) -> int:
    match = _CELL_REF_RE.match(reference)
    if not match:
        raise ProjectContractError(f"Invalid Excel cell reference: {reference}")
    letters = match.group(1)
    result = 0
    for char in letters:
        result = result * 26 + (ord(char) - 64)
    return result - 1


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    result: list[str] = []
    for item in root.findall(f"{{{_NS_MAIN}}}si"):
        result.append("".join(node.text or "" for node in item.iter(f"{{{_NS_MAIN}}}t")))
    return result


def _workbook_sheets(archive: ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall(f"{{{_NS_PKG_REL}}}Relationship")
    }
    sheets: dict[str, str] = {}
    for sheet in workbook.findall(f".//{{{_NS_MAIN}}}sheet"):
        name = sheet.attrib.get("name", "")
        rel_id = sheet.attrib.get(f"{{{_NS_REL}}}id")
        target = targets.get(rel_id or "")
        if target:
            if target.startswith("/"):
                resolved = target.lstrip("/")
            elif target.startswith("xl/"):
                resolved = target
            else:
                resolved = f"xl/{target}"
            sheets[name] = resolved
    return sheets


def _sheet_rows(archive: ZipFile, path: str) -> list[list[str]]:
    shared = _shared_strings(archive)
    root = ET.fromstring(archive.read(path))
    output: list[list[str]] = []
    for row in root.findall(f".//{{{_NS_MAIN}}}row"):
        values: dict[int, str] = {}
        max_col = -1
        for cell in row.findall(f"{{{_NS_MAIN}}}c"):
            ref = cell.attrib.get("r", "A1")
            col = _column_index(ref)
            max_col = max(max_col, col)
            kind = cell.attrib.get("t")
            value = ""
            if kind == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter(f"{{{_NS_MAIN}}}t"))
            else:
                raw = cell.find(f"{{{_NS_MAIN}}}v")
                if raw is not None and raw.text is not None:
                    if kind == "s":
                        try:
                            value = shared[int(raw.text)]
                        except (ValueError, IndexError) as exc:
                            raise ProjectContractError("Invalid shared-string reference in workbook.") from exc
                    elif kind == "b":
                        value = "true" if raw.text == "1" else "false"
                    else:
                        value = raw.text
            values[col] = value
        output.append([values.get(index, "") for index in range(max_col + 1)])
    return output


def _parse_boolean(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise ProjectContractError(f"Invalid boolean value: {value!r}")


def _convert(kind: str, value: str) -> Any:
    if kind == "null":
        return None
    if kind == "boolean":
        return _parse_boolean(value)
    if kind == "integer":
        try:
            return int(float(value))
        except ValueError as exc:
            raise ProjectContractError(f"Invalid integer value: {value!r}") from exc
    if kind == "number":
        try:
            float(value)
        except ValueError as exc:
            raise ProjectContractError(f"Invalid numeric value: {value!r}") from exc
        return value
    if kind == "string":
        return value
    if kind == "object":
        return {}
    if kind == "array":
        return []
    raise ProjectContractError(f"Unsupported contract type: {kind!r}")


def _path_parts(path: str) -> list[str | int]:
    if not path or len(path) > MAX_PATH_LENGTH:
        raise ProjectContractError("Contract contains an empty or excessively long path.")
    result: list[str | int] = []
    for part in path.split("."):
        if not part or part in {"__proto__", "prototype", "constructor"}:
            raise ProjectContractError(f"Unsafe or invalid contract path: {path!r}")
        result.append(int(part) if part.isdigit() else part)
    return result


def _set_path(root: Any, parts: list[str | int], value: Any) -> None:
    current = root
    for index, part in enumerate(parts[:-1]):
        nxt = parts[index + 1]
        if isinstance(part, int):
            if not isinstance(current, list):
                raise ProjectContractError("Array index used under a non-array path.")
            while len(current) <= part:
                current.append(None)
            if current[part] is None:
                current[part] = [] if isinstance(nxt, int) else {}
            current = current[part]
        else:
            if not isinstance(current, dict):
                raise ProjectContractError("Object key used under a non-object path.")
            if part not in current or current[part] is None:
                current[part] = [] if isinstance(nxt, int) else {}
            current = current[part]
    last = parts[-1]
    if isinstance(last, int):
        if not isinstance(current, list):
            raise ProjectContractError("Array index used under a non-array path.")
        while len(current) <= last:
            current.append(None)
        current[last] = value
    else:
        if not isinstance(current, dict):
            raise ProjectContractError("Object key used under a non-object path.")
        current[last] = value


def parse_project_contract_xlsx(payload: bytes) -> dict[str, Any]:
    """Parse and validate an XLSX project-input contract."""

    if not payload:
        raise ProjectContractError("The uploaded workbook is empty.")
    if len(payload) > MAX_WORKBOOK_BYTES:
        raise ProjectContractError("The uploaded workbook exceeds the 8 MB contract limit.")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            sheets = _workbook_sheets(archive)
            path = sheets.get("Project Contract")
            if not path:
                raise ProjectContractError("The workbook does not contain the 'Project Contract' sheet.")
            rows = _sheet_rows(archive, path)
    except (BadZipFile, KeyError, ET.ParseError) as exc:
        raise ProjectContractError("The uploaded file is not a valid LandValue360 XLSX contract.") from exc

    if not rows:
        raise ProjectContractError("The project contract sheet is empty.")
    header = [item.strip() for item in rows[0]]
    required = ["Path", "Type", "Value"]
    if header[:3] != required:
        raise ProjectContractError("The project contract header was changed or is unsupported.")
    if len(rows) - 1 > MAX_CONTRACT_ROWS:
        raise ProjectContractError("The project contract contains too many rows.")

    metadata: dict[str, str] = {}
    contract_rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows[1:], start=2):
        if not row or not row[0].strip():
            continue
        path, kind, value = (row + ["", "", ""])[:3]
        path = path.strip()
        kind = kind.strip().casefold()
        if path in seen:
            raise ProjectContractError(f"Duplicate path at row {row_number}: {path}")
        seen.add(path)
        if path.startswith("__meta__."):
            metadata[path] = value
        else:
            contract_rows.append((path, kind, value))

    if metadata.get("__meta__.magic") != _MAGIC:
        raise ProjectContractError("The workbook is not a LandValue360 project-input contract.")
    contract_version = metadata.get("__meta__.contract_version")
    if contract_version not in {"0.7.0", "0.7.1", "0.8.0", "0.8.1", "0.8.2", "0.8.3", "0.8.4", PROJECT_CONTRACT_VERSION}:
        raise ProjectContractError(
            f"Unsupported contract version {contract_version!r}; expected a supported version from 0.7.0 through {PROJECT_CONTRACT_VERSION}."
        )

    snapshot: dict[str, Any] = {}
    # Container rows must be applied before their children; shallower paths first.
    for path, kind, value in sorted(contract_rows, key=lambda item: (item[0].count("."), item[0])):
        converted = _convert(kind, value)
        _set_path(snapshot, _path_parts(path), converted)
    if not snapshot:
        raise ProjectContractError("The workbook did not contain a project snapshot.")
    required = {"project_id", "project_name", "reporting_currency", "valuation_date", "planning", "planning_products", "products", "costs", "funding", "partnership"}
    missing = sorted(required - set(snapshot))
    if missing:
        raise ProjectContractError(f"The workbook is missing required project section(s): {', '.join(missing)}.")
    allowed = required | {"land_value_baseline", "finance_model", "negotiation_studio", "scenario_studio", "valuation_context", "risk_register", "sensitivity_studio", "tender_studio", "landowner_studio", "ui_state"}
    unknown = sorted(set(snapshot) - allowed)
    if unknown:
        raise ProjectContractError(f"The workbook contains unsupported top-level section(s): {', '.join(unknown)}.")
    if not isinstance(snapshot.get("planning"), dict) or not isinstance(snapshot.get("products"), list) or not isinstance(snapshot.get("costs"), list):
        raise ProjectContractError("Planning must be an object and products/costs must be arrays.")
    return snapshot
