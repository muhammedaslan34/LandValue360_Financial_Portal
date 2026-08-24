from __future__ import annotations
import json
from pathlib import Path
from landvalue360_portal.packages import portal_package_schema

out = Path('schemas/portal-submission-1.0.0.schema.json')
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(portal_package_schema(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(out)
