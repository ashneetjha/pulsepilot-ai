import logging
import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
# Load .env from the flask-api directory (same folder as app.py)
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv()

from models import db
from routes.auth import auth_bp
from routes.customers import customers_bp
from routes.campaigns import campaigns_bp
from routes.dashboard import dashboard_bp
from routes.ai_routes import ai_bp
from routes.receipts import receipts_bp
from routes.pages import pages_bp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
PORT = int(os.environ.get("PORT", 8080))

if not SESSION_SECRET:
    logger.warning("SESSION_SECRET is not set — using ephemeral random secret (sessions will not persist across restarts)")
    SESSION_SECRET = secrets.token_hex(32)
if not os.environ.get("GEMINI_API_KEY"):
    logger.warning("GEMINI_API_KEY is not set — AI features will be unavailable")


def _get_db_uri() -> str:
    instance_path = os.path.join(os.path.dirname(__file__), "instance")
    os.makedirs(instance_path, exist_ok=True)
    return f"sqlite:///{os.path.join(instance_path, 'xenopilot.db')}"


def _init_db(app: Flask) -> None:
    with app.app_context():
        db.create_all()
        try:
            from seed import seed_database
            seed_database()
        except Exception as e:
            logger.error(f"Seed failed: {e}")


def create_app() -> Flask:
    app = Flask(__name__)

    app.config["SECRET_KEY"] = SESSION_SECRET
    app.config["SQLALCHEMY_DATABASE_URI"] = _get_db_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False},
    }
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_PATH"] = "/"

    db.init_app(app)

    CORS(app, supports_credentials=True)

    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(customers_bp, url_prefix="/api")
    app.register_blueprint(campaigns_bp, url_prefix="/api")
    app.register_blueprint(dashboard_bp, url_prefix="/api")
    app.register_blueprint(ai_bp, url_prefix="/api")
    app.register_blueprint(receipts_bp, url_prefix="/api")
    app.register_blueprint(pages_bp)

    @app.get("/api/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api"):
            return jsonify({"error": "Not found"}), 404
        return render_template("login.html", error=""), 404

    @app.errorhandler(500)
    def internal_error(e):
        logger.error(f"Internal server error: {e}")
        return jsonify({"error": "Internal server error"}), 500

    return app


app = create_app()
_init_db(app)

if __name__ == "__main__":
    logger.info(f"Starting XenoPilot Flask API on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
