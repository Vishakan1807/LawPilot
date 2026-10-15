"""LawPilot - legal research and practice management for Indian advocates."""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from lawpilot.config import Config

__version__ = "2.0.0"

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(config: Config = None) -> Flask:
    cfg = config or Config.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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

    @app.before_request
    def _before():
        if request.path.startswith("/api/"):
            # CSRF defence: browsers cannot add custom headers cross-site without a CORS preflight,
            # and this app never enables CORS.
            if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Requested-With") != "LawPilot":
                return jsonify(error="Missing request header."), 400
            load_current_user()

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
        app.logger.exception("Unhandled error on %s", request.path)
        if request.path.startswith("/api/"):
            return jsonify(error="Something went wrong on the server. The error has been logged."), 500
        return "Internal server error", 500

    from lawpilot.routes import register_blueprints
    register_blueprints(app)

    @app.get("/health")
    def health():
        return jsonify(status="ok", version=__version__)

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
