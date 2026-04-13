import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import requests
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv("key.env")

from services.weaviate_service import search_chunks

# URL y clave para la API de Groq
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Modelo principal, bajar a llama-3.1-8b-instant si se necesita mas velocidad
MODEL_NAME = "llama-3.3-70b-versatile"

# Numero de turnos del historial que se incluyen en el prompt
MAX_HISTORY = 6


# Convierte la lista de chunks en texto legible agrupado por libro y ordenado por indice
# Soporta tanto BookChunk con chunk_index como BookSummary con summary_index
def build_context(chunks: list[dict]) -> str:
    groups: dict[str, list] = defaultdict(list)
    for chunk in chunks:
        groups[chunk.get("book_id", "unknown")].append(chunk)

    parts = []
    for bid, book_chunks in groups.items():
        book_chunks.sort(key=lambda c: c.get("chunk_index",
                                              c.get("summary_index", 0)))
        book_info = (book_chunks[0].get("book") or [{}])[0]
        title     = book_info.get("title",  "Unknown")
        author    = book_info.get("author", "Unknown")
        combined  = "\n\n".join(c.get("content", "") for c in book_chunks)
        print(f"  {title} con {len(book_chunks)} chunks")
        parts.append(f"BOOK: {title}\nAUTHOR: {author}\n\n{combined}")

    return "\n\n---\n\n".join(parts)


# Construye el prompt completo con historial, contexto y pregunta del usuario
# El historial permite al LLM entender el flujo de la conversacion
# La instruccion de idioma hace que responda en el mismo idioma que la pregunta
# El mensaje de fallback evita alucinaciones cuando el contexto no contiene la respuesta
def build_prompt(context: str, question: str, history=None) -> str:
    history_text = ""
    if history:
        history_text = "Recent conversation:\n"
        for turn in history[-MAX_HISTORY:]:
            history_text += f"User: {turn['question']}\nAssistant: {turn['answer']}\n"
        history_text += "\n"

    return (
        "You are a literary assistant. Answer the question using only the "
        "book fragments below. Be concise (2-4 sentences). "
        "Answer in the same language as the question. "
        "If the fragments do not contain the answer, say: "
        "'No tengo suficiente información en los libros cargados.'\n\n"
        f"{history_text}"
        f"--- FRAGMENTS ---\n{context}\n--- END ---\n\n"
        f"Question: {question}\nAnswer:"
    )


# Pipeline RAG completo que devuelve la respuesta del LLM token por token via streaming
# Si se pasan chunks prefetched los usa directamente para no hacer el retrieval dos veces
# El caso __ask_user__ se maneja aqui tambien como fallback aunque normalmente lo captura bot_routes
def ask_rag_stream(question: str, history=None, prefetched_chunks=None):
    print(f"\n{'='*50}")
    print(f"Pregunta: {question}")

    # Usa chunks ya buscados si vienen de bot_routes, si no hace el retrieval aqui
    chunks = prefetched_chunks if prefetched_chunks is not None else search_chunks(question, history=history)

    # Si el pipeline pide aclaracion devuelve un mensaje neutro y termina
    if chunks and chunks[0].get("__ask_user__"):
        yield "No estoy seguro sobre qué libro me preguntas. ¿Podrías mencionarlo?"
        return

    if not chunks:
        yield "No tengo información sobre eso en los libros cargados."
        return

    print(f"Chunks recuperados: {len(chunks)}")

    ctx    = build_context(chunks)
    prompt = build_prompt(ctx, question, history)
    print(f"Prompt: {len(prompt)} chars usando {MODEL_NAME}")

    if not GROQ_API_KEY:
        yield "Error: GROQ_API_KEY no está configurada."
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
                "max_tokens":  600,
                "temperature": 0.2,
            },
            stream=True,
            timeout=30,
        )

        print(f"Groq status: {response.status_code}")

        # El servicio de Groq esta saturado, pedir reintento
        if response.status_code == 429:
            yield "El servicio está ocupado, intenta de nuevo en un momento."
            return

        if response.status_code != 200:
            print(f"Error Groq: {response.text}")
            yield "Error al conectar con el modelo."
            return

        # Lee el stream SSE linea por linea y extrae el texto de cada token
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
        yield "El modelo tardó demasiado en responder."
    except Exception as e:
        print(f"Error: {e}")
        yield "Error inesperado."


# Version sincrona del pipeline, util para tests o el endpoint GET
def ask_rag(question: str, history=None) -> str:
    return "".join(ask_rag_stream(question, history))