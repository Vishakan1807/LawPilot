"""Bring-your-own-key: each user connects their own AI provider keys and switches between them."""
import pytest

from lawpilot import ai_access
from lawpilot.llm import LLMClient, LLMError, LLMResult

GOOD = "test-key-good-123456"
OTHER = "test-key-other-654321"


class FakeProviderClient(LLMClient):
    calls = []

    def __init__(self, provider, key, model):
        self.provider, self.key, self.model = provider, key, model

    def complete(self, system, user, max_tokens=4000, effort="medium"):
        FakeProviderClient.calls.append((self.provider, self.model, self.key))
        if "bad" in self.key:
            raise LLMError(f"{self.provider} rejected the API key.")
        if "quota" in self.key and "connectivity check" not in system:
            raise LLMError(f"{self.provider} quota exceeded.")
        if "connectivity check" in system:
            return LLMResult("OK", self.provider, self.model)
        return LLMResult('{"status": "answered", "short_answer": "Yes [E1]."}', self.provider, self.model)


@pytest.fixture(autouse=True)
def fake_providers(monkeypatch):
    FakeProviderClient.calls = []
    monkeypatch.setattr(ai_access, "make_client", lambda p, k, m="": FakeProviderClient(p, k, m))
    monkeypatch.setattr(ai_access, "list_models", lambda p, k: ["model-a", "model-b"])


def test_connect_switch_and_remove(make_client):
    c = make_client("byok@x.test")
    v = c.get("/api/account/ai").json
    assert v["active"]["source"] == "none" and not v["keys"]
    assert {p["key"] for p in v["providers"]} >= {"anthropic", "gemini", "openai", "mistral", "deepseek", "groq", "openrouter"}

    r = c.c.put("/api/account/ai/gemini", json={"api_key": GOOD}, headers={"X-Requested-With": "LawPilot"})
    assert r.status_code == 200, r.json
    assert r.json["active"] == {"source": "own", "reason": "", "provider": "gemini", "model": "gemini-2.5-flash"}
    assert GOOD not in r.get_data(as_text=True)                       # the key never comes back
    assert r.json["keys"][0]["key_hint"] == "…3456"

    r = c.c.put("/api/account/ai/groq", json={"api_key": OTHER, "model": "model-b", "activate": False},
                headers={"X-Requested-With": "LawPilot"})
    assert r.json["active"]["provider"] == "gemini"                   # adding a second key does not switch
    r = c.post("/api/account/ai/groq/activate")
    assert r.json["active"]["provider"] == "groq" and r.json["active"]["model"] == "model-b"
    assert c.get("/api/system/status").json["llm"]["source"] == "own"

    assert c.get("/api/account/ai/groq/models").json["models"] == ["model-a", "model-b"]
    r = c.c.put("/api/account/ai/groq", json={"model": "model-a"}, headers={"X-Requested-With": "LawPilot"})
    assert r.json["active"]["model"] == "model-a"

    r = c.delete("/api/account/ai/groq")                              # removing the active one falls back
    assert r.json["active"]["provider"] == "gemini"
    r = c.delete("/api/account/ai/gemini")
    assert r.json["active"]["source"] == "none" and not r.json["keys"]


def test_bad_key_is_not_saved(make_client):
    c = make_client("badkey@x.test")
    r = c.c.put("/api/account/ai/openai", json={"api_key": "bad-key-000000000"}, headers={"X-Requested-With": "LawPilot"})
    assert r.status_code == 400 and "rejected" in r.json["error"]
    assert not c.get("/api/account/ai").json["keys"]
    r = c.c.put("/api/account/ai/openai", json={"api_key": "has spaces in it"}, headers={"X-Requested-With": "LawPilot"})
    assert r.status_code == 400
    assert c.c.put("/api/account/ai/nope", json={"api_key": GOOD}, headers={"X-Requested-With": "LawPilot"}).status_code == 404


def test_keys_are_private_and_encrypted(make_client, app):
    from lawpilot import db as dbmod
    a = make_client("owner-ai@x.test")
    b = make_client("other-ai@x.test")
    a.c.put("/api/account/ai/mistral", json={"api_key": GOOD}, headers={"X-Requested-With": "LawPilot"})
    assert not b.get("/api/account/ai").json["keys"]
    assert b.get("/api/account/ai/mistral/models").status_code == 400  # b has no key to use
    conn = dbmod.connect(app.config["LP"], pooled=False)
    stored = conn.execute("SELECT key_enc FROM user_llm_keys").fetchone()[0]
    conn.close()
    assert GOOD not in stored


def test_research_uses_the_users_own_model_and_records_errors(make_client):
    c = make_client("uses@x.test")
    c.c.put("/api/account/ai/deepseek", json={"api_key": GOOD}, headers={"X-Requested-With": "LawPilot"})
    FakeProviderClient.calls = []
    s = c.post("/api/research", {"query": "Is privacy a fundamental right under Article 21?"}).json["session"]
    assert s["llm_model"].startswith("deepseek:") and FakeProviderClient.calls[0][2] == GOOD
    assert c.get("/api/account/ai").json["keys"][0]["last_error"] is None

    c.c.put("/api/account/ai/deepseek", json={"api_key": "quota-key-123456"}, headers={"X-Requested-With": "LawPilot"})
    s = c.post("/api/research", {"query": "Is privacy a fundamental right under Article 21?"}).json["session"]
    assert s["outcome"] == "evidence_only"                            # degrades to passages, never a 500
    assert "quota" in c.get("/api/account/ai").json["keys"][0]["last_error"]


def test_server_key_policy(make_client, admin, app, monkeypatch):
    import lawpilot.routes as routes
    server = FakeProviderClient("gemini", "server-key-123456", "gemini-2.5-flash")
    monkeypatch.setattr(routes, "server_llm", lambda: server)
    lawyer = make_client("policy@x.test")
    lp = app.config["LP"]
    assert lp.shared_key_policy == "admins"
    assert lawyer.get("/api/system/status").json["llm"]["source"] == "none"     # default: lawyers bring a key
    assert admin.get("/api/system/status").json["llm"]["source"] == "server"
    lp.shared_key_policy = "everyone"
    try:
        assert lawyer.get("/api/system/status").json["llm"]["source"] == "server"
        lawyer.c.put("/api/account/ai/gemini", json={"api_key": GOOD}, headers={"X-Requested-With": "LawPilot"})
        assert lawyer.get("/api/system/status").json["llm"]["source"] == "own"   # own key wins
        lawyer.post("/api/account/ai/use-server")
        assert lawyer.get("/api/system/status").json["llm"]["source"] == "server"
    finally:
        lp.shared_key_policy = "admins"
    lp.shared_key_policy = "nobody"
    try:
        assert admin.get("/api/system/status").json["llm"]["source"] == "none"
    finally:
        lp.shared_key_policy = "admins"


def test_admin_can_set_shared_key_policy(admin):
    r = admin.post("/api/admin/ai", {"shared_key_policy": "everyone"})
    assert r.status_code == 200 and r.json["shared_key_policy"] == "everyone"
    assert admin.post("/api/admin/ai", {"shared_key_policy": "chaos"}).status_code == 400


# ── Provider errors and automatic model fallback ───────────────────────────

class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body

    def close(self):
        pass


def test_provider_errors_are_classified_with_actionable_messages():
    from lawpilot.llm import _http_failure
    e = _http_failure("groq", FakeResponse(404, {"error": {"message": "The model `llama-3.3-70b-versatile` does not "
                                                            "exist or you do not have access to it.",
                                                 "code": "model_not_found"}}))
    assert e.kind == "model" and "Groq does not offer this model" in str(e)
    e = _http_failure("openai", FakeResponse(429, {"error": {"message": "You exceeded your current quota",
                                                             "code": "insufficient_quota"}}))
    assert e.kind == "quota" and "no free tier" in str(e)
    e = _http_failure("openrouter", FakeResponse(403, {"error": {"message": "Key limit exceeded (weekly limit)."}}))
    assert e.kind == "quota" and ":free" in str(e)
    e = _http_failure("groq", FakeResponse(429, {"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}}))
    assert e.kind == "rate_limit"
    e = _http_failure("mistral", FakeResponse(401, {"message": "Unauthorized"}))
    assert e.kind == "auth" and "rejected this API key" in str(e)


def test_no_credit_errors_are_not_retried(monkeypatch):
    import lawpilot.llm as llm_mod
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return FakeResponse(429, {"error": {"message": "quota", "code": "insufficient_quota"}})
    monkeypatch.setattr(llm_mod.requests, "post", fake_post)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    client = llm_mod.OpenAICompatibleClient("openai", "sk-test-123456789", "gpt-4o-mini")
    with pytest.raises(LLMError) as info:
        client.complete("s", "u")
    assert info.value.kind == "quota" and len(calls) == 1
    with pytest.raises(LLMError):
        list(client.stream("s", "u"))
    assert len(calls) == 2


def test_candidate_models_skip_non_chat_and_prefer_good_ones():
    groq = ["whisper-large-v3", "llama-guard-4-12b", "gpt-oss-20b", "llama-3.3-70b-versatile", "playai-tts"]
    assert ai_access.candidate_models("groq", groq, exclude={"x"})[:2] == ["llama-3.3-70b-versatile", "gpt-oss-20b"]
    router = ["openai/gpt-4o", "google/gemma-3-27b-it:free", "meta-llama/llama-3.3-70b-instruct:free"]
    assert ai_access.candidate_models("openrouter", router) == ["meta-llama/llama-3.3-70b-instruct:free",
                                                               "google/gemma-3-27b-it:free"]


def _model_aware(monkeypatch, unavailable, kind="model", models=()):
    class Picky(FakeProviderClient):
        def complete(self, system, user, max_tokens=4000, effort="medium"):
            if self.model in unavailable:
                raise LLMError(f"{self.provider} cannot use {self.model}", 404, kind)
            return super().complete(system, user, max_tokens, effort)
    monkeypatch.setattr(ai_access, "make_client", lambda p, k, m="": Picky(p, k, m))
    monkeypatch.setattr(ai_access, "list_models", lambda p, k: list(models))


def test_retired_default_model_is_replaced_automatically(make_client, monkeypatch):
    _model_aware(monkeypatch, {"llama-3.3-70b-versatile", "my-chosen-model"},
                 models=["whisper-large-v3", "llama-guard-4-12b", "openai/gpt-oss-120b", "llama-3.1-8b-instant"])
    c = make_client("retired@x.test")
    r = c.c.put("/api/account/ai/groq", json={"api_key": GOOD}, headers={"X-Requested-With": "LawPilot"})
    assert r.status_code == 200, r.json
    assert r.json["check"]["model"] == "openai/gpt-oss-120b" and "chose" in r.json["check"]["note"]
    assert r.json["active"]["model"] == "openai/gpt-oss-120b"
    # A model the user picked on purpose is never swapped silently.
    r = c.c.put("/api/account/ai/groq", json={"api_key": GOOD, "model": "my-chosen-model"},
                headers={"X-Requested-With": "LawPilot"})
    assert r.status_code == 400                                   # explicit choice: report, do not swap


def test_openrouter_without_credit_falls_back_to_a_free_model(make_client, monkeypatch):
    _model_aware(monkeypatch, {"meta-llama/llama-3.3-70b-instruct:free", "openai/gpt-4o"}, kind="quota",
                 models=["openai/gpt-4o", "meta-llama/llama-3.3-70b-instruct:free", "google/gemma-3-27b-it:free"])
    c = make_client("router@x.test")
    r = c.c.put("/api/account/ai/openrouter", json={"api_key": GOOD}, headers={"X-Requested-With": "LawPilot"})
    assert r.status_code == 200, r.json
    assert r.json["check"]["model"] == "google/gemma-3-27b-it:free"


def test_catalogue_marks_free_options():
    pricing = {p["key"]: p["pricing"] for p in ai_access.catalog()}
    assert pricing["gemini"] == "free" and pricing["groq"] == "free" and pricing["openrouter"] == "free_models"
    assert pricing["openai"] == "paid"
