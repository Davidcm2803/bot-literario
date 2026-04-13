import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
print("PATH:", sys.path[0])

from flask import Blueprint, request, jsonify, current_app
from middlewares.auth_middleware import require_auth
from models.users import register_user, login_user, deactivate_user, change_password

user_bp = Blueprint("user", __name__, url_prefix="/auth")


# Registra un nuevo usuario recibiendo username, email y password en el cuerpo JSON
@user_bp.route("/register", methods=["POST"])
def register():
    data   = request.get_json(force=True)
    client = current_app.config["WEAVIATE_CLIENT"]
    result = register_user(
        client,
        username=data.get("username", ""),
        email=data.get("email",    ""),
        password=data.get("password", ""),
    )
    return jsonify(result), 201 if result["success"] else 400


# Autentica un usuario y devuelve un JWT que debe enviarse en el header Authorization Bearer
@user_bp.route("/login", methods=["POST"])
def login():
    data   = request.get_json(force=True)
    client = current_app.config["WEAVIATE_CLIENT"]
    result = login_user(
        client,
        username=data.get("username", ""),
        password=data.get("password", ""),
    )
    return jsonify(result), 200 if result["success"] else 401


# Devuelve los datos del usuario autenticado usando el token del header
@user_bp.route("/me", methods=["GET"])
@require_auth
def me(current_user):
    return jsonify({"success": True, "user": current_user})


# Cambia la contrasena del usuario autenticado validando primero la contrasena actual
@user_bp.route("/change-password", methods=["POST"])
@require_auth
def change_pwd(current_user):
    data   = request.get_json(force=True)
    client = current_app.config["WEAVIATE_CLIENT"]
    # Extrae el token del header Authorization para pasarlo al modelo
    token  = request.headers.get("Authorization", "").split(" ", 1)[1]
    result = change_password(
        client,
        token=token,
        old_password=data.get("old_password", ""),
        new_password=data.get("new_password", ""),
    )
    return jsonify(result), 200 if result["success"] else 400


# Desactiva la cuenta del usuario autenticado de forma permanente
@user_bp.route("/deactivate", methods=["DELETE"])
@require_auth
def deactivate(current_user):
    client = current_app.config["WEAVIATE_CLIENT"]
    token  = request.headers.get("Authorization", "").split(" ", 1)[1]
    result = deactivate_user(client, token)
    return jsonify(result)