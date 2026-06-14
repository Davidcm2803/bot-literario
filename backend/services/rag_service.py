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


# Agrupa chunks por libro ordenados por indice; el libro con mayor score queda primero
def build_context(chunks: list[dict]) -> str:
    seen_books: list[str] = []
    groups: dict[str, list] = defaultdict(list)
    for chunk in chunks:
        bid = chunk.get("book_id", "unknown")
        if bid not in groups:
            seen_books.append(bid)
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


# Construye el prompt final; solo incluye preguntas anteriores, nunca respuestas
# para evitar que el LLM tome como verdad respuestas previas que pueden estar mal
def build_prompt(context: str, question: str, history=None) -> str:
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

    # Detecta si la pregunta involucra multiples libros para activar la regla de comparacion
    books_in_context = set()
    for line in context.splitlines():
        if line.startswith("BOOK: "):
            books_in_context.add(line[6:].strip())

    comparison_rule = ""
    if len(books_in_context) >= 2:
        comparison_rule = (
            "5b. If the question compares two books or characters from different books, "
            "structure your answer addressing both sides explicitly. "
            "Do not skip one side because it has fewer fragments.\n"
        )

    return (
        "You are a literary assistant. Answer questions using ONLY the book fragments below.\n\n"
        "RULES:\n"
        "1. Answer in the SAME language as the question.\n"
        "2. Be direct and concise: 2-3 sentences maximum.\n"
        "3. Base your answer ONLY on the fragments. Never invent or assume facts.\n"
        "4. Read ALL fragments before answering, the answer may be near the end.\n"
        "5. If a fragment explicitly describes an event (death, blinding, betrayal), "
        "state it clearly. Do not say it is not mentioned if it appears anywhere.\n"
        f"{comparison_rule}"
        "6. If the fragments do not contain the answer, say exactly: "
        "'The fragments provided do not cover this.' and nothing more.\n\n"
        f"{history_text}"
        f"--- FRAGMENTS ---\n{context}\n--- END ---\n\n"
        f"Question: {question}\nAnswer:"
    )


# Pipeline RAG completo con streaming; acepta chunks prefetched para no repetir el retrieval
def ask_rag_stream(question: str, history=None, prefetched_chunks=None):
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


# Version sincrona del pipeline, util para tests o endpoints GET
def ask_rag(question: str, history=None) -> str:
    return "".join(ask_rag_stream(question, history))