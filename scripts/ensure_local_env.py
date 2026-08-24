from __future__ import annotations
import secrets
from pathlib import Path

source = Path('.env.example')
target = Path('.env')
if target.exists():
    print('.env already exists.')
    raise SystemExit(0)
text = source.read_text(encoding='utf-8')
text = text.replace('change-this-to-a-long-random-secret', secrets.token_urlsafe(64))
target.write_text(text, encoding='utf-8')
print('Created local .env with a random secret.')
