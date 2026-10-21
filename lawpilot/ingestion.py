"""Ingestion pipeline: text -> sanitised chunks -> embeddings + full-text index.

Used for both the public legal corpus (admin uploads, URL imports, seed data)
and private matter documents (matter_id set). Every ingest:
  * stores the original text as an immutable source version,
  * detects exact duplicates by content hash within the same scope,
  * records an ingestion job (success, duplicate or failure) for the admin view.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from werkzeug.utils import secure_filename

from lawpilot import guard
from lawpilot.chunking import chunk_pages
from lawpilot.db import new_id, now
from lawpilot.embeddings import EmbeddingError, LocalHashEmbedder, get_embedder, to_blob

log = logging.getLogger("lawpilot.ingestion")

KINDS = {
    "judgment": "Judgment / order",
    "statute": "Act / statute",
    "constitution": "Constitution",
    "rule": "Rules / regulations",
    "notification": "Notification / circular / gazette",
    "commentary": "Commentary / article",
    "editorial_summary": "Editorial case summary",
    "user_document": "Matter document",
}
AUTHORITY_CLASSES = {
    "primary": "Primary authority",
    "official": "Official explanatory material",
    "secondary": "Secondary source",
    "user_upload": "User-uploaded document",
}
LEGAL_STATUSES = {"good_law", "overruled", "partly_overruled", "repealed", "amended", "unknown"}
VERIFICATION = {"unverified", "editorial_summary", "verified_official"}

META_FIELDS = ["kind", "authority_class", "title", "citation", "neutral_citation", "case_number", "court",
               "court_level", "bench", "judges", "parties", "decision_date", "jurisdiction", "act_name",
               "legal_domain", "legal_status", "status_note", "verification", "source_url"]


@dataclass
class IngestResult:
    source_id: Optional[str]
    status: str          # succeeded | duplicate | failed
    message: str = ""
    chunks: int = 0
    version: int = 1


def infer_court_level(court: Optional[str]) -> str:
    c = (court or "").lower()
    if "supreme court" in c:
        return "supreme"
    if "high court" in c:
        return "high"
    if any(k in c for k in ("tribunal", "nclat", "nclt", "itat", "cestat", "ngt", "commission", "appellate authority")):
        return "tribunal"
    if any(k in c for k in ("district", "sessions", "magistrate", "civil judge", "family court", "commercial court")):
        return "district"
    return "none"


def text_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().lower().encode()).hexdigest()


def bump_generation(db) -> None:
    db.execute("INSERT INTO meta(key, value) VALUES ('index_generation', '1') "
               "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(meta.value AS INTEGER) + 1 AS TEXT)")


def store_embedding_sql(db) -> Tuple[str, str]:
    """(column, placeholder) for writing an embedding on this database."""
    if db.dialect == "postgres" and db.has_pgvector:
        return "embedding_vec", f"?::{db.vector_type}"
    return "embedding", "?"


def embedding_value(db, vec):
    if db.dialect == "postgres" and db.has_pgvector:
        return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    return to_blob(vec)


def _normalise_meta(meta: Dict) -> Dict:
    m = {k: (meta.get(k) or None) for k in META_FIELDS}
    if isinstance(meta.get("alt_citations"), list):
        m["alt_citations"] = meta["alt_citations"]
    else:
        m["alt_citations"] = [c.strip() for c in (meta.get("alt_citations") or "").split(";") if c.strip()]
    for k in ("title", "citation", "court", "parties", "judges", "act_name"):
        if m.get(k):
            m[k] = str(m[k]).strip()[:500]
    m["kind"] = m["kind"] if m["kind"] in KINDS else "judgment"
    m["authority_class"] = m["authority_class"] if m["authority_class"] in AUTHORITY_CLASSES else "primary"
    m["legal_status"] = m["legal_status"] if m["legal_status"] in LEGAL_STATUSES else "unknown"
    m["verification"] = m["verification"] if m["verification"] in VERIFICATION else "unverified"
    m["court_level"] = m["court_level"] or infer_court_level(m["court"])
    m["source_url"] = safe_url(m["source_url"])
    return m


def safe_url(url: Optional[str]) -> Optional[str]:
    """Only http(s) links are stored, since they are rendered as clickable links."""
    url = (url or "").strip()
    return url if re.match(r"^https?://", url, re.I) else None


def ingest(db, cfg, pages: List[Tuple[Optional[int], str]], meta: Dict, *,
           origin: str, user_id: Optional[str] = None, matter_id: Optional[str] = None,
           file_bytes: Optional[bytes] = None, filename: Optional[str] = None, mime: Optional[str] = None,
           replace_source_id: Optional[str] = None, note: Optional[str] = None,
           chat_id: Optional[str] = None) -> IngestResult:
    job_id = new_id()
    started = now()
    input_ref = filename or meta.get("source_url") or meta.get("title") or "text"
    db.execute("INSERT INTO ingestion_jobs (id, input_ref, status, created_by, started_at) VALUES (?,?,?,?,?)",
               (job_id, input_ref[:300], "running", user_id, started))
    try:
        result = _ingest(db, cfg, pages, meta, origin=origin, user_id=user_id, matter_id=matter_id,
                         file_bytes=file_bytes, filename=filename, mime=mime,
                         replace_source_id=replace_source_id, note=note, chat_id=chat_id)
    except Exception as exc:  # recorded for the admin view, then surfaced to the caller
        db.rollback()
        # The rollback also undid the 'running' job row, so record the failure as a fresh row.
        db.execute("INSERT INTO ingestion_jobs (id, input_ref, status, message, created_by, started_at,"
                   " finished_at) VALUES (?,?,?,?,?,?,?)",
                   (job_id, input_ref[:300], "failed", str(exc)[:1000], user_id, started, now()))
        db.commit()
        log.exception("Ingestion failed for %s", input_ref)
        return IngestResult(None, "failed", str(exc))
    db.execute("UPDATE ingestion_jobs SET status = ?, message = ?, source_id = ?, finished_at = ? WHERE id = ?",
               (result.status, result.message, result.source_id, now(), job_id))
    db.commit()
    return result


def _ingest(db, cfg, pages, meta, *, origin, user_id, matter_id, file_bytes, filename, mime,
            replace_source_id, note, chat_id=None) -> IngestResult:
    m = _normalise_meta(meta)
    if not m["title"]:
        raise ValueError("A title is required.")
    raw_text = "\n\n".join(t for _, t in pages)
    if len(raw_text.strip()) < 40:
        raise ValueError("The document contains too little text to index.")
    sha = text_hash(raw_text)

    if replace_source_id:
        existing = db.execute("SELECT * FROM sources WHERE id = ?", (replace_source_id,)).fetchone()
        if not existing:
            raise ValueError("Source to update not found.")
        if existing["sha256"] == sha:
            return IngestResult(replace_source_id, "duplicate", "Identical to the current version; nothing changed.",
                                version=existing["current_version"])
    else:
        if chat_id:
            scope_sql, params = "chat_id = ?", (sha, chat_id)
        elif matter_id:
            scope_sql, params = "matter_id = ?", (sha, matter_id)
        else:
            scope_sql, params = "matter_id IS NULL AND chat_id IS NULL", (sha,)
        dup = db.execute(f"SELECT id, title FROM sources WHERE sha256 = ? AND {scope_sql} AND is_active = 1",
                         params).fetchone()
        if dup:
            return IngestResult(dup["id"], "duplicate", f"Already indexed as '{dup['title']}'.")

    sanitized_pages, flags = [], set()
    for page_no, text in pages:
        clean, f = guard.sanitize(text)
        flags.update(f)
        sanitized_pages.append((page_no, clean))

    is_statute = m["kind"] in ("statute", "constitution", "rule", "notification")
    chunks = chunk_pages(sanitized_pages, is_statute=is_statute,
                         para_numbers=m["kind"] not in ("editorial_summary", "commentary"))
    if not chunks:
        raise ValueError("No indexable passages were produced from this document.")

    ts = now()
    page_count = max((p for p, _ in pages if p is not None), default=None)
    if replace_source_id:
        source_id = replace_source_id
        version = db.execute("SELECT current_version FROM sources WHERE id = ?", (source_id,)).fetchone()[0] + 1
        _delete_chunks(db, source_id)
        sets = ", ".join(f"{k} = ?" for k in META_FIELDS)
        db.execute(f"UPDATE sources SET {sets}, alt_citations = ?, sha256 = ?, current_version = ?, page_count = ?,"
                   f" security_flags = ?, updated_at = ?, retrieved_at = ?, status = 'ready', error = NULL WHERE id = ?",
                   [m[k] for k in META_FIELDS] + [json.dumps(m["alt_citations"]), sha, version, page_count,
                                                  json.dumps(sorted(flags)), ts, ts, source_id])
    else:
        source_id = new_id()
        version = 1
        cols = META_FIELDS + ["alt_citations", "origin", "retrieved_at", "matter_id", "uploaded_by",
                              "original_filename", "mime", "page_count", "sha256", "current_version",
                              "security_flags", "created_at", "updated_at", "id", "chat_id"]
        vals = [m[k] for k in META_FIELDS] + [json.dumps(m["alt_citations"]), origin, ts, matter_id, user_id,
                                              filename, mime, page_count, sha, 1, json.dumps(sorted(flags)), ts, ts,
                                              source_id, chat_id]
        db.execute(f"INSERT INTO sources ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)

    file_path = None
    if file_bytes is not None:
        folder = Path(cfg.upload_dir) / source_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"v{version}_{secure_filename(filename or 'document') or 'document'}"
        path.write_bytes(file_bytes)
        file_path = str(path)

    db.execute("INSERT INTO source_versions (id, source_id, version, sha256, original_text, file_path, note,"
               " created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (new_id(), source_id, version, sha, raw_text, file_path, note, user_id, ts))

    embedder = get_embedder(cfg)
    emb_col, emb_ph = store_embedding_sql(db)
    embed_error = None
    try:
        vectors = embedder.embed_documents([_embed_text(m, c) for c in chunks])
        model_name = embedder.name
    except EmbeddingError as exc:
        # Keep the document searchable by meaning with the built-in model; Re-embed upgrades it later.
        embed_error = str(exc)
        fallback = LocalHashEmbedder()
        vectors = fallback.embed_documents([_embed_text(m, c) for c in chunks])
        model_name = fallback.name
    meta_text = " ".join(filter(None, [m["title"], m["citation"], m["neutral_citation"], " ".join(m["alt_citations"]),
                                       m["court"], m["act_name"], m["parties"], m["judges"], m["case_number"]]))
    for i, (c, vec) in enumerate(zip(chunks, vectors)):
        chunk_id = f"{source_id}:{version}:{i}"
        db.execute(f"INSERT INTO chunks (id, source_id, version, idx, text, heading, page, para_ref, section_ref,"
                   f" token_count, {emb_col}, embedding_model) VALUES (?,?,?,?,?,?,?,?,?,?,{emb_ph},?)",
                   (chunk_id, source_id, version, i, c.text, c.heading, c.page, c.para_ref, c.section_ref,
                    len(c.text.split()), embedding_value(db, vec) if vec is not None else None, model_name))
        db.execute("INSERT INTO chunks_fts (chunk_id, text, heading, meta) VALUES (?,?,?,?)",
                   (chunk_id, c.text, " ".join(filter(None, [c.heading, c.section_ref])), meta_text))
    bump_generation(db)
    msg = f"Indexed {len(chunks)} passages" + (f" (version {version})" if version > 1 else "")
    if embed_error:
        msg += f"; embedded with the built-in model for now (run Re-embed later): {embed_error}"
    if flags:
        msg += f"; flagged: {', '.join(sorted(flags))}"
    return IngestResult(source_id, "succeeded", msg, len(chunks), version)


def _embed_text(m: Dict, c) -> str:
    """Prefix passages with their context so the vector carries the source identity."""
    ctx = " | ".join(filter(None, [m["title"], m["act_name"], c.section_ref, c.heading]))
    return f"{ctx}\n{c.text}" if ctx else c.text


def _delete_chunks(db, source_id: str) -> None:
    db.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id = ?)", (source_id,))
    db.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))


def delete_source(db, cfg, source_id: str) -> None:
    _delete_chunks(db, source_id)
    db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    bump_generation(db)
    db.commit()
    folder = Path(cfg.upload_dir) / source_id
    if folder.is_dir() and folder.resolve().parent == Path(cfg.upload_dir).resolve():
        shutil.rmtree(folder, ignore_errors=True)


def reembed_all(db, cfg, batch: int = 128) -> int:
    """Re-embed chunks produced by a different embedding model than the active one."""
    embedder = get_embedder(cfg)
    emb_col, emb_ph = store_embedding_sql(db)
    total = 0
    while True:
        rows = db.execute(
            "SELECT c.id, c.text, c.heading, c.section_ref, s.title, s.act_name FROM chunks c "
            "JOIN sources s ON s.id = c.source_id WHERE c.embedding_model IS NOT ? LIMIT ?",
            (embedder.name, batch)).fetchall()
        if not rows:
            break
        texts = [" | ".join(filter(None, [r["title"], r["act_name"], r["section_ref"], r["heading"]])) + "\n" + r["text"]
                 for r in rows]
        vecs = embedder.embed_documents(texts)
        for r, v in zip(rows, vecs):
            db.execute(f"UPDATE chunks SET {emb_col} = {emb_ph}, embedding_model = ? WHERE id = ?",
                       (embedding_value(db, v), embedder.name, r["id"]))
        total += len(rows)
        db.commit()
    bump_generation(db)
    db.commit()
    return total


def reindex_source(db, cfg, source_id: str, pages: List[Tuple[Optional[int], str]]) -> int:
    """Rebuild a source's passages from freshly extracted pages (e.g. after a chunking fix), same version.

    The stored original text and version history are untouched; only the search index is rebuilt.
    """
    row = db.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if not row:
        raise ValueError("Source not found.")
    m = {k: row[k] for k in META_FIELDS}
    m["alt_citations"] = json.loads(row["alt_citations"] or "[]")
    sanitized = [(p, guard.sanitize(t)[0]) for p, t in pages]
    chunks = chunk_pages(sanitized, is_statute=m["kind"] in ("statute", "constitution", "rule", "notification"),
                         para_numbers=m["kind"] not in ("editorial_summary", "commentary"))
    if not chunks:
        raise ValueError("No indexable passages were produced.")
    texts = [_embed_text(m, c) for c in chunks]
    try:
        embedder = get_embedder(cfg)
        vectors, model_name = embedder.embed_documents(texts), embedder.name
    except EmbeddingError:
        fallback = LocalHashEmbedder()
        vectors, model_name = fallback.embed_documents(texts), fallback.name
    version = row["current_version"]
    emb_col, emb_ph = store_embedding_sql(db)
    _delete_chunks(db, source_id)
    meta_text = " ".join(filter(None, [m["title"], m["citation"], m["neutral_citation"], " ".join(m["alt_citations"]),
                                       m["court"], m["act_name"], m["parties"], m["judges"], m["case_number"]]))
    for i, (c, vec) in enumerate(zip(chunks, vectors)):
        chunk_id = f"{source_id}:{version}:{i}"
        db.execute(f"INSERT INTO chunks (id, source_id, version, idx, text, heading, page, para_ref, section_ref,"
                   f" token_count, {emb_col}, embedding_model) VALUES (?,?,?,?,?,?,?,?,?,?,{emb_ph},?)",
                   (chunk_id, source_id, version, i, c.text, c.heading, c.page, c.para_ref, c.section_ref,
                    len(c.text.split()), embedding_value(db, vec), model_name))
        db.execute("INSERT INTO chunks_fts (chunk_id, text, heading, meta) VALUES (?,?,?,?)",
                   (chunk_id, c.text, " ".join(filter(None, [c.heading, c.section_ref])), meta_text))
    db.execute("UPDATE sources SET updated_at = ? WHERE id = ?", (now(), source_id))
    bump_generation(db)
    db.commit()
    return len(chunks)
