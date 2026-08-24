# LandValue360 Financial Portal 2.2.0

## Release purpose

Version 2.2.0 is the launch-baseline governance and usability release built on the validated 2.1 financial model. It preserves the Platform 2.1.1 deterministic monthly kernel and Portal Financial Adapter 2.1.0 while adding owner-level administration, audited cross-organization access, account recovery controls, accessible bilingual contextual help, and cleaner project autosave validation.

## Added

- Global `PLATFORM_ADMIN` project registry across all organizations and users.
- Direct administrator access to project inputs, financial dashboards, calculation runs, uploaded reports, documents, PDF, Excel and project-package exports.
- Audited administrator access banners without changing project ownership.
- User activity view covering memberships, sessions, login attempts and audit events.
- Secure password-reset link workflow.
- One-time temporary-password workflow with mandatory password change on first login.
- Administrator session revocation, account suspension and reactivation.
- Server-enforced restricted state while a temporary password must be changed.
- Bilingual contextual-help glossary for financial indicators and essential project inputs.
- Accessible tooltip/popover behavior for mouse, keyboard and touch devices.
- Financial indicator guide in the printable PDF report.
- Alembic migration `0007_admin_governance_and_security`.

## Improved

- Project autosave now waits for a schema-valid draft and no longer sends incomplete numeric values during editing.
- API validation errors identify the affected field instead of exposing only a generic HTTP 422 message.
- Administration is organized around users, organizations, memberships, projects, reports, activity and financial policy.
- Project and financial pages disclose when an administrator is reviewing another user's project.
- Standard-user inputs remain simple; financing, sales/cost curves, collection timing and advanced model controls remain policy-managed and restricted to authorized analyst/administrator roles.
- Complete financial outputs remain available to the user, including project, developer, landowner, residual-value, negotiation and cash-flow results.

## Financial model compatibility

- Embedded Platform engine: `2.1.1`.
- Portal financial adapter: `2.1.0`.
- Financial input/result schemas: unchanged at `2.1.0`.
- No formula, ledger, XNPV, XIRR, residual-value or optimization equation was changed by this release.
- Existing immutable Calculation Runs remain reproducible against their original Project, Policy and Engine versions.
