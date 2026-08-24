"""Deterministic constraint-remediation search for LandValue360 1.0.0.

The solver does not optimize an opaque objective and never mutates a saved
project.  It reruns the authoritative calculation through a supplied evaluator
and records the smallest tested single-lever change that closes all mandatory
calculation and finance constraints.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any, Callable

CONSTRAINT_SOLVER_VERSION = "0.4.0"


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _passes(output: dict[str, Any]) -> bool:
    truth = output.get("financial_truth") or ((output.get("unified_financial_result") or {}).get("financial_truth") or {})
    invariants = output.get("engine_invariants") or ((output.get("unified_financial_result") or {}).get("engine_invariants") or {})
    if truth:
        if truth.get("status") != "PASS" or not truth.get("feasible"):
            return False
        if invariants and not invariants.get("passed"):
            return False
        return output.get("status") in {"SUCCESS", "SUCCESS_WITH_WARNINGS"}
    if invariants and not invariants.get("passed"):
        return False
    decision = output.get("decision_explanation") or {}
    if decision.get("status") == "FAIL":
        return False
    if output.get("status") not in {"SUCCESS", "SUCCESS_WITH_WARNINGS"}:
        return False
    approved = output.get("approved_case") or {}
    if any(item.get("mandatory", True) and not item.get("passed") for item in approved.get("constraints") or []):
        return False
    finance = output.get("finance_analysis") or {}
    if any(
        item.get("mandatory", True)
        and item.get("status") != "NOT_CONFIGURED"
        and not item.get("passed")
        for item in finance.get("constraints") or []
    ):
        return False
    return True


def _metric_summary(output: dict[str, Any]) -> dict[str, Any]:
    truth = output.get("financial_truth") or ((output.get("unified_financial_result") or {}).get("financial_truth") or {})
    if truth:
        return {
            "developer_irr": truth.get("developer_irr"),
            "developer_npv": truth.get("developer_npv"),
            "developer_profit": truth.get("developer_profit"),
            "funding_gap": truth.get("funding_gap"),
            "equity_irr": truth.get("developer_equity_irr"),
            "structured_funding_gap": truth.get("funding_gap"),
            "deferred_development_cost": truth.get("deferred_development_cost"),
            "terminal_debt_balance": truth.get("terminal_debt"),
            "calculation_hash": truth.get("calculation_hash"),
        }
    approved = output.get("approved_case") or {}
    metrics = approved.get("metrics") or {}
    finance = output.get("finance_analysis") or {}
    fmetrics = finance.get("metrics") or {}
    return {
        "developer_irr": metrics.get("developer_irr"),
        "developer_npv": metrics.get("developer_npv"),
        "developer_profit": metrics.get("developer_profit"),
        "funding_gap": metrics.get("funding_gap"),
        "equity_irr": fmetrics.get("developer_equity_irr"),
        "structured_funding_gap": fmetrics.get("structured_funding_gap"),
        "deferred_development_cost": fmetrics.get("deferred_development_cost"),
        "terminal_debt_balance": fmetrics.get("terminal_debt_balance"),
    }




def _project_targets(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every project representation that drives a governed recalculation.

    v0.9 envelopes retain both the normalized kernel project and the original
    governed project snapshot used by the unified monthly engine.  Solver levers must update
    both representations or the legacy and authoritative paths would evaluate
    different assumptions.
    """

    targets: list[dict[str, Any]] = []
    core = candidate.get("project")
    if isinstance(core, dict):
        targets.append(core)
    governed = (candidate.get("application_extensions") or {}).get("governed_project_snapshot")
    if isinstance(governed, dict) and governed is not core:
        targets.append(governed)
    return targets


def _finance_model_targets(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for project in _project_targets(candidate):
        model = project.setdefault("finance_model", {})
        if model not in targets:
            targets.append(model)
    extension_model = (candidate.setdefault("application_extensions", {})
                       .setdefault("project", {})
                       .setdefault("finance_model", {}))
    if extension_model not in targets:
        targets.append(extension_model)
    return targets

def _trace_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(encoded).hexdigest()


def _binary_search(
    *,
    lower: Decimal,
    upper: Decimal,
    increasing_helps: bool,
    apply_value: Callable[[dict[str, Any], Decimal], None],
    envelope: dict[str, Any],
    evaluate: Callable[[dict[str, Any]], dict[str, Any]],
    iterations: int = 12,
) -> tuple[Decimal, dict[str, Any]] | None:
    """Find the smallest change from the baseline-side bound that passes."""

    trial_bound = deepcopy(envelope)
    apply_value(trial_bound, upper if increasing_helps else lower)
    bound_output = evaluate(trial_bound)
    if not _passes(bound_output):
        return None

    lo, hi = lower, upper
    best_value = upper if increasing_helps else lower
    best_output = bound_output
    for _ in range(iterations):
        mid = (lo + hi) / Decimal("2")
        candidate = deepcopy(envelope)
        apply_value(candidate, mid)
        result = evaluate(candidate)
        passed = _passes(result)
        if passed:
            best_value, best_output = mid, result
            if increasing_helps:
                hi = mid
            else:
                lo = mid
        else:
            if increasing_helps:
                lo = mid
            else:
                hi = mid
    return best_value, best_output


def solve_constraints(
    envelope: dict[str, Any],
    base_output: dict[str, Any],
    *,
    evaluate: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Return ranked single-lever remedies verified by full recalculation."""

    if _passes(base_output):
        return {
            "constraint_solver_version": CONSTRAINT_SOLVER_VERSION,
            "status": "NOT_REQUIRED",
            "suggestions": [],
            "explanation_ar": "لا توجد قيود إلزامية فاشلة تستلزم حلاً حسابياً.",
            "explanation_en": "No failed mandatory constraint requires a calculated remedy.",
        }
    if base_output.get("status") == "FAILED" and not base_output.get("approved_case"):
        return {
            "constraint_solver_version": CONSTRAINT_SOLVER_VERSION,
            "status": "NOT_CALCULABLE",
            "suggestions": [],
            "explanation_ar": "يجب تصحيح أخطاء المدخلات أولاً قبل البحث عن تعديلات الجدوى.",
            "explanation_en": "Input errors must be corrected before feasibility remedies can be searched.",
        }

    project = envelope.get("project") or {}
    policy = envelope.get("policy") or {}
    extensions = envelope.get("application_extensions") or {}
    pext = extensions.get("project") or {}
    suggestions: list[dict[str, Any]] = []

    def add(
        *, lever: str, title_ar: str, title_en: str, current: Decimal | str,
        required: Decimal | str, unit: str, output: dict[str, Any], normalized_impact: Decimal,
        rationale_ar: str, rationale_en: str,
    ) -> None:
        payload = {
            "lever": lever,
            "current_value": str(current),
            "required_value": str(required),
            "unit": unit,
            "resulting_metrics": _metric_summary(output),
        }
        suggestions.append({
            "rank": 0,
            "lever": lever,
            "title_ar": title_ar,
            "title_en": title_en,
            "current_value": str(current),
            "required_value": str(required),
            "delta": str(_d(required) - _d(current)),
            "relative_change": str(normalized_impact),
            "unit": unit,
            "solves_all_constraints": True,
            "resulting_metrics": payload["resulting_metrics"],
            "rationale_ar": rationale_ar,
            "rationale_en": rationale_en,
            "trace_hash": _trace_hash(payload),
        })

    # 1. Landowner share. Freeze selection to MANUAL for each candidate.
    partnership = project.get("partnership") or {}
    current_share = _d(partnership.get("manual_share", partnership.get("share_rate", "0.10")))
    min_share = _d((policy.get("share_policy") or {}).get("policy_minimum_share", "0"))

    def set_share(candidate: dict[str, Any], value: Decimal) -> None:
        for target_project in _project_targets(candidate):
            part = target_project.setdefault("partnership", {})
            part["approved_selection"] = "MANUAL"
            part["approved_share_source"] = "MANUAL"
            part["manual_share"] = str(value)
            part["share_rate"] = str(value)

    if current_share > min_share:
        found = _binary_search(
            lower=min_share, upper=current_share, increasing_helps=False,
            apply_value=set_share, envelope=envelope, evaluate=evaluate,
        )
        if found:
            value, result = found
            add(
                lever="LANDOWNER_SHARE", title_ar="خفض حصة مالك الأرض", title_en="Reduce landowner share",
                current=current_share, required=value, unit="% of selected basis", output=result,
                normalized_impact=(current_share - value) / max(current_share, Decimal("0.000001")),
                rationale_ar="أعلى حصة قريبة من الحالية اجتازت جميع القيود بعد إعادة الحساب الكامل.",
                rationale_en="The nearest lower share that passed all constraints under full recalculation.",
            )

    # 2. Sales price multiplier.
    def set_price_multiplier(candidate: dict[str, Any], multiplier: Decimal) -> None:
        for target_project in _project_targets(candidate):
            for item in target_project.get("products") or []:
                item["unit_price"] = str(_d(item.get("unit_price")) * multiplier)

    found = _binary_search(
        lower=Decimal("1"), upper=Decimal("1.50"), increasing_helps=True,
        apply_value=set_price_multiplier, envelope=envelope, evaluate=evaluate,
    )
    if found:
        value, result = found
        add(
            lever="SALES_PRICE", title_ar="رفع متوسط أسعار البيع", title_en="Increase average sales prices",
            current="1", required=value, unit="multiplier", output=result,
            normalized_impact=value - Decimal("1"),
            rationale_ar="أقل مضاعف موحد لأسعار المنتجات اجتاز جميع القيود في الاختبار.",
            rationale_en="The smallest uniform product-price multiplier that passed every tested constraint.",
        )

    # 3. Development-cost multiplier. Apply to every supported cost method.
    def set_cost_multiplier(candidate: dict[str, Any], multiplier: Decimal) -> None:
        for target_project in _project_targets(candidate):
            for item in target_project.get("costs") or []:
                method = str(item.get("calculation_method") or "LEGACY_QUANTITY_X_RATE").upper()
                if method in {"FIXED_AMOUNT", "MANUAL_AMOUNT"}:
                    item["fixed_amount"] = str(_d(item.get("fixed_amount", item.get("unit_cost"))) * multiplier)
                elif method == "PERCENT_OF_COST":
                    item["percentage_rate"] = str(_d(item.get("percentage_rate")) * multiplier)
                else:
                    item["unit_cost"] = str(_d(item.get("unit_cost")) * multiplier)

    found = _binary_search(
        lower=Decimal("0.70"), upper=Decimal("1"), increasing_helps=False,
        apply_value=set_cost_multiplier, envelope=envelope, evaluate=evaluate,
    )
    if found:
        value, result = found
        add(
            lever="DEVELOPMENT_COST", title_ar="خفض كلف التطوير", title_en="Reduce development costs",
            current="1", required=value, unit="multiplier", output=result,
            normalized_impact=Decimal("1") - value,
            rationale_ar="أعلى مضاعف كلفة قريب من الحالة الحالية اجتاز جميع القيود.",
            rationale_en="The closest lower cost multiplier that passed all constraints.",
        )

    # 4. Manual equity. This suggestion remains useful even where the current
    # policy is fixed; the title states that a policy switch is also required.
    base_equity = _d((project.get("funding") or {}).get("committed_equity", "0"))
    total_cost = _d((base_output.get("approved_case") or {}).get("metrics", {}).get("developer_total_cost_including_land_consideration", "0"))
    upper_equity = max(base_equity, total_cost * Decimal("0.75"), Decimal("1"))

    def set_equity(candidate: dict[str, Any], amount: Decimal) -> None:
        for target_project in _project_targets(candidate):
            target_project.setdefault("funding", {})["committed_equity"] = str(amount)
        # The composed kernel envelope already contains the fixed-policy
        # fallback. An explicit project commitment overrides that fallback in
        # the frozen kernel, which is numerically equivalent to testing Manual.

    # Only test equity when composed extension can be interpreted by evaluator.
    try:
        found = _binary_search(
            lower=base_equity, upper=upper_equity, increasing_helps=True,
            apply_value=set_equity, envelope=envelope, evaluate=evaluate,
        )
    except Exception:  # The solver must never break an official calculation.
        found = None
    if found:
        value, result = found
        add(
            lever="COMMITTED_EQUITY", title_ar="التحول إلى Equity Manual ورفع الالتزام", title_en="Switch to Manual Equity and increase commitment",
            current=base_equity, required=value, unit=str(project.get("reporting_currency") or ""), output=result,
            normalized_impact=(value - base_equity) / max(total_cost, Decimal("1")),
            rationale_ar="أقل التزام حقوق ملكية اجتاز القيود بعد تطبيق وضع Manual وإعادة الحساب.",
            rationale_en="The lowest manual equity commitment that passed after full recalculation.",
        )

    # 5. Senior commitment.
    base_debt = _d((project.get("funding") or {}).get("committed_financing", "0"))
    upper_debt = max(base_debt, total_cost, Decimal("1"))

    def set_debt(candidate: dict[str, Any], amount: Decimal) -> None:
        for target_project in _project_targets(candidate):
            target_project.setdefault("funding", {})["committed_financing"] = str(amount)

    found = _binary_search(
        lower=base_debt, upper=upper_debt, increasing_helps=True,
        apply_value=set_debt, envelope=envelope, evaluate=evaluate,
    )
    if found:
        value, result = found
        add(
            lever="COMMITTED_FINANCING", title_ar="رفع التمويل الملتزم", title_en="Increase committed financing",
            current=base_debt, required=value, unit=str(project.get("reporting_currency") or ""), output=result,
            normalized_impact=(value - base_debt) / max(total_cost, Decimal("1")),
            rationale_ar="أقل التزام تمويلي اختباري أغلق القيود مع بقاء الشروط الأخرى ثابتة.",
            rationale_en="The lowest tested financing commitment that closed all constraints with other assumptions unchanged.",
        )

    # 6. Spend-policy switch, only if it produces a complete PASS rather than
    # hiding cost as a deferred balance.
    current_spend = str((pext.get("finance_model") or {}).get("spend_policy") or "LEGACY/SCHEDULE_DRIVEN")
    for mode in ("CASH_DRIVEN", "HYBRID"):
        candidate = deepcopy(envelope)
        for finance_model in _finance_model_targets(candidate):
            finance_model["spend_policy"] = mode
            finance_model["allow_negative_cash"] = False
        result = evaluate(candidate)
        if _passes(result):
            add(
                lever="SPEND_POLICY", title_ar=f"اعتماد سياسة الصرف {mode}", title_en=f"Use {mode} spend policy",
                current=current_spend, required=mode, unit="policy", output=result,
                normalized_impact=Decimal("0.20"),
                rationale_ar="تم قبول الاقتراح فقط لأن إعادة الحساب اكتملت دون فجوة أو كلفة مؤجلة.",
                rationale_en="Suggested only because recalculation passed without a funding gap or deferred terminal cost.",
            )
            break

    suggestions.sort(key=lambda item: (_d(item["relative_change"], "999"), item["lever"]))
    for index, item in enumerate(suggestions, 1):
        item["rank"] = index
    return {
        "constraint_solver_version": CONSTRAINT_SOLVER_VERSION,
        "status": "SOLUTIONS_FOUND" if suggestions else "NO_SINGLE_LEVER_SOLUTION",
        "suggestions": suggestions,
        "explanation_ar": (
            "كل اقتراح هو أقل تعديل أحادي تم التحقق منه بإعادة تشغيل المحرك الكامل. لا يعني ذلك أنه القرار التجاري الأفضل؛ يجب اختبار حزمة تفاوضية واعتمادها."
            if suggestions else
            "لم يجد البحث ضمن الحدود المحافظة تعديلاً أحادياً يغلق جميع القيود. يلزم حل مركب أو تغيير نطاق المشروع."
        ),
        "explanation_en": (
            "Each suggestion is the smallest verified single-lever change found by rerunning the full engine. It is not necessarily the best commercial decision; a combined approved case must be tested."
            if suggestions else
            "No conservative single-lever change closed every constraint. A combined solution or scope change is required."
        ),
        "search_limits": {
            "sales_price_multiplier_max": "1.50",
            "development_cost_multiplier_min": "0.70",
            "binary_search_iterations": 12,
            "single_lever_only": True,
        },
    }
