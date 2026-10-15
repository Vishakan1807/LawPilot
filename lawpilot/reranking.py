"""Legal reranking.

Final score blends:
  relevance   - fused keyword+vector rank (RRF), normalised
  coverage    - share of the question's key terms / provisions found in the passage
  authority   - court hierarchy (Supreme Court > High Court > tribunal > lower courts);
                statutes and the Constitution count as top-tier primary law
  class       - primary > official > secondary > user document
  jurisdiction- High Court of the preferred state, when one is named
  recency     - only when the question asks for a time period
  status      - overruled / repealed material is pushed down (but kept, so it can
                surface as contrary authority or a limitation)
Each score comes with human-readable reasons shown in the UI.
"""
from __future__ import annotations

import re
from typing import List

from lawpilot.embeddings import tokenize

COURT_WEIGHT = {"supreme": 1.0, "high": 0.75, "tribunal": 0.55, "district": 0.35, "none": 0.45}
KIND_WEIGHT = {"statute": 1.0, "constitution": 1.0, "rule": 0.85, "notification": 0.7}
CLASS_WEIGHT = {"primary": 1.0, "official": 0.75, "secondary": 0.45, "user_upload": 0.6}
STATUS_PENALTY = {"overruled": 0.2, "repealed": 0.2, "partly_overruled": 0.07, "amended": 0.03}
COURT_LABEL = {"supreme": "Supreme Court", "high": "High Court", "tribunal": "Tribunal", "district": "Lower court"}

MIN_COVERAGE = 0.25


def _query_terms(analysis) -> List[str]:
    terms = set(tokenize(" ".join(analysis.key_terms)))
    for s in analysis.sections + analysis.articles:
        terms.add(re.match(r"\d+[A-Za-z]*", s).group(0).lower())
    for p in analysis.parties:
        terms.update(tokenize(p))
    return sorted(terms)


def coverage(c, terms: List[str]) -> float:
    if not terms:
        return 0.0
    hay = set(tokenize(" ".join(filter(None, [c.text, c.source.get("title"), c.source.get("citation"),
                                              c.source.get("act_name"), c.section_ref, c.heading]))))
    return sum(1 for t in terms if t in hay) / len(terms)


def score_one(c, analysis, max_rrf: float, terms: List[str] = None) -> None:
    terms = terms if terms is not None else _query_terms(analysis)
    src = c.source
    c.coverage = coverage(c, terms)
    rel = (c.rrf / max_rrf) if max_rrf else 0.0
    if src.get("matter_id"):
        authority = 0.6
    else:
        authority = KIND_WEIGHT.get(src.get("kind"), COURT_WEIGHT.get(src.get("court_level") or "none", 0.45))
    klass = CLASS_WEIGHT.get(src.get("authority_class"), 0.5)
    juris = 0.0
    if analysis.preferred_state and src.get("court_level") == "high":
        juris = 1.0 if analysis.preferred_state.lower() in (src.get("jurisdiction") or src.get("court") or "").lower() else 0.0
    recency = 0.0
    year = (src.get("decision_date") or "")[:4]
    if (analysis.year_from or analysis.year_to) and year.isdigit():
        y = int(year)
        inside = (not analysis.year_from or y >= analysis.year_from) and (not analysis.year_to or y <= analysis.year_to)
        recency = 1.0 if inside else -0.5
    penalty = STATUS_PENALTY.get(src.get("legal_status"), 0.0)
    if src.get("legal_status") == "repealed" and src.get("kind") in KIND_WEIGHT:
        penalty = 0.06   # repealed codes (IPC, CrPC, Evidence Act) still govern pre-July-2024 matters

    c.score = round(0.45 * rel + 0.22 * c.coverage + 0.13 * authority + 0.07 * klass + 0.05 * juris
                    + 0.04 * recency - penalty + (0.2 if c.exact else 0.0)
                    + (0.25 if getattr(c, "provision", "") else 0.0), 4)

    reasons = []
    if c.exact:
        reasons.append("Exact citation / case-name match")
    if getattr(c, "provision", ""):
        reasons.append(f"Provision named in the question: {c.provision}")
    if src.get("matter_id"):
        reasons.append("From this matter's documents")
    elif src.get("kind") in KIND_WEIGHT:
        reasons.append("Statutory text")
    elif src.get("court_level") in COURT_LABEL:
        reasons.append(COURT_LABEL[src["court_level"]])
    if terms:
        reasons.append(f"Matches {round(c.coverage * len(terms))}/{len(terms)} key terms")
    if c.lex_rank is not None and c.vec_rank is not None:
        reasons.append("Found by keyword and semantic search")
    elif c.lex_rank is not None:
        reasons.append("Found by keyword search")
    elif c.vec_rank is not None:
        reasons.append("Found by semantic search")
    if juris:
        reasons.append("Preferred jurisdiction")
    if penalty:
        reasons.append(f"Legal status: {src.get('legal_status').replace('_', ' ')}")
    c.reasons = [r for r in c.reasons if r] + reasons
    c.excerpt = best_excerpt(c.text, terms)


def rerank(cands: list, analysis) -> list:
    if not cands:
        return []
    terms = _query_terms(analysis)
    max_rrf = max(c.rrf for c in cands) or 1.0
    for c in cands:
        score_one(c, analysis, max_rrf, terms)
    return sorted(cands, key=lambda c: c.score, reverse=True)


def is_relevant(c) -> bool:
    return c.exact or bool(getattr(c, "provision", "")) or c.coverage >= MIN_COVERAGE


def diversify(ranked: list, top_k: int, per_source: int = 2) -> list:
    """Keep at most `per_source` passages per source and drop clearly irrelevant ones."""
    out, counts = [], {}
    for c in ranked:
        if not is_relevant(c):
            continue
        if counts.get(c.source_id, 0) >= per_source:
            continue
        counts[c.source_id] = counts.get(c.source_id, 0) + 1
        out.append(c)
        if len(out) >= top_k:
            break
    return out


def best_excerpt(text: str, terms: List[str], max_chars: int = 480) -> str:
    """Pick the densest run of sentences for display; the full passage is still kept."""
    sentences = re.split(r"(?<=[.;?!])\s+", text)
    if len(text) <= max_chars or len(sentences) <= 1:
        return text[:max_chars] + ("..." if len(text) > max_chars else "")
    tset = set(terms)
    best, best_score = 0, -1.0
    for i in range(len(sentences)):
        window, j = "", i
        while j < len(sentences) and len(window) + len(sentences[j]) <= max_chars:
            window += sentences[j] + " "
            j += 1
        s = sum(1 for t in tokenize(window) if t in tset) / (1 + len(window) / 400)
        if s > best_score:
            best, best_score = i, s
    out, j = "", best
    while j < len(sentences) and len(out) + len(sentences[j]) <= max_chars:
        out += sentences[j] + " "
        j += 1
    out = out.strip() or text[:max_chars]
    return ("..." if best > 0 else "") + out + ("..." if j < len(sentences) else "")
