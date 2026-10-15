"""Research sessions and saved authorities."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from lawpilot import activity, query_analysis
from lawpilot.auth import ApiError, load_matter, login_required, require_perm
from lawpilot.db import get_db, new_id, now, query_all, query_one
from lawpilot.research import run_research
from lawpilot.routes import body, cfg, llm, need, page_args

bp = Blueprint("research", __name__, url_prefix="/api")


@bp.post("/research")
@require_perm("research.run")
def research():
    data = body()
    need(data, "query")
    question = data["query"].strip()
    if len(question) > 4000:
        raise ApiError("Question is too long (4000 characters max).")
    matter = load_matter(data["matter_id"]) if data.get("matter_id") else None
    record = run_research(get_db(), cfg(), llm(), user_id=g.user["id"], question=question,
                          mode=data.get("mode", "quick"), filters=data.get("filters"), matter=matter,
                          language=(g.user.get("prefs") or {}).get("language", "en"))
    activity.log("research.run", f"{record['mode'].title()} research: {question[:120]}", "research", record["id"],
                 matter_id=record["matter_id"])
    return jsonify(session=record)


@bp.post("/research/analyze")
@login_required
def analyze():
    data = body()
    need(data, "query")
    return jsonify(analysis=query_analysis.analyze(data["query"]).to_dict())


def _load_session(session_id: str) -> dict:
    s = query_one("SELECT * FROM research_sessions WHERE id = ?", (session_id,))
    if not s:
        raise ApiError("Research session not found.", 404)
    if s["matter_id"]:
        load_matter(s["matter_id"])          # anyone on the matter may read it; others get 404
    elif s["user_id"] != g.user["id"]:
        raise ApiError("Research session not found.", 404)
    else:
        # The matter was deleted: do not keep showing its private document text.
        for e in s["evidence"]:
            if e.get("from_matter"):
                e["text"] = e["excerpt"] = "[document from a deleted matter]"
    return s


@bp.get("/research")
@login_required
def history():
    limit, offset = page_args(30)
    q = (request.args.get("q") or "").strip()
    clauses, params = ["user_id = ?"], [g.user["id"]]
    if request.args.get("matter_id"):
        load_matter(request.args["matter_id"])
        clauses, params = ["matter_id = ?"], [request.args["matter_id"]]
    if q:
        clauses.append("query LIKE ?")
        params.append(f"%{q}%")
    rows = query_all(f"SELECT id, query, mode, outcome, matter_id, created_at, duration_ms FROM research_sessions "
                     f"WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [limit, offset])
    return jsonify(items=rows)


@bp.get("/research/<session_id>")
@login_required
def get_session(session_id):
    return jsonify(session=_load_session(session_id))


@bp.delete("/research/<session_id>")
@login_required
def delete_session(session_id):
    s = query_one("SELECT user_id, query FROM research_sessions WHERE id = ?", (session_id,))
    if not s or s["user_id"] != g.user["id"]:
        raise ApiError("Research session not found.", 404)
    db = get_db()
    db.execute("DELETE FROM research_sessions WHERE id = ?", (session_id,))
    db.commit()
    activity.log("research.deleted", f"Deleted research: {s['query'][:100]}", "research", session_id)
    return jsonify(ok=True)


@bp.post("/research/<session_id>/save")
@login_required
def save_session(session_id):
    s = _load_session(session_id)
    data = body()
    matter_id = data.get("matter_id") or s["matter_id"]
    if matter_id:
        load_matter(matter_id, "edit")
    db = get_db()
    sid = new_id()
    db.execute("INSERT INTO saved_items (id, user_id, matter_id, kind, research_id, note, created_at) "
               "VALUES (?,?,?,?,?,?,?)", (sid, g.user["id"], matter_id, "research", session_id,
                                          (data.get("note") or "")[:2000] or None, now()))
    db.commit()
    activity.log("research.saved", f"Saved research '{s['query'][:80]}'" + (" to a matter" if matter_id else ""),
                 "saved_item", sid, matter_id=matter_id)
    return jsonify(id=sid), 201


@bp.post("/saved")
@login_required
def save_authority():
    data = body()
    need(data, "source_id")
    src = query_one("SELECT id, title, matter_id FROM sources WHERE id = ?", (data["source_id"],))
    if not src:
        raise ApiError("Source not found.", 404)
    if src["matter_id"]:
        load_matter(src["matter_id"])
    matter_id = data.get("matter_id")
    if matter_id:
        load_matter(matter_id, "edit")
    db = get_db()
    sid = new_id()
    db.execute("INSERT INTO saved_items (id, user_id, matter_id, kind, source_id, chunk_id, note, created_at) "
               "VALUES (?,?,?,?,?,?,?,?)", (sid, g.user["id"], matter_id, "authority", src["id"],
                                            data.get("chunk_id"), (data.get("note") or "")[:2000] or None, now()))
    db.commit()
    activity.log("authority.saved", f"Saved authority '{src['title'][:100]}'", "saved_item", sid, matter_id=matter_id)
    return jsonify(id=sid), 201


@bp.get("/saved")
@login_required
def list_saved():
    clauses, params = ["si.user_id = ?"], [g.user["id"]]
    if request.args.get("matter_id"):
        load_matter(request.args["matter_id"])
        clauses, params = ["si.matter_id = ?"], [request.args["matter_id"]]
    if request.args.get("kind"):
        clauses.append("si.kind = ?")
        params.append(request.args["kind"])
    rows = query_all(
        f"SELECT si.*, s.title AS source_title, s.citation, s.court, s.legal_status, s.verification, "
        f"r.query AS research_query, r.outcome AS research_outcome, m.title AS matter_title FROM saved_items si "
        f"LEFT JOIN sources s ON s.id = si.source_id LEFT JOIN research_sessions r ON r.id = si.research_id "
        f"LEFT JOIN matters m ON m.id = si.matter_id WHERE {' AND '.join(clauses)} ORDER BY si.created_at DESC "
        f"LIMIT 300", params)
    return jsonify(items=rows)


@bp.delete("/saved/<item_id>")
@login_required
def delete_saved(item_id):
    item = query_one("SELECT * FROM saved_items WHERE id = ?", (item_id,))
    if not item:
        raise ApiError("Saved item not found.", 404)
    if item["user_id"] != g.user["id"]:
        if not item["matter_id"]:
            raise ApiError("Saved item not found.", 404)
        load_matter(item["matter_id"], "manage")
    db = get_db()
    db.execute("DELETE FROM saved_items WHERE id = ?", (item_id,))
    db.commit()
    activity.log("saved.deleted", "Removed a saved item", "saved_item", item_id, matter_id=item["matter_id"])
    return jsonify(ok=True)
