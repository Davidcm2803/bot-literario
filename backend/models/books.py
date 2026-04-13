import os
import re
import uuid
import requests
import weaviate
from dotenv import load_dotenv
load_dotenv("key.env")

# Tamaño de cada chunk de texto en palabras
CHUNK_SIZE          = 350
# Palabras que se repiten entre chunks para mantener contexto
CHUNK_OVERLAP       = 50
# Palabras por bloque al dividir el texto para resumir
SUMMARY_BLOCK_WORDS = 10_000
# Palabras de solapamiento entre bloques de resumen
SUMMARY_OVERLAP     = 1_000

# URL de la API de Groq para completar chat
GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
# Modelo usado para generar los resumenes
SUMMARY_MODEL = "llama-3.1-8b-instant"


# Limpia el texto crudo del archivo y extrae metadatos como titulo, autor, idioma y año
def _clean_text(raw: str) -> tuple[str, dict]:
    metadata = {"title": "Desconocido", "author": "Desconocido",
                "language": "Desconocido", "year": 0}

    # Elimina caracteres que no son imprimibles ni latin extendido
    raw = re.sub(r'[^\x09\x0A\x0D\x20-\x7E\xA0-\xFF\u0100-\u024F]', '', raw)

    # Busca campos de metadatos en el encabezado del archivo
    for pattern, key in [
        (r"Title:\s*(.+)",    "title"),
        (r"Author:\s*(.+)",   "author"),
        (r"Language:\s*(.+)", "language"),
    ]:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            metadata[key] = m.group(1).strip()

    # Busca el año de publicacion en la linea de fecha de lanzamiento
    m = re.search(r"Release [Dd]ate:.*?(\d{4})", raw)
    if m:
        metadata["year"] = int(m.group(1))

    # Si no se encontro titulo o autor, usa las primeras lineas del archivo
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    if metadata["title"]  == "Desconocido" and lines:
        metadata["title"]  = lines[0]
    if metadata["author"] == "Desconocido" and len(lines) > 1:
        metadata["author"] = lines[1]

    # Recorta el texto para quedarse solo con el contenido del libro, sin el encabezado de Gutenberg
    text = raw
    for marker in [r"\*\*\* START OF (THE|THIS) PROJECT GUTENBERG",
                   r"\*\*\* BEGIN OF (THE|THIS) PROJECT GUTENBERG"]:
        parts = re.split(marker, text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text = parts[-1]
            break

    # Recorta el pie de pagina de Gutenberg al final del texto
    for marker in [r"\*\*\* END OF (THE|THIS) PROJECT GUTENBERG",
                   r"\*\*\* END OF THE PROJECT GUTENBERG"]:
        parts = re.split(marker, text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text = parts[0]
            break

    # Normaliza saltos de linea y elimina lineas en blanco excesivas
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), metadata


# Divide el texto en chunks de palabras con solapamiento entre ellos
def _split_into_chunks(text: str,
                       chunk_size: int = CHUNK_SIZE,
                       overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    chunks, start = [], 0
    while start < len(words):
        chunks.append(" ".join(words[start:start + chunk_size]))
        start += chunk_size - overlap
    return chunks


# Divide el texto en bloques grandes para resumir y asigna una posicion relativa a cada bloque
def _split_into_summary_blocks(text: str) -> list[tuple[str, str]]:
    words = text.split()
    total = len(words)
    blocks, start = [], 0

    while start < total:
        end     = min(start + SUMMARY_BLOCK_WORDS, total)
        content = " ".join(words[start:end])

        # Calcula si el bloque esta al inicio, mitad o final del libro
        ratio = start / total if total else 0
        if ratio < 0.25:
            position = "beginning"
        elif ratio > 0.75:
            position = "end"
        else:
            position = "middle"

        blocks.append((content, position))
        start += SUMMARY_BLOCK_WORDS - SUMMARY_OVERLAP

    return blocks


# Llama a la API de Groq para resumir un bloque de texto del libro con enfoque en hechos y personajes
def _summarize_block(raw_text: str, title: str, author: str) -> str:
    groq_api_key = os.environ.get("GROQ_API_KEY")

    # Si no hay clave de API usa las primeras 250 palabras como fallback
    if not groq_api_key:
        print("  GROQ_API_KEY no configurada, usando fallback de 250 palabras")
        return " ".join(raw_text.split()[:250])

    # Limita el texto enviado a la API a las primeras 5000 palabras
    excerpt = " ".join(raw_text.split()[:5000])

    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": SUMMARY_MODEL,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"You are summarizing a section of the book '{title}' by {author}.\n"
                        "Write a dense factual summary in 250-300 words.\n"
                        "You MUST include ALL of the following if present in the text:\n"
                        "- Physical changes to characters (blindness, injuries, death, transformation)\n"
                        "- Key plot twists, reversals, or surprises\n"
                        "- Important decisions and their immediate consequences\n"
                        "- Battles, confrontations, or power struggles\n"
                        "- Relationships between characters (alliances, betrayals, romances)\n"
                        "- Introduced concepts, objects, or locations that affect the plot\n"
                        "Be specific with character names and events. No opinions or analysis.\n\n"
                        f"EXCERPT:\n{excerpt}"
                    ),
                }],
                "max_tokens": 500,
                "temperature": 0.1,
            },
            timeout=45,
        )

        if response.status_code == 200:
            summary = response.json()["choices"][0]["message"]["content"].strip()
            print(f"    Summary generado: {len(summary.split())} palabras")
            return summary

        print(f"  Groq error {response.status_code}, usando fallback")
        return " ".join(raw_text.split()[:250])

    except Exception as e:
        print(f"  Error en summarize: {e}, usando fallback")
        return " ".join(raw_text.split()[:250])


# Genera un resumen general del libro enfocado en temas principales, personajes y premisa central
def _summarize_overview(raw_text: str, title: str, author: str) -> str:
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        return " ".join(raw_text.split()[:250])

    excerpt = " ".join(raw_text.split()[:5000])
    try:
        response = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {groq_api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": SUMMARY_MODEL,
                "messages": [{"role": "user", "content": (
                    f"You are writing an overview of the book '{title}' by {author}.\n"
                    "Write a 200-250 word overview that covers:\n"
                    "- Main themes and central ideas of the book\n"
                    "- Main characters and their roles\n"
                    "- Setting and historical/world context\n"
                    "- The core conflict or central premise\n"
                    "- Why this book is significant or what makes it unique\n"
                    "Be specific with names and details. No opinions or value judgments.\n"
                    "This text will be used to answer questions like "
                    "'what is this book about?' or 'what are the main themes?'.\n\n"
                    f"EXCERPT:\n{excerpt}"
                )}],
                "max_tokens": 400,
                "temperature": 0.1,
            },
            timeout=45,
        )
        if response.status_code == 200:
            overview = response.json()["choices"][0]["message"]["content"].strip()
            print(f"    Overview generado: {len(overview.split())} palabras")
            return overview
        print(f"  Groq error {response.status_code} en overview, usando fallback")
        return " ".join(raw_text.split()[:250])
    except Exception as e:
        print(f"  Error en overview: {e}")
        return " ".join(raw_text.split()[:250])


# Verifica si un libro ya existe en Weaviate comparando titulo y autor exactos
def book_exists(client: weaviate.Client, title: str, author: str) -> bool:
    result = (
        client.query.get("Book", ["title", "author"])
        .with_where({
            "operator": "And",
            "operands": [
                {"path": ["title"],  "operator": "Equal", "valueText": title},
                {"path": ["author"], "operator": "Equal", "valueText": author},
            ]
        })
        .with_limit(1)
        .do()
    )
    return len(result.get("data", {}).get("Get", {}).get("Book", [])) > 0


# Lee un archivo de texto, lo procesa y sube el libro con sus chunks y resumenes a Weaviate
def upload_book(client: weaviate.Client, txt_path: str) -> dict:
    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()

    text, meta = _clean_text(raw)
    title  = meta["title"]
    author = meta["author"]

    # Si el libro ya esta en la base de datos no lo vuelve a subir
    if book_exists(client, title, author):
        return {"status": "skipped", "title": title,
                "reason": "Ya existe en la base de datos"}

    # Crea el objeto principal del libro en Weaviate
    book_id = str(uuid.uuid4())
    client.data_object.create(
        data_object={"title": title, "author": author,
                     "year": meta["year"], "language": meta["language"]},
        class_name="Book",
        uuid=book_id,
    )

    # Divide el texto en chunks y los sube uno por uno con referencia al libro
    chunks = _split_into_chunks(text)
    print(f"    BookChunks: {len(chunks)}")

    for idx, chunk_text in enumerate(chunks):
        chunk_id = str(uuid.uuid4())
        client.data_object.create(
            data_object={"content": chunk_text, "chunk_index": idx, "book_id": book_id},
            class_name="BookChunk",
            uuid=chunk_id,
        )
        client.data_object.reference.add(
            from_class_name="BookChunk", from_uuid=chunk_id,
            from_property_name="book",
            to_class_name="Book",       to_uuid=book_id,
        )
        if idx % 50 == 0:
            print(f"    chunk {idx}/{len(chunks)}...")

    # Divide el texto en bloques grandes, resume cada uno y lo guarda como BookSummary
    blocks = _split_into_summary_blocks(text)
    print(f"    Bloques para summary: {len(blocks)}")

    for idx, (block_text, position) in enumerate(blocks):
        print(f"    Resumiendo bloque {idx + 1}/{len(blocks)} [{position}]...")
        summarized = _summarize_block(block_text, title, author)

        sid = str(uuid.uuid4())
        client.data_object.create(
            data_object={
                "content":       summarized,
                "summary_index": idx,
                "book_id":       book_id,
                "position":      position,
            },
            class_name="BookSummary",
            uuid=sid,
        )
        client.data_object.reference.add(
            from_class_name="BookSummary", from_uuid=sid,
            from_property_name="book",
            to_class_name="Book",         to_uuid=book_id,
        )

    # Genera y guarda un resumen general del libro con indice especial -1
    print(f"    Generando overview summary...")
    overview_text    = " ".join(text.split()[:8000])
    overview_summary = _summarize_overview(overview_text, title, author)

    sid = str(uuid.uuid4())
    client.data_object.create(
        data_object={
            "content":       overview_summary,
            "summary_index": -1,
            "book_id":       book_id,
            "position":      "overview",
        },
        class_name="BookSummary",
        uuid=sid,
    )
    client.data_object.reference.add(
        from_class_name="BookSummary", from_uuid=sid,
        from_property_name="book",
        to_class_name="Book", to_uuid=book_id,
    )
    print(f"    Overview guardado para '{title}'")

    return {
        "status":               "uploaded",
        "title":                title,
        "author":               author,
        "year":                 meta["year"],
        "language":             meta["language"],
        "book_id":              book_id,
        "total_chunks":         len(chunks),
        "total_summary_chunks": len(blocks) + 1,
    }


# Recorre todos los archivos txt de una carpeta y sube cada libro a Weaviate
def upload_all_books(client: weaviate.Client,
                     books_folder: str = "books") -> list[dict]:
    if not os.path.isdir(books_folder):
        raise FileNotFoundError(f"La carpeta '{books_folder}' no existe.")

    txt_files = [f for f in os.listdir(books_folder) if f.lower().endswith(".txt")]
    if not txt_files:
        return [{"status": "empty", "reason": f"No hay .txt en '{books_folder}'"}]

    results = []
    for filename in txt_files:
        filepath = os.path.join(books_folder, filename)
        print(f"  Procesando: {filename} ...")
        try:
            result = upload_book(client, filepath)
            result["file"] = filename
            results.append(result)
            print(f"    -> {result['status'].upper()}: {result.get('title', '?')}")
        except Exception as e:
            results.append({"status": "error", "file": filename, "error": str(e)})
            print(f"    -> ERROR: {e}")

    return results


# Genera overviews para libros ya cargados en la base de datos que no tengan uno todavia
def generate_missing_overviews(client: weaviate.Client) -> list[dict]:
    books = list_books(client)
    results = []

    for book in books:
        book_id = book.get("_additional", {}).get("id")
        title   = book.get("title", "?")
        author  = book.get("author", "?")

        if not book_id:
            continue

        # Verifica si el libro ya tiene un overview guardado
        existing = search_summaries_hybrid(
            client, "overview themes plot",
            limit=1, book_id=book_id, position="overview", alpha=0.75
        )
        if existing:
            print(f"  '{title}' ya tiene overview, saltando...")
            results.append({"title": title, "status": "skipped"})
            continue

        print(f"  Generando overview para '{title}'...")

        # Obtiene los primeros chunks del libro por indice para construir el texto base
        first_chunks = []
        for idx in range(20):
            chunk = get_chunk_by_book_and_index(client, book_id, idx)
            if chunk:
                first_chunks.append(chunk)
            if len(first_chunks) >= 15:
                break
        overview_text = " ".join(c.get("content", "") for c in first_chunks)

        if not overview_text.strip():
            print(f"  Sin chunks para '{title}', saltando...")
            results.append({"title": title, "status": "error", "reason": "no chunks"})
            continue

        overview_summary = _summarize_overview(overview_text, title, author)

        # Guarda el overview como BookSummary con indice -1 y posicion overview
        sid = str(uuid.uuid4())
        client.data_object.create(
            data_object={
                "content":       overview_summary,
                "summary_index": -1,
                "book_id":       book_id,
                "position":      "overview",
            },
            class_name="BookSummary",
            uuid=sid,
        )
        client.data_object.reference.add(
            from_class_name="BookSummary", from_uuid=sid,
            from_property_name="book",
            to_class_name="Book", to_uuid=book_id,
        )
        print(f"  Overview guardado para '{title}'")
        results.append({"title": title, "status": "created"})

    return results


# Busca chunks de texto usando busqueda hibrida combinando vectores y BM25
def search_chunks_hybrid(client: weaviate.Client,
                         query: str,
                         limit: int = 10,
                         book_id: str | None = None,
                         alpha: float = 0.75) -> list[dict]:
    q = (
        client.query
        .get("BookChunk", ["content", "chunk_index", "book_id",
                           "book { ... on Book { title author } }"])
        .with_hybrid(query=query, alpha=alpha, properties=["content"])
        .with_limit(limit)
        .with_additional(["score", "id"])
    )
    # Si se pasa un book_id filtra los resultados para ese libro solamente
    if book_id:
        q = q.with_where({
            "path": ["book_id"], "operator": "Equal", "valueText": book_id
        })
    return q.do().get("data", {}).get("Get", {}).get("BookChunk", [])


# Busca resumenes usando busqueda hibrida con filtros opcionales por libro y posicion
def search_summaries_hybrid(client: weaviate.Client,
                            query: str,
                            limit: int = 4,
                            book_id: str | None = None,
                            position: str | None = None,
                            alpha: float = 0.75) -> list[dict]:
    # Construye los filtros segun los parametros que vengan definidos
    filters = []
    if book_id:
        filters.append({"path": ["book_id"], "operator": "Equal", "valueText": book_id})
    if position:
        filters.append({"path": ["position"], "operator": "Equal", "valueText": position})

    where = None
    if len(filters) == 1:
        where = filters[0]
    elif len(filters) == 2:
        where = {"operator": "And", "operands": filters}

    q = (
        client.query
        .get("BookSummary", ["content", "summary_index", "book_id", "position",
                             "book { ... on Book { title author } }"])
        .with_hybrid(query=query, alpha=alpha, properties=["content"])
        .with_limit(limit)
        .with_additional(["score", "id"])
    )
    if where:
        q = q.with_where(where)

    return q.do().get("data", {}).get("Get", {}).get("BookSummary", [])


# Busca chunks usando solo el componente vectorial de la busqueda hibrida
def search_books(client: weaviate.Client, query: str,
                 limit: int = 10, book_id: str | None = None) -> list[dict]:
    return search_chunks_hybrid(client, query, limit=limit,
                                book_id=book_id, alpha=1.0)


# Busca chunks usando solo BM25 sin componente vectorial
def search_books_bm25(client: weaviate.Client, query: str,
                      limit: int = 8, book_id: str | None = None) -> list[dict]:
    return search_chunks_hybrid(client, query, limit=limit,
                                book_id=book_id, alpha=0.0)


# Recupera un chunk especifico de un libro usando su indice de posicion
def get_chunk_by_book_and_index(client: weaviate.Client,
                                book_id: str,
                                chunk_index: int) -> dict | None:
    try:
        result = (
            client.query
            .get("BookChunk", ["content", "chunk_index", "book_id",
                               "book { ... on Book { title author } }"])
            .with_where({
                "operator": "And",
                "operands": [
                    {"path": ["book_id"],     "operator": "Equal", "valueText": book_id},
                    {"path": ["chunk_index"], "operator": "Equal", "valueInt": chunk_index},
                ]
            })
            .with_limit(1)
            .with_additional(["id"])
            .do()
        )
        chunks = result.get("data", {}).get("Get", {}).get("BookChunk", [])
        return chunks[0] if chunks else None
    except Exception as e:
        print(f"  Error recuperando chunk {chunk_index}: {e}")
        return None


# Expande una lista de chunks incluyendo sus vecinos inmediatos para dar mas contexto
def expand_chunks_with_neighbors(client: weaviate.Client,
                                 chunks: list[dict],
                                 window: int = 1) -> list[dict]:
    # Registra los chunks originales por clave de libro e indice
    seen: dict[tuple, dict] = {}

    for chunk in chunks:
        bid = chunk.get("book_id")
        idx = chunk.get("chunk_index")
        if bid and idx is not None:
            seen[(bid, idx)] = chunk

    # Para cada chunk busca sus vecinos dentro del rango de la ventana
    for chunk in list(chunks):
        bid = chunk.get("book_id")
        idx = chunk.get("chunk_index")
        if not bid or idx is None:
            continue

        for delta in range(-window, window + 1):
            if delta == 0:
                continue
            nidx = idx + delta
            if nidx < 0:
                continue
            key = (bid, nidx)
            if key in seen:
                continue
            neighbor = get_chunk_by_book_and_index(client, bid, nidx)
            if neighbor:
                seen[key] = neighbor

    # Devuelve todos los chunks ordenados por libro e indice
    return sorted(seen.values(),
                  key=lambda c: (c.get("book_id", ""), c.get("chunk_index", 0)))


# Devuelve la lista de todos los libros guardados en Weaviate con sus metadatos
def list_books(client: weaviate.Client) -> list[dict]:
    result = (
        client.query.get("Book", ["title", "author", "year", "language"])
        .with_additional(["id"])
        .with_limit(100)
        .do()
    )
    return result.get("data", {}).get("Get", {}).get("Book", [])


# Elimina un libro completo de Weaviate incluyendo todos sus chunks y resumenes
def delete_book(client: weaviate.Client, book_id: str) -> bool:
    # Borra primero todos los objetos relacionados en BookChunk y BookSummary
    for class_name, index_field in [("BookChunk", "chunk_index"),
                                     ("BookSummary", "summary_index")]:
        r = (
            client.query.get(class_name, [index_field])
            .with_where({"path": ["book_id"], "operator": "Equal", "valueText": book_id})
            .with_additional(["id"])
            .with_limit(10000)
            .do()
        )
        for obj in r.get("data", {}).get("Get", {}).get(class_name, []):
            client.data_object.delete(obj["_additional"]["id"], class_name=class_name)

    # Borra el objeto principal del libro
    client.data_object.delete(book_id, class_name="Book")
    return True