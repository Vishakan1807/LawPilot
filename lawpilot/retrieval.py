"""Hybrid retrieval over the legal corpus and (optionally) one authorised matter.

Pipeline:
  1. Scope & filter   - only active sources from the public corpus plus the one
                        matter the caller has already authorised; metadata filters
                        (court, date, source type, ...) are applied in SQL here,
                        before any text can reach the LLM.
  2. Keyword search   - SQLite FTS5 / BM25 with phrase queries for citations
                        and provisions.
  3. Vector search    - cosine similarity over chunk embeddings.
  4. Exact lookup     - direct hits on citation / case name / case number.
  5. Fusion           - reciprocal rank fusion of the lists above.
  6. Rerank           - legal reranking (see reranking.py) with source diversity.
  7. Contrary search  - authorities that overrule/distinguish the hits, and
                        passages using contrary-treatment language.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from lawpilot.embeddings import EmbeddingError, LocalHashEmbedder, from_blob, get_embedder, normalize
from lawpilot.query_analysis import QueryAnalysis
from lawpilot import reranking
from lawpilot.db import DB_ERRORS

log = logging.getLogger("lawpilot.retrieval")

RRF_K = 60
CONTRARY_MARKERS = ["overruled", "overrule", "distinguished", "distinguishable", "per incuriam", "not good law",
                    "dissent", "contrary view", "doubted", "no longer good law", "set aside", "reversed",
                    "cannot be sustained", "struck down"]


@dataclass
class Filters:
    court_levels: List[str] = field(default_factory=list)   # supreme | high | tribunal | district
    court: str = ""                                          # substring, e.g. "Delhi"
    jurisdiction: str = ""
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    kinds: List[str] = field(default_factory=list)
    authority_classes: List[str] = field(default_factory=list)
    legal_domain: str = ""
    include_corpus: bool = True
    include_matter_docs: bool = True
    exclude_overruled: bool = False

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Filters":
        d = d or {}
        f = cls()
        for key in ("court_levels", "kinds", "authority_classes"):
            v = d.get(key) or []
            setattr(f, key, [x for x in (v if isinstance(v, list) else [v]) if x])
        for key in ("court", "jurisdiction", "legal_domain"):
            setattr(f, key, str(d.get(key) or "").strip())
        for key in ("year_from", "year_to"):
            try:
                setattr(f, key, int(d[key]) if d.get(key) not in (None, "") else None)
            except (TypeError, ValueError):
                pass
        for key in ("include_corpus", "include_matter_docs", "exclude_overruled"):
            if key in d:
                setattr(f, key, bool(d[key]))
        return f

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class Candidate:
    chunk_id: str
    source_id: str
    version: int
    text: str
    heading: Optional[str]
    page: Optional[int]
    para_ref: Optional[str]
    section_ref: Optional[str]
    source: Dict
    lex_rank: Optional[int] = None
    vec_rank: Optional[int] = None
    vec_sim: float = 0.0
    exact: bool = False
    provision: str = ""          # e.g. 'Section 482 BNSS' when the question names this provision
    rrf: float = 0.0
    score: float = 0.0
    coverage: float = 0.0
    reasons: List[str] = field(default_factory=list)
    role: str = "candidate"          # candidate | contrary_candidate
    excerpt: str = ""


@dataclass
class RetrievalResult:
    candidates: List[Candidate]
    contrary: List[Candidate]
    stats: Dict
    errors: List[str]

    @property
    def failed(self) -> bool:
        return not self.candidates and len(self.errors) >= 2


# ── Scope ──────────────────────────────────────────────────────────────────

def scope_sql(filters: Filters, matter_id: Optional[str]) -> Tuple[str, list]:
    """WHERE clause over `sources s`. `matter_id` must already be authorised by the caller."""
    clauses = ["s.is_active = 1", "s.status = 'ready'"]
    params: list = []
    scope = []
    if filters.include_corpus:
        scope.append("s.matter_id IS NULL")
    if matter_id and filters.include_matter_docs:
        scope.append("s.matter_id = ?")
        params.append(matter_id)
    if not scope:
        return "0", []
    clauses.append("(" + " OR ".join(scope) + ")")

    # Metadata filters restrict the public corpus; the matter's own documents stay
    # in scope because they rarely carry court/date metadata.
    corpus_only = []
    if filters.court_levels:
        corpus_only.append(f"s.court_level IN ({','.join('?' * len(filters.court_levels))})")
        params.extend(filters.court_levels)
    if filters.court:
        corpus_only.append("s.court LIKE ?")
        params.append(f"%{filters.court}%")
    if filters.jurisdiction:
        # Only narrows state-level courts; Supreme Court rulings and central Acts stay in.
        corpus_only.append("(s.court_level NOT IN ('high', 'district') OR s.jurisdiction LIKE ? OR s.court LIKE ?)")
        params += [f"%{filters.jurisdiction}%"] * 2
    if filters.year_from:
        corpus_only.append("(s.decision_date IS NULL OR substr(s.decision_date, 1, 4) >= ?)")
        params.append(str(filters.year_from))
    if filters.year_to:
        corpus_only.append("(s.decision_date IS NULL OR substr(s.decision_date, 1, 4) <= ?)")
        params.append(str(filters.year_to))
    if filters.kinds:
        corpus_only.append(f"s.kind IN ({','.join('?' * len(filters.kinds))})")
        params.extend(filters.kinds)
    if filters.authority_classes:
        corpus_only.append(f"s.authority_class IN ({','.join('?' * len(filters.authority_classes))})")
        params.extend(filters.authority_classes)
    if filters.legal_domain:
        corpus_only.append("s.legal_domain LIKE ?")
        params.append(f"%{filters.legal_domain}%")
    if filters.exclude_overruled:
        corpus_only.append("s.legal_status NOT IN ('overruled', 'repealed')")
    if corpus_only:
        clauses.append("(s.matter_id IS NOT NULL OR (" + " AND ".join(corpus_only) + "))")
    return " AND ".join(clauses), params


# ── Keyword search ─────────────────────────────────────────────────────────

def _fts_phrase(text: str) -> Optional[str]:
    toks = re.findall(r"[a-z0-9]+", normalize(text))
    return '"' + " ".join(toks) + '"' if toks else None


def build_fts_query(analysis: QueryAnalysis, extra_terms: Sequence[str] = ()) -> str:
    parts = []
    for c in analysis.citations:
        p = _fts_phrase(c)
        if p:
            parts.append(p)
    for s in analysis.sections:
        base = re.match(r"\d+[A-Z]*", s).group(0).lower()
        parts.append(f'"section {base}"')
        parts.append(f'"{base}"')
    for a in analysis.articles:
        base = re.match(r"\d+[A-Za-z]*", a).group(0).lower()
        parts.append(f'"article {base}"')
    for corr in analysis.code_correspondence:
        for side in ("from", "to"):
            m = re.match(r"Section (\S+)", corr[side])
            if m:
                parts.append(f'"section {m.group(1).lower()}"')
    for prov in getattr(analysis, "inferred_provisions", []):
        parts.append(_fts_phrase(prov["phrase"]))
    for p in analysis.parties:
        ph = _fts_phrase(p)
        if ph:
            parts.append(ph)
    for t in list(analysis.key_terms) + list(extra_terms):
        ph = _fts_phrase(t)
        if ph:
            parts.append(ph)
    seen, out = set(), []
    for p in parts:
        if p not in seen and p != '""':
            seen.add(p)
            out.append(p)
    return " OR ".join(out[:40])


def fts_to_tsquery(fts_query: str) -> str:
    """Translate our FTS5-style query ('"a b" OR "c"', AND, parentheses) to PostgreSQL tsquery syntax."""
    out = []
    for tok in re.findall(r'"[^"]*"|\(|\)|\bOR\b|\bAND\b', fts_query):
        if tok == "OR":
            out.append("|")
        elif tok == "AND":
            out.append("&")
        elif tok in ("(", ")"):
            out.append(tok)
        else:
            words = re.findall(r"[a-z0-9]+", tok.lower())
            if words:
                out.append("(" + " <-> ".join(words) + ")")
    return " ".join(out)


def lexical_search(db, fts_query: str, where: str, params: list, limit: int = 60) -> List[Tuple[str, float]]:
    """Keyword search. Returns (chunk_id, score) best first."""
    if not fts_query:
        return []
    if db.dialect == "postgres":
        tsq = fts_to_tsquery(fts_query)
        if not tsq:
            return []
        sql = (f"SELECT f.chunk_id, ts_rank_cd(f.tsv, q) AS score FROM chunks_fts f "
               f"JOIN chunks c ON c.id = f.chunk_id JOIN sources s ON s.id = c.source_id, "
               f"to_tsquery('english', ?) q WHERE f.tsv @@ q AND {where} ORDER BY score DESC LIMIT ?")
        return [(r[0], float(r[1])) for r in db.execute(sql, [tsq] + params + [limit]).fetchall()]
    sql = (f"SELECT f.chunk_id, bm25(chunks_fts, 0.0, 1.0, 0.6, 1.8) AS score FROM chunks_fts f "
           f"JOIN chunks c ON c.id = f.chunk_id JOIN sources s ON s.id = c.source_id "
           f"WHERE chunks_fts MATCH ? AND {where} ORDER BY score LIMIT ?")
    return [(r[0], -r[1]) for r in db.execute(sql, [fts_query] + params + [limit]).fetchall()]


# ── Vector search ──────────────────────────────────────────────────────────

class _VectorIndex:
    """In-memory matrix of chunk embeddings for one model, rebuilt when the corpus changes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.key = None
        self.ids: List[str] = []
        self.source_ids: np.ndarray = np.array([])
        self.matrix: Optional[np.ndarray] = None

    def ensure(self, db, db_path: str, model: str, dim: int) -> None:
        row = db.execute("SELECT value FROM meta WHERE key = 'index_generation'").fetchone()
        key = (db_path, model, row[0] if row else "0")
        with self.lock:
            if key == self.key:
                return
            rows = db.execute("SELECT id, source_id, embedding FROM chunks WHERE embedding_model = ?",
                              (model,)).fetchall()
            vecs = [from_blob(r[2]) for r in rows if r[2] is not None]
            self.ids = [r[0] for r in rows if r[2] is not None]
            self.source_ids = np.array([r[1] for r in rows if r[2] is not None], dtype=object)
            self.matrix = np.vstack(vecs).astype(np.float32) if vecs else np.zeros((0, dim), np.float32)
            self.key = key

    def search(self, qvec: np.ndarray, allowed_sources: Set[str], limit: int) -> List[Tuple[str, float]]:
        with self.lock:
            if self.matrix is None or not len(self.ids):
                return []
            mask = np.fromiter((s in allowed_sources for s in self.source_ids), dtype=bool, count=len(self.ids))
            if not mask.any():
                return []
            idx = np.nonzero(mask)[0]
            sims = self.matrix[idx] @ qvec
            top = idx[np.argsort(-sims)[:limit]]
            sim_by_idx = dict(zip(idx.tolist(), sims.tolist()))
            return [(self.ids[i], float(sim_by_idx[i])) for i in top.tolist()]


_INDEXES: Dict[str, _VectorIndex] = {}
_MODELS_CACHE: Dict[tuple, List[str]] = {}


def vector_literal(vec) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _present_models(db, cfg) -> List[str]:
    """Embedding models that have produced vectors in this library (cached per index generation)."""
    gen = db.execute("SELECT value FROM meta WHERE key = 'index_generation'").fetchone()
    key = (getattr(cfg, "database_url", "") or str(cfg.db_path), gen[0] if gen else "0")
    if key not in _MODELS_CACHE:
        _MODELS_CACHE.clear()
        _MODELS_CACHE[key] = [r[0] for r in db.execute(
            "SELECT DISTINCT embedding_model FROM chunks WHERE embedding_model IS NOT NULL").fetchall()]
    return _MODELS_CACHE[key]


def _search_model(db, cfg, model: str, qvec, where: str, params: list, limit: int) -> List[Tuple[str, float]]:
    if db.dialect == "postgres" and db.has_pgvector:
        # pgvector: nearest neighbours computed inside PostgreSQL (HNSW index), filters applied in SQL.
        q = vector_literal(qvec)
        vt = db.vector_type
        sql = (f"SELECT c.id, 1 - (c.embedding_vec <=> ?::{vt}) AS sim FROM chunks c "
               f"JOIN sources s ON s.id = c.source_id WHERE c.embedding_model = ? AND c.embedding_vec IS NOT NULL "
               f"AND {where} ORDER BY c.embedding_vec <=> ?::{vt} LIMIT ?")
        rows = db.execute(sql, [q, model] + params + [q, limit]).fetchall()
        return [(r[0], float(r[1])) for r in rows]
    index = _INDEXES.setdefault(model, _VectorIndex())
    index.ensure(db, getattr(cfg, "database_url", "") or str(cfg.db_path), model, len(qvec))
    allowed = {r[0] for r in db.execute(f"SELECT s.id FROM sources s WHERE {where}", params).fetchall()}
    return index.search(qvec, allowed, limit) if allowed else []


def vector_search(db, cfg, query_text: str, where: str, params: list, limit: int = 60) -> Tuple[List[Tuple[str, float]], str]:
    """Search by meaning across every embedding model present in the library.

    While a library is being moved to a new embedding model, passages still embedded with the
    built-in model stay searchable through it, so semantic search never goes blind mid-migration.
    """
    active = get_embedder(cfg)
    models = _present_models(db, cfg)
    plan = [active] if active.name in models else []
    if LocalHashEmbedder.name in models and active.name != LocalHashEmbedder.name:
        plan.append(LocalHashEmbedder())
    lists, used, errors = [], [], []
    for emb in plan:
        try:
            qvec = emb.embed_query(query_text)
        except EmbeddingError as exc:
            errors.append(str(exc))
            continue
        lists.append(_search_model(db, cfg, emb.name, qvec, where, params, limit))
        used.append(emb.name)
    if not lists and errors:
        raise EmbeddingError(errors[0])
    merged, seen = [], set()
    for rank in range(limit):                       # round-robin keeps each model's best hits near the top
        for lst in lists:
            if rank < len(lst) and lst[rank][0] not in seen:
                seen.add(lst[rank][0])
                merged.append(lst[rank])
    return merged[:limit], " + ".join(used) or active.name


# ── Exact lookups ──────────────────────────────────────────────────────────

def provision_targets(analysis: QueryAnalysis) -> List[Tuple[str, str]]:
    """(Act title, 'Section N') pairs the question names or implies, e.g. ('Bharatiya Nagarik Suraksha Sanhita, 2023', 'Section 482')."""
    out = []
    for corr in analysis.code_correspondence:
        for side in ("from", "to"):
            m = re.match(r"Section (\S+) (.+)$", corr[side])
            if m:
                out.append((m.group(2), f"Section {m.group(1)}"))
    for statute in analysis.statutes:
        for sec in analysis.sections:
            out.append((statute, f"Section {re.match(r'[0-9]+[A-Z]*', sec).group(0)}"))
    return list(dict.fromkeys(out))


def provision_chunk_ids(db, analysis: QueryAnalysis, where: str, params: list) -> Dict[str, str]:
    """Fetch the exact statutory sections a question names, even when their wording shares no keywords
    with the question (BNSS s.482 never says 'anticipatory')."""
    hits: Dict[str, str] = {}
    for act, sec in provision_targets(analysis)[:8]:
        short = re.sub(r",?\s*\d{4}$", "", act).strip()
        rows = db.execute(f"SELECT c.id FROM chunks c JOIN sources s ON s.id = c.source_id WHERE {where} "
                          f"AND s.title LIKE ? AND c.section_ref = ? ORDER BY c.idx LIMIT 2",
                          params + [f"%{short}%", sec]).fetchall()
        for r in rows:
            hits.setdefault(r[0], f"{sec} of {act}")
    return hits


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _contains_seq(hay: List[str], needle: List[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def exact_source_ids(db, analysis: QueryAnalysis, where: str, params: list) -> Set[str]:
    """Sources whose citation / case number / title exactly matches something in the query."""
    ids: Set[str] = set()
    for c in analysis.citations + analysis.case_numbers:
        needle = _tokens(c)
        year = next((t for t in needle if len(t) == 4 and t.isdigit()), None)
        if not needle or not year:
            continue
        rows = db.execute(
            f"SELECT s.id, coalesce(s.citation,'') || ' ' || coalesce(s.neutral_citation,'') || ' ' || "
            f"coalesce(s.alt_citations,'') || ' ' || coalesce(s.case_number,'') FROM sources s "
            f"WHERE {where} AND (s.citation LIKE ? OR s.alt_citations LIKE ? OR s.neutral_citation LIKE ? "
            f"OR s.case_number LIKE ?)", params + [f"%{year}%"] * 4).fetchall()
        ids.update(r[0] for r in rows if _contains_seq(_tokens(r[1]), needle))
    for p in analysis.parties:
        for r in db.execute(f"SELECT s.id FROM sources s WHERE {where} AND (s.title LIKE ? OR s.parties LIKE ?) "
                            f"LIMIT 10", params + [f"%{p}%", f"%{p}%"]):
            ids.add(r[0])
    return ids


# ── Orchestration ──────────────────────────────────────────────────────────

def _load_candidates(db, chunk_ids: Sequence[str]) -> Dict[str, Candidate]:
    out: Dict[str, Candidate] = {}
    ids = list(dict.fromkeys(chunk_ids))
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        rows = db.execute(
            f"SELECT c.id, c.source_id, c.version, c.text, c.heading, c.page, c.para_ref, c.section_ref, s.* "
            f"FROM chunks c JOIN sources s ON s.id = c.source_id WHERE c.id IN ({','.join('?' * len(part))})", part)
        for r in rows:
            d = dict(r)
            src = {k: d[k] for k in ("title", "kind", "authority_class", "citation", "neutral_citation", "court",
                                     "court_level", "bench", "decision_date", "jurisdiction", "act_name",
                                     "legal_status", "status_note", "verification", "source_url", "matter_id",
                                     "origin", "retrieved_at", "verified_at", "parties", "case_number",
                                     "security_flags", "alt_citations")}
            out[d["id"]] = Candidate(chunk_id=d["id"], source_id=d["source_id"], version=d["version"],
                                     text=d["text"], heading=d["heading"], page=d["page"], para_ref=d["para_ref"],
                                     section_ref=d["section_ref"], source=src)
    return out


def retrieve(db, cfg, analysis: QueryAnalysis, filters: Filters, matter_id: Optional[str] = None,
             top_k: int = 8, extra_query: str = "") -> RetrievalResult:
    where, params = scope_sql(filters, matter_id)
    errors: List[str] = []
    query_text = analysis.query + (" " + extra_query if extra_query else "")

    lex: List[Tuple[str, float]] = []
    try:
        lex = lexical_search(db, build_fts_query(analysis), where, params)
    except DB_ERRORS as exc:
        db.rollback()
        errors.append(f"keyword search failed: {exc}")
        log.exception("lexical search failed")

    vec: List[Tuple[str, float]] = []
    model = None
    try:
        vec, model = vector_search(db, cfg, query_text, where, params)
    except Exception as exc:  # embedding API outages must not take down research
        db.rollback()
        errors.append(f"vector search failed: {exc}")
        log.exception("vector search failed")

    exact_ids: Set[str] = set()
    try:
        exact_ids = exact_source_ids(db, analysis, where, params)
    except DB_ERRORS as exc:
        db.rollback()
        errors.append(f"citation lookup failed: {exc}")

    exact_chunk_ids: List[str] = []
    for sid in exact_ids:
        exact_chunk_ids += [r[0] for r in db.execute(
            "SELECT id FROM chunks WHERE source_id = ? ORDER BY idx LIMIT 2", (sid,))]

    provision_hits: Dict[str, str] = {}
    try:
        provision_hits = provision_chunk_ids(db, analysis, where, params)
    except DB_ERRORS as exc:
        db.rollback()
        errors.append(f"provision lookup failed: {exc}")

    cands = _load_candidates(db, [c for c, _ in lex] + [c for c, _ in vec] + exact_chunk_ids + list(provision_hits))
    for rank, (cid, _) in enumerate(lex):
        if cid in cands:
            cands[cid].lex_rank = rank
            cands[cid].rrf += 1.0 / (RRF_K + rank)
    for rank, (cid, sim) in enumerate(vec):
        if cid in cands:
            cands[cid].vec_rank = rank
            cands[cid].vec_sim = sim
            cands[cid].rrf += 1.0 / (RRF_K + rank)
    for cid in exact_chunk_ids:
        if cid in cands:
            cands[cid].exact = True
            cands[cid].rrf += 1.0 / RRF_K
    for cid, label in provision_hits.items():
        if cid in cands:
            cands[cid].provision = label
            cands[cid].rrf += 1.0 / RRF_K

    ranked = reranking.rerank(list(cands.values()), analysis)
    if analysis.search_type == "citation" and any(c.exact for c in ranked):
        # A citation lookup that resolved: other hits only matched on "SCC" / the year.
        ranked = [c for c in ranked if c.exact]
    final = reranking.diversify(ranked, top_k)
    contrary = find_contrary(db, analysis, final, where, params, exclude={c.chunk_id for c in final})

    stats = {
        "keyword_hits": len(lex), "vector_hits": len(vec), "exact_matches": len(exact_ids),
        "candidates_considered": len(cands), "embedding_model": model,
        "relevant": sum(1 for c in final if reranking.is_relevant(c)),
    }
    return RetrievalResult(candidates=final, contrary=contrary, stats=stats, errors=errors)


def find_contrary(db, analysis: QueryAnalysis, hits: List[Candidate], where: str, params: list,
                  exclude: Set[str], limit: int = 3) -> List[Candidate]:
    hit_sources = list({c.source_id for c in hits})
    chunk_ids: List[str] = []
    reasons: Dict[str, str] = {}
    if hit_sources:
        marks = ",".join("?" * len(hit_sources))
        rel_rows = db.execute(
            f"SELECT r.from_source_id, r.to_source_id, r.relation, r.note FROM authority_relations r "
            f"WHERE (r.to_source_id IN ({marks}) OR r.from_source_id IN ({marks})) "
            f"AND r.relation IN ('overrules', 'distinguishes', 'reverses')", hit_sources + hit_sources).fetchall()
        for frm, to, rel, note in rel_rows:
            other = frm if to in hit_sources else to
            if other in hit_sources:
                continue
            allowed = db.execute(f"SELECT 1 FROM sources s WHERE s.id = ? AND {where}", [other] + params).fetchone()
            if not allowed:
                continue
            row = db.execute("SELECT id FROM chunks WHERE source_id = ? ORDER BY idx LIMIT 1", (other,)).fetchone()
            if row:
                chunk_ids.append(row[0])
                reasons[row[0]] = f"Related authority ({rel.replace('_', ' ')})" + (f": {note}" if note else "")
    try:
        marker_q = build_fts_query(analysis)
        if marker_q:
            q = f"({marker_q}) AND ({' OR '.join(_fts_phrase(m) for m in CONTRARY_MARKERS)})"
            for cid, _ in lexical_search(db, q, where, params, limit=15):
                chunk_ids.append(cid)
                reasons.setdefault(cid, "Uses contrary-treatment language")
    except DB_ERRORS:
        db.rollback()
        log.exception("contrary search failed")

    cands = _load_candidates(db, [c for c in chunk_ids if c not in exclude])
    out = []
    for cid in chunk_ids:
        c = cands.get(cid)
        if not c or any(o.chunk_id == cid for o in out):
            continue
        c.role = "contrary_candidate"
        c.reasons.append(reasons.get(cid, ""))
        reranking.score_one(c, analysis, max_rrf=1.0)
        if reasons.get(cid, "").startswith("Related") or c.coverage >= 0.25:
            out.append(c)
        if len(out) >= limit:
            break
    return out
