"""Platform administration: the legal corpus, ingestion monitoring, users, deletion requests."""
from __future__ import annotations

import ipaddress
import json
import socket
from urllib.parse import urljoin, urlparse

import requests
from flask import Blueprint, current_app, g, jsonify, request

from lawpilot import activity
from lawpilot.auth import ApiError, platform_admin_required, revoke_all_sessions
from lawpilot.db import get_db, new_id, now, query_all, query_one
from lawpilot.embeddings import get_embedder
from lawpilot.extract import ExtractionError, extract, html_to_text
from lawpilot.ingestion import AUTHORITY_CLASSES, KINDS, LEGAL_STATUSES, META_FIELDS, VERIFICATION, \
    bump_generation, delete_source, infer_court_level, ingest, reembed_all, safe_url
from lawpilot.routes import body, cfg, need, page_args

bp = Blueprint("admin", __name__, url_prefix="/api/admin")

MAX_IMPORT_BYTES = 20 * 1024 * 1024


@bp.get("/corpus/options")
@platform_admin_required
def options():
    return jsonify(kinds=KINDS, authority_classes=AUTHORITY_CLASSES, legal_statuses=sorted(LEGAL_STATUSES),
                   verification=sorted(VERIFICATION))


@bp.get("/corpus/stats")
@platform_admin_required
def stats():
    db = get_db()
    active_model = get_embedder(cfg()).name

    def group(col):
        return {r[0] or "unknown": r[1] for r in db.execute(
            f"SELECT {col}, COUNT(*) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL GROUP BY {col}")}
    return jsonify(
        by_kind=group("kind"), by_class=group("authority_class"), by_court_level=group("court_level"),
        by_verification=group("verification"), by_status=group("legal_status"), by_origin=group("origin"),
        total_sources=db.execute("SELECT COUNT(*) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL").fetchone()[0],
        inactive_sources=db.execute("SELECT COUNT(*) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL AND is_active = 0").fetchone()[0],
        matter_documents=db.execute("SELECT COUNT(*) FROM sources WHERE matter_id IS NOT NULL").fetchone()[0],
        total_passages=db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        fts_rows=db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0],
        embedding={"active_model": active_model,
                   "by_model": {r[0] or "none": r[1] for r in db.execute(
                       "SELECT embedding_model, COUNT(*) FROM chunks GROUP BY embedding_model")},
                   "stale": db.execute("SELECT COUNT(*) FROM chunks WHERE embedding_model IS NOT ?",
                                       (active_model,)).fetchone()[0]},
        jobs={r[0]: r[1] for r in db.execute("SELECT status, COUNT(*) FROM ingestion_jobs GROUP BY status")},
        recent_failures=query_all("SELECT input_ref, message, started_at FROM ingestion_jobs WHERE status = 'failed' "
                                  "ORDER BY started_at DESC LIMIT 10"),
        index_generation=(db.execute("SELECT value FROM meta WHERE key = 'index_generation'").fetchone() or ["0"])[0],
    )


@bp.get("/corpus/jobs")
@platform_admin_required
def jobs():
    limit, offset = page_args(50)
    status = request.args.get("status")
    sql = ("SELECT j.*, u.name AS created_by_name, s.title AS source_title FROM ingestion_jobs j "
           "LEFT JOIN users u ON u.id = j.created_by LEFT JOIN sources s ON s.id = j.source_id")
    params = []
    if status:
        sql += " WHERE j.status = ?"
        params.append(status)
    return jsonify(items=query_all(sql + " ORDER BY j.started_at DESC LIMIT ? OFFSET ?", params + [limit, offset]))


@bp.get("/corpus/sources")
@platform_admin_required
def list_sources():
    limit, offset = page_args(50)
    clauses, params = ["matter_id IS NULL AND chat_id IS NULL"], []
    q = (request.args.get("q") or "").strip()
    if q:
        clauses.append("(title LIKE ? OR citation LIKE ? OR court LIKE ? OR act_name LIKE ?)")
        params += [f"%{q}%"] * 4
    for key in ("kind", "authority_class", "verification", "legal_status", "origin"):
        if request.args.get(key):
            clauses.append(f"{key} = ?")
            params.append(request.args[key])
    where = " AND ".join(clauses)
    total = get_db().execute(f"SELECT COUNT(*) FROM sources WHERE {where}", params).fetchone()[0]
    rows = query_all(f"SELECT id, title, kind, authority_class, citation, court, decision_date, legal_status, "
                     f"verification, origin, is_active, current_version, updated_at, retrieved_at, verified_at, "
                     f"(SELECT COUNT(*) FROM chunks c WHERE c.source_id = sources.id) AS passages "
                     f"FROM sources WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?", params + [limit, offset])
    return jsonify(total=total, items=rows)


def _meta_from(data) -> dict:
    meta = {k: (data.get(k) or None) for k in META_FIELDS}
    meta["alt_citations"] = data.get("alt_citations") or ""
    return meta


def _finish(res, label: str):
    if res.status == "failed":
        raise ApiError(f"Ingestion failed: {res.message}")
    if res.status == "succeeded":
        activity.log("corpus.ingested", f"{label}: {res.message}", "source", res.source_id)
    return jsonify(id=res.source_id, status=res.status, message=res.message, version=res.version), \
        201 if res.status == "succeeded" else 200


@bp.post("/corpus/upload")
@platform_admin_required
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        raise ApiError("Choose a file.")
    meta = _meta_from(request.form)
    if not meta["title"]:
        meta["title"] = f.filename.rsplit(".", 1)[0]
    data = f.read()
    try:
        pages = extract(data, f.filename)
    except ExtractionError as exc:
        raise ApiError(str(exc))
    res = ingest(get_db(), cfg(), pages, meta, origin="admin_upload", user_id=g.user["id"], file_bytes=data,
                 filename=f.filename, mime=f.mimetype, note=request.form.get("note"))
    return _finish(res, f"Uploaded {meta['title']}")


@bp.post("/corpus/text")
@platform_admin_required
def paste_text():
    data = body()
    need(data, "title", "text")
    res = ingest(get_db(), cfg(), [(None, data["text"])], _meta_from(data), origin="admin_upload",
                 user_id=g.user["id"], note=data.get("note"))
    return _finish(res, f"Added {data['title']}")


def _check_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ApiError("Only http(s) URLs can be imported.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        raise ApiError("Could not resolve that host.")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ApiError("That URL points to a private network address and cannot be imported.")


def _fetch(url: str):
    """Download a public URL, re-checking every redirect hop against private addresses."""
    headers = {"User-Agent": "LawPilot corpus importer (+contact your administrator)"}
    for _ in range(4):
        _check_public_url(url)
        r = requests.get(url, headers=headers, timeout=30, allow_redirects=False, stream=True)
        if r.is_redirect:
            url = urljoin(url, r.headers.get("Location", ""))
            continue
        if r.status_code != 200:
            raise ApiError(f"The server returned HTTP {r.status_code}.")
        chunks, size = [], 0
        for part in r.iter_content(65536):
            size += len(part)
            if size > MAX_IMPORT_BYTES:
                raise ApiError("The document is larger than 20 MB.")
            chunks.append(part)
        return url, r.headers.get("Content-Type", ""), b"".join(chunks)
    raise ApiError("Too many redirects.")


@bp.post("/corpus/import-url")
@platform_admin_required
def import_url():
    from lawpilot import indiacode
    data = body()
    need(data, "url")
    url = data["url"].strip()
    official = None
    if indiacode.is_indiacode(url):
        # Viewer / handle links are web pages; fetch the real PDF and fill blanks from official metadata.
        try:
            official = indiacode.resolve(url)
        except indiacode.IndiaCodeError as exc:
            raise ApiError(str(exc))
        form_defaults = {"kind": "judgment", "verification": "unverified"}   # untouched dropdowns
        for k, v in official["meta"].items():
            given = (data.get(k) or "").strip()
            if v and (not given or form_defaults.get(k) == given):
                data[k] = v
        url = official["pdf_url"]
    try:
        final_url, ctype, content = _fetch(url)
    except requests.RequestException as exc:
        raise ApiError(f"Could not download: {exc}")
    is_pdf = "pdf" in ctype.lower() or final_url.lower().endswith(".pdf") or content[:5] == b"%PDF-"
    filename = official["filename"] if official else (final_url.rstrip("/").rsplit("/", 1)[-1] or "document")
    if not is_pdf and len(content) < 20000 and b"<script" in content.lower():
        raise ApiError("That link is a web page that loads its document with JavaScript, so there is no text to "
                       "import. Open the document, use its Download button, and import the downloaded PDF "
                       "(Upload file) or the direct .pdf link instead.")
    try:
        if is_pdf:
            pages = extract(content, filename if filename.lower().endswith(".pdf") else filename + ".pdf")
        else:
            pages = extract(content, "page.html")
    except ExtractionError as exc:
        raise ApiError(str(exc))
    meta = _meta_from(data)
    meta["source_url"] = meta["source_url"] or final_url
    if not meta["title"]:
        meta["title"] = filename
    note = data.get("note") or ("Fetched from India Code (official repository): " + meta["source_url"] if official else None)
    res = ingest(get_db(), cfg(), pages, meta, origin="indiacode" if official else "url_import", user_id=g.user["id"],
                 file_bytes=content, filename=filename if is_pdf else filename + ".html",
                 mime=ctype.split(";")[0] or None, note=note)
    return _finish(res, f"Imported {meta['title']}")


@bp.get("/corpus/url-preview")
@platform_admin_required
def url_preview():
    """Official details for a link, so the form can be filled before importing (India Code links)."""
    from lawpilot import indiacode
    url = (request.args.get("url") or "").strip()
    if not indiacode.is_indiacode(url):
        return jsonify(recognised=False, meta={})
    try:
        info = indiacode.resolve(url)
    except indiacode.IndiaCodeError as exc:
        raise ApiError(str(exc))
    return jsonify(recognised=True, source="India Code", meta=info["meta"], long_title=info["long_title"])


# ── Indian Kanoon official API (paid token from api.indiankanoon.org) ─────

KANOON_API = "https://api.indiankanoon.org"


def _kanoon(path: str, params: dict = None) -> dict:
    token = cfg().kanoon_api_token
    if not token:
        raise ApiError("Set KANOON_API_TOKEN to use the Indian Kanoon API.", 503)
    try:
        r = requests.post(f"{KANOON_API}{path}", data=params or {}, timeout=30,
                          headers={"Authorization": f"Token {token}", "Accept": "application/json"})
    except requests.RequestException as exc:
        raise ApiError(f"Indian Kanoon API unreachable: {exc}", 502)
    if r.status_code != 200:
        raise ApiError(f"Indian Kanoon API returned HTTP {r.status_code}: {r.text[:200]}", 502)
    try:
        return r.json()
    except ValueError:
        raise ApiError("Indian Kanoon API returned an unexpected response.", 502)


@bp.get("/corpus/kanoon/search")
@platform_admin_required
def kanoon_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        raise ApiError("Enter a search query.")
    data = _kanoon("/search/", {"formInput": q, "pagenum": request.args.get("page", "0")})
    docs = [{"doc_id": d.get("tid"), "title": html_to_text(d.get("title") or "").strip(),
             "court": d.get("docsource"), "date": d.get("publishdate"),
             "snippet": html_to_text(d.get("headline") or "").strip()[:400]} for d in data.get("docs", [])]
    return jsonify(items=docs)


@bp.post("/corpus/kanoon/import")
@platform_admin_required
def kanoon_import():
    data = body()
    need(data, "doc_id")
    doc_id = str(data["doc_id"]).strip()
    if not doc_id.isdigit():
        raise ApiError("Invalid document id.")
    doc = _kanoon(f"/doc/{doc_id}/")
    text = html_to_text(doc.get("doc") or "")
    meta = _meta_from(data)
    meta["title"] = meta["title"] or html_to_text(doc.get("title") or "").strip() or f"Indian Kanoon doc {doc_id}"
    meta["court"] = meta["court"] or doc.get("docsource")
    meta["decision_date"] = meta["decision_date"] or doc.get("publishdate")
    meta["source_url"] = f"https://indiankanoon.org/doc/{doc_id}/"
    meta["kind"] = meta["kind"] or "judgment"
    res = ingest(get_db(), cfg(), [(None, text)], meta, origin="kanoon_api", user_id=g.user["id"],
                 note=f"Indian Kanoon doc {doc_id}")
    return _finish(res, f"Imported Indian Kanoon doc {doc_id}")


# ── Edit / version / delete ────────────────────────────────────────────────

@bp.patch("/corpus/sources/<source_id>")
@platform_admin_required
def update_source(source_id):
    src = query_one("SELECT * FROM sources WHERE id = ? AND matter_id IS NULL AND chat_id IS NULL", (source_id,))
    if not src:
        raise ApiError("Source not found.", 404)
    data = body()
    fields = {}
    for k in META_FIELDS:
        if k in data:
            fields[k] = (str(data[k]).strip() or None) if data[k] is not None else None
    if "alt_citations" in data:
        v = data["alt_citations"]
        fields["alt_citations"] = json.dumps(v if isinstance(v, list) else [c.strip() for c in (v or "").split(";") if c.strip()])
    for key, allowed in (("kind", KINDS), ("authority_class", AUTHORITY_CLASSES), ("legal_status", LEGAL_STATUSES),
                         ("verification", VERIFICATION)):
        if key in fields and fields[key] not in allowed:
            raise ApiError(f"Invalid {key}.")
    if "source_url" in fields:
        fields["source_url"] = safe_url(fields["source_url"])
    if "court" in fields and "court_level" not in fields:
        fields["court_level"] = infer_court_level(fields["court"])
    if fields.get("verification") == "verified_official":
        fields["verified_at"] = now()
    if "is_active" in data:
        fields["is_active"] = 1 if data["is_active"] else 0
    if not fields:
        raise ApiError("Nothing to update.")
    fields["updated_at"] = now()
    db = get_db()
    db.execute(f"UPDATE sources SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
               list(fields.values()) + [source_id])
    s = db.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    meta_text = " ".join(filter(None, [s["title"], s["citation"], s["neutral_citation"], s["alt_citations"], s["court"],
                                       s["act_name"], s["parties"], s["judges"], s["case_number"]]))
    db.execute("UPDATE chunks_fts SET meta = ? WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id = ?)",
               (meta_text, source_id))
    bump_generation(db)
    db.commit()
    activity.log("corpus.updated", f"Updated '{s['title']}': {', '.join(k for k in fields if k != 'updated_at')}",
                 "source", source_id)
    return jsonify(ok=True)


@bp.post("/corpus/sources/<source_id>/version")
@platform_admin_required
def new_version(source_id):
    src = query_one("SELECT * FROM sources WHERE id = ? AND matter_id IS NULL AND chat_id IS NULL", (source_id,))
    if not src:
        raise ApiError("Source not found.", 404)
    meta = {k: src.get(k) for k in META_FIELDS}
    meta["alt_citations"] = src.get("alt_citations") or []
    f = request.files.get("file")
    if f and f.filename:
        data = f.read()
        try:
            pages = extract(data, f.filename)
        except ExtractionError as exc:
            raise ApiError(str(exc))
        res = ingest(get_db(), cfg(), pages, meta, origin=src["origin"], user_id=g.user["id"], file_bytes=data,
                     filename=f.filename, mime=f.mimetype, replace_source_id=source_id, note=request.form.get("note"))
    else:
        data = body()
        need(data, "text")
        res = ingest(get_db(), cfg(), [(None, data["text"])], meta, origin=src["origin"], user_id=g.user["id"],
                     replace_source_id=source_id, note=data.get("note"))
    return _finish(res, f"New version of {src['title']}")


@bp.delete("/corpus/sources/<source_id>")
@platform_admin_required
def remove_source(source_id):
    src = query_one("SELECT title FROM sources WHERE id = ? AND matter_id IS NULL AND chat_id IS NULL", (source_id,))
    if not src:
        raise ApiError("Source not found.", 404)
    delete_source(get_db(), cfg(), source_id)
    activity.log("corpus.deleted", f"Deleted '{src['title']}' from the corpus", "source", source_id)
    return jsonify(ok=True)


@bp.post("/corpus/relations")
@platform_admin_required
def add_relation():
    data = body()
    need(data, "from_source_id", "to_source_id", "relation")
    if data["relation"] not in ("overrules", "follows", "distinguishes", "affirms", "reverses"):
        raise ApiError("Invalid relation.")
    if data["from_source_id"] == data["to_source_id"]:
        raise ApiError("A source cannot relate to itself.")
    db = get_db()
    for sid in (data["from_source_id"], data["to_source_id"]):
        if not db.execute("SELECT 1 FROM sources WHERE id = ? AND matter_id IS NULL AND chat_id IS NULL", (sid,)).fetchone():
            raise ApiError("Source not found.", 404)
    rid = new_id()
    db.execute("INSERT OR IGNORE INTO authority_relations (id, from_source_id, to_source_id, relation, note) "
               "VALUES (?,?,?,?,?)", (rid, data["from_source_id"], data["to_source_id"], data["relation"],
                                      (data.get("note") or "")[:500] or None))
    if data["relation"] in ("overrules", "reverses") and data.get("update_status", True):
        db.execute("UPDATE sources SET legal_status = 'overruled', updated_at = ? WHERE id = ?",
                   (now(), data["to_source_id"]))
    db.commit()
    activity.log("corpus.relation_added", f"Recorded '{data['relation']}' relationship", "source", data["from_source_id"])
    return jsonify(id=rid), 201


@bp.delete("/corpus/relations")
@platform_admin_required
def delete_relation():
    data = body()
    need(data, "from_source_id", "to_source_id", "relation")
    db = get_db()
    db.execute("DELETE FROM authority_relations WHERE from_source_id = ? AND to_source_id = ? AND relation = ?",
               (data["from_source_id"], data["to_source_id"], data["relation"]))
    db.commit()
    return jsonify(ok=True)


@bp.post("/corpus/reembed")
@platform_admin_required
def reembed():
    from lawpilot.embeddings import EmbeddingError
    db = get_db()
    model = get_embedder(cfg()).name
    try:
        n = reembed_all(db, cfg())
    except EmbeddingError as exc:
        db.rollback()
        left = db.execute("SELECT COUNT(*) FROM chunks WHERE embedding_model IS NOT ?", (model,)).fetchone()[0]
        raise ApiError(f"Re-embedding stopped: {exc} Passages still waiting: {left}. "
                       f"Progress so far is kept; click Re-embed again to continue.", 502)
    activity.log("corpus.reembedded", f"Re-embedded {n} passages with {model}")
    return jsonify(reembedded=n)


# ── Users ──────────────────────────────────────────────────────────────────

@bp.get("/users")
@platform_admin_required
def users():
    q = (request.args.get("q") or "").strip()
    sql = ("SELECT u.id, u.email, u.name, u.status, u.is_platform_admin, u.created_at, u.last_login_at, "
           "f.name AS firm_name, fm.role FROM users u LEFT JOIN firm_members fm ON fm.user_id = u.id "
           "LEFT JOIN firms f ON f.id = fm.firm_id")
    params = []
    if q:
        sql += " WHERE u.email LIKE ? OR u.name LIKE ?"
        params = [f"%{q}%", f"%{q}%"]
    return jsonify(items=query_all(sql + " ORDER BY u.created_at DESC LIMIT 500", params))


@bp.patch("/users/<user_id>")
@platform_admin_required
def update_user(user_id):
    data = body()
    db = get_db()
    if user_id == g.user["id"] and (data.get("status") == "disabled" or data.get("is_platform_admin") is False):
        raise ApiError("You cannot disable or demote yourself.")
    if "status" in data:
        if data["status"] not in ("active", "disabled"):
            raise ApiError("Invalid status.")
        db.execute("UPDATE users SET status = ? WHERE id = ?", (data["status"], user_id))
        if data["status"] == "disabled":
            revoke_all_sessions(user_id)
    if data.get("is_platform_admin"):
        busy = db.execute("SELECT (SELECT COUNT(*) FROM firm_members WHERE user_id = ?) + "
                          "(SELECT COUNT(*) FROM matters WHERE owner_id = ?)", (user_id, user_id)).fetchone()[0]
        if busy:
            raise ApiError("This person belongs to a firm or has matters. Platform administrator accounts cannot hold "
                           "client work, so create a separate account for administration instead.")
    if "is_platform_admin" in data:
        db.execute("UPDATE users SET is_platform_admin = ? WHERE id = ?", (1 if data["is_platform_admin"] else 0, user_id))
    db.commit()
    activity.log("admin.user_updated", f"Updated user {user_id}: {json.dumps(data)}", "user", user_id)
    return jsonify(ok=True)


@bp.get("/deletion-requests")
@platform_admin_required
def deletion_requests():
    return jsonify(items=query_all(
        "SELECT d.*, u.email, u.name FROM deletion_requests d JOIN users u ON u.id = d.user_id "
        "WHERE d.status = 'pending' ORDER BY d.requested_at"))


@bp.post("/deletion-requests/<req_id>/process")
@platform_admin_required
def process_deletion(req_id):
    """Delete personal data; firm-owned records stay with the firm (retention)."""
    db = get_db()
    req = db.execute("SELECT * FROM deletion_requests WHERE id = ? AND status = 'pending'", (req_id,)).fetchone()
    if not req:
        raise ApiError("Request not found.", 404)
    uid = req["user_id"]
    membership = db.execute("SELECT * FROM firm_members WHERE user_id = ?", (uid,)).fetchone()
    if membership:
        heir = db.execute("SELECT user_id FROM firm_members WHERE firm_id = ? AND user_id != ? ORDER BY "
                          "CASE role WHEN 'firm_admin' THEN 0 WHEN 'partner' THEN 1 ELSE 2 END LIMIT 1",
                          (membership["firm_id"], uid)).fetchone()
        if heir:
            db.execute("UPDATE matters SET owner_id = ? WHERE owner_id = ? AND firm_id = ?",
                       (heir[0], uid, membership["firm_id"]))
            db.execute("UPDATE sources SET uploaded_by = NULL WHERE uploaded_by = ?", (uid,))
    for (sid,) in db.execute("SELECT s.id FROM sources s JOIN matters m ON m.id = s.matter_id WHERE m.owner_id = ?",
                             (uid,)).fetchall():
        delete_source(db, cfg(), sid)
    for (sid,) in db.execute("SELECT s.id FROM sources s JOIN ai_chats c ON c.id = s.chat_id WHERE c.user_id = ?",
                             (uid,)).fetchall():
        delete_source(db, cfg(), sid)
    db.execute("DELETE FROM ai_chats WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM matters WHERE owner_id = ?", (uid,))
    db.execute("DELETE FROM research_sessions WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM drafts WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM saved_items WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM firm_members WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    db.execute("UPDATE tasks SET assigned_to = NULL WHERE assigned_to = ?", (uid,))
    db.execute("UPDATE users SET email = ?, name = 'Deleted user', phone = NULL, bar_enrolment = NULL, "
               "password_hash = '!', status = 'disabled', prefs = '{}' WHERE id = ?", (f"deleted-{uid}@invalid", uid))
    db.execute("UPDATE deletion_requests SET status = 'completed', processed_at = ? WHERE id = ?", (now(), req_id))
    db.commit()
    activity.log("admin.account_deleted", f"Processed deletion request {req_id}", "user", uid)
    return jsonify(ok=True)


# ── AI settings ────────────────────────────────────────────────────────────

def _ai_view():
    from lawpilot import settings
    from lawpilot.llm import DEFAULT_MODELS, PROVIDER_LABELS
    from lawpilot.routes import llm
    client = llm()
    return {**settings.public_view(get_db(), current_app.config["LP"]),
            "providers": [{"key": k, "label": v, "default_model": DEFAULT_MODELS[k]} for k, v in PROVIDER_LABELS.items()],
            "active": {"provider": client.provider, "model": client.model} if client else None,
            "embedding_model": get_embedder(cfg()).name}


@bp.get("/ai")
@platform_admin_required
def ai_settings():
    return jsonify(_ai_view())


@bp.post("/ai")
@platform_admin_required
def save_ai_settings():
    from lawpilot import settings
    from lawpilot.llm import PROVIDER_LABELS
    data = body()
    updates = {}
    if "llm_provider" in data:
        if data["llm_provider"] not in ("", "none", *PROVIDER_LABELS):
            raise ApiError("Unknown provider.")
        updates["llm_provider"] = data["llm_provider"]
    if "llm_model" in data:
        updates["llm_model"] = data["llm_model"] or ""
    if "embedding_provider" in data:
        if data["embedding_provider"] not in ("local", "voyage", "openai"):
            raise ApiError("Unknown embedding provider.")
        updates["embedding_provider"] = data["embedding_provider"]
    for k, v in (data.get("keys") or {}).items():
        if k in settings.SECRET_FIELDS and v is not None:
            updates[k] = v
    settings.save(get_db(), current_app.config["LP"], updates, g.user["id"])
    g.pop("lp_cfg", None)
    changed = ", ".join(sorted(k for k in updates if not k.endswith("_key"))) or "API keys"
    activity.log("admin.ai_settings", f"Updated AI settings ({changed})")
    return jsonify(_ai_view())


def _key_for(provider: str, supplied: str = "") -> str:
    from lawpilot.llm import provider_keys
    return supplied or provider_keys(cfg()).get(provider, "")


@bp.post("/ai/test")
@platform_admin_required
def test_ai():
    import time
    from lawpilot.llm import LLMError, make_client
    data = body()
    need(data, "provider")
    started = time.time()
    try:
        client = make_client(data["provider"], _key_for(data["provider"], data.get("api_key") or ""),
                             data.get("model") or "")
        res = client.complete("You are a connectivity check. Reply with the single word OK.", "ping",
                              max_tokens=400, effort="low")
    except LLMError as exc:
        return jsonify(ok=False, error=str(exc))
    except Exception as exc:  # surface unexpected provider errors to the admin verbatim
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}")
    return jsonify(ok=True, reply=res.text.strip()[:200], model=res.model,
                   seconds=round(time.time() - started, 1))


@bp.post("/ai/models")
@platform_admin_required
def ai_models():
    from lawpilot.llm import LLMError, list_models
    data = body()
    need(data, "provider")
    try:
        return jsonify(models=list_models(data["provider"], _key_for(data["provider"], data.get("api_key") or "")))
    except LLMError as exc:
        raise ApiError(str(exc))


# ── Bulk imports & starter content ─────────────────────────────────────────

@bp.post("/imports/sci")
@platform_admin_required
def start_sci_import():
    from lawpilot.importers import start_background_import
    data = body()
    try:
        y1 = int(data.get("year_from"))
        y2 = int(data.get("year_to") or y1)
        limit = int(data["limit_per_year"]) if data.get("limit_per_year") else None
    except (TypeError, ValueError):
        raise ApiError("Enter valid years.")
    if not (1950 <= y1 <= y2 <= 2100) or y2 - y1 > 80:
        raise ApiError("Years must be between 1950 and the current year.")
    try:
        run_id = start_background_import(cfg(), list(range(y1, y2 + 1)), limit, g.user["id"])
    except RuntimeError as exc:
        raise ApiError(str(exc), 409)
    activity.log("corpus.import_started", f"Started Supreme Court import for {y1}-{y2}"
                 + (f" (up to {limit}/year)" if limit else ""), "import", run_id)
    return jsonify(id=run_id), 201


@bp.get("/imports")
@platform_admin_required
def list_imports():
    return jsonify(items=query_all("SELECT r.*, u.name AS created_by_name FROM import_runs r "
                                   "LEFT JOIN users u ON u.id = r.created_by ORDER BY r.started_at DESC LIMIT 50"))


@bp.post("/imports/<run_id>/cancel")
@platform_admin_required
def cancel_import(run_id):
    db = get_db()
    db.execute("UPDATE import_runs SET status = 'cancelling' WHERE id = ? AND status = 'running'", (run_id,))
    db.commit()
    return jsonify(ok=True)


@bp.post("/corpus/starter")
@platform_admin_required
def starter_summaries():
    """Load or remove the 66 starter editorial summaries."""
    action = body().get("action")
    db = get_db()
    if action == "load":
        from lawpilot.seed import seed_corpus
        n = seed_corpus(db, cfg())
        activity.log("corpus.starter_loaded", f"Loaded {n} starter summaries")
        return jsonify(count=n)
    if action == "remove":
        ids = [r[0] for r in db.execute("SELECT id FROM sources WHERE origin = 'seed'").fetchall()]
        for sid in ids:
            delete_source(db, cfg(), sid)
        activity.log("corpus.starter_removed", f"Removed {len(ids)} starter summaries")
        return jsonify(count=len(ids))
    raise ApiError("action must be 'load' or 'remove'.")


# ── Platform overview & firms (counts only; no client content) ─────────────

@bp.get("/overview")
@platform_admin_required
def overview():
    from lawpilot.routes import llm
    db = get_db()
    one = lambda sql, p=(): db.execute(sql, p).fetchone()[0]  # noqa: E731
    from datetime import datetime, timedelta, timezone
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()
    client = llm()
    model = get_embedder(cfg()).name
    last_import = query_one("SELECT status, imported, failed, message, started_at FROM import_runs "
                            "ORDER BY started_at DESC LIMIT 1")
    c = cfg()
    return jsonify(
        users={"total": one("SELECT COUNT(*) FROM users WHERE email NOT LIKE 'deleted-%'"),
               "active_7d": one("SELECT COUNT(*) FROM users WHERE is_platform_admin = 0 AND last_login_at >= ?", (week_ago,)),
               "admins": one("SELECT COUNT(*) FROM users WHERE is_platform_admin = 1"),
               "disabled": one("SELECT COUNT(*) FROM users WHERE status = 'disabled'"),
               "deletion_pending": one("SELECT COUNT(*) FROM deletion_requests WHERE status = 'pending'")},
        firms=one("SELECT COUNT(*) FROM firms"),
        individuals=one("SELECT COUNT(*) FROM users u WHERE is_platform_admin = 0 AND status = 'active' AND "
                        "NOT EXISTS (SELECT 1 FROM firm_members fm WHERE fm.user_id = u.id)"),
        matters=one("SELECT COUNT(*) FROM matters"),
        matter_documents=one("SELECT COUNT(*) FROM sources WHERE matter_id IS NOT NULL"),
        research_7d=one("SELECT COUNT(*) FROM research_sessions WHERE created_at >= ?", (week_ago,)),
        library={"total": one("SELECT COUNT(*) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL AND is_active = 1"),
                 "judgments": one("SELECT COUNT(*) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL AND kind = 'judgment'"),
                 "acts": one("SELECT COUNT(*) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL AND kind IN "
                             "('statute', 'constitution', 'rule', 'notification')"),
                 "summaries": one("SELECT COUNT(*) FROM sources WHERE origin = 'seed'"),
                 "passages": one("SELECT COUNT(*) FROM chunks c JOIN sources s ON s.id = c.source_id "
                                 "WHERE s.matter_id IS NULL AND s.chat_id IS NULL"),
                 "stale_embeddings": one("SELECT COUNT(*) FROM chunks WHERE embedding_model IS NOT ?", (model,))},
        last_import=last_import,
        ai={"configured": bool(client), "provider": client.provider if client else None,
            "model": client.model if client else None},
        embedding_model=model,
        email_configured=bool(c.smtp_host),
        database="PostgreSQL" if db.dialect == "postgres" else "SQLite",
        registration_open=c.allow_registration,
        recent_users=query_all("SELECT u.name, u.email, u.created_at, u.last_login_at, f.name AS firm_name "
                               "FROM users u LEFT JOIN firm_members fm ON fm.user_id = u.id LEFT JOIN firms f "
                               "ON f.id = fm.firm_id WHERE u.is_platform_admin = 0 ORDER BY u.created_at DESC LIMIT 6"),
    )


@bp.get("/firms")
@platform_admin_required
def firms():
    rows = query_all(
        "SELECT f.id, f.name, f.city, f.created_at, "
        "(SELECT COUNT(*) FROM firm_members m WHERE m.firm_id = f.id) AS members, "
        "(SELECT COUNT(*) FROM matters x WHERE x.firm_id = f.id) AS matters, "
        "(SELECT MAX(u.last_login_at) FROM firm_members m JOIN users u ON u.id = m.user_id "
        " WHERE m.firm_id = f.id) AS last_active FROM firms f ORDER BY f.created_at DESC")
    for r in rows:
        r["admins"] = query_all("SELECT u.name, u.email FROM firm_members m JOIN users u ON u.id = m.user_id "
                                "WHERE m.firm_id = ? AND m.role = 'firm_admin'", (r["id"],))
    return jsonify(items=rows)
