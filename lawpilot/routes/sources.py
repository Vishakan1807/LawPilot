"""Open a source (judgment, Act, matter document), its versions, and search the corpus directly."""
from __future__ import annotations

import os
import re
from pathlib import Path

from flask import Blueprint, g, jsonify, request, send_file

from lawpilot.auth import ApiError, load_matter, login_required
from lawpilot.db import get_db, query_all, query_one
from lawpilot.retrieval import Filters, build_fts_query, lexical_search, scope_sql
from lawpilot import query_analysis
from lawpilot.routes import cfg, page_args

bp = Blueprint("sources", __name__, url_prefix="/api")


def _load_source(source_id: str) -> dict:
    s = query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    if not s:
        raise ApiError("Source not found.", 404)
    if s["matter_id"]:
        load_matter(s["matter_id"])
    elif s.get("chat_id"):
        # A file uploaded into an AI-mode chat is private to that chat's owner.
        owner = query_one("SELECT user_id FROM ai_chats WHERE id = ?", (s["chat_id"],))
        if not owner or owner["user_id"] != g.user["id"]:
            raise ApiError("Source not found.", 404)
    return s


@bp.get("/sources/<source_id>")
@login_required
def get_source(source_id):
    s = _load_source(source_id)
    version = int(request.args.get("version") or s["current_version"])
    v = query_one("SELECT version, sha256, original_text, note, created_at, file_path FROM source_versions "
                  "WHERE source_id = ? AND version = ?", (source_id, version))
    if not v:
        raise ApiError("Version not found.", 404)
    s["text"] = v.pop("original_text")
    s["has_file"] = bool(v.pop("file_path"))
    s["version_info"] = v
    s["versions"] = query_all("SELECT version, sha256, note, created_at FROM source_versions WHERE source_id = ? "
                              "ORDER BY version DESC", (source_id,))
    s["passages"] = query_all("SELECT id, idx, page, para_ref, section_ref, heading, text FROM chunks "
                              "WHERE source_id = ? ORDER BY idx", (source_id,)) if version == s["current_version"] else []
    s["relations"] = query_all(
        "SELECT r.relation, r.note, 'outgoing' AS direction, t.id AS other_id, t.title AS other_title, "
        "t.citation AS other_citation FROM authority_relations r JOIN sources t ON t.id = r.to_source_id "
        "WHERE r.from_source_id = ? UNION ALL "
        "SELECT r.relation, r.note, 'incoming', f.id, f.title, f.citation FROM authority_relations r "
        "JOIN sources f ON f.id = r.from_source_id WHERE r.to_source_id = ?", (source_id, source_id))
    return jsonify(source=s)


@bp.get("/sources/<source_id>/file")
@login_required
def download_file(source_id):
    s = _load_source(source_id)
    version = int(request.args.get("version") or s["current_version"])
    row = query_one("SELECT file_path FROM source_versions WHERE source_id = ? AND version = ?", (source_id, version))
    if not row or not row["file_path"]:
        raise ApiError("No original file is stored for this source.", 404)
    path = Path(row["file_path"]).resolve()
    if Path(cfg().upload_dir).resolve() not in path.parents or not path.is_file():
        raise ApiError("File not available.", 404)
    return send_file(str(path), as_attachment=True,
                     download_name=s["original_filename"] or os.path.basename(path))


@bp.get("/corpus/search")
@login_required
def corpus_search():
    """Structured search of the public corpus by case name, party, citation, judge, court, Act, date, text."""
    limit, offset = page_args(25, 100)
    a = request.args
    f = Filters.from_dict({
        "court_levels": a.getlist("court_level"), "kinds": a.getlist("kind"), "court": a.get("court"),
        "year_from": a.get("year_from"), "year_to": a.get("year_to"),
        "authority_classes": a.getlist("authority_class"), "legal_domain": a.get("domain"),
        "include_matter_docs": False,
    })
    where, base_params = scope_sql(f, None)
    params = list(base_params)
    clauses = [where]
    for field, cols in (("title", ["s.title", "s.parties"]), ("citation", ["s.citation", "s.neutral_citation",
                                                                            "s.alt_citations", "s.case_number"]),
                        ("judge", ["s.judges", "s.bench"]), ("act", ["s.act_name", "s.title"])):
        val = (a.get(field) or "").strip()
        if val:
            clauses.append("(" + " OR ".join(f"{c} LIKE ?" for c in cols) + ")")
            params += [f"%{val}%"] * len(cols)
    q = (a.get("q") or "").strip()
    order = "s.decision_date DESC NULLS LAST, s.title"
    chunk_sources = {}
    if q:
        analysis = query_analysis.analyze(q)
        hits = lexical_search(get_db(), build_fts_query(analysis), where, list(base_params), limit=400)
        for pos, (cid, _score) in enumerate(hits):          # hits are best first
            chunk_sources.setdefault(cid.split(":")[0], pos)
        meta_like = "(s.title LIKE ? OR s.citation LIKE ? OR s.parties LIKE ? OR s.alt_citations LIKE ?)"
        ids = list(chunk_sources)
        if ids:
            clauses.append(f"(s.id IN ({','.join('?' * len(ids))}) OR {meta_like})")
            params += ids + [f"%{q}%"] * 4
        else:
            clauses.append(meta_like)
            params += [f"%{q}%"] * 4
    rows = query_all(
        f"SELECT s.id, s.title, s.kind, s.authority_class, s.citation, s.court, s.court_level, s.decision_date, "
        f"s.act_name, s.legal_status, s.verification, s.jurisdiction, s.source_url FROM sources s "
        f"WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT 1000", params)
    if q:
        # Sources whose title or citation contains the query come first, then full-text rank.
        rank = {sid: i for i, sid in enumerate(sorted(chunk_sources, key=lambda k: chunk_sources[k]))}
        ql = q.lower()
        rows.sort(key=lambda r: (0 if ql in f"{r['title']} {r['citation'] or ''}".lower() else 1,
                                 rank.get(r["id"], len(rank))))
    return jsonify(total=len(rows), items=rows[offset:offset + limit])


@bp.get("/corpus/coverage")
@login_required
def coverage():
    db = get_db()
    def counts(col):
        return {r[0] or "unknown": r[1] for r in db.execute(
            f"SELECT {col}, COUNT(*) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL AND is_active = 1 GROUP BY {col}")}
    years = db.execute("SELECT MIN(substr(decision_date,1,4)), MAX(substr(decision_date,1,4)) FROM sources "
                       "WHERE matter_id IS NULL AND chat_id IS NULL AND decision_date IS NOT NULL").fetchone()
    return jsonify(by_kind=counts("kind"), by_class=counts("authority_class"), by_court_level=counts("court_level"),
                   by_verification=counts("verification"),
                   total=db.execute("SELECT COUNT(*) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL AND is_active = 1").fetchone()[0],
                   year_range=[years[0], years[1]],
                   last_updated=db.execute("SELECT MAX(updated_at) FROM sources WHERE matter_id IS NULL AND chat_id IS NULL").fetchone()[0])
