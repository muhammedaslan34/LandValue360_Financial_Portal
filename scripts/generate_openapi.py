from __future__ import annotations
import json
from pathlib import Path
from landvalue360_portal.main import create_app

path = Path("release_artifacts/openapi.json")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
print(path)
