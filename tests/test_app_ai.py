import io

from lawpilot.llm import LLMClient, LLMResult

LEASE = """LEASE AGREEMENT

1. This lease is made on 1 April 2024 between Ramesh Kumar (Lessor) and Sunita Rao (Lessee) for the flat at
No. 12, MG Road, Bengaluru.

2. The monthly rent payable by the Lessee is Rs. 45,000, due on or before the 5th day of each month.

3. The Lessee shall pay a refundable security deposit of Rs. 2,70,000 at the time of signing.

4. Either party may terminate this lease by giving three months' written notice. Disputes shall be referred
to arbitration under the Arbitration and Conciliation Act, 1996, seated at Bengaluru."""


class ChatLLM(LLMClient):
    """Answers like a chat model and records every prompt it was sent."""
    provider = "fake"
    model = "chat-1"

    def __init__(self):
        self.calls = []

    def complete(self, system, user, max_tokens=4000, effort="medium"):
        self.calls.append((system, user))
        if "standalone search query" in system:
            return LLMResult("security deposit amount under the lease agreement", self.provider, self.model)
        if "Summarise the document" in system:
            return LLMResult("### Lease summary\n- Rent is Rs. 45,000 per month [S1].", self.provider, self.model)
        return LLMResult("The monthly rent payable by the Lessee is Rs. 45,000 [S1].", self.provider, self.model)


def _upload(client, chat_id, text=LEASE, name="lease.txt"):
    return client.post(f"/api/ai/chats/{chat_id}/files",
                       {"file": (io.BytesIO(text.encode()), name)}, content_type="multipart/form-data")


def test_chat_flow_files_answers_and_summary(app, make_client):
    llm = ChatLLM()
    app.extensions["lawpilot_llm"] = llm
    a = make_client("ai-user@example.com")
    chat = a.post("/api/ai/chats", {}).json["chat"]
    r = _upload(a, chat["id"])
    assert r.status_code == 200 and r.json["results"][0]["status"] == "succeeded", r.json
    file_id = r.json["files"][0]["id"]

    r = a.post(f"/api/ai/chats/{chat['id']}/messages", {"content": "What rent is payable under this agreement?"})
    assert r.status_code == 200, r.json
    msg = r.json["message"]
    assert "45,000" in msg["content"]
    assert any(c["origin"] == "file" and c["source_id"] == file_id for c in msg["citations"])
    assert msg["extra"]["verification"]["checked"] == 1
    assert "text" not in msg["citations"][0]                      # full passages are not sent to the browser
    assert r.json["chat"]["title"].startswith("What rent is payable")
    answer_prompt = llm.calls[-1][1]
    assert "<source" in answer_prompt and "Rs. 45,000" in answer_prompt

    # A follow-up is rewritten into a standalone query using the conversation.
    r = a.post(f"/api/ai/chats/{chat['id']}/messages", {"content": "and the deposit?"})
    assert r.json["message"]["extra"]["query"] == "security deposit amount under the lease agreement"
    assert "CONVERSATION SO FAR" in llm.calls[-1][1]

    # "Summarise" reads the whole file rather than searching it.
    r = a.post(f"/api/ai/chats/{chat['id']}/messages", {"content": "Summarise this", "attachments": [file_id]})
    m = r.json["message"]
    assert m["extra"]["mode"] == "summary" and "Lease summary" in m["content"]
    assert "<document" in llm.calls[-1][1] and "arbitration" in llm.calls[-1][1].lower()

    full = a.get(f"/api/ai/chats/{chat['id']}").json
    assert [x["role"] for x in full["messages"]] == ["user", "assistant"] * 3
    assert full["messages"][4]["attachments"][0]["id"] == file_id

    # Regenerate replaces the last answer.
    r = a.post(f"/api/ai/chats/{chat['id']}/regenerate")
    assert r.status_code == 200
    assert len(a.get(f"/api/ai/chats/{chat['id']}").json["messages"]) == 6


def test_chat_files_are_private(app, make_client):
    app.extensions["lawpilot_llm"] = ChatLLM()
    a = make_client("owner@example.com")
    b = make_client("other@example.com")
    chat = a.post("/api/ai/chats", {}).json["chat"]
    file_id = _upload(a, chat["id"]).json["files"][0]["id"]

    assert b.get(f"/api/ai/chats/{chat['id']}").status_code == 404
    assert b.post(f"/api/ai/chats/{chat['id']}/messages", {"content": "rent?"}).status_code == 404
    assert _upload(b, chat["id"]).status_code == 404
    assert b.get(f"/api/sources/{file_id}").status_code == 404
    assert a.get(f"/api/sources/{file_id}").status_code == 200
    assert b.get("/api/ai/chats").json["items"] == []

    # Chat files never appear in the public library or in anyone's research.
    lib = b.get("/api/corpus/search?q=Sunita").json
    assert all(s["id"] != file_id for s in lib.get("items", []))
    r = b.post("/api/research", {"query": "monthly rent payable by the Lessee Sunita Rao", "mode": "quick"}).json
    assert all(e["source_id"] != file_id for e in r["session"]["evidence"])
    other = b.post("/api/ai/chats", {}).json["chat"]
    r = b.post(f"/api/ai/chats/{other['id']}/messages", {"content": "monthly rent payable by Sunita Rao"}).json
    assert all(c["source_id"] != file_id for c in r["message"]["citations"])


def test_delete_chat_removes_files(app, make_client):
    app.extensions["lawpilot_llm"] = ChatLLM()
    a = make_client("deleter@example.com")
    chat = a.post("/api/ai/chats", {"use_library": False}).json["chat"]
    assert chat["use_library"] is False
    file_id = _upload(a, chat["id"]).json["files"][0]["id"]
    assert a.patch(f"/api/ai/chats/{chat['id']}", {"title": "Lease review", "pinned": True}).json["chat"]["pinned"]
    assert a.delete(f"/api/ai/chats/{chat['id']}").status_code == 200
    assert a.get(f"/api/sources/{file_id}").status_code == 404
    assert a.get("/api/ai/chats").json["items"] == []


def test_works_without_llm(app, make_client):
    app.extensions["lawpilot_llm"] = None
    a = make_client("nollm@example.com")
    chat = a.post("/api/ai/chats", {}).json["chat"]
    r = a.post(f"/api/ai/chats/{chat['id']}/messages", {"content": "What is the punishment for murder under the BNS?"})
    assert r.status_code == 200
    assert "not configured" in r.json["message"]["content"]


def test_platform_admin_can_use_ai_mode(admin):
    chat = admin.post("/api/ai/chats", {}).json["chat"]
    assert admin.get(f"/api/ai/chats/{chat['id']}").status_code == 200
