import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, Response, stream_with_context
from services.rag_service import ask_rag, ask_rag_stream

bot_bp = Blueprint("bot", __name__)


# Endpoint principal que recibe preguntas del usuario via GET o POST
@bot_bp.route("/ask", methods=["GET", "POST"])
def ask():
    # En GET la pregunta viene como parametro en la URL
    if request.method == "GET":
        question = request.args.get("question", "").strip()
        if not question:
            return jsonify({"error": "No se envió pregunta"}), 400
        answer = ask_rag(question)
        return jsonify({"answer": answer})

    # En POST la pregunta e historial vienen en el cuerpo JSON
    data     = request.json
    question = data.get("question", "").strip()
    history  = data.get("history", [])

    if not question:
        return jsonify({"error": "No se envió pregunta"}), 400

    # Antes de iniciar el stream se hace una busqueda previa de chunks para detectar
    # si el pipeline necesita que el usuario aclare sobre que libro quiere preguntar.
    # Si se detecta el caso especial devuelve JSON con la lista de libros en vez de stream.
    # El frontend distingue los dos casos por el Content-Type de la respuesta:
    #   application/json significa que hay que mostrar el selector de libros
    #   text/plain significa que es un stream normal de tokens
    from services.weaviate_service import search_chunks
    from models.books import list_books
    from flask import current_app

    chunks = search_chunks(question, history=history)

    # Si el primer chunk tiene la bandera especial se devuelve la lista de libros disponibles
    if chunks and chunks[0].get("__ask_user__"):
        client = current_app.config["WEAVIATE_CLIENT"]
        books  = list_books(client)
        book_list = [
            {
                "id":     b.get("_additional", {}).get("id", ""),
                "title":  b.get("title",  "Unknown"),
                "author": b.get("author", "Unknown"),
            }
            for b in books
        ]
        return jsonify({"type": "book_select", "books": book_list})

    # El retrieval ya se hizo arriba asi que se pasan los chunks directamente al stream
    def generate():
        for token in ask_rag_stream(question, history, prefetched_chunks=chunks):
            yield token.encode("utf-8")

    # Devuelve la respuesta como stream de texto plano desactivando el buffer del proxy
    return Response(
        stream_with_context(generate()),
        content_type="text/plain; charset=utf-8",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control":     "no-cache",
        }
    )