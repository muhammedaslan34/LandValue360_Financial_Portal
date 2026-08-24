from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("LV360_RUN_BROWSER_E2E") != "1", reason="Set LV360_RUN_BROWSER_E2E=1 to run browser E2E")


def wait_port(port: int, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("Server did not start")


def test_landowner_submission_browser():
    from playwright.sync_api import sync_playwright

    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as temp:
        port = 8099
        env = os.environ.copy()
        env.update({
            "PYTHONPATH": str(root / "app"),
            "LV360_PORTAL_DATABASE_URL": f"sqlite+pysqlite:///{Path(temp) / 'e2e.db'}",
            "LV360_PORTAL_LOCAL_STORAGE_PATH": str(Path(temp) / "private"),
            "LV360_PORTAL_AUTO_VERIFY_EMAIL": "true",
            "LV360_PORTAL_SECRET_KEY": "e2e-secret-key-long-enough-for-tests-only",
            "LV360_PORTAL_TRUSTED_HOSTS": "127.0.0.1,localhost",
        })
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "landvalue360_portal.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            wait_port(port)
            with sync_playwright() as playwright:
                executable = os.environ.get("LV360_CHROMIUM_EXECUTABLE") or shutil.which("chromium") or shutil.which("google-chrome")
                kwargs = {"headless": True}
                if executable:
                    kwargs["executable_path"] = executable
                browser = playwright.chromium.launch(**kwargs)
                page = browser.new_page(locale="ar-SA")
                page.goto(f"http://127.0.0.1:{port}/register")
                page.fill('[name="full_name"]', 'E2E User')
                page.fill('[name="email"]', 'e2e@example.com')
                page.fill('[name="organization_name"]', 'E2E Org')
                page.fill('[name="password"]', 'StrongPass123!')
                page.check('[name="accepted_terms"]')
                page.click('button[type="submit"]')
                page.wait_for_url(f"http://127.0.0.1:{port}/portal")
                page.goto(f"http://127.0.0.1:{port}/portal/projects/new")
                page.fill('[name="name"]', 'E2E Project')
                page.click('button[type="submit"]')
                page.wait_for_url(lambda url: '/portal/projects/' in url and not url.endswith('/new'))
                page.fill('[name="gross_land_area_sqm"]', '10000')
                page.fill('[name="excluded_land_area_sqm"]', '0')
                page.fill('[name="far"]', '2')
                page.fill('#productRows [data-key="unit_selling_price"]', '1000')
                page.click('#addCost')
                page.fill('#costRows [data-key="name"]', 'Construction')
                page.fill('#costRows [data-key="amount"]', '8000000')
                page.click('#saveProject')
                page.wait_for_function("document.getElementById('saveState').textContent.includes('الحفظ')")
                page.click('[data-step="documents"]')
                page.on("dialog", lambda dialog: dialog.accept())
                page.click('#submitProject')
                # Confirm dialog may already be pending depending on browser scheduling.
                page.wait_for_url(lambda url: url.endswith('/status'), timeout=15000)
                assert 'SUBMITTED' in page.content()
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
