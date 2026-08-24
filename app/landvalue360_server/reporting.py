"""Institutional CSV/XLSX exports using only the Python standard library.

The XLSX writer deliberately emits a small, auditable OOXML subset rather than
introducing a spreadsheet library into the application runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO, StringIO
import csv
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


@dataclass(frozen=True, slots=True)
class Cell:
    value: Any
    style: int = 0
    number: bool = False


def _number(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _col_name(index: int) -> str:
    result = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _safe_sheet_name(name: str, used: set[str]) -> str:
    cleaned = "".join(" " if char in "[]:*?/\\" else char for char in name).strip()[:31] or "Sheet"
    candidate = cleaned
    counter = 2
    while candidate in used:
        suffix = f" {counter}"
        candidate = cleaned[: 31 - len(suffix)] + suffix
        counter += 1
    used.add(candidate)
    return candidate


def _cell_xml(row: int, col: int, cell: Cell) -> str:
    ref = f"{_col_name(col)}{row + 1}"
    style = f' s="{cell.style}"' if cell.style else ""
    if cell.value is None:
        return f'<c r="{ref}"{style}/>'
    if cell.number:
        value = _number(cell.value)
        if value is not None:
            return f'<c r="{ref}"{style}><v>{escape(format(value, "f"))}</v></c>'
    display_value = ("Yes" if cell.value else "No") if isinstance(cell.value, bool) else cell.value
    text = escape(str(display_value))
    return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(rows: list[list[Cell]], widths: list[float] | None = None) -> str:
    max_col = max((len(row) for row in rows), default=1)
    max_row = max(len(rows), 1)
    dimension = f"A1:{_col_name(max_col - 1)}{max_row}"
    cols = ""
    if widths:
        cols = "<cols>" + "".join(
            f'<col min="{index + 1}" max="{index + 1}" width="{width}" customWidth="1"/>'
            for index, width in enumerate(widths)
        ) + "</cols>"
    body = []
    for rindex, row in enumerate(rows):
        body.append(
            f'<row r="{rindex + 1}">' + "".join(_cell_xml(rindex, cindex, cell) for cindex, cell in enumerate(row)) + "</row>"
        )
    auto_filter = f'<autoFilter ref="A1:{_col_name(max_col - 1)}{max_row}"/>' if max_row > 1 else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="18"/>'
        f'{cols}<sheetData>{"".join(body)}</sheetData>{auto_filter}'
        '</worksheet>'
    )


def build_xlsx(sheets: list[tuple[str, list[list[Cell]], list[float] | None]]) -> bytes:
    used: set[str] = set()
    normalized = [(_safe_sheet_name(name, used), rows, widths) for name, rows, widths in sheets]
    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index + 1}" r:id="rId{index + 1}"/>'
        for index, (name, _, _) in enumerate(normalized)
    )
    workbook_rels = "".join(
        f'<Relationship Id="rId{index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index + 1}.xml"/>'
        for index in range(len(normalized))
    ) + f'<Relationship Id="rId{len(normalized) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(len(normalized))
    )
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with BytesIO() as buffer:
        with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
            archive.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                f'{overrides}</Types>',
            )
            archive.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                '</Relationships>',
            )
            archive.writestr(
                "xl/workbook.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets>{workbook_sheets}</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>',
            )
            archive.writestr(
                "xl/_rels/workbook.xml.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'{workbook_rels}</Relationships>',
            )
            archive.writestr(
                "xl/styles.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<numFmts count="3"><numFmt numFmtId="164" formatCode="#&#44;##0.00;[Red](#&#44;##0.00);-"/>'
                '<numFmt numFmtId="165" formatCode="0.00%"/><numFmt numFmtId="166" formatCode="0.00x"/></numFmts>'
                '<fonts count="2"><font><sz val="10"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Aptos"/></font></fonts>'
                '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF123F3A"/><bgColor indexed="64"/></patternFill></fill></fills>'
                '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                '<cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center"/></xf>'
                '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                '<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
                '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                '</styleSheet>',
            )
            for index, (_, rows, widths) in enumerate(normalized):
                archive.writestr(f"xl/worksheets/sheet{index + 1}.xml", _sheet_xml(rows, widths))
            archive.writestr(
                "docProps/core.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                '<dc:creator>LandValue360 Enterprise</dc:creator><dc:title>Calculation export</dc:title>'
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>',
            )
            archive.writestr(
                "docProps/app.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
                'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
                '<Application>LandValue360 Enterprise</Application></Properties>',
            )
        return buffer.getvalue()


def cash_flow_rows(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Return authoritative cash-flow events for CSV and report exports.

    Governed Unified Engine 1.0.0 runs export the canonical reconciled event ledger.
    Legacy series are retained only when no unified ledger exists.
    """

    currency = output.get("reporting_currency") or ""
    ledger = output.get("event_ledger") or ((output.get("unified_financial_result") or {}).get("event_ledger") or {})
    rows: list[dict[str, Any]] = []
    if ledger.get("events"):
        rows.extend(
            {
                "date": item.get("date"),
                "series_id": f"ENGINE:{item.get('event_type') or 'EVENT'}",
                "description": f"Authoritative Unified Engine 1.0.0 event — {item.get('account') or ''} / {item.get('counterparty') or ''}",
                "label": item.get("event_id") or "",
                "amount": item.get("cash_effect"),
                "currency": currency,
            }
            for item in ledger.get("events") or []
        )

    seen: set[str] = set()
    series = list(output.get("cash_flow_series") or [])
    finance = output.get("finance_analysis") or {}
    series.extend(finance.get("cash_flow_series") or [])
    for item in series:
        series_id = str(item.get("series_id") or "")
        if not series_id or series_id in seen:
            continue
        seen.add(series_id)
        for point in item.get("points") or []:
            rows.append(
                {
                    "date": point.get("date"),
                    "series_id": series_id,
                    "description": ("Legacy audit series — " if ledger.get("events") else "") + (item.get("description") or ""),
                    "label": point.get("label") or "",
                    "amount": point.get("amount"),
                    "currency": item.get("currency") or currency,
                }
            )
    return sorted(rows, key=lambda row: (str(row["date"]), str(row["series_id"])))

def cash_flow_csv(output: dict[str, Any]) -> bytes:
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["date", "series_id", "description", "label", "amount", "currency"])
    writer.writeheader()
    writer.writerows(cash_flow_rows(output))
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _header(*values: str) -> list[Cell]:
    return [Cell(value, style=1) for value in values]


def calculation_xlsx(run: Any) -> bytes:
    """Export one calculation run with Unified Engine 1.0.0 as the display authority."""

    output = run.output_snapshot or {}
    unified = output.get("unified_financial_result") or {}
    truth = output.get("financial_truth") or unified.get("financial_truth") or {}
    ledger = output.get("event_ledger") or unified.get("event_ledger") or {}
    invariants = output.get("engine_invariants") or unified.get("engine_invariants") or {}
    manifest = output.get("engine_manifest") or unified.get("engine_manifest") or {}
    monthly = unified.get("monthly_cashflow") or []
    currency = output.get("reporting_currency") or unified.get("currency") or ""

    summary_rows: list[list[Cell]] = [
        _header("Field", "Value", "Unit / source"),
        [Cell("Project"), Cell(output.get("project_name")), Cell("")],
        [Cell("Calculation run"), Cell(run.id), Cell("")],
        [Cell("Application version"), Cell(run.application_version), Cell("")],
        [Cell("Engine version"), Cell(manifest.get("engine_version") or run.calculation_model_version), Cell("Authoritative")],
        [Cell("Calculation hash"), Cell(truth.get("calculation_hash") or unified.get("calculation_hash")), Cell("SHA-256")],
        [Cell("Ledger hash"), Cell(ledger.get("ledger_hash")), Cell("SHA-256")],
        [Cell("Invariant hash"), Cell(invariants.get("invariant_hash")), Cell("SHA-256")],
        [Cell("Decision status"), Cell(truth.get("status") or run.status), Cell("Unified Engine 1.0.0")],
        [Cell("Approved structure"), Cell(truth.get("method")), Cell("")],
        [Cell("Approved share"), Cell(truth.get("approved_share"), style=3, number=True), Cell("% / entered measure")],
    ]
    money_keys = {
        "gross_potential_revenue", "gross_sales", "net_sales", "gross_collections", "net_collections",
        "planned_total_cost", "development_cost", "government_cost_contribution",
        "third_party_cost_contribution", "government_consideration", "government_npv",
        "developer_profit", "developer_npv", "developer_equity_npv", "project_npv",
        "peak_funding_gap", "funding_gap", "peak_negative_cash", "peak_debt", "peak_equity",
        "interest_total", "financing_fees_total", "terminal_debt", "deferred_development_cost",
        "deferred_contractual_payment", "mandatory_shortfall", "unmodeled_scope",
        "terminal_unpaid_obligations",
    }
    rate_keys = {"developer_profit_on_cost", "developer_irr", "developer_equity_irr", "project_irr"}
    multiple_keys = {"developer_multiple", "developer_equity_multiple"}
    operational_keys = {
        "finance_mode", "spend_policy", "schedule_extension_months", "original_completion_date",
        "adjusted_completion_date", "configured_horizon_months", "required_horizon_months",
        "project_duration_months", "feasible", "failed_constraints",
    }
    for key in list(money_keys) + list(rate_keys) + list(multiple_keys) + list(operational_keys):
        if key not in truth:
            continue
        value = truth.get(key)
        style = 2 if key in money_keys else 3 if key in rate_keys else 4 if key in multiple_keys else 0
        unit = currency if key in money_keys else "%" if key in rate_keys else "x" if key in multiple_keys else ""
        summary_rows.append([Cell(key.replace("_", " ").title()), Cell(value, style=style, number=style > 0), Cell(unit)])

    monthly_rows = [_header(
        "Month", "Date", "Opening cash", "Gross contracted sales", "Net contracted sales",
        "Sales collections", "Planned cost", "Executed cost", "Deferred cost",
        "Public consideration", "Interest", "Fees", "Debt draw", "Equity contribution",
        "Debt repayment", "Developer distribution", "Landowner distribution",
        "Opening debt", "Closing debt", "Funding gap", "Mandatory shortfall", "Closing cash"
    )]
    for row in monthly:
        monthly_rows.append([
            Cell(row.get("month"), number=True), Cell(row.get("date")),
            Cell(row.get("opening_cash"), style=2, number=True),
            Cell(row.get("gross_contracted_sales"), style=2, number=True),
            Cell(row.get("net_contracted_sales"), style=2, number=True),
            Cell(row.get("sales_collections"), style=2, number=True),
            Cell(row.get("planned_cost"), style=2, number=True),
            Cell(row.get("actual_cost"), style=2, number=True),
            Cell(row.get("deferred_cost"), style=2, number=True),
            Cell(row.get("government_payment"), style=2, number=True),
            Cell(row.get("interest_paid"), style=2, number=True),
            Cell(row.get("financing_fees"), style=2, number=True),
            Cell(row.get("financing_draw"), style=2, number=True),
            Cell(row.get("equity_contribution"), style=2, number=True),
            Cell(row.get("financing_repayment"), style=2, number=True),
            Cell(row.get("developer_distribution"), style=2, number=True),
            Cell(row.get("landowner_distribution"), style=2, number=True),
            Cell(row.get("opening_debt"), style=2, number=True),
            Cell(row.get("ending_debt"), style=2, number=True),
            Cell(row.get("unsupported_funding_gap"), style=2, number=True),
            Cell(row.get("mandatory_shortfall"), style=2, number=True),
            Cell(row.get("ending_cash"), style=2, number=True),
        ])

    invariant_rows = [_header("Invariant", "Label", "Status", "Actual", "Operator", "Threshold", "Mandatory")]
    for item in invariants.get("checks") or []:
        invariant_rows.append([
            Cell(item.get("invariant_id")), Cell(item.get("label")),
            Cell("PASS" if item.get("passed") else "FAIL"), Cell(item.get("actual")),
            Cell(item.get("operator")), Cell(item.get("threshold")), Cell(item.get("mandatory")),
        ])

    contract_rows = [_header("Constraint", "Label", "Status", "Actual", "Operator", "Threshold", "Severity", "Reason")]
    for item in truth.get("constraints") or []:
        contract_rows.append([
            Cell(item.get("constraint_id")), Cell(item.get("label")),
            Cell("PASS" if item.get("passed") else "FAIL"), Cell(item.get("actual"), number=True),
            Cell(item.get("operator")), Cell(item.get("threshold"), number=True),
            Cell(item.get("severity")), Cell(item.get("reason")),
        ])

    event_rows = [_header("Event ID", "Month", "Date", "Type", "Account", "Counterparty", "Amount", "Cash effect", "Debt effect", "Equity effect", "Mandatory")]
    for item in ledger.get("events") or []:
        event_rows.append([
            Cell(item.get("event_id")), Cell(item.get("month"), number=True), Cell(item.get("date")),
            Cell(item.get("event_type")), Cell(item.get("account")), Cell(item.get("counterparty")),
            Cell(item.get("amount"), style=2, number=True), Cell(item.get("cash_effect"), style=2, number=True),
            Cell(item.get("debt_effect"), style=2, number=True), Cell(item.get("equity_effect"), style=2, number=True),
            Cell(item.get("mandatory")),
        ])

    reconciliation_rows = [_header("Month", "Date", "Opening cash", "Net events", "Closing cash", "Cash variance", "Opening debt", "Net debt events", "Closing debt", "Debt variance", "Balanced")]
    for item in ledger.get("monthly_reconciliation") or []:
        reconciliation_rows.append([
            Cell(item.get("month"), number=True), Cell(item.get("date")),
            Cell(item.get("opening_cash"), style=2, number=True), Cell(item.get("net_cash_events"), style=2, number=True),
            Cell(item.get("ending_cash"), style=2, number=True), Cell(item.get("cash_variance"), style=2, number=True),
            Cell(item.get("opening_debt"), style=2, number=True), Cell(item.get("net_debt_events"), style=2, number=True),
            Cell(item.get("ending_debt"), style=2, number=True), Cell(item.get("debt_variance"), style=2, number=True),
            Cell(item.get("balanced")),
        ])

    decision = output.get("decision_explanation") or {}
    decision_rows = [_header("Status", "Severity", "Domain", "Constraint", "Actual", "Operator", "Threshold", "Unit", "Reason AR", "Reason EN", "Remediation AR", "Remediation EN")]
    for item in decision.get("causes") or []:
        decision_rows.append([
            Cell(item.get("status")), Cell(item.get("severity")), Cell(item.get("domain")), Cell(item.get("constraint_id")),
            Cell(item.get("actual"), number=True), Cell(item.get("operator")), Cell(item.get("threshold"), number=True), Cell(item.get("unit")),
            Cell(item.get("reason_ar")), Cell(item.get("reason_en")), Cell(item.get("remediation_ar")), Cell(item.get("remediation_en")),
        ])
    if len(decision_rows) == 1:
        decision_rows.append([Cell(decision.get("status") or truth.get("status")), Cell(""), Cell("ENGINE"), Cell(decision.get("headline_en") or "No failed mandatory constraints")])

    solver = output.get("constraint_solver") or {}
    solver_rows = [_header("Rank", "Lever", "Current", "Required", "Delta", "Relative change", "Unit", "Solves all", "Rationale AR", "Rationale EN", "Trace hash")]
    for item in solver.get("suggestions") or []:
        solver_rows.append([
            Cell(item.get("rank"), number=True), Cell(item.get("lever")),
            Cell(item.get("current_value"), number=True), Cell(item.get("required_value"), number=True),
            Cell(item.get("delta"), number=True), Cell(item.get("relative_change"), style=3, number=True),
            Cell(item.get("unit")), Cell(item.get("solves_all_constraints")),
            Cell(item.get("rationale_ar")), Cell(item.get("rationale_en")), Cell(item.get("trace_hash")),
        ])
    if len(solver_rows) == 1:
        solver_rows.append([Cell(None), Cell(solver.get("status") or "NOT_REQUIRED"), Cell(None), Cell(None), Cell(None), Cell(None), Cell(None), Cell(None), Cell(solver.get("explanation_ar")), Cell(solver.get("explanation_en")), Cell(None)])

    comparison = truth.get("legacy_reconciliation") or {}
    legacy_rows = [_header("Metric", "Unified Engine 1.0.0", "Legacy audit value", "Variance", "Display authority")]
    legacy_metrics = comparison.get("legacy_metrics") or {}
    differences = comparison.get("differences") or {}
    for key in sorted(set(legacy_metrics) | set(differences)):
        legacy_rows.append([
            Cell(key), Cell(truth.get(key), style=2, number=True),
            Cell(legacy_metrics.get(key), style=2, number=True), Cell(differences.get(key), style=2, number=True),
            Cell(comparison.get("display_authority") or "UNIFIED_MONTHLY_MODEL"),
        ])

    manifest_rows = [_header("Field", "Value")]
    for key, value in manifest.items():
        manifest_rows.append([Cell(key), Cell(value)])
    manifest_rows.extend([
        [Cell("Ledger status"), Cell(ledger.get("status"))],
        [Cell("Maximum cash variance"), Cell(ledger.get("maximum_cash_variance"), style=2, number=True)],
        [Cell("Maximum debt variance"), Cell(ledger.get("maximum_debt_variance"), style=2, number=True)],
        [Cell("Engine invariant status"), Cell(invariants.get("status"))],
    ])

    closure_rows = [
        _header("Field", "Value", "Unit / interpretation"),
        [Cell("Engine status"), Cell(truth.get("status")), Cell("Authoritative")],
        [Cell("Finance mode"), Cell(truth.get("finance_mode")), Cell("")],
        [Cell("Spend policy"), Cell(truth.get("spend_policy")), Cell("")],
        [Cell("Original completion date"), Cell(truth.get("original_completion_date")), Cell("")],
        [Cell("Adjusted completion date"), Cell(truth.get("adjusted_completion_date")), Cell("")],
        [Cell("Schedule extension"), Cell(truth.get("schedule_extension_months"), number=True), Cell("months")],
        [Cell("Terminal debt"), Cell(truth.get("terminal_debt"), style=2, number=True), Cell(currency)],
        [Cell("Deferred development cost"), Cell(truth.get("deferred_development_cost"), style=2, number=True), Cell(currency)],
        [Cell("Deferred contractual payment"), Cell(truth.get("deferred_contractual_payment"), style=2, number=True), Cell(currency)],
        [Cell("Mandatory shortfall"), Cell(truth.get("mandatory_shortfall"), style=2, number=True), Cell(currency)],
        [Cell("Unmodeled scope"), Cell(truth.get("unmodeled_scope"), style=2, number=True), Cell(currency)],
        [Cell("Terminal unpaid obligations"), Cell(truth.get("terminal_unpaid_obligations"), style=2, number=True), Cell(currency)],
        [Cell("Ledger status"), Cell(ledger.get("status")), Cell("Must be RECONCILED")],
        [Cell("Invariant status"), Cell(invariants.get("status")), Cell("Must be PASS")],
    ]

    cost_rows = [_header("Cost ID", "Name", "Method", "Basis", "Reference", "Basis amount", "Resolved quantity", "Quantity unit", "Unit cost", "Resolved base cost", "Note")]
    for item in (output.get("cost_calculation") or {}).get("items") or []:
        cost_rows.append([Cell(item.get("cost_id")), Cell(item.get("name")), Cell(item.get("calculation_method")), Cell(item.get("basis_label")), Cell(item.get("basis_reference_id")), Cell(item.get("basis_amount"), style=2, number=True), Cell(item.get("resolved_quantity"), number=True), Cell(item.get("quantity_unit")), Cell(item.get("resolved_unit_cost"), style=2, number=True), Cell(item.get("resolved_base_cost"), style=2, number=True), Cell(item.get("calculation_note"))])

    return build_xlsx([
        ("Engine Executive Summary", summary_rows, [42, 28, 30]),
        ("Finance Schedule", monthly_rows, [10, 14] + [18] * 20),
        ("Engine Invariants", invariant_rows, [34, 58, 12, 20, 12, 20, 12]),
        ("Contract Constraints", contract_rows, [34, 48, 12, 20, 12, 20, 14, 70]),
        ("Decision Explanation", decision_rows, [14, 14, 18, 30, 18, 12, 18, 14, 55, 55, 60, 60]),
        ("Solver Suggestions", solver_rows, [10, 24, 18, 18, 18, 18, 16, 14, 60, 60, 68]),
        ("Event Ledger", event_rows, [28, 10, 14, 28, 28, 28, 18, 18, 18, 18, 12]),
        ("Ledger Reconciliation", reconciliation_rows, [10, 14] + [18] * 8 + [12]),
        ("Legacy Reconciliation", legacy_rows, [32, 20, 20, 20, 28]),
        ("Engine Manifest", manifest_rows, [42, 90]),
        ("Finance Closure", closure_rows, [42, 24, 48]),
        ("Cost Resolution", cost_rows, [20, 34, 26, 34, 22, 18, 18, 14, 18, 20, 55]),
    ])

def valuation_xlsx(run: Any) -> bytes:
    """Export one immutable valuation run as an auditable multi-sheet workbook."""

    output = run.output_snapshot or {}
    reconciliation = output.get("reconciliation") or {}
    readiness = output.get("institutional_readiness") or {}
    maturity = output.get("study_maturity") or {}
    context = output.get("valuation_context") or {}
    quality = output.get("data_quality") or {}
    currency = output.get("reporting_currency") or run.reporting_currency or ""

    summary_rows: list[list[Cell]] = [
        _header("Field", "Value", "Unit / currency"),
        [Cell("Valuation run"), Cell(run.id), Cell("")],
        [Cell("Status"), Cell(run.status), Cell("")],
        [Cell("Mode"), Cell(run.mode), Cell("")],
        [Cell("Basis of value"), Cell(run.basis_of_value), Cell("")],
        [Cell("Purpose"), Cell(run.purpose), Cell("")],
        [Cell("Valuation date"), Cell(run.valuation_date.isoformat()), Cell("")],
        [Cell("Valuation model"), Cell(run.valuation_model_version), Cell("")],
        [Cell("Reconciled value"), Cell(reconciliation.get("reconciled_value"), style=2, number=True), Cell(currency)],
        [Cell("Low value"), Cell(reconciliation.get("low_value"), style=2, number=True), Cell(currency)],
        [Cell("High value"), Cell(reconciliation.get("high_value"), style=2, number=True), Cell(currency)],
        [Cell("Value / gross land sqm"), Cell(reconciliation.get("value_per_gross_land_sqm"), style=2, number=True), Cell(f"{currency}/sqm")],
        [Cell("Method dispersion"), Cell(reconciliation.get("method_dispersion"), style=3, number=True), Cell("%")],
        [Cell("Data quality score"), Cell(quality.get("score"), style=2, number=True), Cell("/100")],
        [Cell("Data quality grade"), Cell(quality.get("grade")), Cell("")],
        [Cell("Institutional readiness"), Cell(readiness.get("score"), style=2, number=True), Cell("/100")],
        [Cell("Readiness grade"), Cell(readiness.get("grade")), Cell("")],
        [Cell("Institutional gate"), Cell("PASS" if readiness.get("institutional_gate_passed") else "NOT PASSED"), Cell("")],
        [Cell("Cost estimate class"), Cell(maturity.get("cost_estimate_class") or context.get("cost_estimate_class")), Cell("")],
        [Cell("Design maturity"), Cell(maturity.get("design_maturity") or context.get("design_maturity")), Cell("")],
        [Cell("Measurement basis"), Cell(maturity.get("measurement_basis") or context.get("measurement_basis")), Cell("")],
        [Cell("Study maturity score"), Cell(maturity.get("score"), style=2, number=True), Cell("/100")],
        [Cell("Study maturity grade"), Cell(maturity.get("grade")), Cell("")],
    ]

    method_rows: list[list[Cell]] = [_header("Method", "Value", "Low", "High", "Input weight", "Confidence", "Normalized weight", "Source", "Explanation")]
    for method in output.get("methods") or []:
        method_rows.append(
            [
                Cell(method.get("method_id")),
                Cell(method.get("value"), style=2, number=True),
                Cell(method.get("low_value"), style=2, number=True),
                Cell(method.get("high_value"), style=2, number=True),
                Cell(method.get("input_weight"), style=3, number=True),
                Cell(method.get("confidence"), style=3, number=True),
                Cell(method.get("normalized_weight"), style=3, number=True),
                Cell(method.get("source")),
                Cell(method.get("explanation")),
            ]
        )

    comparable_rows: list[list[Cell]] = [_header("Method", "Comparable", "Label", "Raw unit price", "Total adjustment", "Adjusted unit price", "Indicated subject value", "Reliability", "Evidence ID")]
    for method in output.get("methods") or []:
        for comp in (method.get("diagnostics") or {}).get("comparables") or []:
            comparable_rows.append(
                [
                    Cell(method.get("method_id")), Cell(comp.get("comparable_id")), Cell(comp.get("label")),
                    Cell(comp.get("raw_unit_price"), style=2, number=True), Cell(comp.get("total_adjustment"), style=3, number=True),
                    Cell(comp.get("adjusted_unit_price"), style=2, number=True), Cell(comp.get("indicated_subject_value"), style=2, number=True),
                    Cell(comp.get("reliability_weight"), style=3, number=True), Cell(comp.get("evidence_document_id")),
                ]
            )

    evidence_rows: list[list[Cell]] = [_header("Evidence type", "Critical", "Status", "Score", "Weight", "Weighted score", "Document ID", "Expired", "Gate")]
    for item in quality.get("evidence_requirements") or []:
        evidence_rows.append(
            [
                Cell(item.get("evidence_type")), Cell(item.get("critical")), Cell(item.get("status")),
                Cell(item.get("score"), style=3, number=True), Cell(item.get("weight"), style=3, number=True),
                Cell(item.get("weighted_score"), style=3, number=True), Cell(item.get("document_id")), Cell(item.get("expired")), Cell(item.get("gate")),
            ]
        )

    assumption_rows: list[list[Cell]] = [_header("Key", "Criticality", "Approval", "Evidence", "Confidence", "Quality score")]
    for item in quality.get("assumptions") or []:
        assumption_rows.append(
            [
                Cell(item.get("assumption_key")), Cell(item.get("criticality")), Cell(item.get("approval_status")),
                Cell(item.get("evidence_status")), Cell(item.get("confidence_score"), style=2, number=True), Cell(item.get("score"), style=3, number=True),
            ]
        )

    readiness_rows: list[list[Cell]] = [_header("Gate", "Score", "Status")]
    for gate, item in (quality.get("readiness_gates") or {}).items():
        readiness_rows.append([Cell(gate), Cell(item.get("score"), style=2, number=True), Cell(item.get("status"))])
    for key, value in (readiness.get("components") or {}).items():
        readiness_rows.append([Cell(f"Readiness component: {key}"), Cell(value, style=2, number=True), Cell("")])
    for key in ("cost_estimate_class_score", "design_maturity_score", "measurement_basis_score"):
        readiness_rows.append([Cell(f"Study maturity: {key}"), Cell(maturity.get(key), style=2, number=True), Cell("")])

    warning_rows: list[list[Cell]] = [_header("Code", "Message")]
    for item in output.get("warnings") or []:
        warning_rows.append([Cell(item.get("code")), Cell(item.get("message"))])
    for item in output.get("limitations") or []:
        warning_rows.append([Cell("LIMITATION"), Cell(item)])

    return build_xlsx(
        [
            ("Valuation Summary", summary_rows, [34, 32, 20]),
            ("Method Reconciliation", method_rows, [22, 18, 18, 18, 16, 16, 18, 32, 70]),
            ("Market Comparables", comparable_rows, [20, 18, 28, 18, 18, 20, 22, 16, 38]),
            ("Evidence Quality", evidence_rows, [24, 12, 18, 14, 14, 18, 38, 12, 18]),
            ("Assumption Quality", assumption_rows, [48, 16, 18, 18, 16, 16]),
            ("Readiness", readiness_rows, [38, 18, 18]),
            ("Warnings & Limits", warning_rows, [30, 100]),
        ]
    )


def valuation_report_html(run: Any) -> str:
    """Return a self-contained printable valuation indication report."""

    from html import escape as html_escape

    output = run.output_snapshot or {}
    rec = output.get("reconciliation") or {}
    quality = output.get("data_quality") or {}
    readiness = output.get("institutional_readiness") or {}
    maturity = output.get("study_maturity") or {}
    subject = output.get("subject") or {}
    currency = html_escape(str(output.get("reporting_currency") or run.reporting_currency or ""))

    def money(value: Any) -> str:
        number = _number(value)
        return "—" if number is None else f"{number:,.2f} {currency}"

    def pct(value: Any) -> str:
        number = _number(value)
        return "—" if number is None else f"{number * 100:,.2f}%"

    methods = "".join(
        "<tr>"
        f"<td>{html_escape(str(item.get('method_id') or ''))}</td>"
        f"<td>{money(item.get('value'))}</td>"
        f"<td>{money(item.get('low_value'))} – {money(item.get('high_value'))}</td>"
        f"<td>{pct(item.get('normalized_weight'))}</td>"
        f"<td>{html_escape(str(item.get('source') or ''))}</td>"
        "</tr>"
        for item in output.get("methods") or []
    )
    evidence = "".join(
        "<tr>"
        f"<td>{html_escape(str(item.get('evidence_type') or ''))}</td>"
        f"<td>{'Yes' if item.get('critical') else 'No'}</td>"
        f"<td>{html_escape(str(item.get('status') or ''))}</td>"
        f"<td>{pct(item.get('score'))}</td>"
        "</tr>"
        for item in quality.get("evidence_requirements") or []
    )
    warnings = "".join(
        f"<li><strong>{html_escape(str(item.get('code') or ''))}</strong> — {html_escape(str(item.get('message') or ''))}</li>"
        for item in output.get("warnings") or []
    ) or "<li>No model warnings were recorded.</li>"
    limitations = "".join(f"<li>{html_escape(str(item))}</li>" for item in output.get("limitations") or [])
    title = html_escape(str(subject.get("project_name") or "Valuation report"))
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Valuation indication</title>
<style>
body{{font-family:Arial,sans-serif;color:#172322;margin:0;background:#eef3f1}}main{{max-width:1050px;margin:24px auto;background:#fff;padding:42px;box-shadow:0 6px 30px #0002}}
h1{{margin:0;color:#123f3a}}h2{{border-bottom:2px solid #d7e5df;padding-bottom:8px;margin-top:34px}}.meta{{color:#5a6a67}}.grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}
.card{{border:1px solid #d7e5df;border-radius:10px;padding:16px}}.card strong{{display:block;font-size:1.25rem;margin-top:6px}}table{{border-collapse:collapse;width:100%;font-size:.92rem}}th,td{{border:1px solid #d7e5df;padding:9px;text-align:left}}th{{background:#123f3a;color:#fff}}
.notice{{background:#fff7dc;border-left:5px solid #bd8b00;padding:14px}}@media print{{body{{background:#fff}}main{{box-shadow:none;margin:0;max-width:none}}button{{display:none}}}}@media(max-width:700px){{main{{padding:20px;margin:0}}.grid{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<button onclick="window.print()">Print / Save PDF</button>
<p class="meta">LandValue360 Enterprise · valuation model {html_escape(str(run.valuation_model_version))} · run {html_escape(run.id)}</p>
<h1>{title}</h1><p class="meta">Basis: {html_escape(run.basis_of_value)} · Purpose: {html_escape(run.purpose)} · Date: {run.valuation_date.isoformat()}</p>
<div class="grid">
<div class="card">Reconciled value<strong>{money(rec.get('reconciled_value'))}</strong></div>
<div class="card">Value range<strong>{money(rec.get('low_value'))}<br>{money(rec.get('high_value'))}</strong></div>
<div class="card">Data quality<strong>{html_escape(str(quality.get('score') or '—'))}/100</strong><span>{html_escape(str(quality.get('grade') or ''))}</span></div>
<div class="card">Institutional readiness<strong>{html_escape(str(readiness.get('score') or '—'))}/100</strong><span>{html_escape(str(readiness.get('grade') or ''))}</span></div>
<div class="card">Study maturity<strong>{html_escape(str(maturity.get('score') or '—'))}/100</strong><span>{html_escape(str(maturity.get('grade') or ''))}</span></div>
</div>
<h2>Valuation reconciliation</h2><table><thead><tr><th>Method</th><th>Indication</th><th>Range</th><th>Weight</th><th>Source</th></tr></thead><tbody>{methods}</tbody></table>
<h2>Study maturity</h2><table><tbody>
<tr><th>Cost estimate class</th><td>{html_escape(str(maturity.get('cost_estimate_class') or '—'))}</td><th>Design maturity</th><td>{html_escape(str(maturity.get('design_maturity') or '—'))}</td></tr>
<tr><th>Measurement basis</th><td>{html_escape(str(maturity.get('measurement_basis') or '—'))}</td><th>Institutional maturity gate</th><td>{'PASS' if maturity.get('institutional_gate_passed') else 'NOT PASSED'}</td></tr>
</tbody></table>
<h2>Evidence quality</h2><table><thead><tr><th>Requirement</th><th>Critical</th><th>Status</th><th>Score</th></tr></thead><tbody>{evidence}</tbody></table>
<h2>Warnings</h2><ul>{warnings}</ul>
<div class="notice"><strong>Decision-support limitation.</strong><ul>{limitations}</ul></div>
<p class="meta">Input hash: {html_escape(run.input_hash)}<br>Output hash: {html_escape(run.output_hash)}</p>
</main></body></html>'''


def _flatten_analysis(prefix: str, value: Any, rows: list[list[Cell]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten_analysis(f"{prefix}.{key}" if prefix else str(key), item, rows)
    elif isinstance(value, list):
        if not value:
            rows.append([Cell(prefix), Cell("[]")])
        elif all(not isinstance(item, (dict, list)) for item in value):
            rows.append([Cell(prefix), Cell(", ".join(str(item) for item in value))])
        else:
            for index, item in enumerate(value):
                _flatten_analysis(f"{prefix}[{index}]", item, rows)
    else:
        numeric = _number(value)
        rows.append([Cell(prefix), Cell(value, style=2 if numeric is not None else 0, number=numeric is not None)])


def analysis_xlsx(run: Any) -> bytes:
    """Export a release-0.7 immutable analysis run to an auditable workbook."""
    output = run.output_snapshot or {}
    summary = [
        _header("Field", "Value"),
        [Cell("Analysis run"), Cell(run.id)],
        [Cell("Analysis type"), Cell(run.analysis_type)],
        [Cell("Status"), Cell(run.status)],
        [Cell("Model version"), Cell(run.analysis_model_version)],
        [Cell("Project version"), Cell(run.project_version_id)],
        [Cell("Policy version"), Cell(run.policy_pack_version_id)],
        [Cell("Input hash"), Cell(run.input_hash)],
        [Cell("Output hash"), Cell(run.output_hash)],
        [Cell("Created at"), Cell(run.created_at.isoformat())],
    ]
    flat_rows: list[list[Cell]] = [_header("Path", "Value")]
    _flatten_analysis("", output, flat_rows)
    sheets: list[tuple[str, list[list[Cell]], list[float] | None]] = [
        ("Analysis Summary", summary, [34, 70]),
        ("Output Detail", flat_rows, [70, 40]),
    ]
    if run.analysis_type == "RISK":
        rows = [_header("Risk", "Category", "Type", "Inherent", "Residual", "Level", "Owner", "Allocation", "Mitigation", "Contract clause")]
        for item in output.get("items") or []:
            rows.append([Cell(item.get("title")), Cell(item.get("category")), Cell(item.get("risk_type")), Cell(item.get("inherent_score"), number=True), Cell(item.get("residual_score"), number=True), Cell(item.get("residual_level")), Cell(item.get("owner")), Cell(item.get("allocation")), Cell(item.get("mitigation")), Cell(item.get("contract_clause_required"))])
        sheets.append(("Risk Register", rows, [34, 16, 16, 14, 14, 14, 20, 16, 65, 16]))
    elif run.analysis_type == "SENSITIVITY":
        rows = [_header("Driver", "Unit", "Shock", "Feasible", "Developer IRR", "Developer NPV", "Government NPV", "Funding gap")]
        for item in output.get("one_way") or []:
            summary_item = item.get("summary") or {}
            rows.append([Cell(item.get("driver")), Cell(item.get("unit")), Cell(item.get("shock"), number=True), Cell(summary_item.get("feasible")), Cell(summary_item.get("developer_irr"), style=3, number=True), Cell(summary_item.get("developer_npv"), style=2, number=True), Cell(summary_item.get("government_npv"), style=2, number=True), Cell(summary_item.get("funding_gap"), style=2, number=True)])
        sheets.append(("One-way Sensitivity", rows, [24, 14, 16, 12, 18, 20, 20, 20]))
        tornado = [_header("Driver", "Metric", "Base", "Low", "High", "Swing")]
        for item in output.get("tornado") or []:
            tornado.append([Cell(item.get("driver")), Cell(item.get("target_metric")), Cell(item.get("base_value"), number=True), Cell(item.get("low_value"), number=True), Cell(item.get("high_value"), number=True), Cell(item.get("swing"), number=True)])
        sheets.append(("Tornado", tornado, [24, 22, 20, 20, 20, 20]))
    elif run.analysis_type == "MONTE_CARLO":
        stats = [_header("Metric", "Count", "P10", "P50", "P90", "Minimum", "Maximum", "Mean")]
        for metric, item in (output.get("statistics") or {}).items():
            stats.append([Cell(metric), Cell(item.get("count"), number=True), Cell(item.get("p10"), number=True), Cell(item.get("p50"), number=True), Cell(item.get("p90"), number=True), Cell(item.get("minimum"), number=True), Cell(item.get("maximum"), number=True), Cell(item.get("mean"), number=True)])
        sheets.append(("Monte Carlo Statistics", stats, [28] + [18] * 7))
    elif run.analysis_type == "TENDER_EVALUATION":
        rows = [_header("Rank", "Bidder", "Method", "Eligible", "Financial", "Technical", "Risk & guarantees", "Integrity", "Total", "Public value", "Developer IRR", "Funding gap", "Disqualifications")]
        for item in output.get("rows") or []:
            summary_item = item.get("summary") or {}
            rows.append([Cell(item.get("rank"), number=True), Cell(item.get("bidder")), Cell(item.get("method")), Cell(item.get("eligible")), Cell(item.get("financial_score"), number=True), Cell(item.get("technical_score"), number=True), Cell(item.get("risk_guarantees_score"), number=True), Cell(item.get("integrity_score"), number=True), Cell(item.get("total_score"), number=True), Cell(item.get("bid_implied_land_value"), style=2, number=True), Cell(summary_item.get("developer_irr"), style=3, number=True), Cell(summary_item.get("funding_gap"), style=2, number=True), Cell(", ".join(item.get("disqualifications") or []))])
        sheets.append(("Bid Evaluation", rows, [10, 26, 18, 12] + [16] * 8 + [42]))
    elif run.analysis_type == "TENDER_READINESS":
        rows = [_header("Component", "Score")]
        for key, value in (output.get("components") or {}).items():
            rows.append([Cell(key), Cell(value, number=True)])
        sheets.append(("Readiness Components", rows, [36, 18]))
    elif run.analysis_type == "LANDOWNER_FAIR_SHARE":
        comparison = [_header("Method", "Status", "Measure type", "Fair floor", "Recommended", "Technical ceiling", "Eligible base", "Government value", "Government NPV", "Developer IRR", "Developer NPV", "Developer POC", "Developer multiple", "Funding gap", "Peak debt", "Interest", "Terminal deferred", "Unmodeled scope", "Mandatory shortfall")]
        for item in output.get("contract_comparison") or []:
            comparison.append([Cell(item.get("method")), Cell(item.get("status")), Cell(item.get("measure_type")), Cell(item.get("fair_floor"), number=True), Cell(item.get("recommended"), number=True), Cell(item.get("technical_ceiling"), number=True), Cell(item.get("eligible_base_total"), style=2, number=True), Cell(item.get("government_value"), style=2, number=True), Cell(item.get("government_npv"), style=2, number=True), Cell(item.get("developer_irr"), style=3, number=True), Cell(item.get("developer_npv"), style=2, number=True), Cell(item.get("developer_profit_on_cost"), style=3, number=True), Cell(item.get("developer_multiple"), number=True), Cell(item.get("peak_funding_gap"), style=2, number=True), Cell(item.get("peak_debt"), style=2, number=True), Cell(item.get("interest_total"), style=2, number=True), Cell(item.get("terminal_deferred_cost"), style=2, number=True), Cell(item.get("unmodeled_scope"), style=2, number=True), Cell(item.get("mandatory_shortfall"), style=2, number=True)])
        sheets.append(("Contract Comparison", comparison, [18, 26, 14] + [18] * 16))
        monthly = [_header("Month", "Gross contracted", "Net contracted", "Gross collections", "Net collections", "Government payment", "Planned cost", "Actual cost", "Deferred cost", "Debt draw", "Debt repayment", "Interest", "Distribution", "Developer distribution", "Landowner distribution", "Ending debt", "Ending cash")]
        for item in output.get("monthly_cashflow") or []:
            monthly.append([Cell(item.get("month"), number=True), Cell(item.get("gross_contracted_sales"), style=2, number=True), Cell(item.get("net_contracted_sales"), style=2, number=True), Cell(item.get("gross_collections"), style=2, number=True), Cell(item.get("net_collections"), style=2, number=True), Cell(item.get("government_payment"), style=2, number=True), Cell(item.get("planned_cost"), style=2, number=True), Cell(item.get("actual_cost"), style=2, number=True), Cell(item.get("deferred_cost"), style=2, number=True), Cell(item.get("financing_draw"), style=2, number=True), Cell(item.get("financing_repayment"), style=2, number=True), Cell(item.get("interest_paid"), style=2, number=True), Cell(item.get("distribution"), style=2, number=True), Cell(item.get("developer_distribution"), style=2, number=True), Cell(item.get("landowner_distribution"), style=2, number=True), Cell(item.get("ending_debt"), style=2, number=True), Cell(item.get("ending_cash"), style=2, number=True)])
        sheets.append(("Monthly Cashflow", monthly, [10] + [18] * 16))
        reasons = [_header("Constraint", "Label", "Actual", "Operator", "Threshold", "Passed", "Severity", "Reason")]
        for item in (output.get("decision_explanation") or {}).get("reasons") or []:
            reasons.append([Cell(item.get("constraint_id")), Cell(item.get("label")), Cell(item.get("actual"), number=True), Cell(item.get("operator")), Cell(item.get("threshold"), number=True), Cell(item.get("passed")), Cell(item.get("severity")), Cell(item.get("reason"))])
        sheets.append(("Decision Explanation", reasons, [28, 34, 18, 12, 18, 12, 14, 72]))
        distributions = [_header("Month", "Required reserve", "Distributed cash", "Developer", "Landowner", "Rule")]
        for item in output.get("annual_distributions") or []:
            distributions.append([Cell(item.get("month"), number=True), Cell(item.get("required_reserve"), style=2, number=True), Cell(item.get("distributable_cash"), style=2, number=True), Cell(item.get("developer_distribution"), style=2, number=True), Cell(item.get("landowner_distribution"), style=2, number=True), Cell(item.get("rule"))])
        sheets.append(("Distribution Ledger", distributions, [10, 20, 20, 20, 20, 70]))
    return build_xlsx(sheets)


def analysis_report_html(run: Any) -> str:
    output = run.output_snapshot or {}
    def esc(value: Any) -> str:
        return escape(str(value if value is not None else "—"))
    cards = []
    for key in ("score", "grade", "probability_any_constraint_failure", "probability_funding_gap", "recommended_bid_id", "recommendation"):
        if key in output:
            cards.append(f'<div class="card"><span>{esc(key.replace("_", " ").title())}</span><strong>{esc(output.get(key))}</strong></div>')
    detail_rows: list[list[Cell]] = []
    _flatten_analysis("", output, detail_rows)
    table = "".join(f"<tr><td>{esc(row[0].value)}</td><td>{esc(row[1].value)}</td></tr>" for row in detail_rows[:500])
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>LandValue360 {esc(run.analysis_type)} report</title><style>
    body{{font-family:Arial,sans-serif;margin:32px;color:#18312e}}h1{{margin-bottom:4px}}.meta{{color:#60736f}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:24px 0}}.card{{border:1px solid #d8e1df;padding:16px;border-radius:10px}}.card span{{display:block;color:#60736f;font-size:12px}}.card strong{{font-size:20px}}table{{border-collapse:collapse;width:100%;font-size:12px}}td,th{{border:1px solid #dde5e3;padding:7px;text-align:left}}th{{background:#123f3a;color:white}}.limit{{margin-top:24px;padding:12px;background:#fff7df;border:1px solid #ead39b}}
    @media print{{body{{margin:12mm}}}}</style></head><body><h1>LandValue360 Enterprise — {esc(run.analysis_type)}</h1><p class="meta">Run {esc(run.id)} · Model {esc(run.analysis_model_version)} · Output hash {esc(run.output_hash)} · {esc(run.created_at.isoformat())}</p><div class="grid">{''.join(cards)}</div><h2>Analysis detail</h2><table><thead><tr><th>Path</th><th>Value</th></tr></thead><tbody>{table}</tbody></table><div class="limit">Decision-support output. Tender awards, professional valuation, legal compliance and investment decisions require authorized professional and committee review.</div></body></html>'''
