"""Matters (cases/files), their team, hearings, tasks, notes and documents."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from lawpilot import activity, rbac
from lawpilot.auth import (ApiError, access_for_matter, accessible_matter_ids, load_matter, login_required,
                           practice_required, require_perm)
from lawpilot.db import get_db, new_id, now, query_all, query_one
from lawpilot.extract import ExtractionError, extract
from lawpilot.ingestion import delete_source, ingest
from lawpilot.mailer import send_mail
from lawpilot.routes import body, cfg, need

bp = Blueprint("matters", __name__, url_prefix="/api")

MATTER_FIELDS = {"title": 200, "client_name": 200, "case_number": 120, "case_type": 80, "court": 160,
                 "jurisdiction": 80, "opposing_party": 200, "stage": 80, "description": 4000}
STATUSES = ("open", "closed", "archived")


def _clean_fields(data: dict) -> dict:
    out = {}
    for k, limit in MATTER_FIELDS.items():
        if k in data:
            out[k] = (str(data[k] or "").strip()[:limit]) or None
    if "status" in data:
        if data["status"] not in STATUSES:
            raise ApiError("Invalid status.")
        out["status"] = data["status"]
    if "confidential" in data:
        out["confidential"] = 1 if data["confidential"] else 0
    return out


def _refresh_next_hearing(db, matter_id: str) -> None:
    today = now()[:10]
    row = db.execute("SELECT MIN(date) FROM hearings WHERE matter_id = ? AND date >= ?", (matter_id, today)).fetchone()
    db.execute("UPDATE matters SET next_hearing = ?, updated_at = ? WHERE id = ?", (row[0], now(), matter_id))


@bp.get("/matters")
@practice_required
def list_matters():
    ids = accessible_matter_ids()
    if not ids:
        return jsonify(matters=[])
    clauses = [f"m.id IN ({','.join('?' * len(ids))})"]
    params = list(ids)
    status = request.args.get("status", "open")
    if status != "all":
        clauses.append("m.status = ?")
        params.append(status)
    q = (request.args.get("q") or "").strip()
    if q:
        clauses.append("(m.title LIKE ? OR m.client_name LIKE ? OR m.case_number LIKE ? OR m.court LIKE ? "
                       "OR m.opposing_party LIKE ?)")
        params += [f"%{q}%"] * 5
    rows = query_all(
        f"SELECT m.*, u.name AS owner_name, "
        f"(SELECT COUNT(*) FROM sources s WHERE s.matter_id = m.id) AS document_count, "
        f"(SELECT COUNT(*) FROM tasks t WHERE t.matter_id = m.id AND t.status = 'open') AS open_tasks "
        f"FROM matters m JOIN users u ON u.id = m.owner_id WHERE {' AND '.join(clauses)} "
        f"ORDER BY CASE WHEN m.next_hearing IS NULL THEN 1 ELSE 0 END, m.next_hearing, m.updated_at DESC", params)
    for r in rows:
        r["my_access"] = access_for_matter(r)
    return jsonify(matters=rows)


@bp.post("/matters")
@practice_required
@require_perm("matter.create")
def create_matter():
    data = body()
    need(data, "title")
    fields = _clean_fields(data)
    db = get_db()
    mid = new_id()
    ts = now()
    firm_id = g.membership["firm_id"] if g.get("membership") else None
    cols = ["id", "firm_id", "owner_id", "created_at", "updated_at"] + list(fields)
    vals = [mid, firm_id, g.user["id"], ts, ts] + list(fields.values())
    db.execute(f"INSERT INTO matters ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
    db.execute("INSERT INTO matter_members (matter_id, user_id, access, added_at) VALUES (?,?,?,?)",
               (mid, g.user["id"], "lead", ts))
    db.commit()
    activity.log("matter.created", f"Created matter '{fields['title']}'", "matter", mid, matter_id=mid)
    return jsonify(id=mid), 201


@bp.get("/matters/<matter_id>")
@login_required
def get_matter(matter_id):
    m = load_matter(matter_id)
    m["members"] = query_all("SELECT u.id, u.name, u.email, mm.access FROM matter_members mm JOIN users u "
                             "ON u.id = mm.user_id WHERE mm.matter_id = ? ORDER BY u.name", (matter_id,))
    m["hearings"] = query_all("SELECT * FROM hearings WHERE matter_id = ? ORDER BY date DESC", (matter_id,))
    m["tasks"] = query_all("SELECT t.*, a.name AS assignee_name, c.name AS creator_name FROM tasks t "
                           "LEFT JOIN users a ON a.id = t.assigned_to JOIN users c ON c.id = t.created_by "
                           "WHERE t.matter_id = ? ORDER BY t.status, t.due_date IS NULL, t.due_date", (matter_id,))
    m["notes"] = query_all("SELECT n.*, u.name AS author_name FROM notes n JOIN users u ON u.id = n.author_id "
                           "WHERE n.matter_id = ? ORDER BY n.created_at DESC", (matter_id,))
    m["documents"] = query_all("SELECT id, title, original_filename, kind, mime, page_count, status, error, "
                               "security_flags, current_version, created_at, uploaded_by FROM sources "
                               "WHERE matter_id = ? ORDER BY created_at DESC", (matter_id,))
    m["research"] = query_all("SELECT id, query, mode, outcome, created_at FROM research_sessions WHERE matter_id = ? "
                              "ORDER BY created_at DESC LIMIT 50", (matter_id,))
    m["saved"] = query_all(
        "SELECT si.*, s.title AS source_title, s.citation, r.query AS research_query FROM saved_items si "
        "LEFT JOIN sources s ON s.id = si.source_id LEFT JOIN research_sessions r ON r.id = si.research_id "
        "WHERE si.matter_id = ? ORDER BY si.created_at DESC", (matter_id,))
    m["drafts"] = query_all("SELECT id, title, template_key, version, updated_at FROM drafts WHERE matter_id = ? "
                            "ORDER BY updated_at DESC", (matter_id,))
    return jsonify(matter=m)


@bp.patch("/matters/<matter_id>")
@login_required
def update_matter(matter_id):
    m = load_matter(matter_id, "edit")
    fields = _clean_fields(body())
    if "confidential" in fields and m["my_access"] != "manage":
        raise ApiError("Only the matter lead can change confidentiality.", 403)
    if not fields:
        raise ApiError("Nothing to update.")
    if "title" in fields and not fields["title"]:
        raise ApiError("Title cannot be empty.")
    fields["updated_at"] = now()
    db = get_db()
    db.execute(f"UPDATE matters SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
               list(fields.values()) + [matter_id])
    db.commit()
    activity.log("matter.updated", f"Updated '{m['title']}': {', '.join(k for k in fields if k != 'updated_at')}",
                 "matter", matter_id, matter_id=matter_id)
    return jsonify(ok=True)


@bp.delete("/matters/<matter_id>")
@login_required
def delete_matter(matter_id):
    m = load_matter(matter_id, "manage")
    if m["owner_id"] != g.user["id"] and "matter.manage_all" not in g.perms:
        raise ApiError("Only the matter owner can delete it. Consider closing or archiving instead.", 403)
    db = get_db()
    for (sid,) in db.execute("SELECT id FROM sources WHERE matter_id = ?", (matter_id,)).fetchall():
        delete_source(db, cfg(), sid)
    db.execute("DELETE FROM matters WHERE id = ?", (matter_id,))
    db.commit()
    activity.log("matter.deleted", f"Deleted matter '{m['title']}'", "matter", matter_id)
    return jsonify(ok=True)


# ── Team ───────────────────────────────────────────────────────────────────

@bp.post("/matters/<matter_id>/members")
@login_required
def add_member(matter_id):
    m = load_matter(matter_id, "manage")
    data = body()
    access = data.get("access", "member")
    if access not in ("lead", "member", "viewer"):
        raise ApiError("Invalid access level.")
    if not m.get("firm_id"):
        raise ApiError("Create or join a firm to share matters with colleagues.")
    target = query_one("SELECT u.id, u.name FROM users u JOIN firm_members fm ON fm.user_id = u.id "
                       "WHERE fm.firm_id = ? AND (u.id = ? OR u.email = ?)",
                       (m["firm_id"], data.get("user_id") or "", (data.get("email") or "").strip().lower()))
    if not target:
        raise ApiError("That person is not a member of this matter's firm.", 404)
    db = get_db()
    db.execute("INSERT INTO matter_members (matter_id, user_id, access, added_at) VALUES (?,?,?,?) "
               "ON CONFLICT(matter_id, user_id) DO UPDATE SET access = excluded.access",
               (matter_id, target["id"], access, now()))
    db.commit()
    activity.log("matter.member_added", f"Gave {target['name']} {access} access to '{m['title']}'", "matter",
                 matter_id, matter_id=matter_id)
    return jsonify(ok=True)


@bp.delete("/matters/<matter_id>/members/<user_id>")
@login_required
def remove_member(matter_id, user_id):
    m = load_matter(matter_id, "manage")
    if user_id == m["owner_id"]:
        raise ApiError("The matter owner cannot be removed.")
    db = get_db()
    db.execute("DELETE FROM matter_members WHERE matter_id = ? AND user_id = ?", (matter_id, user_id))
    db.commit()
    activity.log("matter.member_removed", f"Removed a member from '{m['title']}'", "matter", matter_id,
                 matter_id=matter_id)
    return jsonify(ok=True)


# ── Hearings ───────────────────────────────────────────────────────────────

@bp.post("/matters/<matter_id>/hearings")
@login_required
def add_hearing(matter_id):
    m = load_matter(matter_id, "edit")
    data = body()
    need(data, "date")
    db = get_db()
    hid = new_id()
    db.execute("INSERT INTO hearings (id, matter_id, date, purpose, court_room, notes, created_by, created_at)"
               " VALUES (?,?,?,?,?,?,?,?)",
               (hid, matter_id, data["date"][:16], (data.get("purpose") or "")[:200] or None,
                (data.get("court_room") or "")[:80] or None, (data.get("notes") or "")[:2000] or None,
                g.user["id"], now()))
    _refresh_next_hearing(db, matter_id)
    db.commit()
    activity.log("hearing.added", f"Hearing on {data['date'][:10]} in '{m['title']}'", "hearing", hid,
                 matter_id=matter_id)
    return jsonify(id=hid), 201


@bp.patch("/hearings/<hearing_id>")
@login_required
def update_hearing(hearing_id):
    h = query_one("SELECT * FROM hearings WHERE id = ?", (hearing_id,))
    if not h:
        raise ApiError("Hearing not found.", 404)
    load_matter(h["matter_id"], "edit")
    data = body()
    fields = {k: (str(data[k] or "").strip() or None) for k in ("date", "purpose", "court_room", "notes", "outcome")
              if k in data}
    if not fields:
        raise ApiError("Nothing to update.")
    db = get_db()
    db.execute(f"UPDATE hearings SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
               list(fields.values()) + [hearing_id])
    _refresh_next_hearing(db, h["matter_id"])
    db.commit()
    activity.log("hearing.updated", f"Updated hearing of {h['date'][:10]}", "hearing", hearing_id,
                 matter_id=h["matter_id"])
    return jsonify(ok=True)


@bp.delete("/hearings/<hearing_id>")
@login_required
def delete_hearing(hearing_id):
    h = query_one("SELECT * FROM hearings WHERE id = ?", (hearing_id,))
    if not h:
        raise ApiError("Hearing not found.", 404)
    load_matter(h["matter_id"], "edit")
    db = get_db()
    db.execute("DELETE FROM hearings WHERE id = ?", (hearing_id,))
    _refresh_next_hearing(db, h["matter_id"])
    db.commit()
    activity.log("hearing.deleted", f"Deleted hearing of {h['date'][:10]}", "hearing", hearing_id,
                 matter_id=h["matter_id"])
    return jsonify(ok=True)


# ── Tasks & help requests ──────────────────────────────────────────────────

def _check_assignee(assignee: str, matter: dict = None) -> None:
    if not assignee or assignee == g.user["id"]:
        return
    if "task.assign" not in g.perms:
        raise ApiError("Your role cannot assign work to others.", 403)
    m = g.get("membership")
    if not m or not query_one("SELECT 1 AS ok FROM firm_members WHERE firm_id = ? AND user_id = ?",
                              (m["firm_id"], assignee)):
        raise ApiError("You can only assign tasks to members of your firm.")
    if matter:
        member = query_one("SELECT * FROM matter_members WHERE matter_id = ? AND user_id = ?", (matter["id"], assignee))
        target_membership = query_one("SELECT * FROM firm_members WHERE user_id = ?", (assignee,))
        level = rbac.matter_access(assignee, target_membership, rbac.effective_permissions(target_membership),
                                   matter, member)
        if level is None:
            if matter["my_access"] != "manage":
                raise ApiError("That colleague has no access to this matter. Ask the matter lead to add them.")
            db = get_db()
            db.execute("INSERT INTO matter_members (matter_id, user_id, access, added_at) VALUES (?,?,?,?)",
                       (matter["id"], assignee, "member", now()))


@bp.post("/tasks")
@practice_required
def create_task():
    data = body()
    need(data, "title")
    matter = load_matter(data["matter_id"], "edit") if data.get("matter_id") else None
    assignee = data.get("assigned_to") or g.user["id"]
    _check_assignee(assignee, matter)
    if data.get("priority", "normal") not in ("low", "normal", "high", "urgent"):
        raise ApiError("Invalid priority.")
    db = get_db()
    tid = new_id()
    db.execute("INSERT INTO tasks (id, matter_id, title, description, assigned_to, created_by, due_date, priority,"
               " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (tid, matter["id"] if matter else None, data["title"].strip()[:200],
                (data.get("description") or "")[:4000] or None, assignee, g.user["id"],
                (data.get("due_date") or "")[:10] or None, data.get("priority", "normal"), now()))
    db.commit()
    who = query_one("SELECT name, email, prefs FROM users WHERE id = ?", (assignee,))
    activity.log("task.created", f"Task '{data['title'].strip()[:80]}' for {who['name']}", "task", tid,
                 matter_id=matter["id"] if matter else None)
    if assignee != g.user["id"] and (who["prefs"] or {}).get("notify_tasks", True):
        send_mail(who["email"], f"New task from {g.user['name']}: {data['title'].strip()[:80]}",
                  f"{g.user['name']} assigned you a task on LawPilot.\n\n{data['title'].strip()}\n"
                  f"{data.get('description') or ''}\n" + (f"Matter: {matter['title']}\n" if matter else "")
                  + (f"Due: {data['due_date']}\n" if data.get("due_date") else "") + f"\n{cfg().base_url}/#/tasks")
    return jsonify(id=tid), 201


@bp.get("/tasks")
@practice_required
def list_tasks():
    scope = request.args.get("scope", "mine")
    col = "t.created_by" if scope == "assigned_by_me" else "t.assigned_to"
    status = request.args.get("status", "open")
    sql = (f"SELECT t.*, m.title AS matter_title, a.name AS assignee_name, c.name AS creator_name FROM tasks t "
           f"LEFT JOIN matters m ON m.id = t.matter_id LEFT JOIN users a ON a.id = t.assigned_to "
           f"JOIN users c ON c.id = t.created_by WHERE {col} = ?")
    params = [g.user["id"]]
    if status != "all":
        sql += " AND t.status = ?"
        params.append(status)
    sql += " ORDER BY t.due_date IS NULL, t.due_date, t.created_at DESC LIMIT 200"
    return jsonify(tasks=query_all(sql, params))


@bp.patch("/tasks/<task_id>")
@login_required
def update_task(task_id):
    t = query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not t:
        raise ApiError("Task not found.", 404)
    matter = None
    if g.user["id"] not in (t["assigned_to"], t["created_by"]):
        if not t["matter_id"]:
            raise ApiError("Task not found.", 404)
        matter = load_matter(t["matter_id"], "manage")
    data = body()
    fields = {}
    for k in ("title", "description", "due_date", "priority"):
        if k in data:
            fields[k] = (str(data[k] or "").strip() or None)
    if "assigned_to" in data:
        if t["matter_id"] and not matter:
            matter = load_matter(t["matter_id"], "view")
        _check_assignee(data["assigned_to"], matter)
        fields["assigned_to"] = data["assigned_to"]
    if "status" in data:
        if data["status"] not in ("open", "done"):
            raise ApiError("Invalid status.")
        fields["status"] = data["status"]
        fields["completed_at"] = now() if data["status"] == "done" else None
    if not fields:
        raise ApiError("Nothing to update.")
    db = get_db()
    db.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
               list(fields.values()) + [task_id])
    db.commit()
    activity.log("task.updated", f"Task '{t['title'][:80]}' " + ("completed" if fields.get("status") == "done" else "updated"),
                 "task", task_id, matter_id=t["matter_id"])
    return jsonify(ok=True)


@bp.delete("/tasks/<task_id>")
@login_required
def delete_task(task_id):
    t = query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not t or (t["created_by"] != g.user["id"] and not (t["matter_id"] and load_matter(t["matter_id"], "manage"))):
        raise ApiError("Task not found.", 404)
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    activity.log("task.deleted", f"Deleted task '{t['title'][:80]}'", "task", task_id, matter_id=t["matter_id"])
    return jsonify(ok=True)


# ── Notes ──────────────────────────────────────────────────────────────────

@bp.post("/matters/<matter_id>/notes")
@login_required
def add_note(matter_id):
    load_matter(matter_id, "edit")
    data = body()
    need(data, "body")
    db = get_db()
    nid = new_id()
    db.execute("INSERT INTO notes (id, matter_id, author_id, body, created_at) VALUES (?,?,?,?,?)",
               (nid, matter_id, g.user["id"], data["body"].strip()[:8000], now()))
    db.commit()
    activity.log("note.added", "Added a note", "note", nid, matter_id=matter_id)
    return jsonify(id=nid), 201


@bp.delete("/notes/<note_id>")
@login_required
def delete_note(note_id):
    n = query_one("SELECT * FROM notes WHERE id = ?", (note_id,))
    if not n:
        raise ApiError("Note not found.", 404)
    m = load_matter(n["matter_id"])
    if n["author_id"] != g.user["id"] and m["my_access"] != "manage":
        raise ApiError("Only the author or matter lead can delete this note.", 403)
    db = get_db()
    db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    db.commit()
    activity.log("note.deleted", "Deleted a note", "note", note_id, matter_id=n["matter_id"])
    return jsonify(ok=True)


# ── Documents ──────────────────────────────────────────────────────────────

@bp.post("/matters/<matter_id>/documents")
@require_perm("document.upload")
def upload_document(matter_id):
    m = load_matter(matter_id, "edit")
    f = request.files.get("file")
    if not f or not f.filename:
        raise ApiError("Choose a file to upload.")
    data = f.read()
    try:
        pages = extract(data, f.filename)
    except ExtractionError as exc:
        raise ApiError(str(exc))
    title = (request.form.get("title") or f.filename).strip()[:300]
    res = ingest(get_db(), cfg(), pages, {
        "title": title, "kind": "user_document", "authority_class": "user_upload",
        "legal_domain": request.form.get("doc_type") or None, "verification": "unverified",
    }, origin="user_upload", user_id=g.user["id"], matter_id=matter_id, file_bytes=data, filename=f.filename,
        mime=f.mimetype)
    if res.status == "failed":
        raise ApiError(f"Could not index the document: {res.message}")
    if res.status == "succeeded":
        activity.log("document.uploaded", f"Uploaded '{title}' to '{m['title']}'", "source", res.source_id,
                     matter_id=matter_id)
    return jsonify(id=res.source_id, status=res.status, message=res.message), 201 if res.status == "succeeded" else 200


@bp.delete("/matters/<matter_id>/documents/<source_id>")
@login_required
def delete_document(matter_id, source_id):
    m = load_matter(matter_id, "edit")
    doc = query_one("SELECT * FROM sources WHERE id = ? AND matter_id = ?", (source_id, matter_id))
    if not doc:
        raise ApiError("Document not found.", 404)
    if doc["uploaded_by"] != g.user["id"] and "document.delete" not in g.perms:
        raise ApiError("Your role cannot delete documents uploaded by others.", 403)
    delete_source(get_db(), cfg(), source_id)
    activity.log("document.deleted", f"Deleted '{doc['title']}' from '{m['title']}'", "source", source_id,
                 matter_id=matter_id)
    return jsonify(ok=True)
