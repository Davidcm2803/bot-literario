import uuid
import hashlib
import hmac
import os
import jwt
import datetime
import weaviate


#  Configuración JWT
JWT_SECRET = os.environ.get("JWT_SECRET", "cambia_este_secreto_en_produccion")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24  # El token dura 24 horas


#  Hash de contraseña

def _hash_password(password: str) -> str:
    """Genera un hash seguro de la contraseña usando SHA-256 con salt."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    # Guardamos salt + hash juntos en hex para poder verificar después
    return salt.hex() + ":" + key.hex()


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verifica que la contraseña coincide con el hash almacenado."""
    try:
        salt_hex, key_hex = stored_hash.split(":")
        salt = bytes.fromhex(salt_hex)
        key = bytes.fromhex(key_hex)
        new_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return hmac.compare_digest(key, new_key)
    except Exception:
        return False


#  Utilidades JWT, token de autenticacion para manejo de expiracion de sesion

def _generate_token(user_id: str, username: str) -> str:
    """Genera un JWT firmado con expiración."""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    """
    Verifica y decodifica un JWT.
    Devuelve el payload si es válido, None si expiró o es inválido.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


#  Creacion de usuario en Weaviate

def user_exists(client: weaviate.Client, username: str = None, email: str = None) -> bool:
    """Verifica si ya existe un usuario con ese username o email."""
    filters = []

    if username:
        filters.append({"path": ["username"], "operator": "Equal", "valueText": username})
    if email:
        filters.append({"path": ["email"], "operator": "Equal", "valueText": email})

    if not filters:
        return False

    where_filter = filters[0] if len(filters) == 1 else {"operator": "Or", "operands": filters}

    result = (
        client.query
        .get("User", ["username", "email"])
        .with_where(where_filter)
        .with_limit(1)
        .do()
    )
    users = result.get("data", {}).get("Get", {}).get("User", [])
    return len(users) > 0


def _get_user_by_username(client: weaviate.Client, username: str) -> dict | None:
    """Busca un usuario por username y devuelve todos sus campos."""
    result = (
        client.query
        .get("User", ["username", "email", "password_hash", "is_active"])
        .with_where({"path": ["username"], "operator": "Equal", "valueText": username})
        .with_additional(["id"])
        .with_limit(1)
        .do()
    )
    users = result.get("data", {}).get("Get", {}).get("User", [])
    return users[0] if users else None


#  API pública de usuarios

def register_user(client: weaviate.Client, username: str, email: str, password: str) -> dict:
    """
    Registra un nuevo usuario en Weaviate.

    Devuelve:
        {"success": True, "user_id": "...", "username": "..."}
        {"success": False, "error": "mensaje de error"}
    """
    # Validaciones básicas
    if not username or len(username) < 3:
        return {"success": False, "error": "El nombre de usuario debe tener al menos 3 caracteres."}
    if not email or "@" not in email:
        return {"success": False, "error": "Email inválido."}
    if not password or len(password) < 6:
        return {"success": False, "error": "La contraseña debe tener al menos 6 caracteres."}

    # Verificar duplicados
    if user_exists(client, username=username):
        return {"success": False, "error": "El nombre de usuario ya está en uso."}
    if user_exists(client, email=email):
        return {"success": False, "error": "El email ya está registrado."}

    # Crear usuario
    user_id = str(uuid.uuid4())
    password_hash = _hash_password(password)

    client.data_object.create(
        data_object={
            "username": username,
            "email": email,
            "password_hash": password_hash,
            "is_active": True,
        },
        class_name="User",
        uuid=user_id,
    )

    return {"success": True, "user_id": user_id, "username": username}


def login_user(client: weaviate.Client, username: str, password: str) -> dict:
    """
    Autentica a un usuario y devuelve un JWT si las credenciales son correctas.

    Devuelve:
        {"success": True, "token": "...", "username": "...", "user_id": "..."}
        {"success": False, "error": "mensaje de error"}
    """
    if not username or not password:
        return {"success": False, "error": "Usuario y contraseña son requeridos."}

    user = _get_user_by_username(client, username)

    if not user:
        return {"success": False, "error": "Credenciales incorrectas."}

    if not user.get("is_active"):
        return {"success": False, "error": "Cuenta desactivada."}

    if not _verify_password(password, user["password_hash"]):
        return {"success": False, "error": "Credenciales incorrectas."}

    user_id = user["_additional"]["id"]
    token = _generate_token(user_id, username)

    return {
        "success": True,
        "token": token,
        "username": username,
        "user_id": user_id,
        "expires_in": f"{JWT_EXPIRATION_HOURS}h",
    }


def get_current_user(client: weaviate.Client, token: str) -> dict:
    """
    Valida el token JWT y devuelve la información del usuario autenticado.

    Devuelve:
        {"success": True, "user": {...}}
        {"success": False, "error": "Token inválido o expirado"}
    """
    payload = verify_token(token)

    if not payload:
        return {"success": False, "error": "Token inválido o expirado."}

    user = _get_user_by_username(client, payload["username"])

    if not user or not user.get("is_active"):
        return {"success": False, "error": "Usuario no encontrado o inactivo."}

    return {
        "success": True,
        "user": {
            "user_id": payload["user_id"],
            "username": payload["username"],
        },
    }


def deactivate_user(client: weaviate.Client, token: str) -> dict:
    """
    Desactiva la cuenta del usuario autenticado (soft delete).
    El usuario ya no podrá hacer login pero sus datos se conservan.
    """
    payload = verify_token(token)
    if not payload:
        return {"success": False, "error": "Token inválido o expirado."}

    user = _get_user_by_username(client, payload["username"])
    if not user:
        return {"success": False, "error": "Usuario no encontrado."}

    user_id = user["_additional"]["id"]
    client.data_object.update(
        data_object={"is_active": False},
        class_name="User",
        uuid=user_id,
    )

    return {"success": True, "message": "Cuenta desactivada correctamente."}


def change_password(client: weaviate.Client, token: str, old_password: str, new_password: str) -> dict:
    """Cambia la contraseña del usuario autenticado."""
    payload = verify_token(token)
    if not payload:
        return {"success": False, "error": "Token inválido o expirado."}

    if not new_password or len(new_password) < 6:
        return {"success": False, "error": "La nueva contraseña debe tener al menos 6 caracteres."}

    user = _get_user_by_username(client, payload["username"])
    if not user:
        return {"success": False, "error": "Usuario no encontrado."}

    if not _verify_password(old_password, user["password_hash"]):
        return {"success": False, "error": "Contraseña actual incorrecta."}

    user_id = user["_additional"]["id"]
    new_hash = _hash_password(new_password)

    client.data_object.update(
        data_object={"password_hash": new_hash},
        class_name="User",
        uuid=user_id,
    )

    return {"success": True, "message": "Contraseña actualizada correctamente."}