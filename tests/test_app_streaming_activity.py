"""Streaming answers, activity logs (own / firm / platform, with privacy), and monitoring."""
import io
import json

from lawpilot import activity, monitoring
from lawpilot.llm import LLMClient, LLMError, LLMResult

H = {"X-Requested-With": "LawPilot"}
LEASE = ("LEASE AGREEMENT\n\n1. The monthly rent payable by the Lessee Sunita Rao is Rs. 45,000, due on the 5th.\n\n"
         "2. A refundable security deposit of Rs. 2,70,000 is payable at signing.\n\n3. Disputes go to arbitration.")


class StreamingLLM(LLMClient):
    provider, model = "fake", "stream-1"

    def __init__(self, fail_after=None):
        self.fail_after = fail_after

    def complete(self, system, user, max_tokens=4000, effort="medium"):
        return LLMResult("The monthly rent is Rs. 45,000 [S1].", self.provider, self.model)

    def stream(self, system, user, max_tokens=4000, effort="medium"):
        for i, piece in enumerate(["The monthly ", "rent is ", "Rs. 45,000 ", "[S1]."]):
            if self.fail_after is not None and i >= self.fail_after:
                raise LLMError("fake quota exceeded")
            yield piece


def _events(resp):
    assert resp.mimetype == "application/x-ndjson"
    return [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]


def _chat_with_file(c):
    chat = c.post("/api/ai/chats", {}).json["chat"]
    c.post(f"/api/ai/chats/{chat['id']}/files", {"file": (io.BytesIO(LEASE.encode()), "lease.txt")},
           content_type="multipart/form-data")
    return chat


# ── Streaming ──────────────────────────────────────────────────────────────

def test_streamed_answer_arrives_in_pieces_and_is_saved(app, make_client):
    app.extensions["lawpilot_llm"] = StreamingLLM()
    c = make_client("stream@x.test")
    chat = _chat_with_file(c)
    r = c.c.post(f"/api/ai/chats/{chat['id']}/messages/stream", json={"content": "What rent is payable?"}, headers=H)
    assert r.status_code == 200
    ev = _events(r)
    kinds = [e["type"] for e in ev]
    assert kinds[0] == "user_message" and kinds[1] == "sources" and kinds[-1] == "done"
    deltas = [e["text"] for e in ev if e["type"] == "delta"]
    assert len(deltas) == 4 and "".join(deltas) == "The monthly rent is Rs. 45,000 [S1]."
    done = ev[-1]["message"]
    assert done["content"] == "The monthly rent is Rs. 45,000 [S1]." and done["model"] == "fake:stream-1"
    assert done["extra"]["verification"]["checked"] == 1
    assert any(s["origin"] == "file" for s in ev[1]["citations"])
    msgs = c.get(f"/api/ai/chats/{chat['id']}").json["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["content"] == done["content"]

    # Regenerating streams too and replaces the answer.
    r = c.c.post(f"/api/ai/chats/{chat['id']}/regenerate/stream", json={}, headers=H)
    assert _events(r)[-1]["type"] == "done"
    assert len(c.get(f"/api/ai/chats/{chat['id']}").json["messages"]) == 2


def test_stream_keeps_partial_text_when_the_provider_fails(app, make_client):
    app.extensions["lawpilot_llm"] = StreamingLLM(fail_after=2)
    c = make_client("streamfail@x.test")
    chat = _chat_with_file(c)
    ev = _events(c.c.post(f"/api/ai/chats/{chat['id']}/messages/stream", json={"content": "What rent?"}, headers=H))
    done = ev[-1]
    assert done["type"] == "done" and done["message"]["extra"]["error"] is True
    assert done["message"]["content"].startswith("The monthly rent is")
    assert "fake quota exceeded" in done["message"]["content"]


def test_stream_without_a_model_and_privacy(app, make_client):
    app.extensions["lawpilot_llm"] = None
    a = make_client("nomodel@x.test")
    b = make_client("intruder@x.test")
    chat = a.post("/api/ai/chats", {}).json["chat"]
    ev = _events(a.c.post(f"/api/ai/chats/{chat['id']}/messages/stream", json={"content": "Punishment for murder?"},
                          headers=H))
    assert "not configured" in ev[-1]["message"]["content"]
    r = b.c.post(f"/api/ai/chats/{chat['id']}/messages/stream", json={"content": "hi"}, headers=H)
    assert r.status_code == 404                                      # private chats stay private when streaming


# ── Activity logs ──────────────────────────────────────────────────────────

def _firm_with_member(make_client):
    boss = make_client("boss@x.test", name="Firm Boss")
    assert boss.post("/api/firm", {"name": "Iyer & Rao Associates"}).status_code == 201
    link = boss.post("/api/firm/invitations", {"email": "junior@x.test", "role": "junior_associate"}).json["invite_link"]
    junior = make_client("junior@x.test", name="Junior Advocate")
    assert junior.post("/api/invitations/accept", {"token": link.split("token=")[1]}).status_code == 200
    junior.refresh()
    return boss, junior


def test_own_activity_shows_detail(app, make_client):
    app.extensions["lawpilot_llm"] = None
    c = make_client("own@x.test")
    c.post("/api/research", {"query": "Secret strategy question about Article 21"})
    other = make_client()
    other.post("/api/auth/login", {"email": "own@x.test", "password": "wrong-pass-1"})
    r = c.get("/api/account/activity").json
    labels = [i["label"] for i in r["items"]]
    assert "Ran a legal research query" in labels and "Failed sign-in attempt" in labels
    research = next(i for i in r["items"] if i["action"] == "research.run")
    assert "Secret strategy question" in research["summary"]           # your own view keeps the detail
    assert {c["key"] for c in r["categories"]} >= {"account", "research"}
    only = c.get("/api/account/activity?category=research").json["items"]
    assert only and all(i["category"] == "research" for i in only)


def test_firm_activity_is_neutral_and_limited_to_the_firm(app, make_client):
    app.extensions["lawpilot_llm"] = None
    boss, junior = _firm_with_member(make_client)
    junior.post("/api/research", {"query": "Confidential client strategy on bail"})
    outsider = make_client("outsider@x.test")
    outsider.post("/api/research", {"query": "Outsider question"})

    r = boss.get("/api/firm/activity")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Confidential client strategy" not in body and "Outsider" not in body
    items = r.json["items"]
    assert any(i["user_name"] == "Junior Advocate" and i["label"] == "Ran a legal research query" for i in items)
    assert all(set(i) == {"ts", "user_id", "user_name", "category", "label"} for i in items)   # no summary, no IP
    names = {m["name"]: m for m in r.json["members"]}
    assert names["Junior Advocate"]["role"] == "junior_associate" and names["Junior Advocate"]["events_30d"] >= 1
    only_junior = boss.get(f"/api/firm/activity?user_id={junior.user['id']}").json["items"]
    assert only_junior and all(i["user_name"] == "Junior Advocate" for i in only_junior)
    csv_text = boss.get("/api/firm/activity?format=csv").get_data(as_text=True)
    assert csv_text.startswith("When (UTC),Member,Category,Activity") and "Confidential" not in csv_text
    # Members without the permission are refused.
    assert junior.get("/api/firm/activity").status_code == 403
    assert outsider.get("/api/firm/activity").status_code == 403


def test_platform_admin_sees_firms_first_then_neutral_member_activity(app, make_client, admin):
    app.extensions["lawpilot_llm"] = None
    boss, junior = _firm_with_member(make_client)
    junior.post("/api/research", {"query": "Privileged client question"})
    solo = make_client("solo@x.test", name="Solo Advocate")
    solo.post("/api/research", {"query": "Solo question"})

    firms = admin.get("/api/admin/activity/firms").json
    firm = next(f for f in firms["firms"] if f["name"] == "Iyer & Rao Associates")
    assert firm["members"] == 2 and firm["events_7d"] >= 2
    assert firms["independent"]["members"] >= 1 and firms["platform"]["members"] >= 1

    r = admin.get(f"/api/admin/activity/firms/{firm['id']}")
    assert r.json["title"] == "Iyer & Rao Associates"
    assert "Privileged client question" not in r.get_data(as_text=True)
    assert any(i["user_name"] == "Junior Advocate" and i["label"] == "Ran a legal research query" for i in r.json["items"])
    ind = admin.get("/api/admin/activity/firms/independent").json
    assert any(i["user_name"] == "Solo Advocate" for i in ind["items"]) and "Solo question" not in json.dumps(ind)
    # Work done after joining the firm is recorded under the firm (sign-up before joining stays independent).
    assert not any(i["user_name"] == "Junior Advocate" and i["category"] == "research" for i in ind["items"])
    assert admin.get("/api/admin/activity/firms/nope").status_code == 404
    assert boss.get("/api/admin/activity/firms").status_code == 403


def test_activity_catalogue_covers_every_logged_action():
    import pathlib
    import re
    src = "".join(p.read_text(encoding="utf-8") for p in pathlib.Path("lawpilot").rglob("*.py"))
    used = set(re.findall(r'activity\.log\(\s*"([a-z_.]+)"', src)) | {"account.login_failed"}
    missing = sorted(a for a in used if a not in activity.ACTIONS)
    assert not missing, f"add neutral labels for: {missing}"


def test_csv_export_neutralises_formulas():
    out = activity.to_csv([{"user_name": "=HYPERLINK(1)"}], [("user_name", "Member")])
    assert "'=HYPERLINK" in out


# ── Monitoring ─────────────────────────────────────────────────────────────

def test_health_ready_and_request_ids(make_client):
    c = make_client()
    r = c.get("/health/ready")
    assert r.status_code == 200 and r.json["database"] == "ok"
    assert len(r.headers["X-Request-ID"]) >= 8
    r = c.c.get("/api/auth/me", headers={"X-Request-ID": "abc12345-req"})
    assert r.headers["X-Request-ID"] == "abc12345-req"
    r = c.c.get("/api/auth/me", headers={"X-Request-ID": "bad id with spaces"})
    assert r.headers["X-Request-ID"] != "bad id with spaces"


def test_unhandled_errors_carry_a_reference(app, make_client, monkeypatch):
    from lawpilot.routes import dashboard
    c = make_client("boom@x.test")
    monkeypatch.setattr(dashboard, "get_embedder", lambda cfg: 1 / 0)
    r = c.get("/api/system/status")
    assert r.status_code == 500 and r.json["request_id"] and r.json["request_id"] in r.json["error"]


def test_client_error_endpoint(make_client):
    c = make_client()
    assert c.post("/api/client-errors", {"message": "TypeError: x is undefined", "path": "#/ai?secret=1"}).status_code == 204
    assert c.post("/api/client-errors", {}).status_code == 204


def test_sentry_scrubbing_removes_client_data():
    event = {
        "request": {"method": "POST", "url": "https://x/api/research?q=secret", "data": {"query": "privileged"},
                    "cookies": {"lp_session": "tok"}, "headers": {"Cookie": "lp_session=tok"}},
        "user": {"id": "u1", "email": "a@b.c", "ip_address": "1.2.3.4"},
        "exception": {"values": [{"stacktrace": {"frames": [{"function": "f", "vars": {"question": "privileged"}}]}}]},
    }
    out = monitoring.scrub_event(event)
    assert out["request"] == {"method": "POST", "url": "https://x/api/research"}
    assert out["user"] == {"id": "u1"}
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "privileged" not in json.dumps(out)
    crumb = monitoring.scrub_breadcrumb({"category": "query", "message": "SELECT secret", "data": {"params": ["x"]}})
    assert crumb["message"] == "SQL query" and crumb["data"] == {}
