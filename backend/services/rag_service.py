#rag_service.py

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import requests
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv(".env")

from services.weaviate_service import search_chunks

GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
MODEL_NAME   = "llama-3.3-70b-versatile"
MAX_HISTORY  = 6


def build_context(chunks: list[dict]) -> str:
    """
    Agrupa chunks por libro ordenados por chunk_index.
    El orden entre libros respeta el ranking del retrieval:
    el libro cuyo primer chunk aparece más arriba en la lista va primero.
    Así, si DUNE MESSIAH tiene el chunk más relevante, aparece primero en el contexto.
    """
    # Determinar el orden de los libros según su primer chunk en el ranking
    seen_books: list[str] = []
    groups: dict[str, list] = defaultdict(list)
    for chunk in chunks:
        bid = chunk.get("book_id", "unknown")
        if bid not in groups:
            seen_books.append(bid)  # primer chunk de este libro en el ranking
        groups[bid].append(chunk)

    parts = []
    for bid in seen_books:
        book_chunks = groups[bid]
        book_chunks.sort(key=lambda c: c.get("chunk_index", c.get("summary_index", 0)))
        book_info = (book_chunks[0].get("book") or [{}])[0]
        title    = book_info.get("title",  "Unknown")
        author   = book_info.get("author", "Unknown")
        combined = "\n\n".join(c.get("content", "") for c in book_chunks)
        print(f"  {title} con {len(book_chunks)} chunks")
        parts.append(f"BOOK: {title}\nAUTHOR: {author}\n\n{combined}")

    return "\n\n---\n\n".join(parts)


def build_prompt(context: str, question: str, history=None) -> str:
    """
    Construye el prompt para el LLM.
    Solo incluye preguntas del historial, nunca respuestas anteriores,
    porque las respuestas previas pueden estar mal y el LLM las toma como verdad.
    """
    history_text = ""
    if history:
        recent_questions = [
            t.get("question", "").strip()
            for t in history[-MAX_HISTORY:]
            if t.get("question", "").strip()
        ]
        if recent_questions:
            history_text = (
                "Previous questions in this conversation (for context only):\n"
                + "\n".join(f"- {q}" for q in recent_questions)
                + "\n\n"
            )

    return (
        "You are a literary assistant. Answer questions using ONLY the book fragments below.\n"
        "The fragments may come from multiple books in the same series — use all of them.\n\n"
        "RULES:\n"
        "1. Answer in the SAME language as the question.\n"
        "2. Be direct and concise: 2-3 sentences maximum.\n"
        "3. Base your answer ONLY on the fragments. Never invent or assume facts.\n"
        "4. Read ALL fragments before answering, the answer may be in any of the books.\n"
        "5. If a fragment explicitly describes an event (death, blinding, betrayal), "
        "state it clearly and mention which book it is from. "
        "Do not say it is not mentioned if it appears anywhere in the fragments.\n"
        "6. If the fragments do not contain the answer, say exactly: "
        "'The fragments provided do not cover this.' and nothing more.\n\n"
        f"{history_text}"
        f"--- FRAGMENTS ---\n{context}\n--- END ---\n\n"
        f"Question: {question}\nAnswer:"
    )


def ask_rag_stream(question: str, history=None, prefetched_chunks=None):
    """
    Pipeline RAG completo con streaming token a token.
    Acepta chunks prefetched para no repetir el retrieval si ya se hizo antes.
    """
    print(f"\n{'='*50}")
    print(f"Pregunta: {question}")

    chunks = prefetched_chunks if prefetched_chunks is not None else search_chunks(question, history=history)

    if chunks and chunks[0].get("__ask_user__"):
        yield "No estoy seguro sobre que libro me preguntas. Podrias mencionarlo?"
        return

    if not chunks:
        yield "No tengo informacion sobre eso en los libros cargados."
        return

    print(f"Chunks recuperados: {len(chunks)}")

    ctx    = build_context(chunks)
    prompt = build_prompt(ctx, question, history)
    print(f"Prompt: {len(prompt)} chars usando {MODEL_NAME}")

    if not GROQ_API_KEY:
        yield "Error: GROQ_API_KEY no esta configurada."
        return

    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       MODEL_NAME,
                "messages":    [{"role": "user", "content": prompt}],
                "stream":      True,
                "max_tokens":  400,
                "temperature": 0,
            },
            stream=True,
            timeout=30,
        )

        print(f"Groq status: {response.status_code}")

        if response.status_code == 429:
            yield "El servicio esta ocupado, intenta de nuevo en un momento."
            return

        if response.status_code != 200:
            print(f"Error Groq: {response.text}")
            yield "Error al conectar con el modelo."
            return

        token_count = 0
        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8") if isinstance(line, bytes) else line
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                print(f"Tokens generados: {token_count}")
                break
            try:
                token = json.loads(data_str)["choices"][0]["delta"].get("content", "")
                if token:
                    token_count += 1
                    yield token
            except json.JSONDecodeError:
                continue

    except requests.exceptions.Timeout:
        yield "El modelo tardo demasiado en responder."
    except Exception as e:
        print(f"Error: {e}")
        yield "Error inesperado."


def ask_rag(question: str, history=None) -> str:
    """Version sincrona del pipeline, util para tests o el endpoint GET."""
    return "".join(ask_rag_stream(question, history))