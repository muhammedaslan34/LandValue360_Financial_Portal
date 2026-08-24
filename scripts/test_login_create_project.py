"""Login locally and create a test project for manual QA."""
from __future__ import annotations

import os
import json
import sys
from urllib import error, request

BASE = os.environ.get("LV360_BASE_URL", "http://127.0.0.1:8090")
EMAIL = os.environ.get("LV360_TEST_EMAIL", "muhammadaslan201999@gmail.com")
PASSWORD = os.environ["LV360_TEST_PASSWORD"]
PROJECT_NAME = os.environ.get("LV360_TEST_PROJECT_NAME", "مشروع اختبار SIA — دمشق")


def http(method: str, path: str, body: dict | None = None, headers: dict | None = None, cookie: str | None = None):
    data = None
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if cookie:
        hdrs["Cookie"] = cookie
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = request.Request(f"{BASE}{path}", data=data, headers=hdrs, method=method)
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            set_cookie = resp.headers.get("Set-Cookie")
            return resp.status, json.loads(raw) if raw else {}, set_cookie
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return exc.code, payload, exc.headers.get("Set-Cookie")


def main() -> int:
    status, login_data, set_cookie = http("POST", "/api/auth/login", {"email": EMAIL, "password": PASSWORD})
    if status != 200:
        print("LOGIN_FAILED", status, login_data)
        return 1

    session_cookie = (set_cookie or "").split(";")[0]
    csrf = login_data.get("csrf_token")
    if not session_cookie or not csrf:
        print("LOGIN_MISSING_SESSION", login_data)
        return 1

    print("LOGIN_OK", login_data.get("redirect", "/portal"))

    status, me, _ = http("GET", "/api/auth/me", cookie=session_cookie)
    if status != 200:
        print("ME_FAILED", status, me)
        return 1

    org_id = None
    # Resolve org from portal new-project page organizations via DB-less approach:
    # list user's organizations from /portal page is not an API; query via sqlite helper below.
    import sqlite3
    from pathlib import Path

    db_path = Path(__file__).resolve().parents[1] / "data" / "portal.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    user_row = conn.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (EMAIL,)).fetchone()
    org_row = conn.execute(
        "SELECT o.id, o.name FROM organizations o "
        "JOIN organization_members m ON m.organization_id=o.id "
        "WHERE m.user_id=? AND m.status='ACTIVE' LIMIT 1",
        (user_row["id"],),
    ).fetchone()
    conn.close()
    if not org_row:
        print("NO_ORG_FOR_USER")
        return 1
    org_id = org_row["id"]
    print("ORG", org_row["name"], org_id)

    status, project, _ = http(
        "POST",
        "/api/projects",
        {
            "organization_id": org_id,
            "name": PROJECT_NAME,
            "description": "مشروع تجريبي لاختبار الحقول والقوائم المنسدلة محلياً.",
            "currency": "USD",
        },
        headers={"X-CSRF-Token": csrf},
        cookie=session_cookie,
    )
    if status != 201:
        print("CREATE_FAILED", status, project)
        return 1

    project_id = project.get("id")
    ref = project.get("reference")
    print("PROJECT_CREATED", ref, project_id)
    print("OPEN", f"{BASE}/portal/projects/{project_id}")
    print("FINANCIAL", f"{BASE}/portal/projects/{project_id}/financial")
    return 0


if __name__ == "__main__":
    if not os.environ.get("LV360_TEST_PASSWORD"):
        print("Set LV360_TEST_PASSWORD environment variable first.", file=sys.stderr)
        sys.exit(2)
    sys.exit(main())
