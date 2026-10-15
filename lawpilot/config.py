"""Runtime configuration, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so python-dotenv is not a dependency."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("LAWPILOT_DATA_DIR", BASE_DIR / "instance")))
    secret_key: str = ""
    dev_mode: bool = False
    secure_cookies: bool = False
    session_days: int = 14
    allow_registration: bool = True
    admin_emails: tuple = ()
    base_url: str = "http://127.0.0.1:5000"
    max_upload_mb: int = 25
    serverless: bool = False        # running on Vercel (no permanent disk, short-lived processes)
    database_url: str = ""          # postgresql://... ; empty = SQLite file in data_dir
    seed_summaries: bool = False    # load the 66 starter editorial summaries on first run

    # LLM
    llm_provider: str = ""          # anthropic | openai | openrouter | groq | none
    llm_model: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    groq_api_key: str = ""
    gemini_api_key: str = ""

    # Embeddings
    embedding_provider: str = "local"   # local | voyage | openai
    voyage_api_key: str = ""

    # Mail
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # External corpus
    kanoon_api_token: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lawpilot.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        _load_dotenv(BASE_DIR / ".env")
        cfg = cls()
        e = os.environ.get
        cfg.serverless = bool(e("VERCEL"))
        default_dir = "/tmp/lawpilot" if cfg.serverless else str(BASE_DIR / "instance")
        cfg.data_dir = Path(e("LAWPILOT_DATA_DIR", default_dir))
        cfg.dev_mode = _bool("LAWPILOT_DEV", False)
        cfg.secure_cookies = _bool("LAWPILOT_SECURE_COOKIES", False)
        cfg.session_days = int(e("LAWPILOT_SESSION_DAYS", "14"))
        cfg.allow_registration = _bool("LAWPILOT_ALLOW_REGISTRATION", True)
        cfg.admin_emails = tuple(x.strip().lower() for x in e("LAWPILOT_ADMIN_EMAILS", "").split(",") if x.strip())
        cfg.base_url = e("LAWPILOT_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
        cfg.max_upload_mb = int(e("LAWPILOT_MAX_UPLOAD_MB", "25"))
        if cfg.serverless:
            cfg.max_upload_mb = min(cfg.max_upload_mb, 4)      # Vercel rejects request bodies over ~4.5 MB
        cfg.database_url = e("DATABASE_URL", "").strip()
        if cfg.database_url.startswith("postgres://"):
            cfg.database_url = "postgresql://" + cfg.database_url[len("postgres://"):]
        cfg.seed_summaries = _bool("LAWPILOT_SEED_SUMMARIES", False)

        cfg.anthropic_api_key = e("ANTHROPIC_API_KEY", "")
        cfg.openai_api_key = e("OPENAI_API_KEY", "")
        cfg.openrouter_api_key = e("OPENROUTER_API_KEY", "")
        cfg.groq_api_key = e("GROQ_API_KEY", "")
        cfg.gemini_api_key = e("GEMINI_API_KEY", "")
        cfg.llm_provider = e("LLM_PROVIDER", "").strip().lower()
        cfg.llm_model = e("LLM_MODEL", "").strip()

        cfg.voyage_api_key = e("VOYAGE_API_KEY", "")
        cfg.embedding_provider = e("EMBEDDING_PROVIDER", "local").strip().lower()

        cfg.smtp_host = e("SMTP_HOST", "")
        cfg.smtp_port = int(e("SMTP_PORT", "587"))
        cfg.smtp_user = e("SMTP_USER", "")
        cfg.smtp_password = e("SMTP_PASSWORD", "")
        cfg.smtp_from = e("SMTP_FROM", cfg.smtp_user)

        cfg.kanoon_api_token = e("KANOON_API_TOKEN", "")

        for k, v in overrides.items():
            setattr(cfg, k, v)

        cfg.data_dir = Path(cfg.data_dir)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.upload_dir.mkdir(parents=True, exist_ok=True)
        cfg.secret_key = cfg.secret_key or e("LAWPILOT_SECRET_KEY", "") or _persistent_secret(cfg.data_dir)
        return cfg


def _persistent_secret(data_dir: Path) -> str:
    """Generate once and keep on disk so sessions survive restarts."""
    path = data_dir / ".secret_key"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_hex(32)
    path.write_text(key)
    return key
