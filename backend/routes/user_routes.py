import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
print("PATH:", sys.path[0])  # agrégalo temporalmente

from flask import Blueprint, request, jsonify, current_app
from middlewares.auth_middleware import require_auth
from models.users import register_user, login_user, deactivate_user, change_password

user_bp = Blueprint("user", __name__, url_prefix="/auth")


@user_bp.route("/register", methods=["POST"])
def register():
    """Body JSON: { "username": "...", "email": "...", "password": "..." }"""
    data = request.get_json(force=True)
    client = current_app.config["WEAVIATE_CLIENT"]
    result = register_user(
        client,
        username=data.get("username", ""),
        email=data.get("email", ""),
        password=data.get("password", ""),
    )
    return jsonify(result), 201 if result["success"] else 400


@user_bp.route("/login", methods=["POST"])
def login():
    """
    Body JSON: { "username": "...", "password": "..." }
    Responde con un JWT que debe enviarse en el header Authorization: Bearer <token>
    """
    data = request.get_json(force=True)
    client = current_app.config["WEAVIATE_CLIENT"]
    result = login_user(
        client,
        username=data.get("username", ""),
        password=data.get("password", ""),
    )
    return jsonify(result), 200 if result["success"] else 401


@user_bp.route("/me", methods=["GET"])
@require_auth
def me(current_user):
    """Devuelve la info del usuario autenticado."""
    return jsonify({"success": True, "user": current_user})


@user_bp.route("/change-password", methods=["POST"])
@require_auth
def change_pwd(current_user):
    """Body JSON: { "old_password": "...", "new_password": "..." }"""
    data = request.get_json(force=True)
    client = current_app.config["WEAVIATE_CLIENT"]
    token = request.headers.get("Authorization", "").split(" ", 1)[1]
    result = change_password(
        client,
        token=token,
        old_password=data.get("old_password", ""),
        new_password=data.get("new_password", ""),
    )
    return jsonify(result), 200 if result["success"] else 400


@user_bp.route("/deactivate", methods=["DELETE"])
@require_auth
def deactivate(current_user):
    """Desactiva la cuenta del usuario autenticado."""
    client = current_app.config["WEAVIATE_CLIENT"]
    token = request.headers.get("Authorization", "").split(" ", 1)[1]
    result = deactivate_user(client, token)
    return jsonify(result)