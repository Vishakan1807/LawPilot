from lawpilot import draft_templates as drafting
from lawpilot.query_analysis import analyze


def test_templates_never_invent_facts():
    for key in drafting.TEMPLATES:
        text = drafting.render(key, {})
        assert "[[" in text
        for fake in ("Alpha Corp", "Beta Enterprises", "CHQ-", "45,00,000"):
            assert fake not in text


def test_render_fills_fields_and_lists():
    text = drafting.render("writ_226", {"court": "HIGH COURT OF KERALA AT ERNAKULAM", "facts": "First fact\nSecond fact"})
    assert "HIGH COURT OF KERALA" in text and "1. First fact\n2. Second fact" in text


def test_draft_lifecycle_and_checks(make_client):
    c = make_client("drafter@x.test")
    r = c.post("/api/drafts", {"template_key": "ni_138_notice", "fields": {"client": "A", "drawer": "B"}})
    assert r.status_code == 201
    did = r.json["id"]
    d = c.get(f"/api/drafts/{did}").json["draft"]
    assert d["check_report"]["ready"] is False
    assert d["check_report"]["issues"][0]["code"] == "UNFILLED_FIELDS"

    bad = d["content"] + "\nReliance is placed on A.K. Gopalan v. State of Madras, AIR 1950 SC 27, and Section 420 IPC."
    assert c.patch(f"/api/drafts/{did}", {"content": bad}).json["version"] == 2
    codes = {i["code"] for i in c.get(f"/api/drafts/{did}").json["draft"]["check_report"]["issues"]}
    assert {"BAD_LAW_CITED", "OLD_CRIMINAL_CODE"} <= codes
    assert c.get(f"/api/drafts/{did}/versions/1").json["content"] == d["content"]

    docx_resp = c.get(f"/api/drafts/{did}/download?format=docx")
    assert docx_resp.status_code == 200 and docx_resp.data[:2] == b"PK"
    assert make_client("other@x.test").get(f"/api/drafts/{did}").status_code == 404


def test_commercial_plaint_formalities(make_client):
    c = make_client("p@x.test")
    r = c.post("/api/drafts/check", {"content": "PLAINT\nFacts...", "template_key": "commercial_plaint"})
    codes = {i["code"] for i in r.json["check"]["issues"]}
    assert {"NO_VERIFICATION", "NO_12A_STATEMENT"} <= codes


def test_ai_assist_requires_llm(make_client):
    c = make_client("ai@x.test")
    assert c.post("/api/drafts/assist", {"instruction": "write grounds"}).status_code == 503


def test_query_analysis():
    a = analyze("Can bail be granted u/s 438 CrPC for offence under Section 498A IPC in Delhi High Court after 2019? "
                "See Arnesh Kumar v. State of Bihar, (2014) 8 SCC 273")
    assert "438" in a.sections and "498A" in a.sections
    assert "Code of Criminal Procedure, 1973" in a.statutes and "Indian Penal Code, 1860" in a.statutes
    assert a.citations == ["(2014) 8 SCC 273"]
    assert "Delhi High Court" in a.courts and a.preferred_state == "Delhi"
    assert a.year_from == 2019
    assert any("Bharatiya Nagarik Suraksha Sanhita" in c["to"] for c in a.code_correspondence)
    assert a.search_type == "citation"
    assert "criminal" in a.domains
