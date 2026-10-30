"""Which AI model answers for a user: their own key first (bring your own key), then the server's.

Each user connects one or more providers with their own API key and picks the active one.
Keys are encrypted at rest and never leave the server. A request resolves to:

  own     the user's active key                       (they pay, no shared quota)
  server  the server's key, if the shared-key policy  (platform admins only by default)
          allows this user
  none    no model; research still returns ranked passages

The client returned is wrapped so failures ("key rejected", "quota exceeded") are recorded
against the user's key and shown in Settings. Recording happens after the request, never
inside `complete()`, because summaries call the model from worker threads.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from lawpilot import crypto
from lawpilot.db import new_id, now
from lawpilot.llm import PROVIDERS, LLMClient, LLMError, LLMResult, list_models, make_client

log = logging.getLogger("lawpilot.ai_access")

PURPOSE = "user-llm"
PING_SYSTEM = "You are a connectivity check. Reply with the single word OK."
KEY_FIELDS = "id, provider, key_hint, model, is_active, last_used_at, last_error, created_at, updated_at"


@dataclass
class Resolution:
    client: Optional[LLMClient]
    source: str                 # own | server | none
    reason: str = ""            # why there is no model, for the UI


class TrackedClient(LLMClient):
    """A user's client that remembers the outcome of its last call (persisted after the request)."""

    def __init__(self, inner: LLMClient, key_id: str):
        self.inner = inner
        self.provider = inner.provider
        self.model = inner.model
        self.key_id = key_id
        self.outcome: Optional[tuple] = None        # ("ok", None) | ("error", message)

    def complete(self, system, user, max_tokens=4000, effort="medium") -> LLMResult:
        try:
            res = self.inner.complete(system, user, max_tokens=max_tokens, effort=effort)
        except LLMError as exc:
            self.outcome = ("error", str(exc)[:500])
            raise
        self.outcome = ("ok", None)
        return res

    def stream(self, system, user, max_tokens=4000, effort="medium"):
        try:
            yield from self.inner.stream(system, user, max_tokens=max_tokens, effort=effort)
        except LLMError as exc:
            self.outcome = ("error", str(exc)[:500])
            raise
        self.outcome = ("ok", None)


# ── Catalogue ──────────────────────────────────────────────────────────────

def catalog() -> List[Dict]:
    return [{"key": p.key, "name": p.name, "label": p.label, "default_model": p.default_model,
             "key_url": p.key_url, "key_prefix": p.key_prefix, "note": p.note, "pricing": p.pricing}
            for p in PROVIDERS.values()]


def _provider(provider: str):
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown AI provider '{provider}'.")
    return PROVIDERS[provider]


def _clean_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        raise LLMError("Paste your API key.")
    if len(key) < 12 or len(key) > 400 or any(ch.isspace() for ch in key):
        raise LLMError("That does not look like an API key. Copy it again from the provider's console.")
    return key


def _ping(client: LLMClient) -> str:
    """One tiny request: proves the key works and the model exists before anything is saved."""
    return client.complete(PING_SYSTEM, "ping", max_tokens=400, effort="low").text.strip()[:80]


# Model ids that are not chat models (speech, images, embeddings, safety classifiers ...).
NON_CHAT = ("whisper", "tts", "guard", "embed", "moderation", "audio", "image", "dall-e", "realtime", "transcribe",
            "search", "vision", "playai", "orpheus", "imagen", "veo", "aqa", "computer-use", "codex", "rerank", "ocr")
# Preferred replacements, best first, when the chosen model is not available to a key.
PREFERRED = {
    "groq": ("llama-3.3-70b", "llama-4-maverick", "gpt-oss-120b", "llama-4-scout", "kimi-k2", "qwen3", "gpt-oss-20b",
             "llama-3.1-8b"),
    "openrouter": ("llama-3.3-70b", "deepseek", "qwen", "gemma", "mistral", "llama"),
    "gemini": ("gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash", "flash"),
    "openai": ("gpt-4o-mini", "gpt-4.1-mini", "gpt-5-mini", "mini", "gpt-4o"),
    "mistral": ("mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"),
    "deepseek": ("deepseek-chat",),
    "anthropic": ("sonnet", "opus", "haiku"),
}


def candidate_models(provider: str, models: List[str], exclude=()) -> List[str]:
    """Chat models from a key's model list, best replacement first. OpenRouter: free models only."""
    chat = [m for m in models if m not in exclude and not any(x in m.lower() for x in NON_CHAT)]
    if provider == "openrouter":
        chat = [m for m in chat if m.endswith(":free")]
    ranked, seen = [], set()
    for pref in PREFERRED.get(provider, ()):
        for m in chat:
            if pref in m.lower() and m not in seen:
                ranked.append(m)
                seen.add(m)
    return ranked + [m for m in chat if m not in seen]


def _connect(provider: str, key: str, model: str, may_switch: bool) -> Dict:
    """Ping the model; if the key cannot use it (retired model, no credit for paid OpenRouter models) and the
    user did not pick it explicitly, find a model the key can use instead."""
    try:
        return {"reply": _ping(make_client(provider, key, model)), "model": model, "note": ""}
    except LLMError as exc:
        switchable = exc.kind == "model" or (exc.kind == "quota" and provider == "openrouter")
        if not may_switch or not switchable:
            raise
        try:
            available = list_models(provider, key)
        except LLMError:
            raise exc
        tried = 0
        for candidate in candidate_models(provider, available, exclude={model}):
            if tried >= 4:
                break
            tried += 1
            try:
                reply = _ping(make_client(provider, key, candidate))
            except LLMError as again:
                if again.kind == "auth":
                    raise again
                continue
            note = (f"“{model}” is not available to this key, so LawPilot chose “{candidate}”. "
                    f"You can change it any time with “Change model”.")
            return {"reply": reply, "model": candidate, "note": note}
        if provider == "openrouter" and exc.kind == "quota":
            raise LLMError(str(exc) + " No free model answered either; try again later.", exc.status, exc.kind)
        raise


# ── Reading ────────────────────────────────────────────────────────────────

def list_keys(db, user_id: str) -> List[Dict]:
    rows = db.execute(f"SELECT {KEY_FIELDS} FROM user_llm_keys WHERE user_id = ? ORDER BY created_at",
                      (user_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_active"] = bool(d["is_active"])
        d["provider_name"] = PROVIDERS[d["provider"]].name if d["provider"] in PROVIDERS else d["provider"]
        out.append(d)
    return out


def _row(db, user_id: str, provider: str):
    return db.execute("SELECT * FROM user_llm_keys WHERE user_id = ? AND provider = ?", (user_id, provider)).fetchone()


def _decrypted(cfg, row) -> Optional[str]:
    return crypto.decrypt(cfg.secret_key, PURPOSE, row["key_enc"])


def server_allowed(cfg, user: dict) -> bool:
    policy = (getattr(cfg, "shared_key_policy", "admins") or "admins").lower()
    if policy == "everyone":
        return True
    if policy == "admins":
        return bool(user.get("is_platform_admin"))
    return False


def resolve(db, cfg, user: Optional[dict], server_client: Optional[LLMClient]) -> Resolution:
    if not user:
        return Resolution(None, "none", "Please sign in.")
    row = db.execute("SELECT * FROM user_llm_keys WHERE user_id = ? AND is_active = 1", (user["id"],)).fetchone()
    if row:
        key = _decrypted(cfg, row)
        if key and row["provider"] in PROVIDERS:
            try:
                return Resolution(TrackedClient(make_client(row["provider"], key, row["model"]), row["id"]), "own")
            except LLMError as exc:
                log.warning("user key unusable: %s", exc)
        return Resolution(None, "none", "Your saved API key can no longer be read. Add it again in Settings → AI models.")
    if server_client and server_allowed(cfg, user):
        return Resolution(server_client, "server")
    return Resolution(None, "none", "Connect your own AI model in Settings → AI models to get written answers.")


def record_outcome(db, client) -> None:
    """Persist the last call's result for a user's key (called after the request)."""
    if not isinstance(client, TrackedClient) or not client.outcome:
        return
    status, message = client.outcome
    if status == "ok":
        db.execute("UPDATE user_llm_keys SET last_used_at = ?, last_error = NULL WHERE id = ?", (now(), client.key_id))
    else:
        db.execute("UPDATE user_llm_keys SET last_error = ? WHERE id = ?", (message, client.key_id))
    db.commit()


# ── Writing ────────────────────────────────────────────────────────────────

def save_key(db, cfg, user_id: str, provider: str, api_key: str, model: str = "", activate: bool = True) -> Dict:
    """Validate a key with one tiny request, then store it encrypted. Raises LLMError if it does not work."""
    p = _provider(provider)
    key = _clean_key(api_key)
    requested = (model or "").strip()
    result = _connect(provider, key, requested or p.default_model, may_switch=requested in ("", p.default_model))
    model = result["model"]
    ts = now()
    existing = _row(db, user_id, provider)
    enc = crypto.encrypt(cfg.secret_key, PURPOSE, key)
    if existing:
        db.execute("UPDATE user_llm_keys SET key_enc = ?, key_hint = ?, model = ?, last_error = NULL, last_used_at = ?, "
                   "updated_at = ? WHERE id = ?", (enc, crypto.hint(key), model, ts, ts, existing["id"]))
    else:
        db.execute("INSERT INTO user_llm_keys (id, user_id, provider, key_enc, key_hint, model, is_active, last_used_at,"
                   " created_at, updated_at) VALUES (?,?,?,?,?,?,0,?,?,?)",
                   (new_id(), user_id, provider, enc, crypto.hint(key), model, ts, ts, ts))
    if activate or not db.execute("SELECT 1 FROM user_llm_keys WHERE user_id = ? AND is_active = 1",
                                  (user_id,)).fetchone():
        _set_active(db, user_id, provider)
    db.commit()
    return result


def change_model(db, cfg, user_id: str, provider: str, model: str) -> Dict:
    row = _row(db, user_id, provider)
    if not row:
        raise LLMError("Connect this provider first.")
    model = (model or "").strip()
    if not model:
        raise LLMError("Choose a model.")
    key = _decrypted(cfg, row)
    if not key:
        raise LLMError("The saved key can no longer be read. Paste it again.")
    reply = _ping(make_client(provider, key, model))
    db.execute("UPDATE user_llm_keys SET model = ?, last_error = NULL, last_used_at = ?, updated_at = ? WHERE id = ?",
               (model, now(), now(), row["id"]))
    db.commit()
    return {"reply": reply, "model": model}


def _set_active(db, user_id: str, provider: Optional[str]) -> None:
    db.execute("UPDATE user_llm_keys SET is_active = 0 WHERE user_id = ?", (user_id,))
    if provider:
        db.execute("UPDATE user_llm_keys SET is_active = 1 WHERE user_id = ? AND provider = ?", (user_id, provider))


def activate(db, user_id: str, provider: Optional[str]) -> None:
    """Make one provider active, or None to stop using an own key (fall back to the server, if allowed)."""
    if provider and not _row(db, user_id, provider):
        raise LLMError("Connect this provider first.")
    _set_active(db, user_id, provider)
    db.commit()


def remove(db, user_id: str, provider: str) -> None:
    row = _row(db, user_id, provider)
    if not row:
        raise LLMError("This provider is not connected.")
    db.execute("DELETE FROM user_llm_keys WHERE id = ?", (row["id"],))
    if row["is_active"]:
        nxt = db.execute("SELECT provider FROM user_llm_keys WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                         (user_id,)).fetchone()
        if nxt:
            _set_active(db, user_id, nxt[0])
    db.commit()


def test_key(db, cfg, user_id: str, provider: str) -> Dict:
    row = _row(db, user_id, provider)
    if not row:
        raise LLMError("This provider is not connected.")
    key = _decrypted(cfg, row)
    if not key:
        raise LLMError("The saved key can no longer be read. Paste it again.")
    try:
        reply = _ping(make_client(provider, key, row["model"]))
    except LLMError as exc:
        db.execute("UPDATE user_llm_keys SET last_error = ? WHERE id = ?", (str(exc)[:500], row["id"]))
        db.commit()
        raise
    db.execute("UPDATE user_llm_keys SET last_error = NULL, last_used_at = ? WHERE id = ?", (now(), row["id"]))
    db.commit()
    return {"reply": reply, "model": row["model"]}


def models_for(db, cfg, user_id: str, provider: str, api_key: str = "") -> List[str]:
    """Models a key can use: the pasted key if given, else the saved one."""
    _provider(provider)
    key = (api_key or "").strip()
    if not key:
        row = _row(db, user_id, provider)
        key = _decrypted(cfg, row) if row else ""
    if not key:
        raise LLMError("Paste your API key first.")
    return list_models(provider, key)
