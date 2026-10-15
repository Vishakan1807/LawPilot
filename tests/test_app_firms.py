def setup_firm(make_client, roles):
    """Create a firm whose admin invites one member per role. Returns (admin, {role: client})."""
    admin = make_client("admin@firm.test", "Admin")
    assert admin.post("/api/firm", {"name": "Test Chambers"}).status_code == 201
    members = {}
    for role in roles:
        email = f"{role}@firm.test"
        token = admin.post("/api/firm/invitations", {"email": email, "role": role}).json["invite_link"].split("token=")[1]
        m = make_client(email, role.title())
        assert m.post("/api/invitations/accept", {"token": token}).status_code == 200
        members[role] = m
    return admin, members


def test_invitation_must_match_email(make_client):
    admin = make_client("a@firm.test")
    admin.post("/api/firm", {"name": "F"})
    token = admin.post("/api/firm/invitations", {"email": "invitee@firm.test", "role": "associate"}).json["invite_link"].split("token=")[1]
    stranger = make_client("stranger@firm.test")
    assert stranger.post("/api/invitations/accept", {"token": token}).status_code == 403
    assert make_client().get(f"/api/invitations/{token}").json["firm_name"] == "F"
    invitee = make_client("invitee@firm.test")
    assert invitee.post("/api/invitations/accept", {"token": token}).status_code == 200
    assert invitee.post("/api/invitations/accept", {"token": token}).status_code == 404   # single use
    assert invitee.refresh()["firm"]["role"] == "associate"


def test_role_permissions_enforced(make_client):
    admin, m = setup_firm(make_client, ["read_only", "associate", "researcher"])
    assert m["read_only"].post("/api/matters", {"title": "x"}).status_code == 403
    assert m["read_only"].post("/api/research", {"query": "privacy"}).status_code == 403
    assert m["researcher"].post("/api/matters", {"title": "x"}).status_code == 403
    assert m["researcher"].post("/api/research", {"query": "privacy"}).status_code == 200
    assert m["associate"].post("/api/matters", {"title": "x"}).status_code == 201
    assert m["associate"].post("/api/firm/invitations", {"email": "z@z.test", "role": "clerk"}).status_code == 403


def test_permission_overrides(make_client):
    admin, m = setup_firm(make_client, ["read_only"])
    ro = m["read_only"]
    admin.patch(f"/api/firm/members/{ro.user['id']}", {"permission_overrides": {"matter.create": True}})
    assert ro.post("/api/matters", {"title": "granted"}).status_code == 201
    admin.patch(f"/api/firm/members/{ro.user['id']}", {"permission_overrides": {}})
    assert ro.post("/api/matters", {"title": "revoked"}).status_code == 403


def test_matter_visibility_and_ethical_walls(make_client):
    admin, m = setup_firm(make_client, ["partner", "associate", "junior_associate"])
    assoc, junior, partner = m["associate"], m["junior_associate"], m["partner"]
    open_id = assoc.post("/api/matters", {"title": "Open matter"}).json["id"]
    secret_id = assoc.post("/api/matters", {"title": "Walled matter", "confidential": True}).json["id"]

    assert partner.get(f"/api/matters/{open_id}").status_code == 200          # partners see firm matters
    assert partner.get(f"/api/matters/{secret_id}").status_code == 404        # but not confidential ones
    assert junior.get(f"/api/matters/{open_id}").status_code == 404           # juniors only see assigned work
    assert partner.patch(f"/api/matters/{open_id}", {"stage": "Arguments"}).status_code == 200

    assoc.post(f"/api/matters/{open_id}/members", {"user_id": junior.user["id"], "access": "viewer"})
    assert junior.get(f"/api/matters/{open_id}").status_code == 200
    assert junior.patch(f"/api/matters/{open_id}", {"stage": "x"}).status_code == 403   # viewer only

    titles = {x["title"] for x in partner.get("/api/matters").json["matters"]}
    assert "Open matter" in titles and "Walled matter" not in titles


def test_removing_member_revokes_access(make_client):
    admin, m = setup_firm(make_client, ["associate"])
    mid = admin.post("/api/matters", {"title": "Admin matter"}).json["id"]
    admin.post(f"/api/matters/{mid}/members", {"user_id": m["associate"].user["id"], "access": "member"})
    own = m["associate"].post("/api/matters", {"title": "Assoc matter"}).json["id"]
    assert m["associate"].get(f"/api/matters/{mid}").status_code == 200
    assert admin.delete(f"/api/firm/members/{m['associate'].user['id']}").status_code == 200
    assert m["associate"].get(f"/api/matters/{mid}").status_code == 404
    assert m["associate"].get(f"/api/matters/{own}").status_code == 404          # firm keeps the matter
    assert admin.get(f"/api/matters/{own}").json["matter"]["owner_id"] == admin.user["id"]


def test_last_admin_protected(make_client):
    admin, m = setup_firm(make_client, ["associate"])
    assert admin.patch(f"/api/firm/members/{admin.user['id']}", {"role": "partner"}).status_code == 400
    assert admin.post("/api/firm/leave").status_code == 400
    assert m["associate"].post("/api/firm/leave").status_code == 200


def test_tasks_help_requests(make_client):
    admin, m = setup_firm(make_client, ["associate", "researcher"])
    mid = m["associate"].post("/api/matters", {"title": "Bail matter"}).json["id"]
    r = m["associate"].post("/api/tasks", {"title": "Find HC rulings on default bail", "matter_id": mid,
                                           "assigned_to": m["researcher"].user["id"], "priority": "high"})
    assert r.status_code == 201
    # The researcher was given access so they can actually help.
    assert m["researcher"].get(f"/api/matters/{mid}").status_code == 200
    mine = m["researcher"].get("/api/tasks").json["tasks"]
    assert mine[0]["title"].startswith("Find HC")
    assert m["researcher"].patch(f"/api/tasks/{mine[0]['id']}", {"status": "done"}).status_code == 200
    stranger = make_client("outsider@x.test")
    assert stranger.post("/api/tasks", {"title": "x", "assigned_to": m["researcher"].user["id"]}).status_code == 400


def test_hearings_update_next_date(make_client):
    c = make_client("solo@x.test")
    mid = c.post("/api/matters", {"title": "Solo matter", "court": "Madras High Court"}).json["id"]
    c.post(f"/api/matters/{mid}/hearings", {"date": "2099-01-05T10:30", "purpose": "Admission"})
    c.post(f"/api/matters/{mid}/hearings", {"date": "2098-06-01", "purpose": "Mention"})
    assert c.get(f"/api/matters/{mid}").json["matter"]["next_hearing"].startswith("2098-06-01")
    assert c.get("/api/dashboard").status_code == 200
