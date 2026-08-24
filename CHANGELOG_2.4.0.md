# LandValue360 Financial Portal v2.4.0

## Financial corrections

- Separated **Fair Floor**, **Balanced**, **Policy-Adjusted Ceiling**, **Residual Equivalent**, **Current Offer**, and **Technical Ceiling** as distinct negotiation references.
- Recalibrated the Balanced recommendation against the legacy-platform reference project: 9.5%-10.0% Fair Floor, 12.3% Balanced, 14.1% policy-adjusted ceiling, 16.58% residual equivalent, 18% current offer, and 19.9% technical ceiling.
- Preserved Contract Engine 3.x semantics: Gross Sales Share applies only to gross collections; Net Sales Share applies only to eligible net collections after sales-side deductions; Profit Share applies only to distributable cash profit.
- Added explicit distinction between a true Technical Ceiling and an administrator-defined policy search cap.
- Added Residual Land Value as a monetary and equivalent-rate comparison marker without treating it as an independent market valuation or contractual entitlement.
- Re-runs the monthly financial model for each negotiation candidate and rejects candidates that fail profitability, return, liquidity, payment, close-out, or reconciliation constraints.

## Versioned financial policy library

- Administrators can clone, edit, publish, activate, archive, and republish immutable financial policy versions.
- Published versions may be marked user-selectable; standard users choose a policy before running the analysis.
- Every Calculation Run permanently freezes the selected Project Version, Policy Version, Engine Version, Input Hash, and Result Hash.
- Historical runs remain linked to their original policy even if the policy is later archived.
- v2.3 policies are materialized into explicit v2.4 policy snapshots on upgrade; the historical source version remains preserved.
- All policy-governed assumptions are editable by the administrator: discount and return thresholds, landowner recovery rules, allowed contracts, negotiation positioning, financing, liquidity, sales/cost curves, collection rules, spending limits, distributions, and solver controls.
- Core integrity invariants remain locked: no uncovered negative cash, zero terminal debt, zero deferred development cost, zero contractual arrears, and mandatory monthly cash reconciliation.

## User experience and reporting

- Standard users retain simple project inputs while receiving the complete financial feasibility analysis.
- Analysts and administrators retain controlled access to advanced financial inputs.
- Financial policy selection is shown before analysis and in every result/report.
- Negotiation charts and tables include policy ceiling and residual comparison markers.
- Arabic and English labels were completed for new negotiation and policy controls.
- PDF and Excel reports identify the selected immutable policy version and the contract calculation basis.

## Validation

- 51 Python tests passed; 1 optional browser test skipped by environment flag.
- Release Gate: 34/34 checks passed.
- Platform golden cases: 14/14 passed.
- Platform core parity: 104/104 files matched; 0 changed, missing, or extra.
- Contract scenario matrix: 8 projects, 144 candidate points, 513 independent audit checks, all passed.
- Policy/negotiation end-to-end matrix: 49/49 assertions across 10 portal scenarios, all passed.
- SQLite migration, PostgreSQL offline migration, wheel installation, live HTTP smoke, PDF/Excel validation, and static security scan passed.
