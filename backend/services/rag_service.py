import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import requests
from collections import defaultdict
from dotenv import load_dotenv

# Carga variables de entorno desde key.env
load_dotenv("key.env")

from services.weaviate_service import search_chunks, detect_mentioned_book_ids

# Configuración del modelo Groq
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
MODEL_NAME   = "llama-3.3-70b-versatile"

# Límites del sistema
MAX_HISTORY       = 10
MAX_CONTEXT_CHARS = 18000


# Convierte score BM25 (mayor=mejor) a "distancia" (menor=mejor)
def _bm25_to_dist(bm25_score: float) -> float:
    normalized = min(bm25_score / 5.0, 1.0)
    return 0.50 - (normalized * 0.40)


# Selecciona los mejores chunks combinando semántico, BM25 y vecinos
def select_best_chunks(chunks: list[dict],
                       max_chars: int = MAX_CONTEXT_CHARS,
                       mentioned_books: set | None = None) -> list[dict]:

    if mentioned_books is None:
        mentioned_books = set()

    BOOK_BOOST = -0.15  # prioriza libro mencionado
    NEUTRAL    =  0.50  # score base

    # Indexa chunks por (book_id, chunk_index)
    chunk_map: dict[tuple, dict] = {
        (c.get("book_id"), c.get("chunk_index")): c for c in chunks
    }

    # Paso 1: calcular score inicial
    scored: dict[tuple, float] = {}

    for c in chunks:
        key     = (c.get("book_id"), c.get("chunk_index"))
        book_id = c.get("book_id", "")
        boost   = BOOK_BOOST if book_id in mentioned_books else 0.0

        add  = c.get("_additional") or {}
        dist = add.get("distance")
        bm25 = add.get("score")

        if dist is not None:
            scored[key] = dist + boost
        elif bm25 is not None:
            scored[key] = _bm25_to_dist(float(bm25)) + boost

    # Paso 2: propagar score a vecinos cercanos
    inherited: dict[tuple, float] = {}

    for key, score in scored.items():
        book_id, idx = key
        if idx is None:
            continue

        for offset in [-2, -1, 1, 2]:
            nkey = (book_id, idx + offset)

            if nkey in chunk_map and nkey not in scored:
                candidate = score + abs(offset) * 0.02

                if nkey not in inherited or candidate < inherited[nkey]:
                    inherited[nkey] = candidate

    scored.update(inherited)

    # Paso 3: asignar score neutro a lo restante
    for key, c in chunk_map.items():
        if key not in scored:
            book_id = c.get("book_id", "")
            boost   = BOOK_BOOST if book_id in mentioned_books else 0.0
            scored[key] = NEUTRAL + boost

    # Ordenar por mejor score y limitar por tamaño total
    all_scored = sorted(
        [(score, chunk_map[key]) for key, score in scored.items()],
        key=lambda x: x[0]
    )

    selected, seen, total = [], set(), 0

    for score, c in all_scored:
        key     = (c.get("book_id"), c.get("chunk_index"))
        content = c.get("content", "")

        if key in seen or total + len(content) > max_chars:
            continue

        seen.add(key)
        selected.append(c)
        total += len(content)

    print(f"  ✂️  Seleccionados {len(selected)}/{len(chunks)} chunks ({total} chars)")
    return selected


# Construye el contexto agrupando chunks por libro
def build_context(chunks: list[dict]) -> str:
    groups: dict[str, list] = defaultdict(list)

    for chunk in chunks:
        groups[chunk.get("book_id", "unknown")].append(chunk)

    parts = []

    for book_id, book_chunks in groups.items():
        book_chunks.sort(key=lambda c: c.get("chunk_index", 0))

        book_info = (book_chunks[0].get("book") or [{}])[0]
        title     = book_info.get("title", "Unknown")
        author    = book_info.get("author", "Unknown")

        combined = "\n\n".join(c.get("content", "") for c in book_chunks)
        indices  = [c.get("chunk_index", "?") for c in book_chunks]

        print(f"  📖 {title} — chunks {indices}")

        parts.append(
            f"BOOK: {title}\nAUTHOR: {author}\nCONTENT:\n{combined}\n"
        )

    return "\n---\n".join(parts)


# Construye el prompt final incluyendo contexto + historial
def build_prompt(context: str, question: str, history=None) -> str:
    history_text = ""

    if history:
        history_text = "Conversation history:\n"
        for turn in history[-MAX_HISTORY:]:
            history_text += f"User: {turn['question']}\n"
            history_text += f"Assistant: {turn['answer']}\n"
        history_text += "\n"

    return f"""You are a literary assistant with deep knowledge of the loaded books.
Answer the user's question using the fragments provided below.

RULES:
- Base your answer on what the fragments say.
- Do NOT invent information.
- Answer in the same language as the user.
- If no info exists, say: "No tengo suficiente información en los libros cargados para responder esa pregunta."
- Be concise (2-4 sentences).

{history_text}
--- FRAGMENTS ---
{context}
--- END OF FRAGMENTS ---

Question: {question}
Answer:"""


# Ejecuta RAG con streaming (respuesta en tiempo real)
def ask_rag_stream(question: str, history=None):
    print(f"\n{'='*50}")
    print(f"📥 Pregunta: {question}")

    # Recupera chunks desde Weaviate
    chunks = search_chunks(question, history=history)
    print(f"🔍 Total chunks recuperados: {len(chunks)}")

    if not chunks:
        yield "No tengo información sobre eso en los libros cargados."
        return

    # Debug de chunks
    for i, c in enumerate(chunks):
        book_info = (c.get("book") or [{}])[0]
        add       = (c.get("_additional") or {})
        score     = add.get("distance", add.get("score", "?"))

        print(f"  Chunk {i+1}: [{c.get('chunk_index','?')}] "
              f"{book_info.get('title','?')} | score: {score}")

    # Detecta si el usuario mencionó un libro específico
    from flask import current_app
    client          = current_app.config["WEAVIATE_CLIENT"]
    mentioned_books = set(detect_mentioned_book_ids(question, client))

    # Selección y construcción de contexto
    best   = select_best_chunks(chunks, mentioned_books=mentioned_books)
    ctx    = build_context(best)
    prompt = build_prompt(ctx, question, history)

    print(f"📝 Prompt ({len(prompt)} chars) → Groq [{MODEL_NAME}]")

    if not GROQ_API_KEY:
        yield "Error: GROQ_API_KEY no está configurada"
        return

    # Llamada al modelo con streaming
    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "max_tokens": 350,
                "temperature": 0.2
            },
            stream=True,
            timeout=30,
        )

        print(f"🤖 Groq status: {response.status_code}")

        if response.status_code == 429:
            yield "El servicio está ocupado, intenta de nuevo."
            return

        if response.status_code != 200:
            print(f"❌ Error Groq: {response.text}")
            yield "Error al conectar con el modelo."
            return

        # Procesa tokens en streaming
        token_count = 0

        for line in response.iter_lines():
            if not line:
                continue

            line = line.decode("utf-8") if isinstance(line, bytes) else line

            if not line.startswith("data: "):
                continue

            data_str = line[6:]

            if data_str == "[DONE]":
                print(f"✅ Stream completo — {token_count} tokens")
                break

            try:
                token = json.loads(data_str)["choices"][0]["delta"].get("content", "")
                if token:
                    token_count += 1
                    yield token
            except json.JSONDecodeError:
                continue

    except requests.exceptions.Timeout:
        yield "El modelo tardó demasiado en responder."
    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        yield "Error inesperado."


# Versión sin streaming (retorna string completo)
def ask_rag(question: str, history=None) -> str:
    return "".join(ask_rag_stream(question, history))