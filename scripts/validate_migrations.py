#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_FINANCIAL_TABLES = {
    "financial_policies",
    "financial_policy_versions",
    "calculation_runs",
    "calculation_run_results",
    "monthly_cashflow_snapshots",
    "negotiation_results",
    "engine_versions",
}


def run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release_artifacts")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="lv360-migration-") as tmp:
        db_path = Path(tmp) / "fresh.db"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "app")
        env["LV360_PORTAL_ENV"] = "development"
        env["LV360_PORTAL_SECRET_KEY"] = "migration-validation-secret-key-with-sufficient-length"
        env["LV360_PORTAL_MIGRATION_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path}"
        migrated = run([sys.executable, "-m", "alembic", "upgrade", "head"], env)
        errors: list[str] = []
        if migrated.returncode != 0:
            errors.append("Fresh SQLite migration failed")
            tables: list[str] = []
            revision = None
        else:
            with sqlite3.connect(db_path) as connection:
                tables = sorted(row[0] for row in connection.execute(
                    "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
                ))
                revision_row = connection.execute("select version_num from alembic_version").fetchone()
                revision = revision_row[0] if revision_row else None
            missing = sorted(EXPECTED_FINANCIAL_TABLES - set(tables))
            if missing:
                errors.append(f"Missing financial tables: {', '.join(missing)}")
            if revision != "0007_admin_governance_and_security":
                errors.append(f"Unexpected Alembic head: {revision}")

        sqlite_report = {
            "status": "PASS" if not errors else "FAIL",
            "database": "fresh temporary SQLite",
            "alembic_head": revision,
            "table_count": len(tables),
            "required_financial_tables": sorted(EXPECTED_FINANCIAL_TABLES),
            "required_tables_present": not (EXPECTED_FINANCIAL_TABLES - set(tables)),
            "stdout_tail": migrated.stdout[-2000:],
            "stderr_tail": migrated.stderr[-2000:],
            "errors": errors,
        }
        (output_dir / "sqlite-migration-validation.json").write_text(
            json.dumps(sqlite_report, indent=2) + "\n", encoding="utf-8"
        )

    pg_env = os.environ.copy()
    pg_env["PYTHONPATH"] = str(ROOT / "app")
    pg_env["LV360_PORTAL_ENV"] = "development"
    pg_env["LV360_PORTAL_SECRET_KEY"] = "migration-validation-secret-key-with-sufficient-length"
    pg_env["LV360_PORTAL_MIGRATION_DATABASE_URL"] = "postgresql://lv360:placeholder@localhost/lv360_portal"
    pg = run([sys.executable, "-m", "alembic", "upgrade", "head", "--sql"], pg_env)
    sql_text = pg.stdout
    (output_dir / "postgresql-migration.sql").write_text(sql_text, encoding="utf-8")
    lower_sql = sql_text.lower()
    missing_sql_tables = sorted(
        table for table in EXPECTED_FINANCIAL_TABLES
        if f"create table {table}" not in lower_sql and f'create table "{table}"' not in lower_sql
    )
    pg_errors: list[str] = []
    if pg.returncode != 0:
        pg_errors.append("PostgreSQL offline migration generation failed")
    if missing_sql_tables:
        pg_errors.append(f"Offline SQL omits financial tables: {', '.join(missing_sql_tables)}")
    pg_report = {
        "status": "PASS" if not pg_errors else "FAIL",
        "mode": "PostgreSQL offline SQL generation; no live database connection",
        "alembic_target": "head",
        "required_financial_tables": sorted(EXPECTED_FINANCIAL_TABLES),
        "missing_tables_in_sql": missing_sql_tables,
        "sql_bytes": len(sql_text.encode("utf-8")),
        "stderr_tail": pg.stderr[-2000:],
        "errors": pg_errors,
    }
    (output_dir / "postgresql-migration-validation.json").write_text(
        json.dumps(pg_report, indent=2) + "\n", encoding="utf-8"
    )

    combined = {
        "status": "PASS" if sqlite_report["status"] == "PASS" and pg_report["status"] == "PASS" else "FAIL",
        "sqlite": sqlite_report,
        "postgresql": pg_report,
    }
    print(json.dumps(combined, indent=2))
    return 0 if combined["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
