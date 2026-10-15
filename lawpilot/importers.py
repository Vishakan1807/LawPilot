"""Bulk import of real Supreme Court of India judgments.

Source: the "Indian Supreme Court Judgments" open dataset on AWS (CC-BY-4.0),
built from the Supreme Court / eCourts judgment portal. Public S3 bucket, read
over plain HTTPS, no AWS account needed:
  metadata/json/year=YYYY/<name>.json        case metadata (title, SCR citation, neutral citation, coram, ...)
  data/pdf/year=YYYY/english/<name>_EN.pdf   judgment text

Imports are incremental: judgments already in the library (same PDF URL or
neutral citation) are skipped without downloading.

Command line:
  python -m lawpilot.importers --years 2023-2024 --limit 100
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import logging
import re
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional

import requests

from lawpilot import db as dbmod
from lawpilot.db import new_id, now
from lawpilot.extract import ExtractionError, extract
from lawpilot.ingestion import ingest

log = logging.getLogger("lawpilot.importers")

SCI_BUCKET = "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com"
SCI_ATTRIBUTION = ("Indian Supreme Court Judgments open dataset (CC-BY-4.0), sourced from the Supreme Court of India "
                   "judgment portal. Not the certified copy; verify against the court's record before relying on it.")
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
HTTP = requests.Session()
HTTP.headers["User-Agent"] = "LawPilot importer"


class ImportCancelled(Exception):
    pass


# ── Dataset access ─────────────────────────────────────────────────────────

def list_sci_year(year: int, limit: Optional[int] = None) -> List[str]:
    """Judgment base names for a year, e.g. '2023_10_1001_1009'."""
    names, token = [], None
    while True:
        params = {"list-type": "2", "prefix": f"metadata/json/year={year}/", "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        r = HTTP.get(SCI_BUCKET + "/", params=params, timeout=60)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for key in root.iter(S3_NS + "Key"):
            k = key.text or ""
            if k.endswith(".json"):
                names.append(k.rsplit("/", 1)[1][:-5])
                if limit and len(names) >= limit:
                    return names
        nxt = root.find(S3_NS + "NextContinuationToken")
        if nxt is None or not nxt.text:
            return names
        token = nxt.text


def sci_pdf_url(year: int, name: str) -> str:
    return f"{SCI_BUCKET}/data/pdf/year={year}/english/{name}_EN.pdf"


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def parse_sci_metadata(record: Dict) -> Dict:
    """Pull structured fields out of the portal HTML stored in the dataset's metadata JSON."""
    h = record.get("raw_html") or ""
    out: Dict = {"neutral_citation": None, "citation": None}

    m = re.search(r"aria-label=\"([^\"]+?)\s*pdf\"", h)
    title = htmllib.unescape(m.group(1)).strip() if m else ""
    if not title:
        m = re.search(r"<strong>(.*?)</strong>\s*-", h, re.S)
        title = _text(m.group(1)) if m else ""
    title = re.sub(r"\s+", " ", title)
    out["parties"] = title
    if title:
        t = re.sub(r"\s+versus\s+", " v. ", title, flags=re.I).title().replace(" V. ", " v. ")
        t = re.sub(r"(?<=\s)(Of|And|The|For|In|On|By|To)(?=\s)", lambda m: m.group(1).lower(), t)
        out["title"] = t.replace(" v. the ", " v. The ")
    else:
        out["title"] = None

    m = re.search(r"class='escrText'>([^<]+)<", h)
    out["citation"] = m.group(1).strip() if m else None
    m = re.search(r"class='ncDisplay'>([^<]+)<", h)
    nc = m.group(1).strip() if m else (record.get("nc_display") or "")
    if nc:
        nc = re.sub(r"(\d{4})\s*INSC\s*(\d+)", r"\1 INSC \2", nc)
    out["neutral_citation"] = nc or None

    m = re.search(r"Coram\s*:\s*(.*?)</strong>", h, re.S)
    if m:
        coram = m.group(1)
        author = re.search(r"([A-Z][A-Z .'\-]+?)\s*<sup[^>]*Author", coram)
        judges = [j.strip(" *") for j in _text(re.sub(r"<sup.*?</sup>", "", coram, flags=re.S)).split(",") if j.strip(" *")]
        out["judges"] = ", ".join(judges)
        out["author_judge"] = author.group(1).strip() if author else None

    m = re.search(r"Coram\s*:.*?</strong>\s*<br>(.*?)<strong class='caseDetailsTD'", h, re.S)
    out["headnote"] = _text(m.group(1)) if m else ""

    def detail(label):
        mm = re.search(label + r"\s*:</span><font[^>]*>\s*([^<]*)<", h)
        return mm.group(1).strip() if mm else None

    date = detail("Decision Date")
    try:
        out["decision_date"] = datetime.strptime(date, "%d-%m-%Y").date().isoformat() if date else None
    except ValueError:
        out["decision_date"] = None
    out["case_number"] = detail("Case No")
    out["disposal"] = detail("Disposal Nature")
    out["bench"] = detail("Bench")
    return out


def _fetch_one(year: int, name: str) -> Dict:
    """Download + parse one judgment (runs in worker threads; no database access)."""
    r = HTTP.get(f"{SCI_BUCKET}/metadata/json/year={year}/{name}.json", timeout=60)
    r.raise_for_status()
    meta = parse_sci_metadata(r.json())
    pdf = HTTP.get(sci_pdf_url(year, name), timeout=120)
    if pdf.status_code == 404:
        return {"meta": meta, "error": "no English PDF in the dataset"}
    pdf.raise_for_status()
    try:
        pages = extract(pdf.content, f"{name}.pdf")
    except ExtractionError as exc:
        return {"meta": meta, "error": str(exc)}
    return {"meta": meta, "pages": pages}


# ── Import ─────────────────────────────────────────────────────────────────

def _exists(db, year: int, name: str) -> bool:
    url = sci_pdf_url(year, name)
    return db.execute("SELECT 1 FROM sources WHERE source_url = ? AND matter_id IS NULL", (url,)).fetchone() is not None


def _ingest_fetched(db, cfg, year: int, name: str, fetched: Dict, user_id: Optional[str]) -> str:
    meta = fetched["meta"]
    if fetched.get("error"):
        raise RuntimeError(fetched["error"])
    pages = list(fetched["pages"])
    if meta.get("headnote"):
        pages.insert(0, (None, "HEADNOTE (Supreme Court Reports, editorial): " + meta["headnote"]))
    res = ingest(db, cfg, pages, {
        "title": meta.get("title") or name, "kind": "judgment", "authority_class": "primary",
        "citation": meta.get("citation"), "neutral_citation": meta.get("neutral_citation"),
        "court": "Supreme Court of India", "court_level": "supreme", "bench": meta.get("bench"),
        "judges": meta.get("judges"), "parties": meta.get("parties"), "decision_date": meta.get("decision_date"),
        "case_number": meta.get("case_number"), "jurisdiction": "India", "verification": "unverified",
        "legal_status": "unknown", "source_url": sci_pdf_url(year, name),
    }, origin="sci_open_data", user_id=user_id, note=SCI_ATTRIBUTION)
    if res.status == "failed":
        raise RuntimeError(res.message)
    return res.status


def run_sci_import(cfg, years: List[int], limit_per_year: Optional[int] = None, user_id: Optional[str] = None,
                   run_id: Optional[str] = None, workers: int = 4) -> Dict:
    """Import judgments for the given years. Progress is written to import_runs."""
    db = dbmod.ResilientConn(cfg)       # survives the database dropping idle connections during downloads
    run_id = run_id or new_id()
    try:
        if not db.execute("SELECT 1 FROM import_runs WHERE id = ?", (run_id,)).fetchone():
            db.execute("INSERT INTO import_runs (id, source, params, status, created_by, started_at) VALUES (?,?,?,?,?,?)",
                       (run_id, "sci_open_data", json.dumps({"years": years, "limit_per_year": limit_per_year}),
                        "running", user_id, now()))
            db.commit()
        counts = {"total": 0, "done": 0, "imported": 0, "skipped": 0, "failed": 0}
        errors: List[str] = []

        def progress(status="running", message=None):
            db.execute("UPDATE import_runs SET status = ?, total = ?, done = ?, imported = ?, skipped = ?, failed = ?,"
                       " message = ?, finished_at = ?, heartbeat_at = ? WHERE id = ?",
                       (status, counts["total"], counts["done"], counts["imported"], counts["skipped"], counts["failed"],
                        message, None if status == "running" else now(), now(), run_id))
            db.commit()

        def cancelled() -> bool:
            row = db.execute("SELECT status FROM import_runs WHERE id = ?", (run_id,)).fetchone()
            return bool(row and row[0] == "cancelling")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for year in years:
                names = list_sci_year(year, limit_per_year)
                counts["total"] += len(names)
                todo = []
                for name in names:
                    if _exists(db, year, name):
                        counts["skipped"] += 1
                        counts["done"] += 1
                    else:
                        todo.append(name)
                progress(message=f"Year {year}: {len(todo)} to import")
                for i in range(0, len(todo), workers * 2):
                    if cancelled():
                        raise ImportCancelled()
                    batch = todo[i:i + workers * 2]
                    futures = {n: pool.submit(_fetch_one, year, n) for n in batch}
                    for name, fut in futures.items():
                        try:
                            status = _ingest_fetched(db, cfg, year, name, fut.result(), user_id)
                            counts["imported" if status == "succeeded" else "skipped"] += 1
                        except Exception as exc:
                            counts["failed"] += 1
                            errors.append(f"{name}: {exc}")
                            log.warning("SCI import %s failed: %s", name, exc)
                        counts["done"] += 1
                    progress(message=f"Year {year}: {counts['done']}/{counts['total']} processed")
        summary = f"Imported {counts['imported']}, skipped {counts['skipped']} already present, failed {counts['failed']}."
        if errors:
            summary += " Examples of failures: " + "; ".join(errors[:3])
        progress("completed", summary)
        return {**counts, "run_id": run_id, "message": summary}
    except ImportCancelled:
        progress("cancelled", f"Cancelled after {counts['done']} of {counts['total']}.")
        return {**counts, "run_id": run_id}
    except Exception as exc:
        log.exception("SCI import failed")
        try:
            db.rollback()
            db.execute("UPDATE import_runs SET status = 'failed', message = ?, finished_at = ? WHERE id = ?",
                       (str(exc)[:1000], now(), run_id))
            db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()


_running: Dict[str, threading.Thread] = {}
STALE_AFTER_MINUTES = 10


def close_stale_runs(db) -> None:
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STALE_AFTER_MINUTES)).replace(microsecond=0).isoformat()
    db.execute("UPDATE import_runs SET status = 'failed', message = 'Interrupted: the import stopped responding. Start it again; judgments already imported are skipped.', finished_at = ? WHERE status IN ('running', 'cancelling') AND COALESCE(heartbeat_at, started_at) < ?", (now(), cutoff))
    db.commit()


def start_background_import(cfg, years: List[int], limit_per_year: Optional[int], user_id: str) -> str:
    db = dbmod.connect(cfg)
    try:
        close_stale_runs(db)
        if db.execute("SELECT 1 FROM import_runs WHERE status IN ('running', 'cancelling')").fetchone():
            raise RuntimeError("An import is already running (possibly from the command line). "
                               "Wait for it to finish or cancel it.")
        run_id = new_id()
        db.execute("INSERT INTO import_runs (id, source, params, status, created_by, started_at) VALUES (?,?,?,?,?,?)",
                   (run_id, "sci_open_data", json.dumps({"years": years, "limit_per_year": limit_per_year}),
                    "running", user_id, now()))
        db.commit()
    finally:
        db.close()

    def target():
        try:
            run_sci_import(cfg, years, limit_per_year, user_id, run_id)
        except Exception:
            pass   # recorded in import_runs

    t = threading.Thread(target=target, name=f"sci-import-{run_id}", daemon=True)
    _running[run_id] = t
    t.start()
    return run_id


def _main():
    from lawpilot.config import Config
    from lawpilot import settings
    parser = argparse.ArgumentParser(description="Import Supreme Court judgments from the open dataset.")
    parser.add_argument("--years", required=True, help="e.g. 2024 or 2020-2024")
    parser.add_argument("--limit", type=int, default=None, help="max judgments per year")
    args = parser.parse_args()
    a, _, b = args.years.partition("-")
    years = list(range(int(a), int(b or a) + 1))
    logging.basicConfig(level=logging.INFO)
    cfg = Config.from_env()
    conn = dbmod.connect(cfg, pooled=False)
    try:
        dbmod.init_schema(conn)
        cfg = settings.effective(conn, cfg)
    finally:
        conn.close()
    result = run_sci_import(cfg, years, args.limit)
    print(result["message"] if "message" in result else result)


if __name__ == "__main__":
    _main()
