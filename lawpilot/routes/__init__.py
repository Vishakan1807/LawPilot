"""HTTP API blueprints."""
from __future__ import annotations

from flask import current_app, g, request

from lawpilot.auth import ApiError


def register_blueprints(app) -> None:
    from lawpilot.routes import (account, activity_routes, admin, ai, ai_models, auth_routes, dashboard, drafts,
                                 firms, matters, research, sources)
    for mod in (auth_routes, account, firms, matters, research, sources, drafts, dashboard, admin, ai, ai_models,
                activity_routes):
        app.register_blueprint(mod.bp)
    app.after_request(_record_llm_outcome)


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


def server_llm():
    """The server's own model (admin AI settings), rebuilt whenever those settings change."""
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


def llm_resolution():
    """How the signed-in user gets a model: their own key, the server's, or none (cached per request)."""
    if "lp_llm" not in g:
        from lawpilot import ai_access
        from lawpilot.db import get_db
        if "lawpilot_llm" in current_app.extensions:    # tests inject one client for everyone
            client = current_app.extensions["lawpilot_llm"]
            g.lp_llm = ai_access.Resolution(client, "server" if client else "none")
        else:
            g.lp_llm = ai_access.resolve(get_db(), cfg(), g.get("user"), server_llm())
    return g.lp_llm


def llm():
    """The model that answers for the signed-in user (None = passages only)."""
    return llm_resolution().client


def _record_llm_outcome(response):
    res = g.get("lp_llm")
    if res is not None and res.client is not None:
        try:
            from lawpilot import ai_access
            from lawpilot.db import get_db
            ai_access.record_outcome(get_db(), res.client)
        except Exception:                               # bookkeeping must never break a response
            current_app.logger.exception("could not record AI key usage")
    return response


def page_args(default: int = 50, maximum: int = 200):
    try:
        limit = max(1, min(maximum, int(request.args.get("limit", default))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        raise ApiError("Invalid paging parameters.")
    return limit, offset
