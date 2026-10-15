"""Rule-based analysis of a legal research question.

Runs before retrieval and needs no LLM. It identifies citations, statutes,
sections, articles, courts, judges, parties, time period and legal domain, and
expands old-code sections to their new-code counterparts (IPC->BNS etc.).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

COURTS = {
    "supreme court": "Supreme Court of India",
    "apex court": "Supreme Court of India",
    "delhi high court": "Delhi High Court", "bombay high court": "Bombay High Court",
    "madras high court": "Madras High Court", "calcutta high court": "Calcutta High Court",
    "karnataka high court": "Karnataka High Court", "kerala high court": "Kerala High Court",
    "allahabad high court": "Allahabad High Court", "gujarat high court": "Gujarat High Court",
    "punjab and haryana high court": "Punjab & Haryana High Court", "rajasthan high court": "Rajasthan High Court",
    "telangana high court": "Telangana High Court", "andhra pradesh high court": "Andhra Pradesh High Court",
    "madhya pradesh high court": "Madhya Pradesh High Court", "patna high court": "Patna High Court",
    "orissa high court": "Orissa High Court", "gauhati high court": "Gauhati High Court",
    "jharkhand high court": "Jharkhand High Court", "chhattisgarh high court": "Chhattisgarh High Court",
    "himachal pradesh high court": "Himachal Pradesh High Court", "uttarakhand high court": "Uttarakhand High Court",
    "jammu and kashmir high court": "Jammu & Kashmir and Ladakh High Court",
    "high court": "High Court",
    "nclat": "NCLAT", "nclt": "NCLT", "ncdrc": "NCDRC", "itat": "ITAT", "cestat": "CESTAT",
    "ngt": "National Green Tribunal", "national green tribunal": "National Green Tribunal",
    "sat": "Securities Appellate Tribunal", "drt": "Debts Recovery Tribunal", "cat": "Central Administrative Tribunal",
}
STATE_OF_HC = {
    "Delhi High Court": "Delhi", "Bombay High Court": "Maharashtra", "Madras High Court": "Tamil Nadu",
    "Calcutta High Court": "West Bengal", "Karnataka High Court": "Karnataka", "Kerala High Court": "Kerala",
    "Allahabad High Court": "Uttar Pradesh", "Gujarat High Court": "Gujarat", "Rajasthan High Court": "Rajasthan",
    "Telangana High Court": "Telangana", "Andhra Pradesh High Court": "Andhra Pradesh",
    "Madhya Pradesh High Court": "Madhya Pradesh", "Patna High Court": "Bihar", "Orissa High Court": "Odisha",
    "Punjab & Haryana High Court": "Punjab", "Gauhati High Court": "Assam",
}

STATUTES = {
    "ipc": "Indian Penal Code, 1860", "indian penal code": "Indian Penal Code, 1860",
    "bns": "Bharatiya Nyaya Sanhita, 2023", "bharatiya nyaya sanhita": "Bharatiya Nyaya Sanhita, 2023",
    "crpc": "Code of Criminal Procedure, 1973", "cr.p.c": "Code of Criminal Procedure, 1973",
    "code of criminal procedure": "Code of Criminal Procedure, 1973",
    "bnss": "Bharatiya Nagarik Suraksha Sanhita, 2023",
    "cpc": "Code of Civil Procedure, 1908", "code of civil procedure": "Code of Civil Procedure, 1908",
    "evidence act": "Indian Evidence Act, 1872", "iea": "Indian Evidence Act, 1872",
    "bsa": "Bharatiya Sakshya Adhiniyam, 2023", "bharatiya sakshya": "Bharatiya Sakshya Adhiniyam, 2023",
    "contract act": "Indian Contract Act, 1872", "companies act": "Companies Act, 2013",
    "it act": "Information Technology Act, 2000", "information technology act": "Information Technology Act, 2000",
    "arbitration act": "Arbitration and Conciliation Act, 1996",
    "arbitration and conciliation": "Arbitration and Conciliation Act, 1996",
    "ndps": "Narcotic Drugs and Psychotropic Substances Act, 1985",
    "posh": "Sexual Harassment of Women at Workplace Act, 2013", "rti": "Right to Information Act, 2005",
    "consumer protection act": "Consumer Protection Act, 2019", "income tax act": "Income-tax Act, 1961",
    "gst": "Central Goods and Services Tax Act, 2017", "motor vehicles act": "Motor Vehicles Act, 1988",
    "transfer of property act": "Transfer of Property Act, 1882", "tpa": "Transfer of Property Act, 1882",
    "specific relief act": "Specific Relief Act, 1963", "limitation act": "Limitation Act, 1963",
    "negotiable instruments act": "Negotiable Instruments Act, 1881", "ni act": "Negotiable Instruments Act, 1881",
    "hindu marriage act": "Hindu Marriage Act, 1955", "hindu succession act": "Hindu Succession Act, 1956",
    "domestic violence act": "Protection of Women from Domestic Violence Act, 2005",
    "pocso": "Protection of Children from Sexual Offences Act, 2012",
    "ibc": "Insolvency and Bankruptcy Code, 2016", "insolvency and bankruptcy code": "Insolvency and Bankruptcy Code, 2016",
    "rera": "Real Estate (Regulation and Development) Act, 2016", "sarfaesi": "SARFAESI Act, 2002",
    "prevention of corruption act": "Prevention of Corruption Act, 1988", "copyright act": "Copyright Act, 1957",
    "trade marks act": "Trade Marks Act, 1999", "patents act": "Patents Act, 1970",
    "commercial courts act": "Commercial Courts Act, 2015", "stamp act": "Indian Stamp Act, 1899",
    "constitution": "Constitution of India",
}

DOMAINS = {
    "criminal": ["bail", "fir", "offence", "accused", "ipc", "bns", "crpc", "bnss", "murder", "cheating", "arrest",
                 "chargesheet", "quash", "acquittal", "conviction", "ndps", "pocso", "498a"],
    "constitutional": ["article", "fundamental right", "writ", "constitution", "basic structure", "article 21"],
    "civil": ["suit", "plaint", "decree", "injunction", "cpc", "specific performance", "possession", "partition"],
    "commercial": ["arbitration", "commercial", "contract", "breach", "damages", "company", "ibc", "insolvency"],
    "family": ["divorce", "maintenance", "custody", "marriage", "alimony", "domestic violence", "succession"],
    "property": ["property", "sale deed", "title", "tenant", "landlord", "eviction", "rera", "mortgage"],
    "tax": ["income tax", "gst", "assessment", "itat", "customs", "excise"],
    "service": ["service", "termination", "disciplinary", "pension", "promotion", "seniority"],
    "cheque_bounce": ["cheque", "138", "negotiable instruments", "dishonour"],
}

# Commonly cited correspondences between the old and new criminal codes (in force from 1 July 2024).
# Shown to users as a research aid; always confirm against the bare Act.
CODE_MAP = {
    ("Indian Penal Code, 1860", "302"): ("Bharatiya Nyaya Sanhita, 2023", "103"),
    ("Indian Penal Code, 1860", "304B"): ("Bharatiya Nyaya Sanhita, 2023", "80"),
    ("Indian Penal Code, 1860", "307"): ("Bharatiya Nyaya Sanhita, 2023", "109"),
    ("Indian Penal Code, 1860", "376"): ("Bharatiya Nyaya Sanhita, 2023", "64"),
    ("Indian Penal Code, 1860", "406"): ("Bharatiya Nyaya Sanhita, 2023", "316"),
    ("Indian Penal Code, 1860", "420"): ("Bharatiya Nyaya Sanhita, 2023", "318"),
    ("Indian Penal Code, 1860", "498A"): ("Bharatiya Nyaya Sanhita, 2023", "85"),
    ("Indian Penal Code, 1860", "120B"): ("Bharatiya Nyaya Sanhita, 2023", "61"),
    ("Indian Penal Code, 1860", "354"): ("Bharatiya Nyaya Sanhita, 2023", "74"),
    ("Indian Penal Code, 1860", "506"): ("Bharatiya Nyaya Sanhita, 2023", "351"),
    ("Code of Criminal Procedure, 1973", "438"): ("Bharatiya Nagarik Suraksha Sanhita, 2023", "482"),
    ("Code of Criminal Procedure, 1973", "439"): ("Bharatiya Nagarik Suraksha Sanhita, 2023", "483"),
    ("Code of Criminal Procedure, 1973", "482"): ("Bharatiya Nagarik Suraksha Sanhita, 2023", "528"),
    ("Code of Criminal Procedure, 1973", "125"): ("Bharatiya Nagarik Suraksha Sanhita, 2023", "144"),
    ("Code of Criminal Procedure, 1973", "41A"): ("Bharatiya Nagarik Suraksha Sanhita, 2023", "35"),
    ("Code of Criminal Procedure, 1973", "164"): ("Bharatiya Nagarik Suraksha Sanhita, 2023", "183"),
    ("Indian Evidence Act, 1872", "65B"): ("Bharatiya Sakshya Adhiniyam, 2023", "63"),
}
REVERSE_CODE_MAP = {v: k for k, v in CODE_MAP.items()}

IPC, BNS = "Indian Penal Code, 1860", "Bharatiya Nyaya Sanhita, 2023"
CRPC, BNSS = "Code of Criminal Procedure, 1973", "Bharatiya Nagarik Suraksha Sanhita, 2023"
IEA, BSA = "Indian Evidence Act, 1872", "Bharatiya Sakshya Adhiniyam, 2023"

# Offences and procedures named in plain words -> (old code, section, new code, section).
# Longest phrases first so "attempt to murder" wins over "murder". A research aid; confirm with the bare Act.
CONCEPT_PROVISIONS = [
    ("attempt to murder", IPC, "307", BNS, "109"), ("attempt to commit murder", IPC, "307", BNS, "109"),
    ("culpable homicide", IPC, "304", BNS, "105"), ("dowry death", IPC, "304B", BNS, "80"),
    ("abetment of suicide", IPC, "306", BNS, "108"), ("criminal conspiracy", IPC, "120B", BNS, "61"),
    ("criminal breach of trust", IPC, "406", BNS, "316"), ("criminal intimidation", IPC, "506", BNS, "351"),
    ("cruelty by husband", IPC, "498A", BNS, "85"), ("dowry harassment", IPC, "498A", BNS, "85"),
    ("outraging modesty", IPC, "354", BNS, "74"), ("molestation", IPC, "354", BNS, "74"),
    ("grievous hurt", IPC, "325", BNS, "117"), ("kidnapping", IPC, "363", BNS, "137"),
    ("dacoity", IPC, "395", BNS, "310"), ("robbery", IPC, "392", BNS, "309"), ("theft", IPC, "379", BNS, "303"),
    ("cheating", IPC, "420", BNS, "318"), ("defamation", IPC, "500", BNS, "356"), ("rape", IPC, "376", BNS, "64"),
    ("murder", IPC, "302", BNS, "103"),
    ("anticipatory bail", CRPC, "438", BNSS, "482"), ("pre-arrest bail", CRPC, "438", BNSS, "482"),
    ("default bail", CRPC, "167", BNSS, "187"), ("statutory bail", CRPC, "167", BNSS, "187"),
    ("regular bail", CRPC, "439", BNSS, "483"), ("quashing", CRPC, "482", BNSS, "528"),
    ("quash", CRPC, "482", BNSS, "528"), ("inherent powers", CRPC, "482", BNSS, "528"),
    ("maintenance", CRPC, "125", BNSS, "144"), ("registration of fir", CRPC, "154", BNSS, "173"),
    ("electronic evidence", IEA, "65B", BSA, "63"), ("electronic record", IEA, "65B", BSA, "63"),
]

CITATION_PATTERNS = [
    r"\(\d{4}\)\s*\d{1,3}\s*SCC\s*(?:\(\w+\)\s*)?\d{1,4}",       # (2017) 10 SCC 1
    r"\d{4}\s*SCC\s*OnLine\s*\w+\s*\d{1,6}",                       # 2021 SCC OnLine SC 123
    r"AIR\s*\d{4}\s*(?:SC|[A-Z][a-z]+|[A-Z]{2,4})\s*\d{1,5}",      # AIR 1978 SC 597
    r"\[?\d{4}\]?\s*\d{0,2}\s*S\.?C\.?R\.?\s*\d{1,4}",            # [1978] 2 SCR 621
    r"\d{4}\s*INSC\s*\d{1,5}",                                     # 2023 INSC 456
    r"\d{4}\s*:\s*[A-Z]{2,6}\s*:\s*\d{1,6}(?:\s*-\s*DB)?",         # 2024:DHC:1234
]
CITATION_RE = re.compile("|".join(f"(?:{p})" for p in CITATION_PATTERNS), re.I)
SECTION_RE = re.compile(r"\b(?:section|sec\.?|s\.|u/s\.?|ss\.)\s*(\d{1,3}[A-Za-z]{0,2}(?:\(\w+\))*)", re.I)
ARTICLE_RE = re.compile(r"\b(?:article|art\.)\s*(\d{1,3}[A-Za-z]?(?:\(\w+\))*)", re.I)
ORDER_RULE_RE = re.compile(r"\border\s+([ivxlc]+|\d+)\s*(?:,?\s*rule\s+(\d+[a-z]?))?", re.I)
VERSUS_RE = re.compile(r"([A-Z][\w.&'\- ]{1,60}?)\s+(?:v\.?|vs\.?|versus)\s+([A-Z][\w.&'\- ]{1,60})")
JUDGE_RE = re.compile(r"(?:justice|hon'?ble)\s+([A-Z][\w.]*(?:\s+[A-Z][\w.]*){0,3})|([A-Z][\w.]*(?:\s+[A-Z][\w.]*){0,2}),?\s+J\.", re.M)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
CASE_NO_RE = re.compile(r"\b(?:W\.?P\.?|Crl\.?\s*A\.?|C\.?A\.?|SLP|O\.?S\.?|CS|Arb\.?\s*P\.?)\s*(?:\(\w+\))?\s*(?:No\.?)?\s*\d+\s*(?:of|/)\s*\d{4}", re.I)


@dataclass
class QueryAnalysis:
    query: str
    legal_issue: str = ""
    jurisdiction: str = "India"
    courts: List[str] = field(default_factory=list)
    preferred_state: Optional[str] = None
    statutes: List[str] = field(default_factory=list)
    sections: List[str] = field(default_factory=list)
    articles: List[str] = field(default_factory=list)
    orders_rules: List[str] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)
    case_numbers: List[str] = field(default_factory=list)
    parties: List[str] = field(default_factory=list)
    judges: List[str] = field(default_factory=list)
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    domains: List[str] = field(default_factory=list)
    code_correspondence: List[Dict[str, str]] = field(default_factory=list)
    inferred_provisions: List[Dict[str, str]] = field(default_factory=list)   # from offence/procedure names
    search_type: str = "topic"   # citation | case_name | provision | topic
    key_terms: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def analyze(query: str, current_year: int = 2026) -> QueryAnalysis:
    q = " ".join((query or "").split())
    ql = q.lower()
    a = QueryAnalysis(query=q)

    a.citations = _uniq(m.group(0).strip() for m in CITATION_RE.finditer(q))
    a.case_numbers = _uniq(m.group(0) for m in CASE_NO_RE.finditer(q))

    for key, name in COURTS.items():
        if re.search(r"\b" + re.escape(key) + r"\b", ql) and name not in a.courts:
            if name == "High Court" and any("High Court" in c for c in a.courts):
                continue
            a.courts.append(name)
    a.preferred_state = next((STATE_OF_HC[c] for c in a.courts if c in STATE_OF_HC), None)

    for key, name in sorted(STATUTES.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"(?<![\w.])" + re.escape(key) + r"(?![\w])", ql) and name not in a.statutes:
            a.statutes.append(name)
    if a.articles or "article" in ql:
        pass
    a.sections = _uniq(m.group(1).upper() for m in SECTION_RE.finditer(q))
    a.articles = _uniq(m.group(1) for m in ARTICLE_RE.finditer(q))
    if a.articles and "Constitution of India" not in a.statutes:
        a.statutes.append("Constitution of India")
    a.orders_rules = _uniq(
        f"Order {m.group(1).upper()}" + (f" Rule {m.group(2)}" if m.group(2) else "") for m in ORDER_RULE_RE.finditer(q))

    for m in VERSUS_RE.finditer(q):
        for p in (m.group(1), m.group(2)):
            p = re.sub(r"^(?:in|the case of|case of|what did|did)\s+", "", p.strip(), flags=re.I).strip(" .,")
            if len(p) > 2:
                a.parties.append(p)
    a.parties = _uniq(a.parties)
    a.judges = _uniq((m.group(1) or m.group(2)).strip() for m in JUDGE_RE.finditer(q))

    _temporal(a, ql, current_year)

    for domain, words in DOMAINS.items():
        if any(re.search(r"\b" + re.escape(w) + r"\b", ql) for w in words):
            a.domains.append(domain)

    for statute in a.statutes:
        for sec in a.sections:
            base = re.match(r"\d+[A-Z]*", sec).group(0)
            if (statute, base) in CODE_MAP:
                new_act, new_sec = CODE_MAP[(statute, base)]
                a.code_correspondence.append({"from": f"Section {base} {statute}", "to": f"Section {new_sec} {new_act}"})
            elif (statute, base) in REVERSE_CODE_MAP:
                old_act, old_sec = REVERSE_CODE_MAP[(statute, base)]
                a.code_correspondence.append({"from": f"Section {base} {statute}", "to": f"Section {old_sec} {old_act}"})

    _infer_provisions(a, ql)

    if a.citations or a.case_numbers:
        a.search_type = "citation"
    elif a.parties:
        a.search_type = "case_name"
    elif (a.sections or a.articles) and len(q.split()) <= 8:
        a.search_type = "provision"
    a.key_terms = key_terms(q)
    a.legal_issue = _issue_summary(a)
    return a


def _temporal(a: QueryAnalysis, ql: str, current_year: int) -> None:
    m = re.search(r"(?:last|past)\s+(\d{1,2})\s+years?", ql)
    if m:
        a.year_from, a.year_to = current_year - int(m.group(1)), current_year
        return
    m = re.search(r"between\s+(\d{4})\s+(?:and|-|to)\s+(\d{4})", ql)
    if m:
        a.year_from, a.year_to = int(m.group(1)), int(m.group(2))
        return
    m = re.search(r"(?:after|since|post)\s+(\d{4})", ql)
    if m:
        a.year_from = int(m.group(1))
    m = re.search(r"(?:before|prior to|pre)\s+(\d{4})", ql)
    if m:
        a.year_to = int(m.group(1))
    if a.year_from is None and a.year_to is None and re.search(r"\b(?:recent|latest)\b", ql):
        a.year_from = current_year - 3


def key_terms(q: str) -> List[str]:
    from lawpilot.embeddings import STOPWORDS
    filler = {"what", "which", "law", "legal", "case", "cases", "judgment", "judgments", "court", "held", "regarding",
              "explain", "tell", "give", "find", "relevant", "position", "india", "indian", "please", "can", "is",
              "under", "whether", "does", "do", "any", "related", "applicable", "provision", "provisions"}
    words = re.findall(r"[A-Za-z][A-Za-z\-']+|\d+[A-Za-z]?", q)
    out = []
    for w in words:
        lw = w.lower()
        if lw in STOPWORDS or lw in filler or len(lw) < 3 and not lw[0].isdigit():
            continue
        out.append(lw)
    return _uniq(out)[:14]


def _issue_summary(a: QueryAnalysis) -> str:
    bits = []
    if a.sections:
        bits.append("Section " + ", ".join(a.sections))
    if a.articles:
        bits.append("Article " + ", ".join(a.articles))
    if a.statutes:
        bits.append("; ".join(a.statutes[:3]))
    if a.domains:
        bits.append(", ".join(a.domains) + " law")
    return " | ".join(bits) or "General legal question"


def _uniq(items) -> List[str]:
    seen, out = set(), []
    for i in items:
        k = i.lower()
        if i and k not in seen:
            seen.add(k)
            out.append(i)
    return out


def _infer_provisions(a: QueryAnalysis, ql: str) -> None:
    """Map offence / procedure names to sections of both the old and the new criminal codes."""
    text = " " + ql + " "
    for phrase, old_act, old_sec, new_act, new_sec in CONCEPT_PROVISIONS:
        if not re.search(r"\b" + re.escape(phrase) + r"\b", text):
            continue
        text = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", text)   # don't also match "murder" inside it
        if any(p["phrase"] == phrase or (p["old"] == f"Section {old_sec} {old_act}") for p in a.inferred_provisions):
            continue
        a.inferred_provisions.append({"phrase": phrase, "old": f"Section {old_sec} {old_act}",
                                      "new": f"Section {new_sec} {new_act}"})
        for act in (old_act, new_act):
            if act not in a.statutes:
                a.statutes.append(act)
        corr = {"from": f"Section {old_sec} {old_act}", "to": f"Section {new_sec} {new_act}"}
        if corr not in a.code_correspondence:
            a.code_correspondence.append(corr)
