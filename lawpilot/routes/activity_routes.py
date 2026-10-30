"""Activity logs for three audiences: yourself, your firm, and platform administrators.

Only a user's own view carries summaries and IP addresses. Firm and platform views are built
from activity.neutral_view(), which exposes the kind of action and nothing about its content.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, g, jsonify, request

from lawpilot import activity
from lawpilot.auth import ApiError, login_required, platform_admin_required
from lawpilot.db import query_all, query_one
from lawpilot.routes import page_args

bp = Blueprint("activity", __name__, url_prefix="/api")

NEUTRAL_COLUMNS = [("ts", "When (UTC)"), ("user_name", "Member"), ("category_label", "Category"), ("label", "Activity")]
PLATFORM, INDEPENDENT = "platform", "independent"


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def _feed(base_clauses: list, base_params: list, view, default_limit: int = 100):
    """Rows for a feed: base scope + the shared filters, newest first, paged."""
    limit, offset = page_args(default_limit, 500)
    clauses, params = activity.filters_sql(request.args)
    rows = query_all(
        f"SELECT a.ts, a.action, a.summary, a.matter_id, a.ip, a.user_id, u.name AS user_name "
        f"FROM activity_log a LEFT JOIN users u ON u.id = a.user_id "
        f"WHERE {' AND '.join(base_clauses + clauses)} ORDER BY a.ts DESC LIMIT ? OFFSET ?",
        base_params + params + [limit + 1, offset])
    has_more = len(rows) > limit
    return [view(r) for r in rows[:limit]], has_more


def _csv_response(items: list, filename: str) -> Response:
    for it in items:
        it["category_label"] = activity.CATEGORIES.get(it["category"], it["category"])
    return Response(activity.to_csv(items, NEUTRAL_COLUMNS), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def _members(member_sql: str, member_params: list, event_scope: str, event_params: list) -> list:
    """Members with their role, last activity and number of actions in the last 30 days."""
    members = query_all(member_sql, member_params)
    stats = {r["user_id"]: r for r in query_all(
        f"SELECT a.user_id, MAX(a.ts) AS last_active, SUM(CASE WHEN a.ts >= ? THEN 1 ELSE 0 END) AS events_30d "
        f"FROM activity_log a WHERE {event_scope} GROUP BY a.user_id", [_days_ago(30)] + event_params)}
    for m in members:
        s = stats.get(m["id"]) or {}
        m["last_active"] = s.get("last_active")
        m["events_30d"] = int(s.get("events_30d") or 0)
    return sorted(members, key=lambda m: m["last_active"] or "", reverse=True)


def _meta() -> dict:
    return {"categories": [{"key": k, "label": v} for k, v in activity.CATEGORIES.items()]}


# ── 1. Your own account ────────────────────────────────────────────────────

@bp.get("/account/activity")
@login_required
def my_activity():
    base, params = ["a.user_id = ?"], [g.user["id"]]
    q = (request.args.get("q") or "").strip()
    if q:
        base.append("(a.summary LIKE ? OR a.action LIKE ?)")
        params += [f"%{q}%"] * 2
    items, more = _feed(base, params, activity.own_view)
    return jsonify(items=items, has_more=more, **_meta())


# ── 2. Your firm (firm admins and partners) ────────────────────────────────

def _firm_id_for_viewer() -> str:
    if not g.get("membership"):
        raise ApiError("You are not part of a firm.", 403)
    if "firm.view_activity" not in g.perms:
        raise ApiError("Your role cannot view firm activity.", 403)
    return g.membership["firm_id"]


def _firm_members(firm_id: str) -> list:
    return _members("SELECT u.id, u.name, fm.role FROM firm_members fm JOIN users u ON u.id = fm.user_id "
                    "WHERE fm.firm_id = ?", [firm_id], "a.firm_id = ?", [firm_id])


@bp.get("/firm/activity")
@login_required
def firm_activity():
    firm_id = _firm_id_for_viewer()
    items, more = _feed(["a.firm_id = ?"], [firm_id], activity.neutral_view,
                        default_limit=5000 if request.args.get("format") == "csv" else 100)
    if request.args.get("format") == "csv":
        return _csv_response(items, "firm-activity.csv")
    return jsonify(items=items, has_more=more, members=_firm_members(firm_id), firm=g.membership["firm_name"],
                   **_meta())


# ── 3. Platform administrators: firms first, then a firm's members ─────────

@bp.get("/admin/activity/firms")
@platform_admin_required
def admin_firms():
    week = _days_ago(7)
    firms = query_all(
        "SELECT f.id, f.name, f.created_at, "
        "(SELECT COUNT(*) FROM firm_members fm WHERE fm.firm_id = f.id) AS members, "
        "(SELECT COUNT(*) FROM activity_log a WHERE a.firm_id = f.id AND a.ts >= ?) AS events_7d, "
        "(SELECT MAX(a.ts) FROM activity_log a WHERE a.firm_id = f.id) AS last_activity "
        "FROM firms f ORDER BY f.name", (week,))
    independent = query_one(
        "SELECT (SELECT COUNT(*) FROM users u WHERE u.is_platform_admin = 0 AND u.email NOT LIKE 'deleted-%' AND "
        "NOT EXISTS (SELECT 1 FROM firm_members fm WHERE fm.user_id = u.id)) AS members, "
        "(SELECT COUNT(*) FROM activity_log a JOIN users u ON u.id = a.user_id WHERE a.firm_id IS NULL AND "
        "u.is_platform_admin = 0 AND a.ts >= ?) AS events_7d, "
        "(SELECT MAX(a.ts) FROM activity_log a JOIN users u ON u.id = a.user_id WHERE a.firm_id IS NULL AND "
        "u.is_platform_admin = 0) AS last_activity", (week, ))
    platform = query_one(
        "SELECT (SELECT COUNT(*) FROM users WHERE is_platform_admin = 1) AS members, "
        "(SELECT COUNT(*) FROM activity_log a JOIN users u ON u.id = a.user_id WHERE u.is_platform_admin = 1 "
        "AND a.ts >= ?) AS events_7d, "
        "(SELECT MAX(a.ts) FROM activity_log a JOIN users u ON u.id = a.user_id WHERE u.is_platform_admin = 1) "
        "AS last_activity", (week,))
    return jsonify(firms=firms, independent=independent, platform=platform)


def _admin_scope(firm_id: str):
    """(title, event scope clauses, params, members) for a firm or one of the two special groups."""
    if firm_id == INDEPENDENT:
        scope = "a.firm_id IS NULL AND a.user_id IN (SELECT id FROM users WHERE is_platform_admin = 0)"
        members = _members("SELECT u.id, u.name, 'individual' AS role FROM users u WHERE u.is_platform_admin = 0 "
                           "AND u.email NOT LIKE 'deleted-%' AND NOT EXISTS (SELECT 1 FROM firm_members fm "
                           "WHERE fm.user_id = u.id)", [], scope, [])
        return "Independent advocates", [scope], [], members
    if firm_id == PLATFORM:
        scope = "a.user_id IN (SELECT id FROM users WHERE is_platform_admin = 1)"
        members = _members("SELECT u.id, u.name, 'platform_admin' AS role FROM users u WHERE u.is_platform_admin = 1",
                           [], scope, [])
        return "Platform administrators", [scope], [], members
    firm = query_one("SELECT id, name FROM firms WHERE id = ?", (firm_id,))
    if not firm:
        raise ApiError("Firm not found.", 404)
    return firm["name"], ["a.firm_id = ?"], [firm_id], _firm_members(firm_id)


@bp.get("/admin/activity/firms/<firm_id>")
@platform_admin_required
def admin_firm_activity(firm_id):
    title, scope, params, members = _admin_scope(firm_id)
    csv_out = request.args.get("format") == "csv"
    items, more = _feed(scope, params, activity.neutral_view, default_limit=5000 if csv_out else 100)
    if csv_out:
        return _csv_response(items, "activity.csv")
    return jsonify(title=title, items=items, has_more=more, members=members, **_meta())
