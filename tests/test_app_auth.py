from conftest import PASSWORD


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
    assert c.post("/api/auth/register", {"email": "bad", "name": "A"}).status_code == 400
    assert c.post("/api/auth/register", {"email": "a@x.test"}).status_code == 400          # name required
    make_client("dup@x.test")
    assert c.post("/api/auth/register", {"email": "dup@x.test", "name": "A"}).status_code == 409


def test_signup_emails_temp_password_and_forces_a_new_one(make_client):
    c = make_client()
    r = c.post("/api/auth/register", {"email": "New@x.test", "name": "New Advocate"})
    assert r.status_code == 201 and "user" not in r.json                       # nobody is signed in yet
    temp = r.json["dev_temp_password"]
    assert c.get("/api/auth/me").json["user"] is None
    # Signing in with the temporary password works, but only to choose a real one.
    r = c.post("/api/auth/login", {"email": "new@x.test", "password": temp})
    assert r.status_code == 200 and r.json["user"]["must_change_password"] is True
    assert r.json["user"]["email_verified"] is True
    blocked = c.get("/api/matters")
    assert blocked.status_code == 403 and blocked.json["code"] == "password_change_required"
    assert c.post("/api/auth/set-password", {"new_password": temp}).status_code == 400     # must differ
    assert c.post("/api/auth/set-password", {"new_password": "short"}).status_code == 400
    r = c.post("/api/auth/set-password", {"new_password": "my-own-pass-2026"})
    assert r.status_code == 200 and r.json["user"]["must_change_password"] is False
    assert c.get("/api/matters").status_code == 200
    # The temporary password and the emailed link are both dead now.
    other = make_client()
    assert other.post("/api/auth/login", {"email": "new@x.test", "password": temp}).status_code == 401
    assert other.post("/api/auth/login", {"email": "new@x.test", "password": "my-own-pass-2026"}).status_code == 200


def test_welcome_link_sets_password_and_signs_in(make_client):
    c = make_client()
    r = c.post("/api/auth/register", {"email": "link@x.test", "name": "Link User", "next": "#/invite?token=abc"})
    link = r.json["dev_set_password_link"]
    assert "welcome=1" in link and "next=%23%2Finvite%3Ftoken%3Dabc" in link
    token = link.split("token=")[1].split("&")[0]
    r = c.post("/api/auth/reset-password", {"token": token, "password": "linked-pass-2026"})
    assert r.status_code == 200 and r.json["user"]["email"] == "link@x.test"
    assert r.json["user"]["must_change_password"] is False
    assert c.get("/api/matters").status_code == 200                              # signed in by the link
    assert c.post("/api/auth/reset-password", {"token": token, "password": "again-pass-2026"}).status_code == 400
    # A link pointing off-site is never carried into the email.
    r = c.post("/api/auth/register", {"email": "evil@x.test", "name": "E", "next": "https://evil.test"})
    assert "next=" not in r.json["dev_set_password_link"]


def test_expired_temp_password(make_client, app):
    from lawpilot import db as dbmod
    c = make_client()
    temp = c.post("/api/auth/register", {"email": "late@x.test", "name": "Late"}).json["dev_temp_password"]
    conn = dbmod.connect(app.config["LP"], pooled=False)
    conn.execute("UPDATE users SET temp_password_expires_at = '2000-01-01T00:00:00+00:00' WHERE email = 'late@x.test'")
    conn.commit()
    conn.close()
    r = c.post("/api/auth/login", {"email": "late@x.test", "password": temp})
    assert r.status_code == 401 and "expired" in r.json["error"]


def test_signup_needs_email_in_production(make_client, app):
    c = make_client()
    lp = app.config["LP"]
    lp.dev_mode = False
    try:
        r = c.post("/api/auth/register", {"email": "prod@x.test", "name": "Prod"})
        assert r.status_code == 503
    finally:
        lp.dev_mode = True


def test_signup_rolls_back_when_mail_fails(make_client, app, monkeypatch):
    from lawpilot.routes import auth_routes
    c = make_client()
    lp = app.config["LP"]
    lp.smtp_host = "smtp.invalid"
    monkeypatch.setattr(auth_routes, "send_mail", lambda *a, **k: False)
    try:
        assert c.post("/api/auth/register", {"email": "nomail@x.test", "name": "N"}).status_code == 502
    finally:
        lp.smtp_host = ""
    r = c.post("/api/auth/register", {"email": "nomail@x.test", "name": "N"})
    assert r.status_code == 201                                                  # nothing was left behind


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
