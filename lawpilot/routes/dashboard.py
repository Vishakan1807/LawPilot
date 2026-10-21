"""Personal dashboard and system status."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, g, jsonify

from lawpilot import __version__
from lawpilot.auth import accessible_matter_ids, login_required
from lawpilot.db import get_db, query_all
from lawpilot.embeddings import get_embedder
from lawpilot.routes import cfg, llm

bp = Blueprint("dashboard", __name__, url_prefix="/api")


@bp.get("/dashboard")
@login_required
def dashboard():
    uid = g.user["id"]
    ids = accessible_matter_ids()
    marks = ",".join("?" * len(ids)) or "''"
    today = date.today().isoformat()
    horizon = (date.today() + timedelta(days=14)).isoformat()
    hearings = query_all(
        f"SELECT h.id, h.date, h.purpose, h.court_room, m.id AS matter_id, m.title AS matter_title, m.court "
        f"FROM hearings h JOIN matters m ON m.id = h.matter_id WHERE h.matter_id IN ({marks}) "
        f"AND h.date >= ? AND h.date <= ? ORDER BY h.date LIMIT 20", ids + [today, horizon + "T23:59"])
    return jsonify(
        upcoming_hearings=hearings,
        my_tasks=query_all("SELECT t.id, t.title, t.due_date, t.priority, t.matter_id, m.title AS matter_title, "
                           "c.name AS creator_name FROM tasks t LEFT JOIN matters m ON m.id = t.matter_id "
                           "JOIN users c ON c.id = t.created_by WHERE t.assigned_to = ? AND t.status = 'open' "
                           "ORDER BY t.due_date IS NULL, t.due_date LIMIT 10", (uid,)),
        recent_matters=query_all(f"SELECT id, title, client_name, court, next_hearing, status, updated_at FROM matters "
                                 f"WHERE id IN ({marks}) AND status = 'open' ORDER BY updated_at DESC LIMIT 6", ids),
        recent_research=query_all("SELECT id, query, mode, outcome, created_at FROM research_sessions WHERE user_id = ? "
                                  "ORDER BY created_at DESC LIMIT 6", (uid,)),
        recent_drafts=query_all("SELECT id, title, updated_at FROM drafts WHERE user_id = ? ORDER BY updated_at DESC "
                                "LIMIT 5", (uid,)),
        saved_authorities=query_all("SELECT si.id, si.source_id, s.title, s.citation, s.legal_status FROM saved_items si "
                                    "JOIN sources s ON s.id = si.source_id WHERE si.user_id = ? AND si.kind = 'authority' "
                                    "ORDER BY si.created_at DESC LIMIT 6", (uid,)),
        counts={
            "open_matters": len(query_all(f"SELECT id FROM matters WHERE id IN ({marks}) AND status = 'open'", ids)),
            "open_tasks": get_db().execute("SELECT COUNT(*) FROM tasks WHERE assigned_to = ? AND status = 'open'",
                                           (uid,)).fetchone()[0],
            "hearings_14d": len(hearings),
        },
    )


@bp.get("/system/status")
@login_required
def status():
    c = cfg()
    client = llm()
    db = get_db()
    return jsonify(
        version=__version__,
        llm={"configured": bool(client), "provider": client.provider if client else None,
             "model": client.model if client else None},
        embeddings={"model": get_embedder(c).name,
                    "semantic": get_embedder(c).name != "local-hash-v1"},
        database={"engine": "PostgreSQL" if db.dialect == "postgres" else "SQLite",
                  "pgvector": bool(getattr(db, "has_pgvector", False))},
        email_configured=bool(c.smtp_host),
        serverless=bool(getattr(c, "serverless", False)),
        kanoon_api=bool(c.kanoon_api_token),
        corpus={"total": db.execute("SELECT COUNT(*) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL AND is_active = 1").fetchone()[0],
                "primary": db.execute("SELECT COUNT(*) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL AND is_active = 1 AND "
                                      "authority_class = 'primary'").fetchone()[0]},
    )
