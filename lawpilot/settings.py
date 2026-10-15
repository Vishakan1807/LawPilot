"""Server settings editable by platform admins (AI provider, model, API keys, embeddings).

Values saved here override environment variables. API keys are encrypted at
rest with a key derived from the server secret; they are never sent back to
the browser, only whether one is set.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
from typing import Dict, Optional

from lawpilot.db import new_id, now

SECRET_FIELDS = ("anthropic_api_key", "openai_api_key", "openrouter_api_key", "groq_api_key", "gemini_api_key",
                 "voyage_api_key")
PLAIN_FIELDS = ("llm_provider", "llm_model", "embedding_provider")
ALL_FIELDS = SECRET_FIELDS + PLAIN_FIELDS


def _fernet(secret_key: str):
    from cryptography.fernet import Fernet
    digest = hashlib.sha256(("lawpilot-settings:" + secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def version(db) -> str:
    row = db.execute("SELECT value FROM meta WHERE key = 'settings_version'").fetchone()
    return row[0] if row else "0"


def load(db, cfg) -> Dict[str, str]:
    """Decrypted settings stored in the database."""
    out = {}
    f = None
    for row in db.execute("SELECT key, value FROM app_settings").fetchall():
        key, value = row[0], row[1]
        if key not in ALL_FIELDS or value is None:
            continue
        if key in SECRET_FIELDS:
            try:
                f = f or _fernet(cfg.secret_key)
                value = f.decrypt(value.encode()).decode()
            except Exception:
                continue   # secret key changed; treat as unset
        out[key] = value
    return out


def save(db, cfg, updates: Dict[str, Optional[str]], user_id: str) -> None:
    """None = leave unchanged, "" = clear, anything else = store."""
    f = _fernet(cfg.secret_key)
    for key, value in updates.items():
        if key not in ALL_FIELDS or value is None:
            continue
        value = str(value).strip()
        db.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        if value:
            stored = f.encrypt(value.encode()).decode() if key in SECRET_FIELDS else value
            db.execute("INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (?,?,?,?)",
                       (key, stored, user_id, now()))
    db.execute("INSERT INTO meta(key, value) VALUES ('settings_version', ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (new_id(),))
    db.commit()


def effective(db, cfg):
    """A copy of the config with database settings applied on top of the environment."""
    stored = load(db, cfg)
    if not stored:
        return cfg
    eff = dataclasses.replace(cfg, **{k: v for k, v in stored.items() if hasattr(cfg, k)})
    return eff


def public_view(db, cfg) -> Dict:
    """What the admin UI may see: which keys are set (and where), never the keys themselves."""
    stored = load(db, cfg)
    eff = effective(db, cfg)
    keys = {}
    for k in SECRET_FIELDS:
        source = "settings" if stored.get(k) else ("environment" if getattr(cfg, k) else None)
        val = getattr(eff, k) or ""
        keys[k] = {"set": bool(val), "source": source, "hint": ("…" + val[-4:]) if len(val) > 8 else ""}
    return {"llm_provider": eff.llm_provider, "llm_model": eff.llm_model,
            "embedding_provider": eff.embedding_provider, "keys": keys}
