"""Law firms: creation, invitations, roles and permissions, membership."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify

from lawpilot import activity, rbac
from lawpilot.auth import ApiError, login_required, new_token, practice_required, require_perm, token_hash
from lawpilot.db import get_db, new_id, now, query_all, query_one
from lawpilot.mailer import send_mail
from lawpilot.routes import body, cfg, need

bp = Blueprint("firms", __name__, url_prefix="/api")


@bp.get("/firm/roles")
@login_required
def roles():
    return jsonify(roles=[{"key": k, "label": v, "permissions": sorted(rbac.ROLE_PERMISSIONS[k])}
                          for k, v in rbac.ROLES.items()],
                   permissions=[{"key": k, "label": v} for k, v in rbac.PERMISSIONS.items()])


@bp.get("/firm")
@login_required
def my_firm():
    m = g.get("membership")
    if not m:
        return jsonify(firm=None)
    firm = query_one("SELECT * FROM firms WHERE id = ?", (m["firm_id"],))
    members = query_all(
        "SELECT u.id, u.name, u.email, u.bar_enrolment, fm.role, fm.permission_overrides, fm.joined_at "
        "FROM firm_members fm JOIN users u ON u.id = fm.user_id WHERE fm.firm_id = ? ORDER BY u.name", (m["firm_id"],))
    for mem in members:
        mem["role_label"] = rbac.ROLES.get(mem["role"], mem["role"])
        mem["effective_permissions"] = sorted(rbac.effective_permissions(mem))
    invites = []
    if "firm.manage_members" in g.perms:
        invites = query_all("SELECT id, email, role, created_at, expires_at FROM firm_invitations WHERE firm_id = ? "
                            "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > ? ORDER BY created_at DESC",
                            (m["firm_id"], now()))
    return jsonify(firm=firm, members=members, invitations=invites, my_role=m["role"])


@bp.post("/firm")
@practice_required
def create_firm():
    if g.get("membership"):
        raise ApiError("You already belong to a firm.")
    data = body()
    need(data, "name")
    db = get_db()
    firm_id = new_id()
    ts = now()
    db.execute("INSERT INTO firms (id, name, city, created_by, created_at) VALUES (?,?,?,?,?)",
               (firm_id, data["name"].strip()[:160], (data.get("city") or "").strip()[:80] or None, g.user["id"], ts))
    db.execute("INSERT INTO firm_members (firm_id, user_id, role, joined_at) VALUES (?,?,?,?)",
               (firm_id, g.user["id"], "firm_admin", ts))
    moved = 0
    if data.get("move_my_matters"):
        moved = db.execute("UPDATE matters SET firm_id = ? WHERE owner_id = ? AND firm_id IS NULL",
                           (firm_id, g.user["id"])).rowcount
    db.commit()
    activity.log("firm.created", f"Created firm {data['name'].strip()}" + (f" and moved {moved} matters" if moved else ""),
                 "firm", firm_id, firm_id=firm_id)
    return jsonify(firm_id=firm_id), 201


@bp.patch("/firm")
@require_perm("firm.manage_members")
def update_firm():
    data = body()
    db = get_db()
    if data.get("name"):
        db.execute("UPDATE firms SET name = ? WHERE id = ?", (data["name"].strip()[:160], g.membership["firm_id"]))
    if "city" in data:
        db.execute("UPDATE firms SET city = ? WHERE id = ?", ((data["city"] or "").strip()[:80] or None,
                                                             g.membership["firm_id"]))
    db.commit()
    activity.log("firm.updated", "Updated firm details", "firm", g.membership["firm_id"])
    return jsonify(ok=True)


def _admin_count(db, firm_id):
    return db.execute("SELECT COUNT(*) FROM firm_members WHERE firm_id = ? AND role = 'firm_admin'",
                      (firm_id,)).fetchone()[0]


@bp.patch("/firm/members/<user_id>")
@require_perm("firm.manage_members")
def update_member(user_id):
    data = body()
    firm_id = g.membership["firm_id"]
    db = get_db()
    member = db.execute("SELECT * FROM firm_members WHERE firm_id = ? AND user_id = ?", (firm_id, user_id)).fetchone()
    if not member:
        raise ApiError("Member not found.", 404)
    changes = []
    if "role" in data:
        role = data["role"]
        if role not in rbac.ROLES:
            raise ApiError("Unknown role.")
        if member["role"] == "firm_admin" and role != "firm_admin" and _admin_count(db, firm_id) == 1:
            raise ApiError("A firm must keep at least one Firm Admin.")
        db.execute("UPDATE firm_members SET role = ? WHERE firm_id = ? AND user_id = ?", (role, firm_id, user_id))
        changes.append(f"role -> {rbac.ROLES[role]}")
    if "permission_overrides" in data:
        overrides = data["permission_overrides"] or {}
        if not isinstance(overrides, dict):
            raise ApiError("permission_overrides must be an object.")
        clean = {k: bool(v) for k, v in overrides.items() if k in rbac.PERMISSIONS}
        if user_id == g.user["id"] and clean.get("firm.manage_members") is False:
            raise ApiError("You cannot revoke your own member-management permission.")
        db.execute("UPDATE firm_members SET permission_overrides = ? WHERE firm_id = ? AND user_id = ?",
                   (json.dumps(clean), firm_id, user_id))
        changes.append("permissions: " + (", ".join(f"{'+' if v else '-'}{k}" for k, v in clean.items()) or "role defaults"))
    db.commit()
    name = db.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()["name"]
    activity.log("firm.member_updated", f"{name}: {'; '.join(changes)}", "user", user_id)
    return jsonify(ok=True)


def _remove_member(db, firm_id: str, user_id: str, new_owner: str) -> None:
    """Remove a member and cut off access that came from the firm."""
    db.execute("DELETE FROM matter_members WHERE user_id = ? AND matter_id IN (SELECT id FROM matters WHERE firm_id = ?)",
               (user_id, firm_id))
    db.execute("UPDATE matters SET owner_id = ? WHERE owner_id = ? AND firm_id = ?", (new_owner, user_id, firm_id))
    db.execute("UPDATE tasks SET assigned_to = NULL WHERE assigned_to = ? AND matter_id IN "
               "(SELECT id FROM matters WHERE firm_id = ?)", (user_id, firm_id))
    db.execute("DELETE FROM firm_members WHERE firm_id = ? AND user_id = ?", (firm_id, user_id))


@bp.delete("/firm/members/<user_id>")
@require_perm("firm.manage_members")
def remove_member(user_id):
    firm_id = g.membership["firm_id"]
    if user_id == g.user["id"]:
        raise ApiError("Use 'Leave firm' to remove yourself.")
    db = get_db()
    member = db.execute("SELECT fm.role, u.name FROM firm_members fm JOIN users u ON u.id = fm.user_id "
                        "WHERE fm.firm_id = ? AND fm.user_id = ?", (firm_id, user_id)).fetchone()
    if not member:
        raise ApiError("Member not found.", 404)
    _remove_member(db, firm_id, user_id, g.user["id"])
    db.commit()
    activity.log("firm.member_removed", f"Removed {member['name']} from the firm; their firm matters now belong to you",
                 "user", user_id)
    return jsonify(ok=True)


@bp.post("/firm/leave")
@practice_required
def leave_firm():
    m = g.get("membership")
    if not m:
        raise ApiError("You are not in a firm.")
    db = get_db()
    others = db.execute("SELECT user_id FROM firm_members WHERE firm_id = ? AND user_id != ? ORDER BY "
                        "CASE role WHEN 'firm_admin' THEN 0 WHEN 'partner' THEN 1 ELSE 2 END",
                        (m["firm_id"], g.user["id"])).fetchall()
    if m["role"] == "firm_admin" and _admin_count(db, m["firm_id"]) == 1 and others:
        raise ApiError("You are the only Firm Admin. Make someone else Firm Admin first.")
    new_owner = others[0]["user_id"] if others else g.user["id"]
    _remove_member(db, m["firm_id"], g.user["id"], new_owner)
    if not others:
        # Last member leaving: the matters become the user's own solo matters again.
        db.execute("UPDATE matters SET firm_id = NULL WHERE firm_id = ?", (m["firm_id"],))
        db.execute("DELETE FROM firms WHERE id = ?", (m["firm_id"],))
    db.commit()
    activity.log("firm.left", f"Left {m['firm_name']}", "firm", m["firm_id"], firm_id=m["firm_id"])
    return jsonify(ok=True)


# ── Invitations ────────────────────────────────────────────────────────────

@bp.post("/firm/invitations")
@require_perm("firm.manage_members")
def invite():
    data = body()
    need(data, "email", "role")
    email = data["email"].strip().lower()
    role = data["role"]
    if role not in rbac.ROLES:
        raise ApiError("Unknown role.")
    firm_id = g.membership["firm_id"]
    db = get_db()
    existing = db.execute("SELECT fm.firm_id FROM users u JOIN firm_members fm ON fm.user_id = u.id WHERE u.email = ?",
                          (email,)).fetchone()
    if existing:
        raise ApiError("That person already belongs to a firm." if existing["firm_id"] != firm_id
                       else "That person is already a member.")
    token = new_token()
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0).isoformat()
    db.execute("UPDATE firm_invitations SET revoked_at = ? WHERE firm_id = ? AND email = ? AND accepted_at IS NULL "
               "AND revoked_at IS NULL", (now(), firm_id, email))
    inv_id = new_id()
    db.execute("INSERT INTO firm_invitations (id, firm_id, email, role, token_hash, invited_by, created_at, expires_at)"
               " VALUES (?,?,?,?,?,?,?,?)", (inv_id, firm_id, email, role, token_hash(token), g.user["id"], now(), expires))
    db.commit()
    link = f"{cfg().base_url}/#/invite?token={token}"
    sent = send_mail(email, f"Invitation to join {g.membership['firm_name']} on LawPilot",
                     f"{g.user['name']} has invited you to join {g.membership['firm_name']} as "
                     f"{rbac.ROLES[role]}.\n\nAccept within 7 days: {link}\n\nSign in or create an account with this "
                     f"email address ({email}) to accept.")
    activity.log("firm.invited", f"Invited {email} as {rbac.ROLES[role]}", "invitation", inv_id)
    # The link is shown to the inviting admin so it can be shared directly when email is not configured.
    return jsonify(ok=True, email_sent=sent, invite_link=link), 201


@bp.delete("/firm/invitations/<inv_id>")
@require_perm("firm.manage_members")
def revoke_invite(inv_id):
    db = get_db()
    db.execute("UPDATE firm_invitations SET revoked_at = ? WHERE id = ? AND firm_id = ?",
               (now(), inv_id, g.membership["firm_id"]))
    db.commit()
    activity.log("firm.invitation_revoked", "Revoked an invitation", "invitation", inv_id)
    return jsonify(ok=True)


def _valid_invite(token: str):
    inv = query_one("SELECT i.*, f.name AS firm_name FROM firm_invitations i JOIN firms f ON f.id = i.firm_id "
                    "WHERE i.token_hash = ?", (token_hash(token or ""),))
    if not inv or inv["accepted_at"] or inv["revoked_at"] or inv["expires_at"] < now():
        raise ApiError("This invitation is invalid, has expired, or was already used.", 404)
    return inv


@bp.get("/invitations/<token>")
def preview_invite(token):
    inv = _valid_invite(token)
    return jsonify(firm_name=inv["firm_name"], email=inv["email"], role=rbac.ROLES.get(inv["role"], inv["role"]))


@bp.post("/invitations/accept")
@practice_required
def accept_invite():
    inv = _valid_invite(body().get("token"))
    if inv["email"] != g.user["email"]:
        raise ApiError(f"This invitation was sent to {inv['email']}. Sign in with that account to accept it.", 403)
    if g.get("membership"):
        raise ApiError("Leave your current firm before joining another.")
    db = get_db()
    db.execute("INSERT INTO firm_members (firm_id, user_id, role, joined_at) VALUES (?,?,?,?)",
               (inv["firm_id"], g.user["id"], inv["role"], now()))
    db.execute("UPDATE firm_invitations SET accepted_at = ? WHERE id = ?", (now(), inv["id"]))
    db.commit()
    activity.log("firm.joined", f"Joined {inv['firm_name']} as {rbac.ROLES[inv['role']]}", "firm", inv["firm_id"],
                 firm_id=inv["firm_id"])
    return jsonify(ok=True, firm_name=inv["firm_name"])
