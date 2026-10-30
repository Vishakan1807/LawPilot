"""Symmetric encryption for secrets stored in the database (API keys).

Keys are derived from the server secret plus a purpose label, so a value encrypted
for one purpose cannot be decrypted as another. Rotating LAWPILOT_SECRET_KEY makes
stored secrets unreadable; callers treat that as "not set".
"""
from __future__ import annotations

import base64
import hashlib
from typing import Optional


def fernet(secret_key: str, purpose: str):
    from cryptography.fernet import Fernet
    digest = hashlib.sha256(f"lawpilot-{purpose}:{secret_key}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(secret_key: str, purpose: str, value: str) -> str:
    return fernet(secret_key, purpose).encrypt(value.encode()).decode()


def decrypt(secret_key: str, purpose: str, token: str) -> Optional[str]:
    try:
        return fernet(secret_key, purpose).decrypt(token.encode()).decode()
    except Exception:
        return None


def hint(value: str) -> str:
    """A safe reminder of which key is stored: the last four characters only."""
    return ("…" + value[-4:]) if value and len(value) > 8 else ""
