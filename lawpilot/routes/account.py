"""Profile, preferences, data export, account deletion, activity history."""
from __future__ import annotations

import json

from flask import Blueprint, Response, g, jsonify, request

from lawpilot import activity
from lawpilot.auth import ApiError, login_required
from lawpilot.db import get_db, new_id, now, query_all, query_one
from lawpilot.routes import body, page_args
from lawpilot.routes.auth_routes import DEFAULT_PREFS, public_user

bp = Blueprint("account", __name__, url_prefix="/api/account")

PREF_KEYS = set(DEFAULT_PREFS)


@bp.get("/profile")
@login_required
def profile():
    return jsonify(user=public_user(g.user))


@bp.patch("/profile")
@login_required
def update_profile():
    data = body()
    fields = {}
    for key, limit in (("name", 120), ("phone", 30), ("bar_enrolment", 60)):
        if key in data:
            val = str(data[key] or "").strip()[:limit]
            if key == "name" and not val:
                raise ApiError("Name cannot be empty.")
            fields[key] = val or None
    if "prefs" in data and isinstance(data["prefs"], dict):
        prefs = {**DEFAULT_PREFS, **(g.user.get("prefs") or {})}
        for k, v in data["prefs"].items():
            if k in PREF_KEYS:
                prefs[k] = v
        fields["prefs"] = json.dumps(prefs)
    if not fields:
        raise ApiError("Nothing to update.")
    fields["updated_at"] = now()
    db = get_db()
    db.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
               list(fields.values()) + [g.user["id"]])
    db.commit()
    activity.log("account.profile_updated", "Updated profile / preferences", "user", g.user["id"])
    g.user = {**g.user, **{k: (json.loads(v) if k == "prefs" else v) for k, v in fields.items()}}
    return jsonify(user=public_user(g.user))


@bp.get("/export")
@login_required
def export_data():
    """Everything this user created, as one JSON file."""
    uid = g.user["id"]
    owned = query_all("SELECT * FROM matters WHERE owner_id = ?", (uid,))
    for m in owned:
        m["hearings"] = query_all("SELECT * FROM hearings WHERE matter_id = ?", (m["id"],))
        m["tasks"] = query_all("SELECT * FROM tasks WHERE matter_id = ?", (m["id"],))
        m["notes"] = query_all("SELECT * FROM notes WHERE matter_id = ?", (m["id"],))
        m["documents"] = query_all("SELECT id, title, original_filename, created_at FROM sources WHERE matter_id = ?",
                                   (m["id"],))
    data = {
        "exported_at": now(),
        "profile": public_user(g.user),
        "matters_owned": owned,
        "documents_uploaded": query_all(
            "SELECT s.id, s.title, s.original_filename, s.matter_id, s.created_at, v.original_text FROM sources s "
            "JOIN source_versions v ON v.source_id = s.id AND v.version = s.current_version WHERE s.uploaded_by = ?",
            (uid,)),
        "research_sessions": query_all("SELECT * FROM research_sessions WHERE user_id = ?", (uid,)),
        "ai_chats": [dict(c, messages=query_all("SELECT role, content, citations, attachments, model, created_at "
                                                "FROM ai_messages WHERE chat_id = ? ORDER BY created_at", (c["id"],)))
                     for c in query_all("SELECT * FROM ai_chats WHERE user_id = ?", (uid,))],
        "saved_items": query_all("SELECT * FROM saved_items WHERE user_id = ?", (uid,)),
        "drafts": query_all("SELECT * FROM drafts WHERE user_id = ?", (uid,)),
        "tasks_assigned_to_me": query_all("SELECT * FROM tasks WHERE assigned_to = ?", (uid,)),
        "notes_written": query_all("SELECT * FROM notes WHERE author_id = ?", (uid,)),
        "activity": query_all("SELECT ts, action, summary, ip FROM activity_log WHERE user_id = ? ORDER BY ts", (uid,)),
    }
    activity.log("account.data_exported", "Exported personal data", "user", uid)
    return Response(json.dumps(data, indent=2, default=str), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=lawpilot-export.json"})


@bp.post("/deletion-request")
@login_required
def request_deletion():
    db = get_db()
    m = g.get("membership")
    if m and m["role"] == "firm_admin":
        admins = db.execute("SELECT COUNT(*) FROM firm_members WHERE firm_id = ? AND role = 'firm_admin'",
                            (m["firm_id"],)).fetchone()[0]
        others = db.execute("SELECT COUNT(*) FROM firm_members WHERE firm_id = ?", (m["firm_id"],)).fetchone()[0] - 1
        if admins == 1 and others > 0:
            raise ApiError("You are the only Firm Admin. Make another member Firm Admin before requesting deletion.")
    if db.execute("SELECT 1 FROM deletion_requests WHERE user_id = ? AND status = 'pending'", (g.user["id"],)).fetchone():
        raise ApiError("A deletion request is already pending.")
    db.execute("INSERT INTO deletion_requests (id, user_id, reason, requested_at) VALUES (?,?,?,?)",
               (new_id(), g.user["id"], (body().get("reason") or "")[:1000], now()))
    db.execute("UPDATE users SET status = 'deletion_requested' WHERE id = ?", (g.user["id"],))
    db.commit()
    activity.log("account.deletion_requested", "Requested account deletion", "user", g.user["id"])
    return jsonify(ok=True, message="Deletion requested. An administrator will process it. Records your firm must "
                                    "retain (matters, documents) stay with the firm; your personal research history "
                                    "and drafts will be deleted.")


@bp.delete("/deletion-request")
@login_required
def cancel_deletion():
    db = get_db()
    db.execute("UPDATE deletion_requests SET status = 'cancelled', processed_at = ? WHERE user_id = ? AND status = 'pending'",
               (now(), g.user["id"]))
    db.execute("UPDATE users SET status = 'active' WHERE id = ?", (g.user["id"],))
    db.commit()
    activity.log("account.deletion_cancelled", "Cancelled account deletion request", "user", g.user["id"])
    return jsonify(ok=True)


@bp.get("/activity")
@login_required
def activity_history():
    """Searchable activity log. scope=firm shows the whole firm for those allowed to see it."""
    limit, offset = page_args(50)
    q = (request.args.get("q") or "").strip()
    scope = request.args.get("scope", "mine")
    clauses, params = [], []
    if scope == "firm":
        if "firm.view_activity" not in g.perms or not g.get("membership"):
            raise ApiError("Your role cannot view firm activity.", 403)
        clauses.append("a.firm_id = ?")
        params.append(g.membership["firm_id"])
    else:
        clauses.append("a.user_id = ?")
        params.append(g.user["id"])
    if q:
        clauses.append("(a.summary LIKE ? OR a.action LIKE ? OR u.name LIKE ?)")
        params += [f"%{q}%"] * 3
    if request.args.get("action"):
        clauses.append("a.action LIKE ?")
        params.append(request.args["action"] + "%")
    rows = query_all(
        f"SELECT a.ts, a.action, a.summary, a.matter_id, a.target_type, a.target_id, u.name AS user_name "
        f"FROM activity_log a LEFT JOIN users u ON u.id = a.user_id WHERE {' AND '.join(clauses)} "
        f"ORDER BY a.ts DESC LIMIT ? OFFSET ?", params + [limit, offset])
    return jsonify(items=rows)
