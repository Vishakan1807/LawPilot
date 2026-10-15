"""HTTP API blueprints."""
from __future__ import annotations

from flask import current_app, g, request

from lawpilot.auth import ApiError


def register_blueprints(app) -> None:
    from lawpilot.routes import account, admin, auth_routes, dashboard, drafts, firms, matters, research, sources
    for mod in (auth_routes, account, firms, matters, research, sources, drafts, dashboard, admin):
        app.register_blueprint(mod.bp)


def body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def need(data: dict, *keys: str) -> None:
    missing = [k for k in keys if not str(data.get(k) or "").strip()]
    if missing:
        raise ApiError(f"Missing required field(s): {', '.join(missing)}.")


def cfg():
    """Effective config: environment plus settings saved by admins (cached per request)."""
    if "lp_cfg" not in g:
        from lawpilot import settings
        from lawpilot.db import get_db
        g.lp_cfg = settings.effective(get_db(), current_app.config["LP"])
    return g.lp_cfg


def llm():
    """The configured LLM client, rebuilt whenever admins change AI settings."""
    if "lawpilot_llm" in current_app.extensions:        # explicit override (tests)
        return current_app.extensions["lawpilot_llm"]
    from lawpilot import settings
    from lawpilot.db import get_db
    from lawpilot.llm import build_llm
    version = settings.version(get_db())
    cached = current_app.extensions.get("lawpilot_llm_cache")
    if not cached or cached[0] != version:
        cached = (version, build_llm(cfg()))
        current_app.extensions["lawpilot_llm_cache"] = cached
    return cached[1]


def page_args(default: int = 50, maximum: int = 200):
    try:
        limit = max(1, min(maximum, int(request.args.get("limit", default))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        raise ApiError("Invalid paging parameters.")
    return limit, offset
