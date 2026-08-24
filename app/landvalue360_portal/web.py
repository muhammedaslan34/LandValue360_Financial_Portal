from __future__ import annotations

from pathlib import Path
from fastapi.templating import Jinja2Templates

PACKAGE_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))
