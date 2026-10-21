"""Database schema and connection helpers for SQLite and PostgreSQL.

SQLite (default, one file) suits development and a single small office.
PostgreSQL (set DATABASE_URL) is for real deployments: full-text search uses a
GIN-indexed tsvector, and vector search uses pgvector when it is installed.

Application code writes SQLite-style SQL with `?` placeholders; the Postgres
wrapper translates it (placeholders, INSERT OR IGNORE, case-insensitive LIKE).
Search queries that differ between engines branch on `conn.dialect`.
"""
from __future__ import annotations

import functools
import json
import logging
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from flask import current_app, g

log = logging.getLogger("lawpilot.db")

try:
    import psycopg
    DB_ERRORS = (sqlite3.Error, psycopg.Error)
except ImportError:  # psycopg is only needed for PostgreSQL
    psycopg = None
    DB_ERRORS = (sqlite3.Error,)

EMBED_DIM = 1024   # every embedding provider is configured to produce 1024 dimensions

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- ── Accounts ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    phone TEXT,
    bar_enrolment TEXT,
    is_platform_admin INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',          -- active | deletion_requested | disabled
    prefs TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS password_resets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS deletion_requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason TEXT,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',          -- pending | cancelled | completed
    processed_at TEXT
);

-- ── Firms ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS firms (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS firm_members (
    firm_id TEXT NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    permission_overrides TEXT NOT NULL DEFAULT '{}',
    joined_at TEXT NOT NULL,
    PRIMARY KEY (firm_id, user_id)
);

CREATE TABLE IF NOT EXISTS firm_invitations (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    invited_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    revoked_at TEXT
);

-- ── Matters ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS matters (
    id TEXT PRIMARY KEY,
    firm_id TEXT REFERENCES firms(id) ON DELETE SET NULL,
    owner_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    client_name TEXT,
    case_number TEXT,
    case_type TEXT,
    court TEXT,
    jurisdiction TEXT,
    opposing_party TEXT,
    stage TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'open',             -- open | closed | archived
    confidential INTEGER NOT NULL DEFAULT 0,
    next_hearing TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matter_members (
    matter_id TEXT NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    access TEXT NOT NULL DEFAULT 'member',           -- lead | member | viewer
    added_at TEXT NOT NULL,
    PRIMARY KEY (matter_id, user_id)
);

CREATE TABLE IF NOT EXISTS hearings (
    id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    purpose TEXT,
    court_room TEXT,
    notes TEXT,
    outcome TEXT,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    matter_id TEXT REFERENCES matters(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    assigned_to TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_by TEXT NOT NULL REFERENCES users(id),
    due_date TEXT,
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'open',             -- open | done
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    author_id TEXT NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- ── Sources (legal corpus + matter documents) ───────────────────────────
-- authority_class: primary | official | secondary | user_upload
-- legal_status:    good_law | overruled | partly_overruled | repealed | amended | unknown
-- verification:    unverified | editorial_summary | verified_official
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    authority_class TEXT NOT NULL,
    title TEXT NOT NULL,
    citation TEXT,
    neutral_citation TEXT,
    alt_citations TEXT NOT NULL DEFAULT '[]',
    case_number TEXT,
    court TEXT,
    court_level TEXT NOT NULL DEFAULT 'none',        -- supreme | high | tribunal | district | none
    bench TEXT,
    judges TEXT,
    parties TEXT,
    decision_date TEXT,
    jurisdiction TEXT,
    act_name TEXT,
    legal_domain TEXT,
    legal_status TEXT NOT NULL DEFAULT 'unknown',
    status_note TEXT,
    verification TEXT NOT NULL DEFAULT 'unverified',
    verified_at TEXT,
    source_url TEXT,
    origin TEXT NOT NULL,                            -- seed | admin_upload | url_import | user_upload | kanoon_api
    retrieved_at TEXT,
    matter_id TEXT REFERENCES matters(id) ON DELETE CASCADE,
    uploaded_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    original_filename TEXT,
    mime TEXT,
    page_count INTEGER,
    sha256 TEXT,
    current_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ready',            -- processing | ready | failed
    error TEXT,
    security_flags TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sources_matter ON sources(matter_id);
CREATE INDEX IF NOT EXISTS ix_sources_sha ON sources(sha256);
CREATE INDEX IF NOT EXISTS ix_sources_citation ON sources(citation);

CREATE TABLE IF NOT EXISTS source_versions (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    original_text TEXT NOT NULL,
    file_path TEXT,
    note TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (source_id, version)
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    heading TEXT,
    page INTEGER,
    para_ref TEXT,
    section_ref TEXT,
    token_count INTEGER NOT NULL DEFAULT 0,
    embedding BLOB,
    embedding_model TEXT
);
CREATE INDEX IF NOT EXISTS ix_chunks_source ON chunks(source_id);
CREATE INDEX IF NOT EXISTS ix_chunks_model ON chunks(embedding_model);

CREATE TABLE IF NOT EXISTS authority_relations (
    id TEXT PRIMARY KEY,
    from_source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    to_source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,                          -- overrules | follows | distinguishes | affirms | reverses
    note TEXT,
    UNIQUE (from_source_id, to_source_id, relation)
);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id TEXT PRIMARY KEY,
    source_id TEXT,
    input_ref TEXT,
    status TEXT NOT NULL,                            -- running | succeeded | failed | duplicate
    message TEXT,
    created_by TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

-- ── Research ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS research_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    matter_id TEXT REFERENCES matters(id) ON DELETE SET NULL,
    query TEXT NOT NULL,
    mode TEXT NOT NULL,
    filters TEXT NOT NULL DEFAULT '{}',
    analysis TEXT NOT NULL DEFAULT '{}',
    outcome TEXT NOT NULL,          -- answered | insufficient_evidence | no_relevant_authority | retrieval_failed | evidence_only
    answer TEXT NOT NULL DEFAULT '{}',
    evidence TEXT NOT NULL DEFAULT '[]',
    verification TEXT NOT NULL DEFAULT '{}',
    llm_model TEXT,
    embedding_model TEXT,
    corpus_snapshot TEXT NOT NULL DEFAULT '{}',
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_research_user ON research_sessions(user_id, created_at);

CREATE TABLE IF NOT EXISTS saved_items (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    matter_id TEXT REFERENCES matters(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                              -- research | authority
    research_id TEXT REFERENCES research_sessions(id) ON DELETE CASCADE,
    source_id TEXT REFERENCES sources(id) ON DELETE CASCADE,
    chunk_id TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);

-- ── Drafts ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    matter_id TEXT REFERENCES matters(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    template_key TEXT,
    fields TEXT NOT NULL DEFAULT '{}',
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    check_report TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_versions (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- ── Activity ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS activity_log (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    user_id TEXT,
    firm_id TEXT,
    matter_id TEXT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    summary TEXT,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS ix_activity_user ON activity_log(user_id, ts);
CREATE INDEX IF NOT EXISTS ix_activity_firm ON activity_log(firm_id, ts);

-- ── Server settings (AI provider, keys stored encrypted) ─────────────────
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_by TEXT,
    updated_at TEXT
);

-- ── AI mode (chat assistant) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_chats (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    matter_id TEXT REFERENCES matters(id) ON DELETE SET NULL,
    use_library INTEGER NOT NULL DEFAULT 1,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_chats_user ON ai_chats(user_id, updated_at);

CREATE TABLE IF NOT EXISTS ai_messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES ai_chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL,              -- user | assistant
    content TEXT NOT NULL,
    citations TEXT NOT NULL DEFAULT '[]',
    attachments TEXT NOT NULL DEFAULT '[]',
    extra TEXT NOT NULL DEFAULT '{}',
    model TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_messages_chat ON ai_messages(chat_id, created_at);

-- ── Bulk imports (e.g. Supreme Court judgments dataset) ─────────────────
CREATE TABLE IF NOT EXISTS import_runs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,            -- running | completed | failed | cancelled
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    imported INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    created_by TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    heartbeat_at TEXT
);
"""

SQLITE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    text,
    heading,
    meta,
    tokenize = 'porter unicode61'
);
"""

PG_FTS = """
CREATE TABLE IF NOT EXISTS chunks_fts (
    chunk_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    heading TEXT,
    meta TEXT,
    tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(meta, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(heading, '')), 'B') ||
        to_tsvector('english', text)
    ) STORED
);
CREATE INDEX IF NOT EXISTS ix_chunks_fts_tsv ON chunks_fts USING GIN (tsv);
"""

JSON_FIELDS = {
    "prefs", "permission_overrides", "alt_citations", "security_flags", "filters",
    "analysis", "answer", "evidence", "verification", "corpus_snapshot", "fields",
    "check_report", "citations", "attachments", "extra",
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


# ── Connections ────────────────────────────────────────────────────────────

class SQLiteConn:
    dialect = "sqlite"
    has_pgvector = False

    def __init__(self, path):
        self.raw = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self.raw.row_factory = sqlite3.Row
        self.raw.execute("PRAGMA foreign_keys = ON")
        self.raw.execute("PRAGMA journal_mode = WAL")
        self.raw.execute("PRAGMA synchronous = NORMAL")

    def execute(self, sql, params=()):
        return self.raw.execute(sql, params)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


class PGRow(dict):
    """Row usable both as r["col"] and r[0], like sqlite3.Row (first duplicate column wins)."""
    __slots__ = ("_vals",)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return dict.__getitem__(self, key)

    def __iter__(self):
        # Like sqlite3.Row: `for (sid,) in rows` / `a, b = row` unpack values. dict(row) still
        # copies names -> values (it goes through keys() once __iter__ is overridden).
        return iter(self._vals)


def _pg_row_factory(cursor):
    names = [d.name for d in cursor.description] if cursor.description else []

    def make(values):
        row = PGRow()
        row._vals = values
        for n, v in zip(names, values):
            if n not in row:
                dict.__setitem__(row, n, v)
        return row
    return make


@functools.lru_cache(maxsize=2048)
def translate_sql(sql: str) -> str:
    """SQLite-flavoured SQL -> PostgreSQL."""
    out = sql.replace("%", "%%")
    if "INSERT OR IGNORE INTO" in out:
        out = out.replace("INSERT OR IGNORE INTO", "INSERT INTO").rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    out = re.sub(r"\bIS NOT \?", "IS DISTINCT FROM ?", out)
    out = re.sub(r"\bLIKE\b", "ILIKE", out)
    # Replace ? placeholders outside single-quoted literals.
    parts = re.split(r"('(?:[^']|'')*')", out)
    return "".join(p if i % 2 else p.replace("?", "%s") for i, p in enumerate(parts))


class PGConn:
    dialect = "postgres"

    def __init__(self, raw, pool=None, has_pgvector=False, vector_type="vector", url=""):
        self.raw = raw
        self.pool = pool
        self.url = url
        self.has_pgvector = has_pgvector
        self.vector_type = vector_type

    def execute(self, sql, params=()):
        return self.raw.execute(translate_sql(sql), list(params) if params else None)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        if self.pool is not None:
            try:
                self.raw.rollback()
            except Exception:
                pass
            self.pool.putconn(self.raw)
        else:
            self.raw.close()


_pools: Dict[str, Any] = {}
_pg_features: Dict[str, tuple] = {}
_pool_lock = threading.Lock()


def _configure_pg(raw):
    raw.row_factory = _pg_row_factory
    raw.prepare_threshold = None          # safe behind PgBouncer / Neon's pooler


def _pool(url: str):
    from psycopg_pool import ConnectionPool
    with _pool_lock:
        if url not in _pools:
            _pools[url] = ConnectionPool(url, min_size=1, max_size=10, configure=_configure_pg,
                                         check=ConnectionPool.check_connection, open=True,
                                         kwargs={"connect_timeout": 15})
        return _pools[url]


class ResilientConn:
    """For long background jobs: reconnects when the server dropped an idle connection.

    Hosted PostgreSQL (e.g. Neon) closes connections that sit idle, such as while a large PDF
    downloads. A failed statement is retried on a fresh connection only when no uncommitted
    writes would be lost; otherwise the error propagates and the caller's own rollback applies.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = connect(cfg, pooled=False)
        self.dirty = False

    def __getattr__(self, name):           # dialect, has_pgvector, vector_type, raw ...
        return getattr(self.conn, name)

    def _lost(self) -> bool:
        raw = getattr(self.conn, "raw", None)
        return bool(getattr(raw, "closed", False) or getattr(raw, "broken", False))

    def _reconnect(self):
        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = connect(self.cfg, pooled=False)
        self.dirty = False

    def execute(self, sql, params=()):
        try:
            cur = self.conn.execute(sql, params)
        except DB_ERRORS:
            if not (self.conn.dialect == "postgres" and self._lost() and not self.dirty):
                raise
            log.warning("Database connection was dropped; reconnecting")
            self._reconnect()
            cur = self.conn.execute(sql, params)
        if not sql.lstrip().upper().startswith("SELECT"):
            self.dirty = True
        return cur

    def commit(self):
        self.conn.commit()
        self.dirty = False

    def rollback(self):
        try:
            self.conn.rollback()
        except DB_ERRORS:
            if not self._lost():
                raise
            self._reconnect()
        self.dirty = False

    def close(self):
        self.conn.close()


def close_pool(url: str) -> None:
    with _pool_lock:
        pool = _pools.pop(url, None)
    if pool is not None:
        pool.close()


def connect(cfg, pooled: bool = True):
    """Open a connection for this config (SQLite file or PostgreSQL URL)."""
    url = getattr(cfg, "database_url", "") or ""
    if not url.startswith(("postgres://", "postgresql://")):
        return SQLiteConn(cfg.db_path)
    if psycopg is None:
        raise RuntimeError("DATABASE_URL points to PostgreSQL but psycopg is not installed: pip install 'psycopg[binary]' psycopg-pool")
    if pooled:
        pool = _pool(url)
        raw = pool.getconn()
    else:
        pool = None
        raw = psycopg.connect(url, connect_timeout=15)
        _configure_pg(raw)
    has_vec, vtype = _pg_features.get(url, (False, "vector"))
    return PGConn(raw, pool, has_vec, vtype, url)


MIGRATIONS = [
    "ALTER TABLE import_runs ADD COLUMN heartbeat_at TEXT",
    # Files uploaded into an AI-mode chat; NULL for the library and matter documents.
    "ALTER TABLE sources ADD COLUMN chat_id TEXT REFERENCES ai_chats(id) ON DELETE CASCADE",
    "CREATE INDEX IF NOT EXISTS ix_sources_chat ON sources(chat_id)",
]


def _migrate(conn) -> None:
    """Add columns introduced after a database was created (each statement is a no-op if already applied)."""
    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
            conn.commit()
        except DB_ERRORS:
            conn.rollback()


def init_schema(conn) -> None:
    if conn.dialect == "sqlite":
        conn.raw.executescript(SCHEMA + SQLITE_FTS)
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        conn.commit()
        _migrate(conn)
        return
    raw = conn.raw
    raw.execute(SCHEMA.replace("BLOB", "BYTEA") + PG_FTS)
    raw.execute("INSERT INTO meta(key, value) VALUES ('schema_version', %s) ON CONFLICT DO NOTHING",
                (str(SCHEMA_VERSION),))
    raw.commit()
    _migrate(conn)
    has_vec, vtype = _setup_pgvector(raw)
    _pg_features[conn.url] = (has_vec, vtype)
    conn.has_pgvector, conn.vector_type = has_vec, vtype


def _setup_pgvector(raw):
    """Enable pgvector if the server has it. Uses halfvec (half the storage) on pgvector >= 0.7."""
    try:
        raw.execute("CREATE EXTENSION IF NOT EXISTS vector")
        raw.commit()
    except Exception as exc:
        raw.rollback()
        log.warning("pgvector is not available (%s); falling back to in-memory vector search", exc)
        return False, "vector"
    existing = raw.execute(
        "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "WHERE c.relname = 'chunks' AND a.attname = 'embedding_vec' AND NOT a.attisdropped").fetchone()
    if existing:
        vtype = "halfvec" if existing[0].startswith("halfvec") else "vector"
    else:
        version = raw.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
        major, minor = (int(x) for x in version.split(".")[:2])
        vtype = "halfvec" if (major, minor) >= (0, 7) else "vector"
        raw.execute(f"ALTER TABLE chunks ADD COLUMN embedding_vec {vtype}({EMBED_DIM})")
        raw.execute(f"CREATE INDEX IF NOT EXISTS ix_chunks_vec ON chunks USING hnsw (embedding_vec {vtype}_cosine_ops)")
        raw.commit()
    return True, vtype


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["LP"])
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def row_to_dict(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    d.pop("embedding_vec", None)
    for key in JSON_FIELDS.intersection(d.keys()):
        if isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except ValueError:
                pass
    d.pop("embedding", None)
    d.pop("password_hash", None)
    d.pop("token_hash", None)
    return d


def rows_to_dicts(rows: Iterable) -> List[Dict[str, Any]]:
    return [row_to_dict(r) for r in rows]


def query_one(sql: str, params=()) -> Optional[Dict[str, Any]]:
    return row_to_dict(get_db().execute(sql, params).fetchone())


def query_all(sql: str, params=()) -> List[Dict[str, Any]]:
    return rows_to_dicts(get_db().execute(sql, params).fetchall())
