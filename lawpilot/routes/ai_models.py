"""Settings → AI models: each user connects their own provider keys and picks the active model."""
from __future__ import annotations

from flask import Blueprint, g, jsonify

from lawpilot import activity, ai_access
from lawpilot.auth import ApiError, login_required
from lawpilot.db import get_db
from lawpilot.llm import PROVIDERS, LLMError
from lawpilot.routes import body, cfg, llm_resolution, server_llm

bp = Blueprint("ai_models", __name__, url_prefix="/api/account/ai")


def _known(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ApiError("Unknown AI provider.", 404)
    return provider


def _view() -> dict:
    g.pop("lp_llm", None)                       # settings may have just changed
    res = llm_resolution()
    server = server_llm()
    return {
        "providers": ai_access.catalog(),
        "keys": ai_access.list_keys(get_db(), g.user["id"]),
        "active": {"source": res.source, "reason": res.reason,
                   "provider": res.client.provider if res.client else None,
                   "model": res.client.model if res.client else None},
        "server": {"available": bool(server) and ai_access.server_allowed(cfg(), g.user),
                   "policy": cfg().shared_key_policy},
    }


def _llm_call(fn):
    """Provider errors are the user's to fix (bad key, no quota), so they come back as 400 with the reason."""
    try:
        return fn()
    except LLMError as exc:
        raise ApiError(str(exc), 400)


@bp.get("")
@login_required
def overview():
    return jsonify(_view())


@bp.put("/<provider>")
@login_required
def save(provider):
    _known(provider)
    data = body()
    db, uid = get_db(), g.user["id"]
    if (data.get("api_key") or "").strip():
        result = _llm_call(lambda: ai_access.save_key(db, cfg(), uid, provider, data["api_key"], data.get("model") or "",
                                                      activate=data.get("activate", True) is not False))
        activity.log("ai.key_saved", f"Connected {PROVIDERS[provider].name} ({result['model']})")
    elif (data.get("model") or "").strip():
        result = _llm_call(lambda: ai_access.change_model(db, cfg(), uid, provider, data["model"]))
        activity.log("ai.model_changed", f"{PROVIDERS[provider].name} model set to {result['model']}")
    else:
        raise ApiError("Paste an API key or choose a model.")
    return jsonify(**_view(), check=result)


@bp.post("/<provider>/activate")
@login_required
def activate(provider):
    _llm_call(lambda: ai_access.activate(get_db(), g.user["id"], _known(provider)))
    activity.log("ai.model_switched", f"Switched to {PROVIDERS[provider].name}")
    return jsonify(_view())


@bp.post("/use-server")
@login_required
def use_server():
    """Stop using an own key; the server's model is used if the policy allows."""
    ai_access.activate(get_db(), g.user["id"], None)
    return jsonify(_view())


@bp.post("/<provider>/test")
@login_required
def test(provider):
    result = _llm_call(lambda: ai_access.test_key(get_db(), cfg(), g.user["id"], _known(provider)))
    return jsonify(**_view(), check=result)


@bp.get("/<provider>/models")
@login_required
def models(provider):
    return jsonify(models=_llm_call(lambda: ai_access.models_for(get_db(), cfg(), g.user["id"], _known(provider))))


@bp.post("/<provider>/models")
@login_required
def models_for_new_key(provider):
    """Models for a key the user has pasted but not saved yet (so they can pick one before saving)."""
    key = (body().get("api_key") or "").strip()
    return jsonify(models=_llm_call(lambda: ai_access.models_for(get_db(), cfg(), g.user["id"], _known(provider), key)))


@bp.delete("/<provider>")
@login_required
def remove(provider):
    _llm_call(lambda: ai_access.remove(get_db(), g.user["id"], _known(provider)))
    activity.log("ai.key_removed", f"Disconnected {PROVIDERS[provider].name}")
    return jsonify(_view())
