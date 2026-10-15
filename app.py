#!/usr/bin/env python3
"""LawPilot entry point.

Development:  python app.py           (http://127.0.0.1:5000)
Production:   gunicorn app:app        (see Procfile)
"""
import os

from lawpilot import create_app

app = create_app()

if __name__ == "__main__":
    # No auto-reloader: it restarts the process on library imports and would kill background imports.
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("LAWPILOT_DEV") == "1", use_reloader=False, threaded=True)
