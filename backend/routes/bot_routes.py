import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify, Response, stream_with_context
from services.rag_service import ask_rag, ask_rag_stream

bot_bp = Blueprint("bot", __name__)


@bot_bp.route("/ask", methods=["GET", "POST"])
def ask():
    if request.method == "GET":
        question = request.args.get("question", "").strip()
        if not question:
            return jsonify({"error": "No se envió pregunta"}), 400
        answer = ask_rag(question)
        return jsonify({"answer": answer})

    data = request.json
    question = data.get("question", "").strip()
    history = data.get("history", [])  

    if not question:
        return jsonify({"error": "No se envió pregunta"}), 400

    def generate():
        for token in ask_rag_stream(question, history):
            yield token.encode("utf-8")

    return Response(
        stream_with_context(generate()),
        content_type="text/plain; charset=utf-8",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        }
    )