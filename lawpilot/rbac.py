"""Roles, permissions and matter-level access rules.

A user either practises individually (no firm) or belongs to exactly one firm.
Firm members get permissions from their role; a Firm Admin can grant or
revoke individual permissions per member on top of the role defaults.
"""
from __future__ import annotations

from typing import Dict, Optional, Set

ROLES = {
    "firm_admin": "Firm Admin",
    "partner": "Partner",
    "associate": "Associate",
    "junior_associate": "Junior Associate",
    "researcher": "Researcher",
    "clerk": "Clerk",
    "read_only": "Read-only",
}

PERMISSIONS = {
    "firm.manage_members": "Invite members, change roles and permissions, remove members",
    "firm.view_activity": "See the firm-wide activity log",
    "matter.create": "Create new matters",
    "matter.view_all": "See every non-confidential matter in the firm",
    "matter.manage_all": "Edit and staff every non-confidential matter in the firm",
    "matter.edit": "Edit matters they are assigned to",
    "document.upload": "Upload documents to matters",
    "document.delete": "Delete matter documents",
    "research.run": "Run legal research",
    "draft.create": "Create and edit drafts",
    "task.assign": "Assign tasks and help requests to colleagues",
}

_ALL = set(PERMISSIONS)
_WRITE = {"matter.edit", "document.upload", "research.run", "draft.create", "task.assign"}

ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    "firm_admin": set(_ALL),
    "partner": _ALL - {"firm.manage_members"},
    "associate": _WRITE | {"matter.create", "document.delete"},
    "junior_associate": _WRITE | {"matter.create"},
    "researcher": {"research.run", "draft.create", "document.upload", "task.assign"},
    "clerk": {"matter.create", "matter.edit", "document.upload", "task.assign"},
    "read_only": set(),
}

# An individual lawyer controls all of their own work.
SOLO_PERMISSIONS = _ALL - {"firm.manage_members", "firm.view_activity", "matter.view_all", "matter.manage_all"}

# Platform administrators run the server (library, AI settings, users). They hold no client work:
# they can test research against the shared library but cannot create matters, drafts or tasks,
# and never see any firm's matters. Practising lawyers use separate, regular accounts.
PLATFORM_ADMIN_PERMISSIONS = {"research.run"}


def effective_permissions(membership: Optional[dict]) -> Set[str]:
    if not membership:
        return set(SOLO_PERMISSIONS)
    perms = set(ROLE_PERMISSIONS.get(membership["role"], set()))
    for perm, granted in (membership.get("permission_overrides") or {}).items():
        if perm not in PERMISSIONS:
            continue
        if granted:
            perms.add(perm)
        else:
            perms.discard(perm)
    return perms


ACCESS_LEVELS = {None: 0, "view": 1, "edit": 2, "manage": 3}


def matter_access(user_id: str, membership: Optional[dict], perms: Set[str],
                  matter: dict, member_row: Optional[dict]) -> Optional[str]:
    """Return None, 'view', 'edit' or 'manage' for this user on this matter."""
    level = None
    if matter["owner_id"] == user_id:
        level = "manage"
    elif member_row:
        level = {"lead": "manage", "member": "edit", "viewer": "view"}.get(member_row["access"], "view")

    same_firm = membership and matter.get("firm_id") and matter["firm_id"] == membership["firm_id"]
    if same_firm and not matter.get("confidential"):
        if "matter.manage_all" in perms:
            level = _max(level, "manage")
        elif "matter.view_all" in perms:
            level = _max(level, "view")

    # Without edit rights a member can only view, whatever the matter says.
    if level in ("edit", "manage") and "matter.edit" not in perms and "matter.manage_all" not in perms \
            and matter["owner_id"] != user_id:
        level = "view"
    return level


def _max(a: Optional[str], b: Optional[str]) -> Optional[str]:
    return a if ACCESS_LEVELS[a] >= ACCESS_LEVELS[b] else b


def at_least(level: Optional[str], required: str) -> bool:
    return ACCESS_LEVELS[level] >= ACCESS_LEVELS[required]
