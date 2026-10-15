"""Starter corpus: editorial summaries of landmark authorities.

These summaries were written by hand/AI, not taken from the judgments, so they
are stored as *secondary* sources with verification = "editorial_summary".
They make the app useful on day one, but real research needs the actual
judgments and bare Acts, which an administrator can add from Corpus > Add source.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from lawpilot.db import new_id
from lawpilot.ingestion import ingest

log = logging.getLogger("lawpilot.seed")

DATA_FILE = Path(__file__).parent / "data" / "landmark_summaries.json"

# (from, to, relation): well-established overrulings among the seeded authorities.
RELATIONS = [
    ("sc-puttaswamy-2017", "sc-mpsharma-1954", "overrules", "Privacy is a fundamental right"),
    ("sc-puttaswamy-2017", "sc-admjabalpur-1976", "overrules", "Majority view in ADM Jabalpur overruled"),
    ("sc-puttaswamy-2017", "sc-kharaksingh-1963", "overrules", "Overruled to the extent it denied a right to privacy"),
    ("sc-rccooper-1970", "sc-gopalan-1950", "overrules", "Rejected the 'silo' reading of fundamental rights"),
    ("sc-maneka-1978", "sc-gopalan-1950", "distinguishes", "Procedure under Article 21 must be fair, just and reasonable"),
    ("sc-golaknath-1967", "sc-shankari-1951", "overrules", None),
    ("sc-kesavananda-1973", "sc-golaknath-1967", "overrules", None),
    ("sc-minerva-1980", "sc-kesavananda-1973", "follows", "Applied the basic structure doctrine"),
    ("sc-inreinterplay-2023", "sc-nnglobal5j-2023", "overrules", "Unstamped arbitration agreements"),
]

DOMAIN_TAGS = {
    "Constitutional Law": "constitutional", "Arbitration": "commercial", "Criminal Law": "criminal",
    "Criminal Procedure": "criminal", "Negotiable Instruments": "cheque_bounce", "Family Law": "family",
    "Civil Procedure": "civil", "Insolvency": "commercial", "Evidence": "criminal", "Environmental Law": "environment",
}


def _domain(tags):
    for t in tags:
        for k, v in DOMAIN_TAGS.items():
            if k.lower() in t.lower():
                return v
    return None


def _text(item: dict) -> str:
    lines = ["EDITORIAL SUMMARY - not the text of the judgment or statute. Verify against the original."]
    if item.get("headnote"):
        lines.append("Summary: " + item["headnote"])
    if item.get("ratio"):
        lines.append("Principle: " + item["ratio"])
    for i, p in enumerate(item.get("propositions") or [], 1):
        lines.append(f"{i}. {p}")
    if item.get("tags"):
        lines.append("Topics: " + ", ".join(item["tags"]))
    return "\n\n".join(lines)


def seed_corpus(db, cfg) -> int:
    if db.execute("SELECT 1 FROM sources WHERE origin = 'seed' LIMIT 1").fetchone():
        return 0
    items = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    key_to_id = {}
    for item in items:
        is_statute = item.get("source_type") == "statute"
        meta = {
            "title": item["title"], "kind": "editorial_summary", "authority_class": "secondary",
            "citation": item.get("citation"), "alt_citations": item.get("alt_citations") or [],
            "court": None if is_statute else item.get("court"), "bench": item.get("bench"),
            "decision_date": item.get("decision_date"), "jurisdiction": "India",
            "legal_status": item.get("legal_status"), "verification": "editorial_summary",
            "legal_domain": _domain(item.get("tags") or []),
            "act_name": item["title"] if is_statute else None,
            "court_level": "none" if is_statute else None,
        }
        res = ingest(db, cfg, [(None, _text(item))], meta, origin="seed", note="starter corpus")
        if res.source_id:
            key_to_id[item["key"]] = res.source_id
    for frm, to, rel, note in RELATIONS:
        if frm in key_to_id and to in key_to_id:
            db.execute("INSERT OR IGNORE INTO authority_relations (id, from_source_id, to_source_id, relation, note)"
                       " VALUES (?,?,?,?,?)", (new_id(), key_to_id[frm], key_to_id[to], rel, note))
            if rel == "overrules":
                db.execute("UPDATE sources SET status_note = COALESCE(status_note, ?) WHERE id = ?",
                           (f"Overruled by {next(i['title'] for i in items if i['key'] == frm)}", key_to_id[to]))
    db.commit()
    log.info("Seeded %d editorial summaries", len(key_to_id))
    return len(key_to_id)
