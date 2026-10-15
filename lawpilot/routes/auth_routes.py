"""Registration, login, logout, password reset, active sessions."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify

from lawpilot import activity
from lawpilot.auth import (ApiError, check_throttle, clear_failures, clear_session_cookie, client_ip,
                           create_session, hash_password, load_from_token, login_required, new_token, record_failure,
                           revoke_all_sessions, revoke_session, set_session_cookie, token_hash,
                           validate_password, verify_password)
from lawpilot.db import get_db, new_id, now
from lawpilot.mailer import send_mail
from lawpilot.routes import body, cfg, need

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DEFAULT_PREFS = {"language": "en", "default_jurisdiction": "", "default_court": "", "research_mode": "quick",
                 "exclude_overruled": False, "notify_hearings": True, "notify_tasks": True, "theme": "system"}


def public_user(user: dict) -> dict:
    from lawpilot import rbac
    prefs = {**DEFAULT_PREFS, **(user.get("prefs") or {})}
    out = {k: user.get(k) for k in ("id", "email", "name", "phone", "bar_enrolment", "status", "created_at",
                                    "last_login_at")}
    out["is_platform_admin"] = bool(user.get("is_platform_admin"))
    out["prefs"] = prefs
    m = g.get("membership")
    out["firm"] = {"id": m["firm_id"], "name": m["firm_name"], "role": m["role"],
                   "role_label": rbac.ROLES.get(m["role"], m["role"])} if m else None
    out["permissions"] = sorted(g.get("perms") or [])
    return out


@bp.post("/register")
def register():
    c = cfg()
    if not c.allow_registration:
        raise ApiError("Registration is disabled on this server.", 403)
    data = body()
    need(data, "email", "name", "password")
    email = data["email"].strip().lower()
    if not EMAIL_RE.match(email):
        raise ApiError("Enter a valid email address.")
    validate_password(data["password"])
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise ApiError("An account with this email already exists.", 409)
    first_user = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    is_admin = 1 if (first_user or email in c.admin_emails) else 0
    ts = now()
    user_id = new_id()
    db.execute("INSERT INTO users (id, email, name, password_hash, phone, bar_enrolment, is_platform_admin, prefs,"
               " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (user_id, email, data["name"].strip()[:120], hash_password(data["password"]),
                (data.get("phone") or "").strip()[:30] or None, (data.get("bar_enrolment") or "").strip()[:60] or None,
                is_admin, json.dumps(DEFAULT_PREFS), ts, ts))
    db.commit()
    activity.log("account.registered", f"Account created for {email}", "user", user_id, user_id=user_id)
    token = create_session(user_id)
    load_from_token(token)
    return set_session_cookie(jsonify(user=public_user(g.user)), token), 201


@bp.post("/login")
def login():
    data = body()
    need(data, "email", "password")
    email = data["email"].strip().lower()
    key = f"{client_ip()}|{email}"
    check_throttle(key)
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row or not verify_password(row["password_hash"], data["password"]):
        record_failure(key)
        raise ApiError("Incorrect email or password.", 401)
    if row["status"] == "disabled":
        raise ApiError("This account has been disabled. Contact your administrator.", 403)
    clear_failures(key)
    token = create_session(row["id"])
    activity.log("account.login", "Signed in", "user", row["id"], user_id=row["id"])
    load_from_token(token)
    return set_session_cookie(jsonify(user=public_user(g.user)), token)


@bp.post("/logout")
def logout():
    if g.get("user"):
        revoke_session(g.session_token)
        activity.log("account.logout", "Signed out", "user", g.user["id"])
    return clear_session_cookie(jsonify(ok=True))


@bp.get("/me")
def me():
    if not g.get("user"):
        return jsonify(user=None)
    return jsonify(user=public_user(g.user))


@bp.post("/forgot-password")
def forgot_password():
    data = body()
    need(data, "email")
    email = data["email"].strip().lower()
    check_throttle(f"reset|{client_ip()}")
    record_failure(f"reset|{client_ip()}")  # counts every request, not just failures
    db = get_db()
    user = db.execute("SELECT id, name FROM users WHERE email = ? AND status != 'disabled'", (email,)).fetchone()
    resp = {"ok": True, "message": "If an account exists for that email, a reset link has been sent."}
    if user:
        token = new_token()
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat()
        db.execute("INSERT INTO password_resets (id, user_id, token_hash, created_at, expires_at) VALUES (?,?,?,?,?)",
                   (new_id(), user["id"], token_hash(token), now(), expires))
        db.commit()
        link = f"{cfg().base_url}/#/reset-password?token={token}"
        sent = send_mail(email, "Reset your LawPilot password",
                         f"Hello {user['name']},\n\nUse this link within one hour to reset your password:\n{link}\n\n"
                         "If you did not ask for this, ignore this email.")
        activity.log("account.password_reset_requested", "Password reset requested", "user", user["id"],
                     user_id=user["id"])
        if not sent and cfg().dev_mode:
            resp["dev_reset_link"] = link   # development only: no mail server configured
    if not cfg().smtp_host:
        resp["mail_configured"] = False
    return jsonify(resp)


@bp.post("/reset-password")
def reset_password():
    data = body()
    need(data, "token", "password")
    validate_password(data["password"])
    db = get_db()
    row = db.execute("SELECT * FROM password_resets WHERE token_hash = ? AND used_at IS NULL",
                     (token_hash(data["token"]),)).fetchone()
    if not row or row["expires_at"] < now():
        raise ApiError("This reset link is invalid or has expired.", 400)
    db.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
               (hash_password(data["password"]), now(), row["user_id"]))
    db.execute("UPDATE password_resets SET used_at = ? WHERE id = ?", (now(), row["id"]))
    db.commit()
    revoke_all_sessions(row["user_id"])
    activity.log("account.password_reset", "Password reset via email link", "user", row["user_id"],
                 user_id=row["user_id"])
    return jsonify(ok=True)


@bp.post("/change-password")
@login_required
def change_password():
    data = body()
    need(data, "current_password", "new_password")
    db = get_db()
    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    if not verify_password(row["password_hash"], data["current_password"]):
        raise ApiError("Current password is incorrect.", 400)
    validate_password(data["new_password"])
    db.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
               (hash_password(data["new_password"]), now(), g.user["id"]))
    db.commit()
    revoke_all_sessions(g.user["id"], except_token=g.session_token)
    activity.log("account.password_changed", "Password changed; other sessions signed out", "user", g.user["id"])
    return jsonify(ok=True)


@bp.get("/sessions")
@login_required
def sessions():
    current = token_hash(g.session_token)
    rows = get_db().execute(
        "SELECT id, created_at, last_seen_at, expires_at, ip, user_agent, token_hash = ? AS current FROM sessions "
        "WHERE user_id = ? AND revoked = 0 AND expires_at > ? ORDER BY last_seen_at DESC",
        (current, g.user["id"], now())).fetchall()
    return jsonify(sessions=[{**dict(r), "current": bool(r["current"])} for r in rows])


@bp.delete("/sessions/<session_id>")
@login_required
def revoke(session_id):
    db = get_db()
    db.execute("UPDATE sessions SET revoked = 1 WHERE id = ? AND user_id = ?", (session_id, g.user["id"]))
    db.commit()
    activity.log("account.session_revoked", "Signed out a session", "session", session_id)
    return jsonify(ok=True)
