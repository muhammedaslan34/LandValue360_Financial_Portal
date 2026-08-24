# LandValue360 Platform 2.1.1 Golden Cases

These fixtures are copied without modification from the supplied LandValue360 Platform 2.1.1 release. They contain independently specified expected values for:

- 10 contract-mechanism cases.
- 4 whole-project monthly-kernel cases.

Run from the portal root:

```bash
PYTHONPATH=app python scripts/validate_golden_cases.py
```

The validator writes `release_artifacts/golden-cases-2.1.1.json` and exits non-zero on any mismatch.
