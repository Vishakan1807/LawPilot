"""Resolve India Code links to the official PDF and its metadata.

India Code (indiacode.gov.in / indiacode.nic.in) is a DSpace repository. The
links people copy from the browser are viewer pages, not files:
  https://indiacode.gov.in/pdf-viewer/<item-uuid>/<file-uuid>
  https://indiacode.gov.in/handle/123456789/620453
Its REST API returns the PDF and the Act's official metadata (title, Act
number, year, enactment date, ministry, central/state).
"""
from __future__ import annotations

import re
from typing import Dict, Optional

import requests

API = "https://indiacode.gov.in/server/api"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
VIEWER_RE = re.compile(rf"indiacode\.(?:gov|nic)\.in/pdf-viewer/({UUID})/({UUID})", re.I)
HANDLE_RE = re.compile(r"indiacode\.(?:gov|nic)\.in/handle/(\d+/\d+)", re.I)
TIMEOUT = 30


class IndiaCodeError(Exception):
    pass


def is_indiacode(url: str) -> bool:
    return bool(VIEWER_RE.search(url or "") or HANDLE_RE.search(url or ""))


def _get_json(url: str) -> Dict:
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        raise IndiaCodeError(f"Could not reach India Code: {exc}") from exc
    if r.status_code != 200:
        raise IndiaCodeError(f"India Code returned HTTP {r.status_code} for {url}")
    return r.json()


def _first(meta: Dict, key: str) -> Optional[str]:
    vals = meta.get(key) or []
    return (vals[0].get("value") or "").strip() or None if vals else None


REPEALING_CODES = {"45 of 2023": "Bharatiya Nyaya Sanhita, 2023", "46 of 2023": "Bharatiya Nagarik Suraksha Sanhita, 2023",
                   "47 of 2023": "Bharatiya Sakshya Adhiniyam, 2023"}


def _clean_title(title: str, number: Optional[str], year: Optional[str]):
    """Repealed Acts are titled like 'The Indian Penal Code, 45 of 1860 (Rep., Act 45 of 2023)'."""
    status, note = "unknown", None
    rep = re.search(r"\(\s*Rep\.?,?\s*(?:by\s+)?(?:Act\s+)?(\d+\s+of\s+\d{4})?[^)]*\)", title, re.I)
    if rep:
        status = "repealed"
        by = rep.group(1)
        note = "Repealed" + (f" by Act {by}" + (f" ({REPEALING_CODES[by]})" if by in REPEALING_CODES else "") if by else "")
        note += ". Still relevant to matters that arose before the repeal took effect; check which law applies."
        title = title[:rep.start()].strip(" ,")
    m = re.search(r",?\s*(\d+)\s+of\s+(\d{4})$", title)
    if m:
        number = number or m.group(1)
        title = title[:m.start()].strip(" ,")
        if not re.search(r"\d{4}", title):
            title = f"{title}, {m.group(2)}"
        year = year or m.group(2)
    title = re.sub(r"(?<=[A-Za-z])\s+(\d{4})$", r", \1", title.strip().rstrip("."))
    return title, number, year, status, note


def resolve(url: str) -> Dict:
    """Return {'pdf_url', 'meta', 'filename'} for an India Code link."""
    m = VIEWER_RE.search(url)
    if m:
        item_id, file_id = m.group(1), m.group(2)
        item = _get_json(f"{API}/core/items/{item_id}")
    else:
        h = HANDLE_RE.search(url)
        if not h:
            raise IndiaCodeError("Not an India Code link.")
        item = _get_json(f"{API}/pid/find?id=hdl:{h.group(1)}")
        bundles = _get_json(f"{API}/core/items/{item['uuid']}/bundles")["_embedded"]["bundles"]
        original = next((b for b in bundles if b.get("name") == "ORIGINAL"), None)
        if not original:
            raise IndiaCodeError("This India Code record has no attached PDF.")
        files = _get_json(f"{API}/core/bundles/{original['uuid']}/bitstreams")["_embedded"]["bitstreams"]
        pdfs = [f for f in files if (f.get("name") or "").lower().endswith(".pdf")] or files
        if not pdfs:
            raise IndiaCodeError("This India Code record has no attached PDF.")
        # Records can hold both the Hindi Gazette scan and the English text. India Code names the English
        # consolidated text 'A<year>-<number>.pdf'; prefer it, then the smallest file.
        pdfs.sort(key=lambda f: (0 if re.match(r"(?i)^a\d{4}-\d+\.pdf$", f.get("name") or "") else 1,
                                 f.get("sizeBytes") or 0))
        file_id = pdfs[0]["uuid"]

    md = item.get("metadata") or {}
    title = _first(md, "dc.title") or item.get("name")
    number, year = _first(md, "dc.identifier.act_number"), _first(md, "dc.date.act_year")
    state = _first(md, "dc.identifier.state_name") or ""
    collection = (_first(md, "dc.identifier.collection") or "ACT").upper()
    kind = "statute" if collection.startswith("ACT") else ("rule" if "RULE" in collection or "REGULATION" in collection
                                                            else "notification")
    title, number, year, legal_status, status_note = _clean_title(title or "", number, year)
    citation = None
    if number and year:
        citation = f"Act No. {int(number) if number.isdigit() else number} of {year}"
    meta = {
        "title": title,
        "kind": kind,
        "authority_class": "primary",
        "verification": "verified_official",     # text comes straight from the official repository
        "legal_status": legal_status,             # India Code only records repeals, not amendments
        "status_note": status_note,
        "citation": citation,
        "act_name": title,
        "decision_date": _first(md, "dc.date.enact_date"),
        "jurisdiction": "India" if state.upper() in ("", "CENTRAL") else state.title(),
        "legal_domain": _first(md, "dc.identifier.ministry_name"),
        "source_url": _first(md, "dc.identifier.uri") or url,
    }
    return {"pdf_url": f"{API}/core/bitstreams/{file_id}/content", "meta": meta,
            "filename": re.sub(r"[^\w.-]+", "_", (title or "act"))[:80] + ".pdf",
            "long_title": _first(md, "dc.title.long_title")}
