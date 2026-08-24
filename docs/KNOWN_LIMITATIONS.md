# Known Limitations - v2.5.0

- Production credentials, a live PostgreSQL database, private object-storage data, DNS records and TLS secrets are not bundled.
- Browser end-to-end execution requires Chromium with localhost access. The packaged test is optional and was skipped in the restricted build environment; API, JavaScript, live Uvicorn, PDF and Excel checks were executed separately.
- Password-reset email delivery requires configured SMTP and the notification worker. An administrator may issue a one-time temporary password when email delivery is unavailable.
- `PLATFORM_ADMIN` remains the trusted platform-owner role. A separately named `PLATFORM_OWNER` role is not introduced in this release.
- Policy versions control model assumptions and negotiation posture. Project-specific facts such as land area, product prices, actual costs, duration and the current commercial offer remain project inputs.
- Policy versions cannot disable mandatory accounting integrity rules: uncovered negative cash is prohibited; terminal debt, deferred costs and contractual arrears must be zero; monthly cash reconciliation is mandatory.
- Residual Land Value is a development-capacity indication and comparison reference, not an independent market valuation.
- User-visible scenario management, sensitivity analysis, Monte Carlo, market-comparable valuation and the separate Developer Edition remain outside this release.
- Existing historical calculation runs are immutable. Generate a new run to obtain the v2.5.0 negotiation chart and report format or to apply another policy version.
