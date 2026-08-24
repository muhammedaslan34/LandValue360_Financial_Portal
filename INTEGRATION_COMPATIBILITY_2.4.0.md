# LandValue360 Financial Portal 2.4.0 - Platform Integration Contract

- Portal application: `2.4.0`
- Platform monthly kernel: `2.1.1`
- Portal adapter: `2.4.0`
- Contract semantics: `3.1.0`
- Internal export marker: `financial-portal-2.4.0`
- Native package format: `LANDVALUE360_PROJECT_PACKAGE 2.1.1`

The vendored Platform core remains byte-for-byte identical to the supplied 2.1.1 baseline. Policy selection, policy-positioned negotiation and version lifecycle are implemented in the portal adapter and service layers. Exported packages carry the effective frozen assumptions of the selected Policy Version; the advanced Platform recalculates them before producing its own reports.
