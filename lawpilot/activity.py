"""Activity history: who did what, when.

Three audiences see this log, with different detail:

  the user themself   everything about their own account, including the summary they wrote
                      ("Quick research: anticipatory bail ...") and the IP address
  their firm          (firm admins / partners, permission firm.view_activity) and
  platform admins     a neutral label only: "Ran a legal research query", "Uploaded a document".
                      Never the question asked, document names, matter names or IP addresses:
                      client work is confidential and stays inside the firm and the user's own view.

The neutral labels come from ACTIONS below, so the privacy boundary does not depend on how
each call site words its summary.
"""
from __future__ import annotations

import csv
import io
from typing import Dict, List, Optional, Tuple

from flask import g, has_request_context

from lawpilot.db import get_db, new_id, now

ACCOUNT, MATTERS, DOCUMENTS, RESEARCH, DRAFTING, CALENDAR, FIRM, LIBRARY, ADMIN = (
    "account", "matters", "documents", "research", "drafting", "calendar", "firm", "library", "admin")

CATEGORIES = {
    ACCOUNT: "Account & security", MATTERS: "Matters", DOCUMENTS: "Documents", RESEARCH: "Research & AI",
    DRAFTING: "Drafting", CALENDAR: "Tasks & hearings", FIRM: "Firm & team", LIBRARY: "Legal library",
    ADMIN: "Administration",
}

# action code -> (category, neutral label shown to firms and platform admins)
ACTIONS: Dict[str, Tuple[str, str]] = {
    "account.registered": (ACCOUNT, "Created an account"),
    "account.login": (ACCOUNT, "Signed in"),
    "account.login_failed": (ACCOUNT, "Failed sign-in attempt"),
    "account.logout": (ACCOUNT, "Signed out"),
    "account.password_set": (ACCOUNT, "Set their password"),
    "account.password_changed": (ACCOUNT, "Changed their password"),
    "account.password_reset_requested": (ACCOUNT, "Requested a password reset"),
    "account.password_reset": (ACCOUNT, "Reset their password"),
    "account.session_revoked": (ACCOUNT, "Signed out another device"),
    "account.profile_updated": (ACCOUNT, "Updated their profile"),
    "account.data_exported": (ACCOUNT, "Exported their personal data"),
    "account.deletion_requested": (ACCOUNT, "Requested account deletion"),
    "account.deletion_cancelled": (ACCOUNT, "Cancelled account deletion"),
    "ai.key_saved": (ACCOUNT, "Connected an AI provider"),
    "ai.key_removed": (ACCOUNT, "Disconnected an AI provider"),
    "ai.model_changed": (ACCOUNT, "Changed their AI model"),
    "ai.model_switched": (ACCOUNT, "Switched AI provider"),

    "matter.created": (MATTERS, "Created a matter"),
    "matter.updated": (MATTERS, "Updated a matter"),
    "matter.deleted": (MATTERS, "Deleted a matter"),
    "matter.member_added": (MATTERS, "Gave a colleague access to a matter"),
    "matter.member_removed": (MATTERS, "Removed a colleague from a matter"),
    "note.added": (MATTERS, "Added a note to a matter"),
    "note.deleted": (MATTERS, "Deleted a matter note"),

    "document.uploaded": (DOCUMENTS, "Uploaded a document to a matter"),
    "document.deleted": (DOCUMENTS, "Deleted a matter document"),

    "research.run": (RESEARCH, "Ran a legal research query"),
    "research.saved": (RESEARCH, "Saved a research result"),
    "research.deleted": (RESEARCH, "Deleted a research result"),
    "authority.saved": (RESEARCH, "Saved an authority"),
    "saved.deleted": (RESEARCH, "Removed a saved item"),
    "ai.message": (RESEARCH, "Asked LawPilot AI a question"),
    "ai.file_uploaded": (RESEARCH, "Added a file to an AI chat"),

    "draft.created": (DRAFTING, "Created a draft"),
    "draft.updated": (DRAFTING, "Edited a draft"),
    "draft.deleted": (DRAFTING, "Deleted a draft"),
    "draft.exported": (DRAFTING, "Exported a draft"),
    "draft.ai_assist": (DRAFTING, "Used AI drafting help"),

    "task.created": (CALENDAR, "Created a task"),
    "task.updated": (CALENDAR, "Updated a task"),
    "task.deleted": (CALENDAR, "Deleted a task"),
    "hearing.added": (CALENDAR, "Added a hearing date"),
    "hearing.updated": (CALENDAR, "Updated a hearing date"),
    "hearing.deleted": (CALENDAR, "Removed a hearing date"),

    "firm.created": (FIRM, "Created the firm"),
    "firm.updated": (FIRM, "Updated firm details"),
    "firm.invited": (FIRM, "Invited a new member"),
    "firm.invitation_revoked": (FIRM, "Revoked an invitation"),
    "firm.joined": (FIRM, "Joined the firm"),
    "firm.left": (FIRM, "Left the firm"),
    "firm.member_updated": (FIRM, "Changed a member's role or permissions"),
    "firm.member_removed": (FIRM, "Removed a member"),

    "corpus.ingested": (LIBRARY, "Added a source to the library"),
    "corpus.updated": (LIBRARY, "Edited a library source"),
    "corpus.deleted": (LIBRARY, "Deleted a library source"),
    "corpus.relation_added": (LIBRARY, "Linked two authorities"),
    "corpus.reembedded": (LIBRARY, "Re-indexed the library"),
    "corpus.import_started": (LIBRARY, "Started a judgment import"),
    "corpus.starter_loaded": (LIBRARY, "Loaded starter summaries"),
    "corpus.starter_removed": (LIBRARY, "Removed starter summaries"),

    "admin.user_updated": (ADMIN, "Changed a user's account"),
    "admin.ai_settings": (ADMIN, "Changed server AI settings"),
    "admin.account_deleted": (ADMIN, "Processed an account deletion"),
}


def describe(action: str) -> Tuple[str, str]:
    """(category, neutral label) for an action code, with a safe fallback for unknown codes."""
    if action in ACTIONS:
        return ACTIONS[action]
    prefix = action.split(".", 1)[0]
    category = {"account": ACCOUNT, "matter": MATTERS, "document": DOCUMENTS, "research": RESEARCH, "ai": RESEARCH,
                "draft": DRAFTING, "task": CALENDAR, "hearing": CALENDAR, "firm": FIRM, "corpus": LIBRARY,
                "admin": ADMIN}.get(prefix, ACCOUNT)
    return category, "Performed an action"


def actions_in(category: str) -> List[str]:
    return [code for code, (cat, _) in ACTIONS.items() if cat == category]


def log(action: str, summary: str = "", target_type: Optional[str] = None,
        target_id: Optional[str] = None, matter_id: Optional[str] = None,
        user_id: Optional[str] = None, firm_id: Optional[str] = None) -> None:
    ip = None
    if has_request_context():
        from lawpilot.auth import client_ip
        ip = client_ip()
        if user_id is None and g.get("user"):
            user_id = g.user["id"]
        if firm_id is None and g.get("membership") and g.get("user") and g.user["id"] == user_id:
            firm_id = g.membership["firm_id"]
    db = get_db()
    if firm_id is None and user_id:
        # Sign-in events are logged before the session is loaded: find the user's firm directly.
        row = db.execute("SELECT firm_id FROM firm_members WHERE user_id = ?", (user_id,)).fetchone()
        firm_id = row[0] if row else None
    db.execute(
        "INSERT INTO activity_log (id, ts, user_id, firm_id, matter_id, action, target_type, target_id, summary, ip)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (new_id(), now(), user_id, firm_id, matter_id, action, target_type, target_id, (summary or "")[:500], ip),
    )
    db.commit()


# ── Reading ────────────────────────────────────────────────────────────────

def own_view(row: dict) -> dict:
    """A user looking at their own activity: full detail."""
    category, label = describe(row["action"])
    return {"ts": row["ts"], "action": row["action"], "category": category, "label": label,
            "summary": row.get("summary") or "", "matter_id": row.get("matter_id"), "ip": row.get("ip")}


def neutral_view(row: dict) -> dict:
    """Firm admins and platform admins: who, when and what kind of action, never the content."""
    category, label = describe(row["action"])
    return {"ts": row["ts"], "user_id": row.get("user_id"), "user_name": row.get("user_name") or "Unknown user",
            "category": category, "label": label}


def filters_sql(args, alias: str = "a") -> Tuple[List[str], list]:
    """Category, member and date filters shared by every activity view."""
    clauses, params = [], []
    category = args.get("category") or ""
    if category in CATEGORIES:
        codes = actions_in(category)
        prefixes = {ACCOUNT: ["account."], MATTERS: ["matter.", "note."], DOCUMENTS: ["document."],
                    RESEARCH: ["research.", "authority.", "saved."], DRAFTING: ["draft."],
                    CALENDAR: ["task.", "hearing."], FIRM: ["firm."], LIBRARY: ["corpus."], ADMIN: ["admin."]}[category]
        parts = [f"{alias}.action IN ({','.join('?' * len(codes))})"] if codes else []
        params += codes
        for p in prefixes:
            parts.append(f"{alias}.action LIKE ?")
            params.append(p + "%")
        clauses.append("(" + " OR ".join(parts) + ")")
    if args.get("user_id"):
        clauses.append(f"{alias}.user_id = ?")
        params.append(args["user_id"])
    if args.get("from"):
        clauses.append(f"{alias}.ts >= ?")
        params.append(str(args["from"])[:10])
    if args.get("to"):
        clauses.append(f"{alias}.ts < ?")
        params.append(str(args["to"])[:10] + "T99")      # inclusive of the whole "to" day
    return clauses, params


def to_csv(rows: List[dict], columns: List[Tuple[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([title for _, title in columns])
    for r in rows:
        w.writerow([_csv_safe(r.get(key)) for key, _ in columns])
    return buf.getvalue()


def _csv_safe(value) -> str:
    """Stop spreadsheet formula injection (a name starting with '=' etc.)."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s
