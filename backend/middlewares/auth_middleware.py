import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from functools import wraps
from flask import request, jsonify, current_app
from models.users import get_current_user


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        # verifica que el header tenga el formato Bearer token
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Token requerido"}), 401
        token = auth_header.split(" ", 1)[1]
        # obtiene el cliente de weaviate desde la config de flask
        client = current_app.config["WEAVIATE_CLIENT"]
        # busca el usuario asociado al token
        result = get_current_user(client, token)
        if not result["success"]:
            return jsonify({"error": result["error"]}), 401
        # agrega el usuario actual a los argumentos de la ruta
        kwargs["current_user"] = result["user"]

        return f(*args, **kwargs)

    return decorated