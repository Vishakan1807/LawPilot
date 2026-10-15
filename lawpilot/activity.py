"""Activity history for important actions."""
from __future__ import annotations

from typing import Optional

from flask import g, has_request_context

from lawpilot.db import get_db, new_id, now


def log(action: str, summary: str = "", target_type: Optional[str] = None,
        target_id: Optional[str] = None, matter_id: Optional[str] = None,
        user_id: Optional[str] = None, firm_id: Optional[str] = None) -> None:
    ip = None
    if has_request_context():
        from lawpilot.auth import client_ip
        ip = client_ip()
        if user_id is None and g.get("user"):
            user_id = g.user["id"]
        if firm_id is None and g.get("membership"):
            firm_id = g.membership["firm_id"]
    db = get_db()
    db.execute(
        "INSERT INTO activity_log (id, ts, user_id, firm_id, matter_id, action, target_type, target_id, summary, ip)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (new_id(), now(), user_id, firm_id, matter_id, action, target_type, target_id, summary[:500], ip),
    )
    db.commit()
