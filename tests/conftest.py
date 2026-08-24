import os
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent / ".runtime"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["LV360_PORTAL_DATABASE_URL"] = f"sqlite+pysqlite:///{TEST_ROOT / 'test.db'}"
os.environ["LV360_PORTAL_LOCAL_STORAGE_PATH"] = str(TEST_ROOT / "private")
os.environ["LV360_PORTAL_AUTO_VERIFY_EMAIL"] = "true"
os.environ["LV360_PORTAL_SECRET_KEY"] = "test-secret-key-that-is-long-enough"
os.environ["LV360_PORTAL_TRUSTED_HOSTS"] = "testserver,127.0.0.1,localhost"

import pytest
from fastapi.testclient import TestClient
from landvalue360_portal import models
from landvalue360_portal.database import Base, engine
from landvalue360_portal.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    storage = TEST_ROOT / "private"
    if storage.exists():
        for p in sorted(storage.rglob("*"), reverse=True):
            if p.is_file(): p.unlink()
            elif p.is_dir(): p.rmdir()
    with TestClient(app) as c:
        yield c
