"""Password hashing, server-side sessions, and request guards."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Optional

from flask import current_app, g, request
from werkzeug.security import check_password_hash, generate_password_hash

from lawpilot import rbac
from lawpilot.db import get_db, new_id, now, query_one, row_to_dict

SESSION_COOKIE = "lp_session"
PASSWORD_METHOD = "pbkdf2:sha256:600000"


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ── Passwords & tokens ─────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return generate_password_hash(password, method=PASSWORD_METHOD)


def verify_password(pw_hash: str, password: str) -> bool:
    return check_password_hash(pw_hash, password)


def validate_password(password: str) -> None:
    if len(password or "") < 10:
        raise ApiError("Password must be at least 10 characters.")
    if password.isdigit() or password.isalpha():
        raise ApiError("Password must mix letters with numbers or symbols.")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


# ── Login throttling (per process; good enough for a single-worker deploy) ─

_attempts = {}
_attempts_lock = threading.Lock()
MAX_ATTEMPTS, WINDOW_SEC = 8, 15 * 60


def check_throttle(key: str) -> None:
    with _attempts_lock:
        stamps = [t for t in _attempts.get(key, []) if time.time() - t < WINDOW_SEC]
        _attempts[key] = stamps
        if len(stamps) >= MAX_ATTEMPTS:
            raise ApiError("Too many attempts. Try again in 15 minutes.", 429)


def record_failure(key: str) -> None:
    with _attempts_lock:
        _attempts.setdefault(key, []).append(time.time())


def clear_failures(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)


# ── Sessions ───────────────────────────────────────────────────────────────

def create_session(user_id: str) -> str:
    cfg = current_app.config["LP"]
    token = new_token()
    ts = now()
    expires = (datetime.now(timezone.utc) + timedelta(days=cfg.session_days)).replace(microsecond=0).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO sessions (id, token_hash, user_id, created_at, expires_at, last_seen_at, ip, user_agent)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (new_id(), token_hash(token), user_id, ts, expires, ts, client_ip(),
         (request.headers.get("User-Agent") or "")[:300]),
    )
    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (ts, user_id))
    db.commit()
    return token


def set_session_cookie(response, token: str):
    cfg = current_app.config["LP"]
    response.set_cookie(SESSION_COOKIE, token, max_age=cfg.session_days * 86400, httponly=True,
                        secure=cfg.secure_cookies, samesite="Lax", path="/")
    return response


def clear_session_cookie(response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def revoke_session(token: str) -> None:
    db = get_db()
    db.execute("UPDATE sessions SET revoked = 1 WHERE token_hash = ?", (token_hash(token),))
    db.commit()


def revoke_all_sessions(user_id: str, except_token: Optional[str] = None) -> None:
    db = get_db()
    if except_token:
        db.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ? AND token_hash != ?",
                   (user_id, token_hash(except_token)))
    else:
        db.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))
    db.commit()


def load_current_user() -> None:
    """Populate g.user / g.membership / g.perms from the session cookie."""
    load_from_token(request.cookies.get(SESSION_COOKIE))


def load_from_token(token: Optional[str]) -> None:
    g.user = None
    g.membership = None
    g.perms = set()
    if not token:
        return
    db = get_db()
    row = db.execute(
        "SELECT s.id AS sid, s.expires_at, s.last_seen_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = ? AND s.revoked = 0",
        (token_hash(token),),
    ).fetchone()
    if not row or row["expires_at"] < now() or row["status"] == "disabled":
        return
    user = row_to_dict(row)
    sid = user.pop("sid")
    user.pop("expires_at", None)
    if user.pop("last_seen_at", "")[:16] != now()[:16]:
        db.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now(), sid))
        db.commit()
    g.user = user
    g.session_token = token
    if user.get("is_platform_admin"):
        g.membership = None
        g.perms = set(rbac.PLATFORM_ADMIN_PERMISSIONS)
        return
    g.membership = query_one(
        "SELECT fm.*, f.name AS firm_name FROM firm_members fm JOIN firms f ON f.id = fm.firm_id WHERE fm.user_id = ?",
        (user["id"],),
    )
    g.perms = rbac.effective_permissions(g.membership)


def client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]).strip()


# ── Guards ─────────────────────────────────────────────────────────────────

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not g.get("user"):
            raise ApiError("Please sign in.", 401)
        return fn(*args, **kwargs)
    return wrapper


def require_perm(perm: str):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if perm not in g.perms:
                raise ApiError("Your role does not allow this action.", 403)
            return fn(*args, **kwargs)
        return wrapper
    return deco


PRACTICE_ONLY = ("Platform administrator accounts run the server and cannot hold client work. "
                 "Use a separate lawyer account for matters, firms, tasks and drafts.")


def practice_required(fn):
    """For practice features (firms, matters, tasks): not available to platform admin accounts."""
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if g.user.get("is_platform_admin"):
            raise ApiError(PRACTICE_ONLY, 403)
        return fn(*args, **kwargs)
    return wrapper


def platform_admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not g.user.get("is_platform_admin"):
            raise ApiError("Administrator access required.", 403)
        return fn(*args, **kwargs)
    return wrapper


def firm_id() -> Optional[str]:
    return g.membership["firm_id"] if g.get("membership") else None


def access_for_matter(matter: dict) -> Optional[str]:
    member = query_one("SELECT * FROM matter_members WHERE matter_id = ? AND user_id = ?",
                       (matter["id"], g.user["id"]))
    return rbac.matter_access(g.user["id"], g.membership, g.perms, matter, member)


def load_matter(matter_id: str, required: str = "view") -> dict:
    """Fetch a matter and enforce access. 404 rather than 403 to avoid leaking existence."""
    matter = query_one("SELECT * FROM matters WHERE id = ?", (matter_id,))
    if not matter:
        raise ApiError("Matter not found.", 404)
    level = access_for_matter(matter)
    if level is None:
        raise ApiError("Matter not found.", 404)
    if not rbac.at_least(level, required):
        raise ApiError("You have view-only access to this matter.", 403)
    matter["my_access"] = level
    return matter


def accessible_matter_ids() -> list:
    """All matter ids the current user can at least view (used to scope retrieval)."""
    user_id = g.user["id"]
    ids = {r["id"] for r in get_db().execute(
        "SELECT id FROM matters WHERE owner_id = ? UNION SELECT matter_id FROM matter_members WHERE user_id = ?",
        (user_id, user_id))}
    if g.membership and ({"matter.view_all", "matter.manage_all"} & g.perms):
        ids |= {r["id"] for r in get_db().execute(
            "SELECT id FROM matters WHERE firm_id = ? AND confidential = 0", (g.membership["firm_id"],))}
    return sorted(ids)
