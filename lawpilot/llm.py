"""LLM provider layer.

Every provider sits behind one interface, `LLMClient.complete(system, user)`, so the
retrieval / reranking / verification pipeline never depends on which model writes the
answer. Anthropic (Claude) is called through the official `anthropic` SDK; the others
speak the OpenAI chat-completions protocol. With no model connected the app still works:
research returns ranked evidence passages without an AI-written answer.

Embeddings are deliberately NOT part of this layer: the shared library is embedded once,
server-side, and a question must be embedded with the same model to be comparable.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Iterator, Optional

import requests

log = logging.getLogger("lawpilot.llm")

@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    label: str                  # shown in pickers
    default_model: str
    key_url: str                # where a user creates an API key
    key_prefix: str = ""        # typical key prefix, used only as a hint
    chat_url: str = ""          # OpenAI-compatible endpoint; empty for Anthropic
    note: str = ""


PROVIDERS = {p.key: p for p in [
    Provider("anthropic", "Anthropic", "Anthropic (Claude)", "claude-opus-5-5",
             "https://console.anthropic.com/settings/keys", "sk-ant-",
             note="Strongest legal reasoning and long documents."),
    Provider("gemini", "Google Gemini", "Google Gemini (has a free tier)", "gemini-2.5-flash",
             "https://aistudio.google.com/apikey", "AIza",
             "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
             note="Free tier available; fast and inexpensive."),
    Provider("openai", "OpenAI", "OpenAI", "gpt-4o-mini", "https://platform.openai.com/api-keys", "sk-",
             "https://api.openai.com/v1/chat/completions"),
    Provider("mistral", "Mistral AI", "Mistral AI", "mistral-large-latest", "https://console.mistral.ai/api-keys", "",
             "https://api.mistral.ai/v1/chat/completions", note="European provider."),
    Provider("deepseek", "DeepSeek", "DeepSeek", "deepseek-chat", "https://platform.deepseek.com/api_keys", "sk-",
             "https://api.deepseek.com/v1/chat/completions", note="Very low cost."),
    Provider("groq", "Groq", "Groq", "llama-3.3-70b-versatile", "https://console.groq.com/keys", "gsk_",
             "https://api.groq.com/openai/v1/chat/completions", note="Very fast open models; free tier."),
    Provider("openrouter", "OpenRouter", "OpenRouter", "meta-llama/llama-3.3-70b-instruct",
             "https://openrouter.ai/keys", "sk-or-", "https://openrouter.ai/api/v1/chat/completions",
             note="One key for hundreds of models."),
]}

# Derived views kept for existing callers.
DEFAULT_MODELS = {k: p.default_model for k, p in PROVIDERS.items()}
OPENAI_COMPATIBLE_URLS = {k: p.chat_url for k, p in PROVIDERS.items() if p.chat_url}
PROVIDER_LABELS = {k: p.label for k, p in PROVIDERS.items()}

# Models that accept the server-side refusal fallback ("default" routing).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str


class LLMClient:
    provider = ""
    model = ""

    def complete(self, system: str, user: str, max_tokens: int = 4000, effort: str = "medium") -> LLMResult:
        raise NotImplementedError

    def stream(self, system: str, user: str, max_tokens: int = 4000, effort: str = "medium") -> Iterator[str]:
        """Yield the answer in pieces as the model writes it. Default: one piece when it is done."""
        yield self.complete(system, user, max_tokens=max_tokens, effort=effort).text

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(self, api_key: str, model: str):
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("The 'anthropic' package is not installed. Run: pip install anthropic") from exc
        self._anthropic = anthropic
        # Explicit base URL so a stray ANTHROPIC_BASE_URL in the environment cannot redirect client data.
        self._client = anthropic.Anthropic(api_key=api_key, base_url=os.environ.get("LAWPILOT_ANTHROPIC_BASE_URL",
                                           "https://api.anthropic.com"), timeout=180.0, max_retries=2)
        self.model = model

    def _request(self, system, user, max_tokens, effort):
        """(messages API, keyword arguments) shared by complete() and stream()."""
        extra_body = {"output_config": {"effort": effort}}
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}], extra_body=extra_body)
        if self.model in _FALLBACK_MODELS:
            extra_body["fallbacks"] = "default"
            return self._client.beta.messages, dict(kwargs, betas=["server-side-fallback-2026-07-01"])
        return self._client.messages, kwargs

    def _error(self, exc) -> "LLMError":
        a = self._anthropic
        if isinstance(exc, a.AuthenticationError):
            return LLMError("Anthropic rejected the API key.")
        if isinstance(exc, a.RateLimitError):
            return LLMError("Anthropic rate limit reached. Try again shortly.")
        if isinstance(exc, a.BadRequestError):
            return LLMError(f"Anthropic rejected the request: {exc.message}")
        if isinstance(exc, a.APIStatusError):
            return LLMError(f"Anthropic API error ({exc.status_code}).")
        if isinstance(exc, a.APIConnectionError):
            return LLMError("Could not reach the Anthropic API.")
        return LLMError(f"Anthropic error: {exc}")

    def complete(self, system, user, max_tokens=4000, effort="medium"):
        api, kwargs = self._request(system, user, max_tokens, effort)
        try:
            resp = api.create(**kwargs)
        except self._anthropic.APIError as exc:
            raise self._error(exc) from exc
        if resp.stop_reason == "refusal":
            raise LLMError("The model declined to answer this request.")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if resp.stop_reason == "max_tokens":
            log.warning("Anthropic response hit max_tokens; output may be truncated")
        return LLMResult(text=text, provider=self.provider, model=getattr(resp, "model", self.model))

    def stream(self, system, user, max_tokens=4000, effort="medium"):
        api, kwargs = self._request(system, user, max_tokens, effort)
        try:
            with api.stream(**kwargs) as s:
                for text in s.text_stream:
                    if text:
                        yield text
                final = s.get_final_message()
        except self._anthropic.APIError as exc:
            raise self._error(exc) from exc
        if final.stop_reason == "refusal":
            raise LLMError("The model declined to answer this request.")


def _http_error(provider: str, r) -> str:
    """Readable error from an OpenAI-style error response."""
    try:
        data = r.json()
        data = data[0] if isinstance(data, list) and data else data
        msg = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
    except ValueError:
        msg = None
    hint = " Check the API key." if r.status_code in (400, 401, 403) and msg and "key" in msg.lower() else ""
    body = (msg.rstrip(".") + ".") if msg else r.text[:300]
    return f"{provider} returned HTTP {r.status_code}: {body}{hint}"


class OpenAICompatibleClient(LLMClient):
    def __init__(self, provider: str, api_key: str, model: str):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.url = OPENAI_COMPATIBLE_URLS[provider]

    def complete(self, system, user, max_tokens=4000, effort="medium"):
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last = ""
        for attempt in range(3):
            try:
                r = requests.post(self.url, json=body, headers=headers, timeout=180)
            except requests.RequestException as exc:
                last = f"Could not reach {self.provider}: {exc}"
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"{self.provider} returned HTTP {r.status_code}"
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise LLMError(_http_error(self.provider, r))
            data = r.json()
            try:
                return LLMResult(text=data["choices"][0]["message"]["content"] or "",
                                 provider=self.provider, model=data.get("model", self.model))
            except (KeyError, IndexError) as exc:
                raise LLMError(f"Unexpected response from {self.provider}") from exc
        raise LLMError(last or f"{self.provider} request failed")

    def stream(self, system, user, max_tokens=4000, effort="medium"):
        """Server-sent events from /chat/completions with "stream": true. Retries only before any text."""
        body = {
            "model": self.model, "max_tokens": max_tokens, "temperature": 0.1, "stream": True,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "Accept": "text/event-stream"}
        r, last = None, ""
        for attempt in range(3):
            try:
                r = requests.post(self.url, json=body, headers=headers, timeout=(15, 180), stream=True)
            except requests.RequestException as exc:
                last = f"Could not reach {self.provider}: {exc}"
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"{self.provider} returned HTTP {r.status_code}"
                r.close()
                time.sleep(2 * (attempt + 1))
                continue
            break
        if r is None:
            raise LLMError(last or f"{self.provider} request failed")
        if r.status_code == 429 or r.status_code >= 500:
            raise LLMError(last)                    # still failing after the retries
        if r.status_code != 200:
            try:
                raise LLMError(_http_error(self.provider, r))
            finally:
                r.close()
        try:
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if isinstance(chunk, dict) and chunk.get("error"):
                    raise LLMError(f"{self.provider}: {(chunk['error'] or {}).get('message', 'stream error')}")
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        yield piece
        except requests.RequestException as exc:
            raise LLMError(f"The connection to {self.provider} dropped while answering.") from exc
        finally:
            r.close()


def provider_keys(cfg) -> dict:
    return {
        "anthropic": cfg.anthropic_api_key,
        "gemini": cfg.gemini_api_key,
        "openai": cfg.openai_api_key,
        "openrouter": cfg.openrouter_api_key,
        "groq": cfg.groq_api_key,
        "mistral": getattr(cfg, "mistral_api_key", ""),
        "deepseek": getattr(cfg, "deepseek_api_key", ""),
    }


def make_client(provider: str, api_key: str, model: str = "") -> LLMClient:
    if provider not in DEFAULT_MODELS:
        raise LLMError(f"Unknown provider '{provider}'.")
    if not api_key:
        raise LLMError(f"No API key set for {PROVIDERS[provider].name}. Paste one and try again.")
    model = model or DEFAULT_MODELS[provider]
    if provider == "anthropic":
        return AnthropicClient(api_key, model)
    return OpenAICompatibleClient(provider, api_key, model)


def build_llm(cfg) -> Optional[LLMClient]:
    """Pick the configured provider, or auto-detect from whichever key is present."""
    keys = provider_keys(cfg)
    provider = cfg.llm_provider
    if provider == "none":
        return None
    if not provider:
        provider = next((p for p, k in keys.items() if k), "")
    if not provider:
        return None
    try:
        return make_client(provider, keys.get(provider, ""), cfg.llm_model)
    except LLMError as exc:
        log.error("%s", exc)
        return None


def list_models(provider: str, api_key: str) -> list:
    """Model ids this key can use, so admins pick a real one instead of guessing names."""
    if provider == "anthropic":
        client = make_client(provider, api_key)
        try:
            return sorted(m.id for m in client._client.models.list(limit=100))
        except Exception as exc:
            raise LLMError(f"Could not list Anthropic models: {exc}") from exc
    url = OPENAI_COMPATIBLE_URLS[provider].rsplit("/chat/completions", 1)[0] + "/models"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    except requests.RequestException as exc:
        raise LLMError(f"Could not reach {provider}: {exc}") from exc
    if r.status_code != 200:
        raise LLMError(_http_error(provider, r))
    ids = [m.get("id", "") for m in r.json().get("data", [])]
    return sorted(i.split("/", 1)[1] if provider == "gemini" and i.startswith("models/") else i for i in ids if i)


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a model response (tolerates code fences / preamble)."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in response")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object in response")
