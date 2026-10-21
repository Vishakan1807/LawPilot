"""Drafting templates and pre-filing checks.

Templates never invent facts. Any field left empty is rendered as a visible
[[Placeholder]] and reported by the checker, so a draft cannot silently go out
with made-up names, amounts or dates.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List

F = lambda key, label, kind="text", required=True, help="": {  # noqa: E731
    "key": key, "label": label, "kind": kind, "required": required, "help": help}

COMMON_ADVOCATE = [F("advocate_name", "Advocate name"), F("advocate_address", "Advocate address / chamber", "textarea"),
                   F("place", "Place")]

TEMPLATES: Dict[str, Dict] = {
    "ni_138_notice": {
        "title": "Demand notice - Section 138, Negotiable Instruments Act",
        "category": "Notice",
        "description": "Statutory notice after a cheque is dishonoured. Must be sent within 30 days of receiving "
                       "the bank's return memo; the drawer then has 15 days to pay.",
        "fields": [F("client", "Client (payee)"), F("drawer", "Drawer (addressee)"),
                   F("drawer_address", "Drawer's address", "textarea"), F("cheque_no", "Cheque number"),
                   F("cheque_date", "Cheque date", "date"), F("amount", "Cheque amount (figures and words)"),
                   F("bank", "Drawee bank and branch"), F("return_memo_date", "Return memo date", "date"),
                   F("return_reason", "Reason for return (as on memo)"),
                   F("liability", "Debt / liability the cheque was given for", "textarea")] + COMMON_ADVOCATE,
        "body": """BY REGISTERED POST A.D. / SPEED POST / EMAIL

Date: {{today}}

To,
{{drawer}}
{{drawer_address}}

Subject: Legal notice under Section 138 read with Section 142 of the Negotiable Instruments Act, 1881 for dishonour of Cheque No. {{cheque_no}} dated {{cheque_date}} for {{amount}}.

Sir/Madam,

Under instructions from and on behalf of my client, {{client}} ("my client"), I serve upon you the following notice:

1. That {{liability}}

2. That in discharge of the said legally enforceable debt/liability, you issued Cheque No. {{cheque_no}} dated {{cheque_date}} for {{amount}}, drawn on {{bank}}, in favour of my client.

3. That my client presented the said cheque within its validity and the same was returned unpaid by the bank vide return memo dated {{return_memo_date}} with the remark "{{return_reason}}".

4. That I therefore call upon you to pay the said amount of {{amount}} to my client within 15 (fifteen) days of receipt of this notice, failing which my client will be constrained to initiate proceedings against you under Section 138 read with Section 142 of the Negotiable Instruments Act, 1881, besides other civil and criminal remedies, entirely at your risk as to costs and consequences.

A copy of this notice is retained in my office for record and further action.

{{advocate_name}}
Advocate for {{client}}
{{advocate_address}}
Place: {{place}}""",
    },
    "arbitration_s21_notice": {
        "title": "Notice invoking arbitration - Section 21, Arbitration and Conciliation Act",
        "category": "Notice",
        "description": "Commences arbitral proceedings (Section 21) and proposes an arbitrator under the "
                       "arbitration clause.",
        "fields": [F("client", "Claimant"), F("respondent", "Respondent"), F("respondent_address", "Respondent's address", "textarea"),
                   F("agreement", "Agreement (title and date)"), F("clause_no", "Arbitration clause number"),
                   F("clause_text", "Text of the arbitration clause", "textarea"),
                   F("dispute", "Disputes and claims", "list", help="One point per line"),
                   F("nominee", "Proposed arbitrator (name and details)")] + COMMON_ADVOCATE,
        "body": """Date: {{today}}

To,
{{respondent}}
{{respondent_address}}

Subject: Notice under Section 21 of the Arbitration and Conciliation Act, 1996 invoking arbitration under Clause {{clause_no}} of the {{agreement}}.

Sir/Madam,

On behalf of my client, {{client}}, I state as follows:

1. That my client and you are parties to the {{agreement}}. Clause {{clause_no}} provides: "{{clause_text}}"

2. That the following disputes have arisen between the parties:
{{dispute}}

3. That my client hereby invokes arbitration under Clause {{clause_no}} and this notice constitutes a request for the disputes to be referred to arbitration within the meaning of Section 21 of the Act.

4. That my client proposes {{nominee}} as arbitrator. You are called upon to concur with this appointment, or to appoint your arbitrator in terms of the arbitration agreement, within 30 days of receipt of this notice, failing which my client will approach the competent court under Section 11 of the Act.

{{advocate_name}}
Advocate for {{client}}
{{advocate_address}}
Place: {{place}}""",
    },
    "arbitration_s11_petition": {
        "title": "Petition for appointment of arbitrator - Section 11(6)",
        "category": "Petition",
        "description": "Filed before the High Court / Supreme Court when the other side fails to appoint an "
                       "arbitrator after a Section 21 notice.",
        "fields": [F("court", "Court"), F("case_no", "Arbitration petition no. (if allotted)", required=False),
                   F("petitioner", "Petitioner"), F("respondent", "Respondent"),
                   F("agreement", "Agreement (title and date)"), F("clause_no", "Arbitration clause number"),
                   F("notice_date", "Date of Section 21 notice", "date"), F("facts", "Facts", "list"),
                   F("grounds", "Grounds", "list"), F("prayers", "Prayers", "list"),
                   F("deponent", "Deponent for verification")] + COMMON_ADVOCATE,
        "body": """IN THE {{court}}
(ORDINARY ORIGINAL CIVIL JURISDICTION)

ARBITRATION PETITION NO. {{case_no}}

IN THE MATTER OF:
{{petitioner}} ... PETITIONER
VERSUS
{{respondent}} ... RESPONDENT

PETITION UNDER SECTION 11(6) OF THE ARBITRATION AND CONCILIATION ACT, 1996

MOST RESPECTFULLY SHOWETH:

1. That the parties entered into the {{agreement}}, Clause {{clause_no}} of which contains an arbitration agreement.

FACTS
{{facts}}

2. That the Petitioner invoked arbitration by notice under Section 21 dated {{notice_date}}. The Respondent has failed to concur in or make the appointment within 30 days of receipt.

GROUNDS
{{grounds}}

PRAYER
In the facts and circumstances stated above, it is most respectfully prayed that this Hon'ble Court may be pleased to:
{{prayers}}

and pass such other order(s) as this Hon'ble Court may deem fit.

PETITIONER
THROUGH
{{advocate_name}}, Advocate
{{advocate_address}}
Place: {{place}}
Date: {{today}}

VERIFICATION
I, {{deponent}}, the deponent above-named, do hereby verify that the contents of the above petition are true and correct to my knowledge and belief, no part of it is false and nothing material has been concealed therefrom. Verified at {{place}} on {{today}}.

DEPONENT""",
    },
    "arbitration_s34_petition": {
        "title": "Petition to set aside arbitral award - Section 34",
        "category": "Petition",
        "description": "Must be filed within 3 months of receiving the award (extendable by up to 30 days on "
                       "sufficient cause, not beyond).",
        "fields": [F("court", "Court"), F("case_no", "O.M.P. / petition no. (if allotted)", required=False),
                   F("petitioner", "Petitioner"), F("respondent", "Respondent"),
                   F("tribunal", "Arbitral tribunal"), F("award_date", "Date of award", "date"),
                   F("receipt_date", "Date award was received", "date"), F("facts", "Facts", "list"),
                   F("grounds", "Grounds under Section 34(2) / 34(2A)", "list"), F("prayers", "Prayers", "list"),
                   F("deponent", "Deponent for verification")] + COMMON_ADVOCATE,
        "body": """IN THE {{court}}

O.M.P. (COMM.) NO. {{case_no}}

IN THE MATTER OF:
{{petitioner}} ... PETITIONER
VERSUS
{{respondent}} ... RESPONDENT

PETITION UNDER SECTION 34 OF THE ARBITRATION AND CONCILIATION ACT, 1996 SEEKING TO SET ASIDE THE ARBITRAL AWARD DATED {{award_date}} PASSED BY {{tribunal}}

MOST RESPECTFULLY SHOWETH:

1. That the impugned award dated {{award_date}} was received by the Petitioner on {{receipt_date}}. This petition is filed within the period prescribed by Section 34(3) of the Act.

FACTS
{{facts}}

GROUNDS
{{grounds}}

PRAYER
It is therefore most respectfully prayed that this Hon'ble Court may be pleased to:
{{prayers}}

PETITIONER
THROUGH
{{advocate_name}}, Advocate
{{advocate_address}}
Place: {{place}}
Date: {{today}}

VERIFICATION
I, {{deponent}}, verify that the contents of the above petition are true and correct to my knowledge and belief and nothing material has been concealed. Verified at {{place}} on {{today}}.

DEPONENT""",
    },
    "writ_226": {
        "title": "Writ petition - Article 226",
        "category": "Petition",
        "description": "Writ petition before a High Court against State action.",
        "fields": [F("court", "High Court"), F("case_no", "W.P. no. (if allotted)", required=False),
                   F("petitioner", "Petitioner(s)", "textarea"), F("respondents", "Respondent(s)", "textarea"),
                   F("impugned", "Impugned order / action (with date)"), F("facts", "Facts", "list"),
                   F("grounds", "Grounds", "list"),
                   F("alt_remedy", "Why no alternative efficacious remedy is available", "textarea"),
                   F("prayers", "Prayers", "list"), F("deponent", "Deponent for affidavit")] + COMMON_ADVOCATE,
        "body": """IN THE {{court}}
(EXTRAORDINARY CIVIL WRIT JURISDICTION)

WRIT PETITION (CIVIL) NO. {{case_no}}

IN THE MATTER OF:
{{petitioner}} ... PETITIONER(S)
VERSUS
{{respondents}} ... RESPONDENT(S)

WRIT PETITION UNDER ARTICLE 226 OF THE CONSTITUTION OF INDIA CHALLENGING {{impugned}}

MOST RESPECTFULLY SHOWETH:

FACTS
{{facts}}

GROUNDS
{{grounds}}

ALTERNATIVE REMEDY
{{alt_remedy}}

The Petitioner has not filed any other petition seeking the same relief before this Hon'ble Court or any other court.

PRAYER
It is most respectfully prayed that this Hon'ble Court may be pleased to issue an appropriate writ, order or direction:
{{prayers}}

PETITIONER
THROUGH
{{advocate_name}}, Advocate
{{advocate_address}}
Place: {{place}}
Date: {{today}}

(To be supported by an affidavit of {{deponent}}.)""",
    },
    "commercial_plaint": {
        "title": "Plaint - commercial suit (Order VII CPC, Commercial Courts Act)",
        "category": "Pleading",
        "description": "Commercial suit with the Section 12A pre-institution mediation statement and the "
                       "statement of truth required for commercial disputes.",
        "fields": [F("court", "Commercial Court"), F("suit_no", "Suit no. (if allotted)", required=False),
                   F("plaintiff", "Plaintiff"), F("defendant", "Defendant"),
                   F("specified_value", "Specified value of the suit"), F("cause_date", "Date cause of action arose", "date"),
                   F("facts", "Facts", "list"),
                   F("mediation", "Section 12A mediation (date of report / why urgent interim relief is sought)", "textarea"),
                   F("jurisdiction", "Territorial and pecuniary jurisdiction", "textarea"),
                   F("valuation", "Valuation and court fee", "textarea"), F("prayers", "Reliefs claimed", "list"),
                   F("deponent", "Deponent for statement of truth")] + COMMON_ADVOCATE,
        "body": """IN THE COURT OF {{court}}

COMMERCIAL SUIT NO. {{suit_no}}

{{plaintiff}} ... PLAINTIFF
VERSUS
{{defendant}} ... DEFENDANT

SUIT FOR RECOVERY / DAMAGES (SPECIFIED VALUE: {{specified_value}})

THE PLAINTIFF ABOVE-NAMED MOST RESPECTFULLY SUBMITS:

1. That this is a commercial dispute within the meaning of Section 2(1)(c) of the Commercial Courts Act, 2015.

FACTS
{{facts}}

CAUSE OF ACTION
The cause of action arose on {{cause_date}} and continues.

PRE-INSTITUTION MEDIATION (SECTION 12A)
{{mediation}}

JURISDICTION
{{jurisdiction}}

VALUATION AND COURT FEE
{{valuation}}

PRAYER
The Plaintiff therefore prays that this Hon'ble Court may be pleased to pass a decree:
{{prayers}}

PLAINTIFF
THROUGH
{{advocate_name}}, Advocate
{{advocate_address}}
Place: {{place}}
Date: {{today}}

STATEMENT OF TRUTH
(Under Order VI Rule 15A and Order XI of the CPC as applicable to commercial suits)
I, {{deponent}}, state that I am competent to make this statement; that the facts stated in paragraphs above are true to my knowledge and belief; and that all documents in the Plaintiff's power, possession, control or custody pertaining to the facts of this suit have been disclosed.

Verified at {{place}} on {{today}}.

DEPONENT""",
    },
    "bail_application": {
        "title": "Regular bail application (Section 483 BNSS / Section 439 CrPC)",
        "category": "Application",
        "description": "Bail application before the Sessions Court or High Court. Use Section 439 CrPC only "
                       "where the old Code still applies to the proceedings.",
        "fields": [F("court", "Court"), F("applicant", "Applicant / accused"), F("fir", "FIR number, police station and date"),
                   F("offences", "Offences alleged (sections)"), F("custody_since", "In custody since", "date"),
                   F("provision", "Provision invoked", help="e.g. Section 483 BNSS"),
                   F("grounds", "Grounds for bail", "list"),
                   F("antecedents", "Criminal antecedents (or 'none')"), F("prayers", "Prayers", "list")] + COMMON_ADVOCATE,
        "body": """IN THE COURT OF {{court}}

BAIL APPLICATION NO. ______ OF {{year}}

IN THE MATTER OF:
{{applicant}} ... APPLICANT
VERSUS
STATE ... RESPONDENT

FIR: {{fir}}
OFFENCES: {{offences}}

APPLICATION FOR REGULAR BAIL UNDER {{provision}}

MOST RESPECTFULLY SHOWETH:

1. That the Applicant has been in custody since {{custody_since}} in connection with the above FIR.

2. That the Applicant's criminal antecedents: {{antecedents}}.

GROUNDS
{{grounds}}

3. That the Applicant undertakes to abide by all conditions that this Hon'ble Court may impose, to join investigation as and when required, and not to tamper with evidence or influence witnesses.

PRAYER
{{prayers}}

APPLICANT
THROUGH
{{advocate_name}}, Advocate
{{advocate_address}}
Place: {{place}}
Date: {{today}}""",
    },
    "legal_opinion": {
        "title": "Legal opinion / advice memo",
        "category": "Opinion",
        "description": "Structured opinion for a client. Pair it with a saved research session to cite authorities.",
        "fields": [F("client", "Client"), F("subject", "Subject"), F("questions", "Questions presented", "list"),
                   F("facts", "Facts relied on", "list"), F("analysis", "Analysis", "textarea"),
                   F("conclusion", "Conclusions and advice", "list"),
                   F("assumptions", "Assumptions and qualifications", "list")] + COMMON_ADVOCATE,
        "body": """PRIVILEGED AND CONFIDENTIAL - LEGAL ADVICE

To: {{client}}
From: {{advocate_name}}
Date: {{today}}
Subject: {{subject}}

1. QUESTIONS PRESENTED
{{questions}}

2. FACTS
This opinion is based on the following facts as provided to us:
{{facts}}

3. ANALYSIS
{{analysis}}

4. CONCLUSIONS AND ADVICE
{{conclusion}}

5. ASSUMPTIONS AND QUALIFICATIONS
{{assumptions}}

This opinion is limited to the laws of India in force on the date above and to the facts stated.

{{advocate_name}}
{{advocate_address}}
{{place}}""",
    },
    "written_submissions": {
        "title": "Written submissions / note of arguments",
        "category": "Submissions",
        "description": "Propositions of law with supporting authorities, for filing after or before oral hearing.",
        "fields": [F("court", "Court"), F("case_no", "Case number"), F("party_for", "On behalf of"),
                   F("parties", "Cause title (X v. Y)"), F("propositions", "Propositions", "list"),
                   F("authorities", "Authorities relied on (with citations)", "list"),
                   F("prayers", "Relief sought", "list")] + COMMON_ADVOCATE,
        "body": """IN THE {{court}}

{{case_no}}

{{parties}}

WRITTEN SUBMISSIONS ON BEHALF OF {{party_for}}

A. PROPOSITIONS
{{propositions}}

B. AUTHORITIES RELIED UPON
{{authorities}}

C. RELIEF
{{prayers}}

{{advocate_name}}
Counsel for {{party_for}}
{{advocate_address}}
Place: {{place}}
Date: {{today}}""",
    },
}


def catalog() -> List[Dict]:
    return [{"key": k, "title": t["title"], "category": t["category"], "description": t["description"],
             "fields": t["fields"]} for k, t in TEMPLATES.items()]


def _fmt_list(value) -> str:
    items = value if isinstance(value, list) else [x for x in str(value or "").splitlines()]
    items = [re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", i).strip() for i in items]
    items = [i for i in items if i]
    return "\n".join(f"{n}. {i}" for n, i in enumerate(items, 1))


def render(template_key: str, values: Dict) -> str:
    tpl = TEMPLATES.get(template_key)
    if not tpl:
        raise KeyError(template_key)
    today = date.today()
    fields = {f["key"]: f for f in tpl["fields"]}
    resolved = {"today": today.strftime("%d.%m.%Y"), "year": str(today.year)}
    for key, f in fields.items():
        v = values.get(key)
        text = _fmt_list(v) if f["kind"] == "list" else str(v or "").strip()
        resolved[key] = text or f"[[{f['label']}]]"

    def sub(m):
        return resolved.get(m.group(1), f"[[{m.group(1)}]]")

    return re.sub(r"\{\{(\w+)\}\}", sub, tpl["body"])


# ── Pre-filing checks ──────────────────────────────────────────────────────

OLD_CODES = [
    (r"\b(?:indian penal code|i\.?p\.?c\.?)\b", "Indian Penal Code", "Bharatiya Nyaya Sanhita, 2023"),
    (r"\b(?:code of criminal procedure|cr\.?\s?p\.?c\.?)\b", "Code of Criminal Procedure", "Bharatiya Nagarik Suraksha Sanhita, 2023"),
    (r"\b(?:indian evidence act|evidence act,? 1872)\b", "Indian Evidence Act", "Bharatiya Sakshya Adhiniyam, 2023"),
]
CITATION_RE = re.compile(r"\(\d{4}\)\s*\d{1,3}\s*SCC\s*\d{1,4}|AIR\s*\d{4}\s*SC\s*\d{1,5}|\d{4}\s*INSC\s*\d{1,5}"
                         r"|\d{4}\s*SCC\s*OnLine\s*\w+\s*\d{1,6}", re.I)


def check_draft(db, content: str, template_key: str = None) -> Dict:
    issues: List[Dict] = []

    def add(severity, code, message, fix=""):
        issues.append({"severity": severity, "code": code, "message": message, "fix": fix})

    placeholders = sorted(set(re.findall(r"\[\[([^\]]+)\]\]", content)))
    if placeholders:
        add("blocker", "UNFILLED_FIELDS", f"{len(placeholders)} field(s) still empty: {', '.join(placeholders[:8])}"
            + ("..." if len(placeholders) > 8 else ""), "Fill in every [[placeholder]] before filing or sending.")

    citations = sorted(set(m.group(0).strip() for m in CITATION_RE.finditer(content)))
    checked = []
    for cit in citations:
        toks = re.findall(r"[a-z0-9]+", cit.lower())
        rows = db.execute("SELECT id, title, citation, alt_citations, legal_status, status_note, verification "
                          "FROM sources WHERE matter_id IS NULL AND chat_id IS NULL AND (citation LIKE ? OR alt_citations LIKE ?)",
                          (f"%{toks[0]}%", f"%{toks[0]}%")).fetchall()
        match = None
        for r in rows:
            hay = re.findall(r"[a-z0-9]+", f"{r['citation'] or ''} {r['alt_citations'] or ''}".lower())
            if any(hay[i:i + len(toks)] == toks for i in range(len(hay) - len(toks) + 1)):
                match = r
                break
        if not match:
            checked.append({"citation": cit, "found": False})
            add("warning", "CITATION_NOT_IN_CORPUS", f"{cit} is not in the LawPilot corpus, so it could not be checked.",
                "Verify the citation and its current status from an official or reported source.")
            continue
        checked.append({"citation": cit, "found": True, "title": match["title"], "legal_status": match["legal_status"]})
        if match["legal_status"] in ("overruled", "repealed"):
            add("blocker", "BAD_LAW_CITED", f"{cit} ({match['title']}) is marked {match['legal_status']}"
                + (f": {match['status_note']}" if match["status_note"] else "."),
                "Remove it or cite it only to distinguish it, and cite the authority that overruled it.")
        elif match["legal_status"] == "partly_overruled":
            add("warning", "PARTLY_OVERRULED", f"{cit} ({match['title']}) is partly overruled.",
                "Confirm the proposition you rely on survives.")

    lower = content.lower()
    for pattern, old, new in OLD_CODES:
        if re.search(pattern, lower):
            add("info", "OLD_CRIMINAL_CODE", f"Refers to the {old}. From 1 July 2024 the {new} applies to new "
                "proceedings; the old code may still govern earlier offences and pending matters.",
                "Confirm which code applies and add a note if both are cited.")

    tpl = template_key or ""
    if tpl in ("arbitration_s11_petition", "arbitration_s34_petition", "writ_226", "commercial_plaint") \
            and not re.search(r"verif|statement of truth|affidavit", lower):
        add("blocker", "NO_VERIFICATION", "No verification / statement of truth found.",
            "Add the verification or affidavit required for this pleading.")
    if tpl == "commercial_plaint" and "12a" not in lower:
        add("blocker", "NO_12A_STATEMENT", "No Section 12A pre-institution mediation statement.",
            "State the mediation outcome or why urgent interim relief is sought.")
    if re.search(r"electronic record|whatsapp|e-?mails? (?:exchanged|correspondence|dated)|cctv|screenshot"
                 r"|call detail|\bcdr\b", lower) \
            and not re.search(r"65\s?b|section 63", lower):
        add("warning", "ELECTRONIC_EVIDENCE_CERTIFICATE", "Electronic evidence is mentioned without a certificate "
            "under Section 63 BSA (Section 65B of the Evidence Act for older matters).",
            "Annex or reserve the required certificate.")

    blockers = sum(1 for i in issues if i["severity"] == "blocker")
    return {"ready": blockers == 0, "blockers": blockers,
            "warnings": sum(1 for i in issues if i["severity"] == "warning"),
            "issues": issues, "citations": checked}
