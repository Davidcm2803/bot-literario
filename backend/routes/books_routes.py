import sys
import os
import random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, current_app
from models.books import upload_all_books, search_books, list_books, delete_book

books_bp = Blueprint("books", __name__)


# Carga todos los archivos txt de la carpeta books a Weaviate y devuelve un resumen del proceso
@books_bp.route("/books/upload", methods=["POST"])
def upload_books():
    client = current_app.config["WEAVIATE_CLIENT"]
    try:
        results  = upload_all_books(client, books_folder="books")
        uploaded = sum(1 for r in results if r["status"] == "uploaded")
        skipped  = sum(1 for r in results if r["status"] == "skipped")
        errors   = sum(1 for r in results if r["status"] == "error")
        return jsonify({
            "summary": {"uploaded": uploaded, "skipped": skipped, "errors": errors},
            "details": results,
        })
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404


# Alias de books/upload incluido para cumplir con el requerimiento del proyecto
@books_bp.route("/load", methods=["GET", "POST"])
def load_books():
    client = current_app.config["WEAVIATE_CLIENT"]
    try:
        results  = upload_all_books(client, books_folder="books")
        uploaded = sum(1 for r in results if r["status"] == "uploaded")
        skipped  = sum(1 for r in results if r["status"] == "skipped")
        errors   = sum(1 for r in results if r["status"] == "error")
        return jsonify({
            "summary": {"uploaded": uploaded, "skipped": skipped, "errors": errors},
            "details": results,
        })
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404


# Devuelve la lista completa de libros almacenados en la base de datos
@books_bp.route("/books", methods=["GET"])
def get_books():
    client = current_app.config["WEAVIATE_CLIENT"]
    books  = list_books(client)
    return jsonify({"books": books, "total": len(books)})


# Elimina un libro especifico junto con todos sus chunks usando su ID
@books_bp.route("/books/<book_id>", methods=["DELETE"])
def remove_book(book_id):
    client = current_app.config["WEAVIATE_CLIENT"]
    try:
        delete_book(client, book_id)
        return jsonify({"success": True, "message": f"Libro {book_id} eliminado."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Realiza una busqueda semantica sobre los libros cargados y devuelve los chunks mas relevantes
@books_bp.route("/ask", methods=["GET"])
def ask():
    client   = current_app.config["WEAVIATE_CLIENT"]
    question = request.args.get("q", "").strip()
    if not question:
        return jsonify({"error": "No se envió pregunta"}), 400

    chunks = search_books(client, question, limit=5)
    return jsonify({"question": question, "results": chunks})


# Genera y devuelve cinco preguntas sugeridas basadas en los titulos y autores cargados
@books_bp.route("/questions", methods=["GET"])
def suggested_questions():
    client = current_app.config["WEAVIATE_CLIENT"]
    books  = list_books(client)

    questions = []
    for book in books:
        title  = book.get("title",  "")
        author = book.get("author", "")
        questions.append(f"¿Quién escribió {title}?")
        questions.append(f"¿De qué trata {title}?")
        questions.append(f"¿Cuál es el tema principal de {title}?")
        questions.append(f"¿Qué pasa al final de {title}?")
        questions.append(f"¿En qué época acontece la historia de {title}?")
        questions.append(f"¿Cuál es el personaje principal de {title}?")

    # Mezcla la lista y devuelve solo las primeras cinco para variar en cada llamada
    random.shuffle(questions)
    return jsonify({"questions": questions[:5]})