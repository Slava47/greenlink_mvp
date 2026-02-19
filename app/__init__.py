import secrets

from flask import Flask, session, request, abort
from markupsafe import Markup
from .config import Config
from .db import init_db_if_needed, close_db
from .routes import bp as main_bp

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # --- Minimal CSRF protection (session-based) ---
    def _csrf_token() -> str:
        tok = session.get("csrf_token")
        if not tok:
            tok = secrets.token_urlsafe(32)
            session["csrf_token"] = tok
        return tok

    @app.before_request
    def _csrf_protect():
        # Only protect state-changing requests
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not token or token != session.get("csrf_token"):
                abort(400)

    @app.context_processor
    def _inject_csrf():
        tok = _csrf_token()
        return {
            "csrf_token": tok,
            "csrf_field": lambda: Markup(f'<input type="hidden" name="_csrf" value="{tok}">'),
        }

    app.register_blueprint(main_bp)

    # DB lifecycle
    init_db_if_needed(app)
    app.teardown_appcontext(close_db)
    return app
