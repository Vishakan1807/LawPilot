"""LLM provider layer.

Anthropic (Claude) is the primary provider and is called through the official
`anthropic` SDK. OpenAI-compatible endpoints (OpenAI, OpenRouter, Groq) are
supported as alternatives. With no key configured the app still works: research
returns ranked evidence passages without an AI-written answer.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("lawpilot.llm")

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-4o-mini",
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.5-flash",
}

OPENAI_COMPATIBLE_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
}

PROVIDER_LABELS = {
    "anthropic": "Anthropic (Claude)",
    "gemini": "Google Gemini (has a free tier)",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "groq": "Groq",
}

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

    def complete(self, system, user, max_tokens=4000, effort="medium"):
        a = self._anthropic
        extra_body = {"output_config": {"effort": effort}}
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
        try:
            if self.model in _FALLBACK_MODELS:
                extra_body["fallbacks"] = "default"
                resp = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], extra_body=extra_body, **kwargs)
            else:
                resp = self._client.messages.create(extra_body=extra_body, **kwargs)
        except a.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key.") from exc
        except a.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached. Try again shortly.") from exc
        except a.BadRequestError as exc:
            raise LLMError(f"Anthropic rejected the request: {exc.message}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"Anthropic API error ({exc.status_code}).") from exc
        except a.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API.") from exc
        if resp.stop_reason == "refusal":
            raise LLMError("The model declined to answer this request.")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if resp.stop_reason == "max_tokens":
            log.warning("Anthropic response hit max_tokens; output may be truncated")
        return LLMResult(text=text, provider=self.provider, model=getattr(resp, "model", self.model))


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


def provider_keys(cfg) -> dict:
    return {
        "anthropic": cfg.anthropic_api_key,
        "gemini": cfg.gemini_api_key,
        "openai": cfg.openai_api_key,
        "openrouter": cfg.openrouter_api_key,
        "groq": cfg.groq_api_key,
    }


def make_client(provider: str, api_key: str, model: str = "") -> LLMClient:
    if provider not in DEFAULT_MODELS:
        raise LLMError(f"Unknown provider '{provider}'.")
    if not api_key:
        raise LLMError(f"No API key set for {PROVIDER_LABELS[provider].split(' (')[0]}. Paste one and try again.")
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
