# LandValue360 Financial Portal 2.1.0 - Platform Integration Contract

- Portal application: `2.1.0`.
- Portal financial adapter: `2.1.0`.
- Embedded deterministic kernel: LandValue360 Platform `2.1.1`.
- Engine source hash: `68a5b5ee9583516a0b7b1ab6004fcd13b0c55481f5668f09e55258b51325e542`.
- Export source marker: `financial-portal-2.1.0`.
- Portable package: `LANDVALUE360_PROJECT_PACKAGE` format `2.1.1`.
- Core parity: `104/104` vendored Python files matched; Platform database migrations are intentionally omitted.
- Portal calculations are server-side and frozen by Project Version, Policy Version, Engine Version, Input Hash and Result Hash.
- Standard-user API responses redact advanced financing, curve and collection inputs; authorized analysts retain full project-specific controls.
- Only a financial-policy administrator may override the current policy or engine version for a Calculation Run.
- The Platform remains the advanced-engine and full-reporting reference and recalculates imported packages from their effective frozen assumptions.
- Financial input snapshots from schema `2.0.0` are upgraded deterministically to `2.1.0`, including corrected total-equity semantics.
