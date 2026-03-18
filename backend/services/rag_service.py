import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import requests
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv("key.env")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.weaviate_service import search_chunks


# Configuración del modelo Groq
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

MODEL_NAME = "llama-3.1-8b-instant"

# Cantidad de mensajes de historial que se usan
MAX_HISTORY = 10


def build_context(chunks):
    context_parts = []

    for i, chunk in enumerate(chunks):
        book = chunk.get("book", [])
        book_info = book[0] if book else {}

        title = book_info.get("title", "Unknown")
        author = book_info.get("author", "Unknown")

        # Aumentado de 500 → 1200 chars para no perder eventos narrativos
        content = chunk.get("content", "")[:1200]

        context_parts.append(
            f"[Fragment {i+1} — {title} by {author}]\n"
            f"{content}\n"
        )

    return "\n---\n".join(context_parts)


def build_prompt(context, question, history=None):
    history_text = ""

    if history:
        history_text = "Conversation history:\n"
        for turn in history[-MAX_HISTORY:]:
            history_text += f"User: {turn['question']}\n"
            history_text += f"Assistant: {turn['answer']}\n"
        history_text += "\n"

    return f"""
You are an expert literary assistant specializing in philosophy and literature,
with deep knowledge of the works present in the provided context.

Your job is to answer the user's question based on the fragments provided below.
These fragments come directly from the books in the library.

INSTRUCTIONS:

1. Read ALL fragments carefully before answering.
2. The answer may be spread across multiple fragments — synthesize them.
3. If a fact is clearly implied or described in the fragments, state it confidently.
4. Do NOT be overly cautious — if the context supports the answer, give it.
5. Only say you don't have information if the topic is genuinely absent from ALL fragments.
6. Never invent facts, authors, dates, or plot points not present in the context.
7. Never copy the fragments verbatim — paraphrase naturally.
8. Answer in the same language as the user's question.
9. Be concise but complete. Prefer 2–4 sentences unless more detail is needed.

{history_text}

--- CONTEXT FRAGMENTS ---
{context}
--- END OF CONTEXT ---

User Question: {question}

Assistant Answer:"""


def ask_rag_stream(question, history=None):

    print(f"\n{'='*50}")
    print(f"📥 Pregunta: {question}")

    # Construir query de búsqueda
    search_query = question
    if history:
        last_question = history[-1]["question"]
        search_query = f"{last_question} {question}"

    # Boost para preguntas sobre personajes conocidos de Dune
    keywords_boost = ["Paul", "Atreides", "Muad'Dib"] if any(
        w in question.lower() for w in ["paul", "atreides", "muad"]
    ) else []
    if keywords_boost:
        search_query = f"{search_query} {' '.join(keywords_boost)}"

    # Buscar chunks en Weaviate
    chunks = search_chunks(search_query)

    print(f"🔍 Chunks encontrados: {len(chunks)}")

    if not chunks:
        print("⚠️ No se encontraron chunks relevantes")
        yield "No tengo información sobre eso en los libros cargados."
        return

    # Debug de chunks
    for i, chunk in enumerate(chunks):
        book = chunk.get("book", [])
        book_info = book[0] if book else {}
        title = book_info.get("title", "?")
        author = book_info.get("author", "?")
        distance = chunk.get("_additional", {}).get("distance", "?")
        print(f"  Chunk {i+1}: {title} - {author} | dist: {distance}")

    # Construir contexto y prompt
    context = build_context(chunks)
    prompt = build_prompt(context, question, history)

    print(f"📝 Prompt ({len(prompt)} chars) enviado a Groq")

    if not GROQ_API_KEY:
        yield "Error: GROQ_API_KEY no está configurada."
        return

    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL_NAME,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "stream": True,
                "max_tokens": 400,
                "temperature": 0.3,
            },
            stream=True,
            timeout=30
        )

        print(f"🤖 Groq status: {response.status_code}")

        if response.status_code != 200:
            print(f"❌ Error de Groq: {response.text}")
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
                print(f"✅ Stream completo — {token_count} tokens generados")
                break

            try:
                data = json.loads(data_str)
                token = data["choices"][0]["delta"].get("content", "")
                if token:
                    token_count += 1
                    yield token
            except json.JSONDecodeError:
                continue

    except requests.exceptions.Timeout:
        print("❌ Timeout conectando a Groq")
        yield "El modelo tardó demasiado en responder."

    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        yield "Error inesperado al generar la respuesta."


def ask_rag(question, history=None):
    return "".join(ask_rag_stream(question, history))
```