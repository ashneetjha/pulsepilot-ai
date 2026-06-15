import logging
import bcrypt
from flask import Blueprint, request, jsonify, session
from models import db, User

logger = logging.getLogger(__name__)
auth_bp = Blueprint("auth", __name__)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    try:
        if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
            return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
        import hashlib
        legacy = hashlib.sha256((password + "xenopilot_salt").encode()).hexdigest()
        return legacy == hashed
    except Exception:
        return False


def user_to_dict(user: User) -> dict:
    return {"id": user.id, "email": user.email}


@auth_bp.post("/auth/signup")
def signup():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    existing = User.query.filter_by(email=email).first()
    if existing:
        return jsonify({"error": "Email already registered"}), 409

    user = User(email=email, password_hash=hash_password(password))
    db.session.add(user)
    db.session.commit()

    session["user_id"] = user.id
    session.permanent = True
    return jsonify({"user": user_to_dict(user)}), 201


@auth_bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not check_password(password, user.password_hash):
        return jsonify({"error": "Invalid email or password"}), 401

    session["user_id"] = user.id
    session.permanent = True
    return jsonify({"user": user_to_dict(user)})


@auth_bp.post("/auth/logout")
def logout():
    session.clear()
    return jsonify({"success": True, "message": "Logged out"})


@auth_bp.get("/auth/me")
def me():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    user = User.query.get(user_id)
    if not user:
        session.clear()
        return jsonify({"error": "Not authenticated"}), 401

    return jsonify(user_to_dict(user))
