import json

import pytest

from conftest import BACKEND, FakeLLM
from lawpilot import db as dbmod
from lawpilot import importers, llm as llm_mod

SCI_RECORD = {
    "path": "2023_10_1001_1009",
    "nc_display": "2023INSC600",
    "raw_html": (
        "<button type='button' role='link' class='btn btn-link p-0 text-start' id='link_99' "
        "aria-label=\" HARI PRAKASH SHUKLA & ORS. versus THE STATE OF UTTAR PRADESH & ANR. pdf\" >"
        "<font size='4'> <strong>HARI PRAKASH SHUKLA & ORS.<span class='fst-italic'> versus </span>THE STATE OF "
        "UTTAR PRADESH & ANR. </strong>- <span class='escrText'>[2023] 10 S.C.R. 1001</span>"
        "<span class='ncDisplay'>2023 INSC 600</span></button></font><br><strong>Coram : KRISHNA MURARI"
        "<sup style=\"color: #268e97;\" class=\"tooltip-sup\" data-tooltip=\"Author\">*</sup>, AHSANUDDIN AMANULLAH"
        "</strong><br> Forest Act, 1927: s. 4 &ndash; Declaration of reserved forest &ndash; Right to be heard by "
        "Forest Officer<br><strong class='caseDetailsTD' ><span style='color:#212F3D' > Decision Date :</span>"
        "<font color='green'> 05-07-2023</font><span style='color:#212F3D' > | Case No :</span><font color='green'>"
        " CIVIL APPEAL No. 9697/2014</font><span style='color:#212F3D' > | Disposal Nature :</span>"
        "<font color='green'> Appeal(s) allowed</font><span style='color:#212F3D' > |  Bench :</span>"
        "<font color='green'> 2 Judges</font></strong>"),
}
JUDGMENT_TEXT = (
    "1. These appeals concern land declared as reserved forest under Section 4 of the Forest Act, 1927.\n\n"
    "2. The appellants claim they were never heard by the Forest Settlement Officer before the declaration.\n\n"
    "3. We hold that forest inhabitants have a right to be heard before their rights are extinguished.")


def test_translate_sql():
    t = dbmod.translate_sql
    assert t("SELECT * FROM x WHERE a = ? AND b LIKE ?") == "SELECT * FROM x WHERE a = %s AND b ILIKE %s"
    assert t("INSERT OR IGNORE INTO r (a) VALUES (?)") == "INSERT INTO r (a) VALUES (%s) ON CONFLICT DO NOTHING"
    assert t("SELECT '?' AS q, x FROM t WHERE m IS NOT ?") == "SELECT '?' AS q, x FROM t WHERE m IS DISTINCT FROM %s"
    assert t("SELECT 100 % 7") == "SELECT 100 %% 7"


def test_search_backend_is_used(make_client, app):
    c = make_client("v@x.test")
    s = c.post("/api/research", {"query": "Is privacy a fundamental right under Article 21?"}).json["session"]
    assert s["analysis"]["retrieval"]["keyword_hits"] > 0
    assert s["analysis"]["retrieval"]["vector_hits"] > 0
    with app.app_context():
        conn = dbmod.get_db()
        if BACKEND == "postgres":
            assert conn.has_pgvector
            assert conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding_vec IS NOT NULL").fetchone()[0] > 0


# ── AI settings ────────────────────────────────────────────────────────────

def test_ai_settings_are_admin_only_and_never_leak_keys(admin, make_client):
    user = make_client("user@x.test")
    assert user.get("/api/admin/ai").status_code == 403
    r = admin.post("/api/admin/ai", {"llm_provider": "gemini", "llm_model": "gemini-test",
                                     "keys": {"gemini_api_key": "AIza-secret-value-1234"}})
    assert r.status_code == 200
    body = json.dumps(r.json)
    assert "secret-value" not in body
    assert r.json["keys"]["gemini_api_key"] == {"set": True, "source": "settings", "hint": "…1234"}
    assert r.json["active"] == {"provider": "gemini", "model": "gemini-test"}
    status = admin.get("/api/system/status").json
    assert status["llm"]["provider"] == "gemini"
    # Clearing the key switches AI answers off again.
    r = admin.post("/api/admin/ai", {"keys": {"gemini_api_key": ""}})
    assert r.json["active"] is None and r.json["keys"]["gemini_api_key"]["set"] is False


def test_keys_are_encrypted_at_rest(admin, app):
    admin.post("/api/admin/ai", {"keys": {"anthropic_api_key": "sk-ant-plaintext-check"}})
    with app.app_context():
        stored = dbmod.get_db().execute("SELECT value FROM app_settings WHERE key = 'anthropic_api_key'").fetchone()[0]
    assert "plaintext-check" not in stored


def test_ai_connection_test_reports_errors(admin, monkeypatch):

    class Broken(FakeLLM):
        def complete(self, *a, **k):
            raise llm_mod.LLMError("Anthropic rejected the API key.")
    monkeypatch.setattr(llm_mod, "make_client", lambda *a, **k: FakeLLM({}))
    ok = admin.post("/api/admin/ai/test", {"provider": "anthropic", "api_key": "x"}).json
    assert ok["ok"] is True
    monkeypatch.setattr(llm_mod, "make_client", lambda *a, **k: Broken())
    bad = admin.post("/api/admin/ai/test", {"provider": "anthropic", "api_key": "x"}).json
    assert bad == {"ok": False, "error": "Anthropic rejected the API key."}
    assert admin.post("/api/admin/ai/test", {"provider": "gemini"}).json["ok"] is False   # no key stored


# ── Starter content & real-data importer ───────────────────────────────────

def test_starter_summaries_can_be_removed_and_reloaded(admin):
    assert admin.post("/api/admin/corpus/starter", {"action": "remove"}).json["count"] == 66
    assert admin.get("/api/system/status").json["corpus"]["total"] == 0
    assert admin.post("/api/admin/corpus/starter", {"action": "load"}).json["count"] == 66


def test_parse_sci_metadata():
    m = importers.parse_sci_metadata(SCI_RECORD)
    assert m["title"] == "Hari Prakash Shukla & Ors. v. The State of Uttar Pradesh & Anr."
    assert m["citation"] == "[2023] 10 S.C.R. 1001"
    assert m["neutral_citation"] == "2023 INSC 600"
    assert m["judges"] == "KRISHNA MURARI, AHSANUDDIN AMANULLAH" and m["author_judge"] == "KRISHNA MURARI"
    assert m["decision_date"] == "2023-07-05"
    assert m["case_number"] == "CIVIL APPEAL No. 9697/2014"
    assert m["bench"] == "2 Judges"
    assert m["headnote"].startswith("Forest Act, 1927")


def test_sci_import_is_incremental_and_searchable(make_client, admin, app, monkeypatch):
    monkeypatch.setattr(importers, "list_sci_year", lambda year, limit=None: ["2023_10_1001_1009", "2023_bad"])

    def fake_fetch(year, name):
        if name == "2023_bad":
            return {"meta": {}, "error": "No text could be extracted (scanned PDF)"}
        return {"meta": importers.parse_sci_metadata(SCI_RECORD), "pages": [(1, JUDGMENT_TEXT)]}
    monkeypatch.setattr(importers, "_fetch_one", fake_fetch)

    cfg = app.config["LP"]
    first = importers.run_sci_import(cfg, [2023], workers=1)
    assert (first["imported"], first["failed"], first["skipped"]) == (1, 1, 0)
    second = importers.run_sci_import(cfg, [2023], workers=1)
    assert second["imported"] == 0 and second["skipped"] == 1          # already present: not downloaded again

    c = make_client("lawyer@x.test")
    s = c.post("/api/research", {"query": "right of forest inhabitants to be heard before reserved forest declaration"}).json["session"]
    top = s["evidence"][0]
    assert top["title"].startswith("Hari Prakash Shukla") and top["court_level"] == "supreme"
    assert top["citation"] == "[2023] 10 S.C.R. 1001" and top["kind"] == "judgment"
    hit = c.get(f"/api/corpus/search?citation=2023 INSC 600").json
    assert hit["total"] == 1

    runs = admin.get("/api/admin/imports").json["items"]
    assert [r["status"] for r in runs] == ["completed", "completed"]


# ── India Code links & embedding outages ───────────────────────────────────

ITEM = {"uuid": "154f98bf-a132-43e9-988a-83d5046c20be", "name": "X", "metadata": {
    "dc.title": [{"value": "The Test Act, 2026"}], "dc.identifier.act_number": [{"value": "09"}],
    "dc.date.act_year": [{"value": "2026"}], "dc.date.enact_date": [{"value": "2026-04-09"}],
    "dc.identifier.state_name": [{"value": "CENTRAL"}], "dc.identifier.collection": [{"value": "ACT"}],
    "dc.identifier.ministry_name": [{"value": "Ministry of Home Affairs"}],
    "dc.identifier.uri": [{"value": "https://indiacode.gov.in/handle/123456789/620453"}]}}


def test_indiacode_links_resolve_to_pdf_and_metadata(monkeypatch):
    from lawpilot import indiacode
    calls = []

    def fake_get(url):
        calls.append(url)
        if "/bundles" in url and "/items/" in url:
            return {"_embedded": {"bundles": [{"name": "ORIGINAL", "uuid": "b1"}]}}
        if "/bundles/b1/bitstreams" in url:
            return {"_embedded": {"bitstreams": [{"uuid": "d724393c-3251-45f7-91b9-0ca4be6311f0", "name": "A2026-9.pdf"}]}}
        return ITEM
    monkeypatch.setattr(indiacode, "_get_json", fake_get)
    viewer = "https://indiacode.gov.in/pdf-viewer/154f98bf-a132-43e9-988a-83d5046c20be/d724393c-3251-45f7-91b9-0ca4be6311f0"
    for url in (viewer, "https://www.indiacode.nic.in/handle/123456789/620453"):
        r = indiacode.resolve(url)
        assert r["pdf_url"].endswith("/core/bitstreams/d724393c-3251-45f7-91b9-0ca4be6311f0/content")
        m = r["meta"]
        assert (m["title"], m["kind"], m["citation"], m["jurisdiction"], m["decision_date"]) == \
            ("The Test Act, 2026", "statute", "Act No. 9 of 2026", "India", "2026-04-09")
    assert not indiacode.is_indiacode("https://example.com/doc.pdf")


def test_import_survives_embedding_outage_and_reembed_reports_it(admin, monkeypatch):
    from lawpilot import embeddings

    class Down(embeddings.LocalHashEmbedder):
        name = "voyage-law-2"

        def embed_documents(self, texts):
            raise embeddings.EmbeddingError("Voyage is limiting this account to 3 requests per minute.")
        embed_query = embed_documents
    monkeypatch.setattr("lawpilot.ingestion.get_embedder", lambda cfg: Down())
    r = admin.post("/api/admin/corpus/text", {"title": "Test ruling on arrest", "kind": "judgment",
                                              "text": "1. The petitioner was arrested without recording reasons under Section 41A.\n\n"
                                                      "2. Arrest without reasons violates the guidelines and the petitioner is released."})
    assert r.status_code == 201 and "built-in model for now" in r.json["message"]
    with admin.c.application.app_context():
        from lawpilot.db import get_db
        models = {row[0] for row in get_db().execute(
            "SELECT DISTINCT c.embedding_model FROM chunks c JOIN sources s ON s.id = c.source_id "
            "WHERE s.title = 'Test ruling on arrest'").fetchall()}
    assert models == {"local-hash-v1"}                                    # still searchable by meaning
    hits = admin.get("/api/corpus/search?q=arrested without recording reasons").json["items"]
    assert hits and hits[0]["title"] == "Test ruling on arrest"           # keyword search works meanwhile

    monkeypatch.setattr("lawpilot.ingestion.get_embedder", lambda cfg: Down())
    monkeypatch.setattr("lawpilot.routes.admin.get_embedder", lambda cfg: Down())
    bad = admin.post("/api/admin/corpus/reembed")
    assert bad.status_code == 502 and "3 requests per minute" in bad.json["error"]


def test_offence_names_map_to_both_codes():
    from lawpilot.query_analysis import analyze
    a = analyze("can anticipatory bail be granted for attempt to murder case?")
    corr = {(c["from"], c["to"]) for c in a.code_correspondence}
    assert ("Section 307 Indian Penal Code, 1860", "Section 109 Bharatiya Nyaya Sanhita, 2023") in corr
    assert ("Section 438 Code of Criminal Procedure, 1973", "Section 482 Bharatiya Nagarik Suraksha Sanhita, 2023") in corr
    assert not any("Section 302" in c["from"] for c in a.code_correspondence)   # "murder" inside "attempt to murder"


def test_repealed_india_code_titles_are_cleaned():
    from lawpilot.indiacode import _clean_title
    t, num, yr, status, note = _clean_title("The Indian Penal Code, 45 of 1860 (Rep., Act 45 of 2023)", None, None)
    assert (t, num, yr, status) == ("The Indian Penal Code, 1860", "45", "1860", "repealed")
    assert "Bharatiya Nyaya Sanhita" in note


def test_evidence_meta_sentences_are_not_claims():
    from lawpilot.research import verify_answer
    ev = [{"id": "E1", "text": "Bail is the rule and jail is the exception.", "title": "X", "citation": None,
           "pinpoint": "", "act_name": None}]
    v = verify_answer({"short_answer": "The provided evidence does not explicitly address attempt to murder. "
                                       "Bail is the rule and jail is the exception [E1]."}, ev)
    assert [c["status"] for c in v["claims"]] == ["supported"]


def test_unreadable_pdf_text_is_rejected():
    from lawpilot.extract import readable
    assert readable([(1, "Section 482. Direction for grant of bail to person apprehending arrest. " * 60)])
    assert not readable([(1, " , ( ) ; [ ] 1. (1) , \n" * 400)])          # Hindi font with no Unicode mapping


def test_indiacode_prefers_english_text_pdf(monkeypatch):
    from lawpilot import indiacode

    def fake_get(url):
        if "/bundles" in url and "/items/" in url:
            return {"_embedded": {"bundles": [{"name": "ORIGINAL", "uuid": "b1"}]}}
        if "/bundles/b1/bitstreams" in url:
            return {"_embedded": {"bitstreams": [
                {"uuid": "hindi-gazette", "name": "202346.pdf", "sizeBytes": 76345954},
                {"uuid": "english-text", "name": "A2023-46.pdf", "sizeBytes": 1948141}]}}
        return ITEM
    monkeypatch.setattr(indiacode, "_get_json", fake_get)
    assert indiacode.resolve("https://indiacode.gov.in/handle/123456789/496550")["pdf_url"].endswith("/english-text/content")


def test_background_connection_reconnects_after_drop(app):
    if BACKEND != "postgres":
        pytest.skip("connection drops only happen with a server database")
    conn = dbmod.ResilientConn(app.config["LP"])
    conn.conn.raw.close()                                   # simulate Neon closing an idle connection
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] >= 0
    conn.close()
