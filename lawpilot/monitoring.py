"""Error monitoring (Sentry), request IDs and health checks.

Sentry is optional: nothing is sent unless SENTRY_DSN is set. Because LawPilot handles
confidential legal work, events are scrubbed before they leave the server: no request bodies,
query strings, cookies, headers or local variables (which could hold client documents or
questions), and no email/IP. What remains is the error, the stack trace (code only), the
route, a request ID and the internal user id, which is enough to find and fix a bug.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

log = logging.getLogger("lawpilot.monitoring")

_enabled = False
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def init(cfg) -> bool:
    """Start Sentry if a DSN is configured. Returns True when monitoring is on."""
    global _enabled
    if not getattr(cfg, "sentry_dsn", ""):
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except ImportError:
        log.error("SENTRY_DSN is set but sentry-sdk is not installed: pip install 'sentry-sdk[flask]'")
        return False
    sentry_sdk.init(
        dsn=cfg.sentry_dsn,
        integrations=[FlaskIntegration()],
        environment=cfg.sentry_environment or "production",
        release=cfg.release or None,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=max(0.0, min(1.0, cfg.sentry_traces_sample_rate)),
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
    )
    _enabled = True
    log.info("Error monitoring enabled (environment=%s)", cfg.sentry_environment)
    return True


def enabled() -> bool:
    return _enabled


def scrub_event(event: dict, hint=None) -> dict:
    """Remove anything that could contain client data before an event leaves the server."""
    req = event.get("request")
    if isinstance(req, dict):
        event["request"] = {k: req[k] for k in ("method", "url") if k in req}
        if "url" in event["request"]:
            event["request"]["url"] = str(event["request"]["url"]).split("?", 1)[0]
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user["id"]} if user.get("id") else {}
    for frame_holder in _exception_frames(event):
        for frame in frame_holder:
            frame.pop("vars", None)
    return event


def _exception_frames(event: dict):
    for exc in ((event.get("exception") or {}).get("values") or []):
        frames = ((exc.get("stacktrace") or {}).get("frames")) or []
        if frames:
            yield frames


def scrub_breadcrumb(crumb: dict, hint=None) -> Optional[dict]:
    """Keep navigation-level breadcrumbs only; drop SQL and HTTP payload details."""
    if crumb.get("category") in ("query", "httplib", "subprocess"):
        data = crumb.get("data") or {}
        crumb["data"] = {k: data[k] for k in ("method", "status_code") if k in data}
        if crumb.get("category") == "query":
            crumb["message"] = "SQL query"
    return crumb


def request_id(incoming: Optional[str]) -> str:
    """Use the platform's request id when it looks safe (Vercel sends one), else make one."""
    if incoming and _REQUEST_ID_RE.match(incoming):
        return incoming
    return uuid.uuid4().hex[:16]


def tag_request(request_id_value: str, user_id: Optional[str]) -> None:
    if not _enabled:
        return
    import sentry_sdk
    sentry_sdk.set_tag("request_id", request_id_value)
    sentry_sdk.set_user({"id": user_id} if user_id else None)


def capture_exception(exc: BaseException) -> None:
    if _enabled:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)


def capture_browser_error(message: str, source: str, line: Optional[int], stack: str, path: str) -> None:
    """A JavaScript error reported by a user's browser."""
    if not _enabled:
        return
    import sentry_sdk
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("origin", "browser")
        scope.set_tag("page", path)
        scope.set_context("browser_error", {"source": source, "line": line, "stack": stack})
        sentry_sdk.capture_message(f"Browser error: {message}", level="error")
