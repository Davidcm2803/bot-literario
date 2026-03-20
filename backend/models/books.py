import os
import re
import uuid
import weaviate
# tamano de cada chunk y cuanto texto se repite entre chunks
CHUNK_SIZE = 300
CHUNK_OVERLAP = 50


# Limpia el texto crudo y extrae metadatos básicos (título, autor, idioma, año)
def _clean_text(raw: str) -> tuple[str, dict]:
    metadata = {
        "title": "Desconocido",
        "author": "Desconocido",
        "language": "Desconocido",
        "year": 0
    }

    # Elimina caracteres invalidos
    raw = re.sub(r'[^\x09\x0A\x0D\x20-\x7E\xA0-\xFF\u0100-\u024F]', '', raw)

    # Extrae metadatos con regex
    for pattern, key in [
        (r"Title:\s*(.+)", "title"),
        (r"Author:\s*(.+)", "author"),
        (r"Language:\s*(.+)", "language"),
    ]:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            metadata[key] = m.group(1).strip()

    # Extrae año de publicación
    m = re.search(r"Release [Dd]ate:.*?(\d{4})", raw)
    if m:
        metadata["year"] = int(m.group(1))

    # Fallback si no hay metadata explícita
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    if metadata["title"] == "Desconocido" and lines:
        metadata["title"] = lines[0]
    if metadata["author"] == "Desconocido" and len(lines) > 1:
        metadata["author"] = lines[1]

    # Elimina cabecera y footer de Project Gutenberg
    text = raw
    for marker in [
        r"\*\*\* START OF (THE|THIS) PROJECT GUTENBERG",
        r"\*\*\* BEGIN OF (THE|THIS) PROJECT GUTENBERG"
    ]:
        parts = re.split(marker, text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text = parts[-1]
            break

    for marker in [
        r"\*\*\* END OF (THE|THIS) PROJECT GUTENBERG",
        r"\*\*\* END OF THE PROJECT GUTENBERG"
    ]:
        parts = re.split(marker, text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text = parts[0]
            break

    # Normaliza saltos de línea
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip(), metadata


# Divide el texto en chunks con solapamiento
def _split_into_chunks(text: str,
                      chunk_size: int = CHUNK_SIZE,
                      overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    chunks = []
    start = 0

    while start < len(words):
        chunks.append(" ".join(words[start:start + chunk_size]))
        start += chunk_size - overlap

    return chunks


# Verifica si un libro ya existe en la base de datos
def book_exists(client: weaviate.Client, title: str, author: str) -> bool:
    result = (
        client.query.get("Book", ["title", "author"])
        .with_where({
            "operator": "And",
            "operands": [
                {"path": ["title"], "operator": "Equal", "valueText": title},
                {"path": ["author"], "operator": "Equal", "valueText": author},
            ]
        })
        .with_limit(1)
        .do()
    )

    return len(result.get("data", {}).get("Get", {}).get("Book", [])) > 0


# Procesa un archivo .txt y lo sube a Weaviate (libro + chunks)
def upload_book(client: weaviate.Client, txt_path: str) -> dict:
    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()

    text, meta = _clean_text(raw)

    # Evita duplicados
    if book_exists(client, meta["title"], meta["author"]):
        return {
            "status": "skipped",
            "title": meta["title"],
            "reason": "Ya existe en la base de datos"
        }

    # Crea el libro
    book_id = str(uuid.uuid4())
    client.data_object.create(
        data_object={
            "title": meta["title"],
            "author": meta["author"],
            "year": meta["year"],
            "language": meta["language"]
        },
        class_name="Book",
        uuid=book_id,
    )

    # Crea chunks del contenido
    chunks = _split_into_chunks(text)
    print(f"    Total chunks: {len(chunks)}")

    for idx, chunk_text in enumerate(chunks):
        chunk_id = str(uuid.uuid4())

        client.data_object.create(
            data_object={
                "content": chunk_text,
                "chunk_index": idx,
                "book_id": book_id
            },
            class_name="BookChunk",
            uuid=chunk_id,
        )

        # Relación chunk → libro
        client.data_object.reference.add(
            from_class_name="BookChunk",
            from_uuid=chunk_id,
            from_property_name="book",
            to_class_name="Book",
            to_uuid=book_id,
        )

        if idx % 10 == 0:
            print(f"    chunk {idx}/{len(chunks)}...")

    return {
        "status": "uploaded",
        "title": meta["title"],
        "author": meta["author"],
        "year": meta["year"],
        "language": meta["language"],
        "book_id": book_id,
        "total_chunks": len(chunks)
    }


# Sube todos los libros .txt de una carpeta
def upload_all_books(client: weaviate.Client, books_folder: str = "books") -> list[dict]:
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


# Búsqueda semántica (vectorial)
def search_books(client: weaviate.Client, query: str,
                 limit: int = 10,
                 book_id: str | None = None) -> list[dict]:

    q = (
        client.query
        .get("BookChunk", ["content", "chunk_index", "book_id",
                           "book { ... on Book { title author } }"])
        .with_near_text({"concepts": [query]})
        .with_limit(limit)
        .with_additional(["distance", "id"])
    )

    if book_id:
        q = q.with_where({
            "path": ["book_id"],
            "operator": "Equal",
            "valueText": book_id
        })

    return q.do().get("data", {}).get("Get", {}).get("BookChunk", [])


# Búsqueda por palabras clave (BM25)
def search_books_bm25(client: weaviate.Client, query: str,
                      limit: int = 8,
                      book_id: str | None = None) -> list[dict]:

    q = (
        client.query
        .get("BookChunk", ["content", "chunk_index", "book_id",
                           "book { ... on Book { title author } }"])
        .with_bm25(query=query, properties=["content"])
        .with_limit(limit)
        .with_additional(["score", "id"])
    )

    if book_id:
        q = q.with_where({
            "path": ["book_id"],
            "operator": "Equal",
            "valueText": book_id
        })

    return q.do().get("data", {}).get("Get", {}).get("BookChunk", [])


# Obtiene un chunk específico por libro e índice
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
                    {"path": ["book_id"], "operator": "Equal", "valueText": book_id},
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
        print(f"  ⚠️ Error recuperando chunk {chunk_index}: {e}")
        return None


# Expande resultados agregando chunks vecinos (context window)
def expand_chunks_with_neighbors(client: weaviate.Client,
                                 chunks: list[dict],
                                 window: int = 2) -> list[dict]:

    seen: dict[tuple, dict] = {}

    # Guarda chunks originales
    for chunk in chunks:
        bid = chunk.get("book_id")
        idx = chunk.get("chunk_index")
        if bid and idx is not None:
            seen[(bid, idx)] = chunk

    # Agrega vecinos
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
                print(f"  📎 Vecino: idx={nidx}")

    return sorted(
        seen.values(),
        key=lambda c: (c.get("book_id", ""), c.get("chunk_index", 0))
    )


# Lista todos los libros almacenados
def list_books(client: weaviate.Client) -> list[dict]:
    result = (
        client.query.get("Book", ["title", "author", "year", "language"])
        .with_additional(["id"])
        .with_limit(100)
        .do()
    )
    return result.get("data", {}).get("Get", {}).get("Book", [])


# Elimina un libro y todos sus chunks asociados
def delete_book(client: weaviate.Client, book_id: str) -> bool:
    result = (
        client.query.get("BookChunk", ["chunk_index"])
        .with_where({
            "path": ["book", "Book", "id"],
            "operator": "Equal",
            "valueText": book_id
        })
        .with_additional(["id"])
        .with_limit(10000)
        .do()
    )

    # Borra chunks
    for chunk in result.get("data", {}).get("Get", {}).get("BookChunk", []):
        client.data_object.delete(chunk["_additional"]["id"], class_name="BookChunk")

    # Borra libro
    client.data_object.delete(book_id, class_name="Book")

    return True