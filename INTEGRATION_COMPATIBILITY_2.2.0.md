# LandValue360 Financial Portal 2.2.0 - Platform Integration Contract

- Portal application: `2.2.0`.
- Embedded Platform monthly financial engine: `2.1.1`.
- Portal financial adapter: `2.1.0`.
- Internal export marker: `financial-portal-2.2.0`.
- Portable package format: `LANDVALUE360_PROJECT_PACKAGE 2.1.1`.
- Portal submission contract: `portal-submission-1.0.0`.
- Database migration head: `0007_admin_governance_and_security`.

The 2.2.0 release changes governance, security, administration, contextual help and client-side validation. It does not change the Platform kernel, the Portal Financial Adapter formulas, the monthly ledger contract, XNPV/XIRR definitions, residual land-value equations, or negotiation-optimizer constraints.

Financial snapshots using standalone schema 2.0.0 remain upgraded deterministically to schema 2.1.0. Portal exports include the frozen effective policy assumptions and require Platform-side recalculation for advanced Platform reports.

Administrator access to another organization's project is an audited supervisory operation. It does not transfer ownership, rewrite historical Project Versions, or alter existing Calculation Runs.
