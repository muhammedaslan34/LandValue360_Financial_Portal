from __future__ import annotations
from landvalue360_portal.database import session_scope
from landvalue360_portal.notifications import deliver_pending

with session_scope() as db:
    count = deliver_pending(db, limit=100)
print(f"Delivered notifications: {count}", flush=True)
