from flask import Flask, request, jsonify
from flask_cors import CORS
from functools import wraps
import weaviate

from models.schema import create_schema
from models.books import upload_all_books, search_books, list_books, delete_book
from models.users import register_user, login_user, get_current_user, deactivate_user, change_password, verify_token 

app = Flask(__name__)
CORS(app)

client = weaviate.Client(
    url="http://localhost:8080",
    timeout_config=(5, 15)
)
create_schema(client)

# Cargar libros automáticamente al iniciar
print("Cargando libros...")
upload_all_books(client, books_folder="books")
print("Libros cargados.")


#  Autenticación
def require_auth(f):
    """Protege rutas que necesitan un usuario autenticado. Inyecta `current_user` en kwargs."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Token requerido"}), 401
        token = auth_header.split(" ", 1)[1]
        result = get_current_user(client, token)
        if not result["success"]:
            return jsonify({"error": result["error"]}), 401
        kwargs["current_user"] = result["user"]
        return f(*args, **kwargs)
    return decorated


# Rutas generales
@app.route("/")
def home():
    return jsonify({"message": "Bot Literario con Weaviate"})


@app.route("/init-db")
def init_db():
    create_schema(client)
    return jsonify({"message": "Schema creado/verificado"})


# Rutas de usuarios
@app.route("/auth/register", methods=["POST"])
def register():
    """
    Body JSON: { "username": "...", "email": "...", "password": "..." }
    """
    data = request.get_json(force=True)
    result = register_user(
        client,
        username=data.get("username", ""),
        email=data.get("email", ""),
        password=data.get("password", ""),
    )
    status = 201 if result["success"] else 400
    return jsonify(result), status


@app.route("/auth/login", methods=["POST"])
def login():
    """
    Body JSON: { "username": "...", "password": "..." }
    Responde con un JWT que debe enviarse en el header Authorization: Bearer <token>
    """
    data = request.get_json(force=True)
    result = login_user(
        client,
        username=data.get("username", ""),
        password=data.get("password", ""),
    )
    status = 200 if result["success"] else 401
    return jsonify(result), status


@app.route("/auth/me", methods=["GET"])
@require_auth
def me(current_user):
    """Devuelve la info del usuario autenticado."""
    return jsonify({"success": True, "user": current_user})


@app.route("/auth/change-password", methods=["POST"])
@require_auth
def change_pwd(current_user):
    """
    Body JSON: { "old_password": "...", "new_password": "..." }
    """
    data = request.get_json(force=True)
    token = request.headers.get("Authorization", "").split(" ", 1)[1]
    result = change_password(
        client,
        token=token,
        old_password=data.get("old_password", ""),
        new_password=data.get("new_password", ""),
    )
    status = 200 if result["success"] else 400
    return jsonify(result), status


@app.route("/auth/deactivate", methods=["DELETE"])
@require_auth
def deactivate(current_user):
    """Desactiva la cuenta del usuario autenticado."""
    token = request.headers.get("Authorization", "").split(" ", 1)[1]
    result = deactivate_user(client, token)
    return jsonify(result)


# Rutas de libros
@app.route("/books/upload", methods=["POST"])
@require_auth
def upload_books(current_user):
    """
    Carga todos los .txt de la carpeta /books a Weaviate.
    Solo disponible para usuarios autenticados.
    """
    try:
        results = upload_all_books(client, books_folder="books")
        uploaded = sum(1 for r in results if r["status"] == "uploaded")
        skipped = sum(1 for r in results if r["status"] == "skipped")
        errors = sum(1 for r in results if r["status"] == "error")
        return jsonify({
            "summary": {"uploaded": uploaded, "skipped": skipped, "errors": errors},
            "details": results,
        })
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404


@app.route("/books", methods=["GET"])
@require_auth
def get_books(current_user):
    """Lista todos los libros en la base de datos."""
    books = list_books(client)
    return jsonify({"books": books, "total": len(books)})


@app.route("/books/<book_id>", methods=["DELETE"])
@require_auth
def remove_book(book_id, current_user):
    """Elimina un libro y sus chunks por ID."""
    try:
        delete_book(client, book_id)
        return jsonify({"success": True, "message": f"Libro {book_id} eliminado."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Ruta de búsqueda
@app.route("/ask", methods=["GET"])
def ask():
    question = request.args.get("q", "").strip()
    if not question:
        return jsonify({"error": "No se envió pregunta"}), 400

    chunks = search_books(client, question, limit=5)
    return jsonify({"question": question, "results": chunks})


if __name__ == "__main__":
    app.run(port=8090, debug=True)