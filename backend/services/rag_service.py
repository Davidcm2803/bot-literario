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

# Cantidad de mensajes de historial que se usan esto se va si no esta logueado
MAX_HISTORY = 10


# Construye el contexto a partir de los chunks de weavite
def build_context(chunks):

    context_parts = []

    for chunk in chunks:

        # información del libro
        book = chunk.get("book", [])
        book_info = book[0] if book else {}

        title = book_info.get("title", "Unknown")
        author = book_info.get("author", "Unknown")

        content = chunk.get("content", "")[:500]

        context_parts.append(
            f"BOOK: {title}\n"
            f"AUTHOR: {author}\n"
            f"CONTENT: {content}\n"
        )

    return "\n".join(context_parts)


# Prompt que se crea para enviar al llm
def build_prompt(context, question, history=None):

    history_text = ""

    # agregar historial de conversación
    if history:

        history_text = "Conversation history:\n"

        for turn in history[-MAX_HISTORY:]:

            history_text += f"User: {turn['question']}\n"
            history_text += f"Assistant: {turn['answer']}\n"

        history_text += "\n"

    return f"""
You are an intelligent literary assistant specialized in philosophy and literature.

Your task is to answer the user's question using ONLY the information contained in the context provided.

RULES:

1. The context is your ONLY source of factual information.
2. If the answer cannot be found in the context, say clearly:
   "No tengo suficiente información en los libros cargados para responder esa pregunta."
3. Never invent information.
4. Never guess authors, titles, dates, or facts.
5. Never reveal or quote the context verbatim.
6. Answer naturally and conversationally, like a knowledgeable human assistant.
7. Keep the answer concise but informative.
8. Always respond in the same language as the user's question.

{history_text}

Context:
{context}

User Question:
{question}

Assistant Answer:
"""


# Funcion principal de RAG
def ask_rag_stream(question, history=None):

    print(f"\n{'='*50}")
    print(f"📥 Pregunta: {question}")

    # Opcional mejorar búsqueda usando historial
    search_query = question

    if history:
        last_question = history[-1]["question"]
        search_query = f"{last_question} {question}"

    # buscar chunks en Weaviate
    chunks = search_chunks(search_query)

    print(f"🔍 Chunks encontrados: {len(chunks)}")

    if not chunks:

        print("⚠️ No se encontraron chunks relevantes")
        yield "No tengo información sobre eso en los libros cargados."
        return

    # mostrar información de debug
    for i, chunk in enumerate(chunks):

        book = chunk.get("book", [])
        book_info = book[0] if book else {}

        title = book_info.get("title", "?")
        author = book_info.get("author", "?")
        distance = chunk.get("_additional", {}).get("distance", "?")

        print(f"  Chunk {i+1}: {title} - {author} | dist: {distance}")

    # construir contexto
    context = build_context(chunks)

    # construir prompt
    prompt = build_prompt(context, question, history)

    print(f"📝 Prompt ({len(prompt)} chars) enviado a Groq")

    # verificar API key
    if not GROQ_API_KEY:

        yield "Error: GROQ_API_KEY no está configurada."
        return

    try:

        # enviar peticion al LLM
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

        # Procesar streaming de tokens desde Groq
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


# Versión no streaming (devuelve texto completo)
def ask_rag(question, history=None):
    return "".join(ask_rag_stream(question, history))