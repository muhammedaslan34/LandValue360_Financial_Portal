# Repository Guidelines

## Project Structure & Module Organization

- `app/` contains the Python packages. `landvalue360_portal` serves FastAPI; valuation, finance, risk, kernel, government, and server logic live in sibling packages.
- `tests/` contains pytest unit, integration, security, release-contract, and browser tests; `tests/browser/` holds Playwright coverage.
- `migrations/` contains Alembic configuration and schema revisions; `schemas/` stores JSON schemas.
- `scripts/` contains preflight, validation, release, backup, and operational utilities. `docs/` contains architecture, deployment, API, and operational references.
- `data/` is for local runtime data; `dist/` and `release_artifacts/` contain release outputs.

## Build, Test, and Development Commands

Use Python 3.12 or newer. Install development dependencies with `python -m pip install -r requirements-dev.txt` (prefer a `.venv`). Start with `./START_PORTAL.sh` or `.\START_PORTAL.bat`; the scripts prepare `.env`, install locked dependencies, run checks and migrations, then serve on `127.0.0.1:8090`.

- `python -m pytest -q` - run the full pytest suite.
- `python -m pytest tests/test_calculations.py -q` - run a focused regression file.
- `python scripts/runtime_preflight.py` - verify Python, imports, storage, and engine provenance.
- `python scripts/verify_release.py` - run release validation before deployment.
- `python scripts/security_scan.py` - run the repository's static security scan.
- `alembic upgrade head` - apply database migrations when working outside the startup scripts.

## Coding Style & Naming Conventions

Follow existing Python style: four-space indentation, type hints, `snake_case` for functions/modules, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Keep financial calculations deterministic and use the project's decimal utilities. No formatter or linter is configured in `pyproject.toml`; match neighboring modules.

## Testing Guidelines

Name files `test_*.py` and test functions `test_*`. Add regression coverage for behavior or security changes, including authorization and tenant-isolation paths where relevant. Tests use SQLite/storage fixtures under `tests/.runtime`; do not commit generated files. Install Playwright if browser tests are needed.

## Commit & Pull Request Guidelines

This checkout has no `.git` metadata, so existing commit conventions cannot be verified. Use a short imperative subject such as `fix(portal): preserve policy versioning` and keep unrelated changes separate. Pull requests should explain the change, list validation commands, call out migrations/configuration changes, and include UI screenshots.

## Security & Configuration Tips

Copy `.env.example` to `.env`; never commit `.env`, credentials, private keys, or generated database/storage files. Review `docs/SECURITY_AR.md` and run the security scan before submitting changes.
