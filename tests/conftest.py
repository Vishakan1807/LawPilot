import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lawpilot import create_app  # noqa: E402
from lawpilot.config import Config  # noqa: E402
from lawpilot.llm import LLMClient, LLMError, LLMResult  # noqa: E402

H = {"X-Requested-With": "LawPilot"}
PASSWORD = "test-pass-2026"


import os
import uuid

# LAWPILOT_TEST_BACKEND=postgres runs the whole suite against PostgreSQL:
#   LAWPILOT_TEST_DATABASE_URL=postgresql://user:pass@host/postgres  (any database the user can CREATE DATABASE from)
# or, if unset, a throwaway local server started with the `pgserver` package.
BACKEND = os.environ.get("LAWPILOT_TEST_BACKEND", "sqlite")


def _base_cfg(data_dir, **kw):
    return Config.from_env(data_dir=data_dir, llm_provider="none", smtp_host="", dev_mode=True,
                           embedding_provider="local", **kw)


@pytest.fixture(scope="session")
def pg_admin_url(tmp_path_factory):
    if BACKEND != "postgres":
        return None
    url = os.environ.get("LAWPILOT_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver
    srv = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    return srv.get_uri()


def _pg_exec(admin_url, sql):
    import psycopg
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute(sql)


def _db_url(admin_url, name):
    base, _, _ = admin_url.rpartition("/")
    return f"{base}/{name}"


@pytest.fixture(scope="session")
def seeded(tmp_path_factory, pg_admin_url):
    """Seed the starter corpus once; each test gets a copy of the database."""
    d = tmp_path_factory.mktemp("seed")
    if BACKEND == "postgres":
        from lawpilot.db import close_pool
        _pg_exec(pg_admin_url, "DROP DATABASE IF EXISTS lp_template")
        _pg_exec(pg_admin_url, "CREATE DATABASE lp_template")
        url = _db_url(pg_admin_url, "lp_template")
        create_app(_base_cfg(d, seed_summaries=True, database_url=url))
        close_pool(url)                     # a template database must have no open connections
        return d
    create_app(_base_cfg(d, seed_summaries=True, database_url=""))
    return d


@pytest.fixture
def app(seeded, tmp_path, pg_admin_url):
    if BACKEND == "postgres":
        from lawpilot.db import close_pool
        name = "t_" + uuid.uuid4().hex[:12]
        _pg_exec(pg_admin_url, f"CREATE DATABASE {name} TEMPLATE lp_template")
        url = _db_url(pg_admin_url, name)
        cfg = _base_cfg(tmp_path, database_url=url)
        cfg.skip_seed = True
        application = create_app(cfg)
        application.config["TESTING"] = True
        yield application
        close_pool(url)
        _pg_exec(pg_admin_url, f"DROP DATABASE IF EXISTS {name}")
        return
    shutil.copy(seeded / "lawpilot.db", tmp_path / "lawpilot.db")
    cfg = _base_cfg(tmp_path, database_url="")
    cfg.skip_seed = True
    application = create_app(cfg)
    application.config["TESTING"] = True
    yield application


class Client:
    """Test client bound to one signed-in user."""

    def __init__(self, app, email=None, name="Test User"):
        self.c = app.test_client()
        self.user = None
        if email:
            r = self.post("/api/auth/register", {"email": email, "name": name, "password": PASSWORD})
            assert r.status_code == 201, r.json
            self.user = r.json["user"]

    def get(self, path):
        return self.c.get(path)

    def post(self, path, data=None, content_type=None):
        if content_type:                       # multipart upload
            return self.c.post(path, data=data, content_type=content_type, headers=H)
        return self.c.post(path, json=data or {}, headers=H)

    def patch(self, path, data=None):
        return self.c.patch(path, json=data or {}, headers=H)

    def delete(self, path, data=None):
        return self.c.delete(path, json=data or {}, headers=H)

    def refresh(self):
        self.user = self.get("/api/auth/me").json["user"]
        return self.user


ADMIN_EMAIL = "platform@lawpilot.test"


def _create_platform_admin(app):
    """Every test server gets a platform admin up front, so test lawyers are ordinary accounts."""
    from lawpilot import db as dbmod
    from lawpilot.auth import hash_password
    conn = dbmod.connect(app.config["LP"], pooled=False)
    ts = dbmod.now()
    conn.execute("INSERT INTO users (id, email, name, password_hash, is_platform_admin, prefs, created_at, updated_at)"
                 " VALUES (?,?,?,?,1,'{}',?,?)", (dbmod.new_id(), ADMIN_EMAIL, "Platform Admin",
                                                  hash_password(PASSWORD), ts, ts))
    conn.commit()
    conn.close()


@pytest.fixture
def make_client(app):
    _create_platform_admin(app)

    def factory(email=None, name="Test User"):
        return Client(app, email, name)
    return factory


@pytest.fixture
def admin(make_client):
    c = make_client()
    assert c.post("/api/auth/login", {"email": ADMIN_EMAIL, "password": PASSWORD}).status_code == 200
    c.refresh()
    assert c.user["is_platform_admin"]
    return c


class FakeLLM(LLMClient):
    """Deterministic stand-in for a real model."""
    provider = "fake"
    model = "fake-1"

    def __init__(self, answer=None, fail=False):
        self.answer = answer
        self.fail = fail
        self.calls = []

    def complete(self, system, user, max_tokens=4000, effort="medium"):
        self.calls.append((system, user))
        if self.fail:
            raise LLMError("simulated outage")
        if "sub-questions" in system:
            return LLMResult('{"sub_questions": ["Is privacy a fundamental right?"]}', self.provider, self.model)
        if "check whether cited" in system:
            return LLMResult('{"verdicts": []}', self.provider, self.model)
        return LLMResult(json.dumps(self.answer), self.provider, self.model)
