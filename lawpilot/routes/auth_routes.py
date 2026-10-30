"""Registration, login, logout, password reset, active sessions."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from flask import Blueprint, g, jsonify

from lawpilot import account_mail, activity
from lawpilot.auth import (ApiError, check_throttle, clear_failures, clear_session_cookie, client_ip,
                           create_session, hash_password, load_from_token, login_required, new_token, record_failure,
                           revoke_all_sessions, revoke_session, set_session_cookie, token_hash,
                           validate_password, verify_password)
from lawpilot.db import get_db, new_id, now
from lawpilot.mailer import send_mail
from lawpilot.routes import body, cfg, need

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
WELCOME_HOURS = 48          # temporary password and set-password link lifetime
RESET_HOURS = 1
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
    out["must_change_password"] = bool(user.get("must_change_password"))
    out["email_verified"] = bool(user.get("email_verified_at"))
    return out


def _expiry(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _password_link(db, user_id: str, hours: int, extra: str = "") -> str:
    """One-time link to choose a password. Only the token's hash is stored."""
    token = new_token()
    db.execute("INSERT INTO password_resets (id, user_id, token_hash, created_at, expires_at) VALUES (?,?,?,?,?)",
               (new_id(), user_id, token_hash(token), now(), _expiry(hours)))
    return f"{cfg().base_url}/#/reset-password?token={token}{extra}"


def _safe_next(value) -> str:
    """Only in-app destinations (e.g. an invitation) may be carried through the welcome email."""
    v = str(value or "")
    return v if v.startswith("#/") and len(v) <= 300 and "//" not in v else ""


@bp.post("/register")
def register():
    """Create an account and email a temporary password plus a link to choose a real one.

    Nobody is signed in here: proving access to the inbox is what activates the account.
    """
    c = cfg()
    if not c.allow_registration:
        raise ApiError("Registration is disabled on this server.", 403)
    data = body()
    need(data, "email", "name")
    email = data["email"].strip().lower()
    name = data["name"].strip()[:120]
    if not EMAIL_RE.match(email):
        raise ApiError("Enter a valid email address.")
    if not c.smtp_host and not c.dev_mode:
        raise ApiError("This server cannot send email yet, so new accounts cannot receive their password. "
                       "Ask the administrator to configure email (SMTP).", 503)
    throttle_key = f"register|{client_ip()}"
    check_throttle(throttle_key, limit=c.signup_rate_limit, window=3600)
    record_failure(throttle_key)                    # every attempt counts: limits sign-up spam per address
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise ApiError("An account with this email already exists. Sign in, or use \u201cForgot password?\u201d.", 409)
    first_user = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    is_admin = 1 if (first_user or email in c.admin_emails) else 0
    temp = account_mail.temporary_password()
    ts = now()
    user_id = new_id()
    db.execute("INSERT INTO users (id, email, name, password_hash, phone, bar_enrolment, is_platform_admin, prefs,"
               " must_change_password, temp_password_expires_at, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?,?,1,?,?,?)",
               (user_id, email, name, hash_password(temp),
                (data.get("phone") or "").strip()[:30] or None, (data.get("bar_enrolment") or "").strip()[:60] or None,
                is_admin, json.dumps(DEFAULT_PREFS), _expiry(WELCOME_HOURS), ts, ts))
    nxt = _safe_next(data.get("next"))
    link = _password_link(db, user_id, WELCOME_HOURS, "&welcome=1" + (f"&next={quote(nxt, safe='')}" if nxt else ""))
    subject, text, html_body = account_mail.welcome(name, email, temp, link, WELCOME_HOURS)
    sent = send_mail(email, subject, text, html_body)
    if c.smtp_host and not sent:
        db.rollback()                               # no half-created account the user can never sign in to
        raise ApiError("We could not send the welcome email. Check the address and try again in a minute.", 502)
    db.commit()
    activity.log("account.registered", f"Account created for {email}", "user", user_id, user_id=user_id)
    resp = {"ok": True, "email": email,
            "message": f"We have emailed a temporary password and a link to set your own password to {email}. "
                       f"Both are valid for {WELCOME_HOURS} hours."}
    if not sent and c.dev_mode:                     # development only: no mail server configured
        resp["dev_temp_password"] = temp
        resp["dev_set_password_link"] = link
    return jsonify(resp), 201


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
        if row:                                     # visible to the account owner as a security signal
            activity.log("account.login_failed", "Failed sign-in attempt", "user", row["id"], user_id=row["id"])
        raise ApiError("Incorrect email or password.", 401)
    if row["status"] == "disabled":
        raise ApiError("This account has been disabled. Contact your administrator.", 403)
    if row["must_change_password"] and (row["temp_password_expires_at"] or "") < now():
        raise ApiError("Your temporary password has expired. Use \u201cForgot password?\u201d to get a new link.", 401)
    clear_failures(key)
    if row["must_change_password"] and not row["email_verified_at"]:
        db.execute("UPDATE users SET email_verified_at = ? WHERE id = ?", (now(), row["id"]))   # they read the email
        db.commit()
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
        link = _password_link(db, user["id"], RESET_HOURS)
        db.commit()
        subject, text, html_body = account_mail.reset(user["name"], link)
        sent = send_mail(email, subject, text, html_body)
        activity.log("account.password_reset_requested", "Password reset requested", "user", user["id"],
                     user_id=user["id"])
        if not sent and cfg().dev_mode:
            resp["dev_reset_link"] = link   # development only: no mail server configured
    if not cfg().smtp_host:
        resp["mail_configured"] = False
    return jsonify(resp)


@bp.post("/reset-password")
def reset_password():
    """Choose a password from an emailed link (welcome or reset). A welcome link also signs the user in."""
    data = body()
    need(data, "token", "password")
    validate_password(data["password"])
    db = get_db()
    row = db.execute("SELECT * FROM password_resets WHERE token_hash = ? AND used_at IS NULL",
                     (token_hash(data["token"]),)).fetchone()
    if not row or row["expires_at"] < now():
        raise ApiError("This link is invalid or has expired. Use \u201cForgot password?\u201d to get a new one.", 400)
    user = db.execute("SELECT id, status, must_change_password FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    if not user or user["status"] == "disabled":
        raise ApiError("This link is invalid or has expired.", 400)
    welcome = bool(user["must_change_password"])
    ts = now()
    db.execute("UPDATE users SET password_hash = ?, must_change_password = 0, temp_password_expires_at = NULL, "
               "email_verified_at = COALESCE(email_verified_at, ?), updated_at = ? WHERE id = ?",
               (hash_password(data["password"]), ts, ts, row["user_id"]))
    db.execute("UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (ts, row["user_id"]))
    db.commit()
    revoke_all_sessions(row["user_id"])
    activity.log("account.password_set" if welcome else "account.password_reset",
                 "Password chosen from the welcome email" if welcome else "Password reset via email link",
                 "user", row["user_id"], user_id=row["user_id"])
    if not welcome:
        return jsonify(ok=True)
    token = create_session(row["user_id"])          # the emailed link proved who they are
    load_from_token(token)
    return set_session_cookie(jsonify(ok=True, user=public_user(g.user)), token)


@bp.post("/set-password")
@login_required
def set_password():
    """First sign-in with a temporary password: choose a real one before anything else."""
    if not g.user.get("must_change_password"):
        raise ApiError("Your password is already set. Change it from Settings \u2192 Security.", 400)
    data = body()
    need(data, "new_password")
    validate_password(data["new_password"])
    db = get_db()
    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    if verify_password(row["password_hash"], data["new_password"]):
        raise ApiError("Choose a password different from the temporary one.")
    ts = now()
    db.execute("UPDATE users SET password_hash = ?, must_change_password = 0, temp_password_expires_at = NULL, "
               "email_verified_at = COALESCE(email_verified_at, ?), updated_at = ? WHERE id = ?",
               (hash_password(data["new_password"]), ts, ts, g.user["id"]))
    db.execute("UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (ts, g.user["id"]))
    db.commit()
    revoke_all_sessions(g.user["id"], except_token=g.session_token)
    activity.log("account.password_set", "Chose own password after signing in with the temporary one", "user",
                 g.user["id"])
    load_from_token(g.session_token)
    return jsonify(ok=True, user=public_user(g.user))


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
