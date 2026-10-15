from conftest import H, PASSWORD


def test_first_user_is_platform_admin(tmp_path):
    import pytest
    from conftest import BACKEND, Client, _base_cfg
    from lawpilot import create_app
    if BACKEND != "sqlite":
        pytest.skip("uses a fresh SQLite server")
    fresh = create_app(_base_cfg(tmp_path / "fresh", database_url=""))
    assert Client(fresh, "first@x.test").user["is_platform_admin"] is True
    assert Client(fresh, "second@x.test").user["is_platform_admin"] is False


def test_platform_admin_holds_no_client_work(admin, make_client):
    assert admin.user["permissions"] == ["research.run"]
    assert admin.post("/api/matters", {"title": "x"}).status_code == 403
    assert admin.post("/api/firm", {"name": "Admin firm"}).status_code == 403
    assert admin.post("/api/tasks", {"title": "x"}).status_code == 403
    assert admin.post("/api/drafts", {"template_key": "legal_opinion"}).status_code == 403
    assert admin.post("/api/research", {"query": "privacy Article 21"}).status_code == 200
    # It cannot see a lawyer's matter either.
    lawyer = make_client("lawyer@x.test")
    mid = lawyer.post("/api/matters", {"title": "Client matter"}).json["id"]
    assert admin.get(f"/api/matters/{mid}").status_code == 404
    # Counts only on the overview.
    o = admin.get("/api/admin/overview").json
    assert o["matters"] == 1 and "Client matter" not in str(o)
    # A lawyer with client work cannot be turned into a platform admin.
    assert admin.patch(f"/api/admin/users/{lawyer.user['id']}", {"is_platform_admin": True}).status_code == 400


def test_register_validation(make_client):
    c = make_client()
    assert c.post("/api/auth/register", {"email": "bad", "name": "A", "password": PASSWORD}).status_code == 400
    assert c.post("/api/auth/register", {"email": "a@x.test", "name": "A", "password": "short"}).status_code == 400
    assert c.post("/api/auth/register", {"email": "a@x.test", "name": "A", "password": "onlyletters"}).status_code == 400
    make_client("dup@x.test")
    assert c.post("/api/auth/register", {"email": "dup@x.test", "name": "A", "password": PASSWORD}).status_code == 409


def test_login_logout_and_session(make_client):
    make_client("login@x.test")
    c = make_client()
    assert c.post("/api/auth/login", {"email": "login@x.test", "password": "wrong-pass-1"}).status_code == 401
    r = c.post("/api/auth/login", {"email": "LOGIN@x.test", "password": PASSWORD})
    assert r.status_code == 200
    assert c.get("/api/auth/me").json["user"]["email"] == "login@x.test"
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").json["user"] is None
    assert c.get("/api/matters").status_code == 401


def test_login_throttling(make_client):
    make_client("throttle@x.test")
    c = make_client()
    for _ in range(8):
        c.post("/api/auth/login", {"email": "throttle@x.test", "password": "nope-nope-1"})
    r = c.post("/api/auth/login", {"email": "throttle@x.test", "password": PASSWORD})
    assert r.status_code == 429


def test_csrf_header_required(make_client):
    c = make_client("csrf@x.test")
    r = c.c.post("/api/matters", json={"title": "x"})          # no X-Requested-With header
    assert r.status_code == 400


def test_password_reset_flow(make_client):
    owner = make_client("reset@x.test")
    anon = make_client()
    r = anon.post("/api/auth/forgot-password", {"email": "reset@x.test"})
    link = r.json["dev_reset_link"]                              # dev mode, no SMTP
    token = link.split("token=")[1]
    assert anon.post("/api/auth/reset-password", {"token": token, "password": "brand-new-pass-9"}).status_code == 200
    assert anon.post("/api/auth/reset-password", {"token": token, "password": "another-pass-9"}).status_code == 400
    assert owner.get("/api/matters").status_code == 401         # old sessions revoked
    assert anon.post("/api/auth/login", {"email": "reset@x.test", "password": "brand-new-pass-9"}).status_code == 200
    # Unknown emails get the same response (no account enumeration).
    r2 = anon.post("/api/auth/forgot-password", {"email": "nobody@x.test"})
    assert r2.status_code == 200 and "dev_reset_link" not in r2.json


def test_change_password_and_sessions(make_client, app):
    c = make_client("chg@x.test")
    other = make_client()
    other.post("/api/auth/login", {"email": "chg@x.test", "password": PASSWORD})
    assert len(c.get("/api/auth/sessions").json["sessions"]) == 2
    assert c.post("/api/auth/change-password", {"current_password": "bad", "new_password": "new-pass-2026"}).status_code == 400
    assert c.post("/api/auth/change-password", {"current_password": PASSWORD, "new_password": "new-pass-2026"}).status_code == 200
    assert other.get("/api/matters").status_code == 401         # other device signed out
    assert c.get("/api/matters").status_code == 200


def test_profile_prefs_export_and_deletion(make_client):
    c = make_client("me@x.test", "Me")
    r = c.patch("/api/account/profile", {"name": "Adv. Me", "prefs": {"default_jurisdiction": "Kerala", "bogus": 1}})
    assert r.json["user"]["name"] == "Adv. Me"
    assert r.json["user"]["prefs"]["default_jurisdiction"] == "Kerala"
    assert "bogus" not in r.json["user"]["prefs"]
    exp = c.get("/api/account/export")
    assert exp.status_code == 200 and b"me@x.test" in exp.data
    assert c.post("/api/account/deletion-request", {"reason": "leaving"}).status_code == 200
    assert c.refresh()["status"] == "deletion_requested"
    assert c.delete("/api/account/deletion-request").status_code == 200
    acts = c.get("/api/account/activity?q=deletion").json["items"]
    assert any(a["action"] == "account.deletion_requested" for a in acts)


def test_security_headers(app):
    r = app.test_client().get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
