"""LawPilot - legal research and practice management for Indian advocates."""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from lawpilot.config import Config

__version__ = "2.0.0"

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(config: Config = None) -> Flask:
    cfg = config or Config.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from lawpilot import monitoring
    monitoring.init(cfg)

    app = Flask(__name__, static_folder=None)
    app.config["LP"] = cfg
    app.config["SECRET_KEY"] = cfg.secret_key
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_upload_mb * 1024 * 1024
    app.config["JSON_SORT_KEYS"] = False

    from lawpilot import db as dbmod

    conn = dbmod.connect(cfg)
    try:
        dbmod.init_schema(conn)
        # Imports that stopped sending heartbeats died with their process (restart, crash). An import
        # running elsewhere (e.g. from the command line) keeps beating and is left alone.
        from lawpilot.importers import close_stale_runs
        close_stale_runs(conn)
        if cfg.seed_summaries and not getattr(cfg, "skip_seed", False):
            from lawpilot.seed import seed_corpus
            seed_corpus(conn, cfg)
    finally:
        conn.close()

    app.teardown_appcontext(dbmod.close_db)

    from lawpilot.auth import ApiError, load_current_user
    # While an account still has its emailed temporary password, only these calls are allowed.
    PASSWORD_CHANGE_PATHS = {"/api/auth/me", "/api/auth/logout", "/api/auth/set-password"}

    @app.before_request
    def _before():
        g.request_id = monitoring.request_id(request.headers.get("X-Request-ID") or request.headers.get("X-Vercel-Id"))
        if request.path.startswith("/api/"):
            # CSRF defence: browsers cannot add custom headers cross-site without a CORS preflight,
            # and this app never enables CORS.
            if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Requested-With") != "LawPilot":
                return jsonify(error="Missing request header."), 400
            load_current_user()
            user = g.get("user")
            monitoring.tag_request(g.request_id, user["id"] if user else None)
            if user and user.get("must_change_password") and request.path not in PASSWORD_CHANGE_PATHS:
                return jsonify(error="Choose your own password to continue.", code="password_change_required"), 403

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        if g.get("request_id"):
            resp.headers.setdefault("X-Request-ID", g.request_id)
        return resp

    @app.errorhandler(ApiError)
    def _api_error(err):
        return jsonify(error=err.message), err.status

    @app.errorhandler(HTTPException)
    def _http_error(err):
        if request.path.startswith("/api/"):
            msg = (f"File is too large (limit {cfg.max_upload_mb} MB on this server)." if err.code == 413
                   else err.description)
            return jsonify(error=msg), err.code
        return err

    @app.errorhandler(Exception)
    def _unhandled(err):
        rid = g.get("request_id", "")
        app.logger.exception("Unhandled error on %s [request %s]", request.path, rid)
        monitoring.capture_exception(err)
        if request.path.startswith("/api/"):
            return jsonify(error=f"Something went wrong on the server. The error has been logged (reference {rid}).",
                           request_id=rid), 500
        return f"Internal server error (reference {rid})", 500

    from lawpilot.routes import register_blueprints
    register_blueprints(app)

    @app.get("/health")
    def health():
        """Liveness: the app is running (no database call, so it is cheap to poll)."""
        return jsonify(status="ok", version=__version__)

    @app.get("/health/ready")
    def ready():
        """Readiness, for uptime monitors: the app can reach its database."""
        try:
            dbmod.get_db().execute("SELECT 1").fetchone()
        except Exception:
            app.logger.exception("Readiness check failed")
            return jsonify(status="unavailable", database="unreachable", version=__version__), 503
        return jsonify(status="ok", database="ok", version=__version__,
                       monitoring=monitoring.enabled(), release=cfg.release or None)

    @app.get("/")
    @app.get("/<path:path>")
    def spa(path: str = ""):
        if path.startswith("api/"):
            return jsonify(error="Not found."), 404
        target = STATIC_DIR / path
        if path and target.is_file() and STATIC_DIR in target.resolve().parents:
            return send_from_directory(STATIC_DIR, path)
        return send_from_directory(STATIC_DIR, "index.html")

    return app
