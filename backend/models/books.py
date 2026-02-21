import os
import re
import uuid
import weaviate


#  Configuración de chunking
CHUNK_SIZE = 1000      # Palabras por chunk
CHUNK_OVERLAP = 100    # Palabras de solapamiento entre chunks


def _clean_gutenberg_text(raw: str) -> tuple[str, dict]:
    """
    Limpia el texto de Proyecto Gutenberg y extrae metadata básica.
    Devuelve (texto_limpio, metadata_dict).
    """
    metadata = {
        "title": "Desconocido",
        "author": "Desconocido",
        "language": "Desconocido",
        "year": 0,
    }

    # Intentar extraer metadata del encabezado de Gutenberg
    title_match = re.search(r"Title:\s*(.+)", raw, re.IGNORECASE)
    author_match = re.search(r"Author:\s*(.+)", raw, re.IGNORECASE)
    language_match = re.search(r"Language:\s*(.+)", raw, re.IGNORECASE)
    year_match = re.search(r"Release [Dd]ate:.*?(\d{4})", raw)

    if title_match:
        metadata["title"] = title_match.group(1).strip()
    if author_match:
        metadata["author"] = author_match.group(1).strip()
    if language_match:
        metadata["language"] = language_match.group(1).strip()
    if year_match:
        metadata["year"] = int(year_match.group(1))

    # Eliminar encabezado y pie de Gutenberg
    start_markers = [
        r"\*\*\* START OF (THE|THIS) PROJECT GUTENBERG",
        r"\*\*\* BEGIN OF (THE|THIS) PROJECT GUTENBERG",
    ]
    end_markers = [
        r"\*\*\* END OF (THE|THIS) PROJECT GUTENBERG",
        r"\*\*\* END OF THE PROJECT GUTENBERG",
    ]

    text = raw
    for marker in start_markers:
        parts = re.split(marker, text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text = parts[-1]
            break

    for marker in end_markers:
        parts = re.split(marker, text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text = parts[0]
            break

    # Limpiar espacios excesivos
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text, metadata


def _split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Divide el texto en chunks de `chunk_size` palabras con `overlap` de solapamiento.
    """
    words = text.split()
    chunks = []
    start = 0

    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start += chunk_size - overlap

    return chunks

#  Funciones principales 

def book_exists(client: weaviate.Client, title: str, author: str) -> bool:
    """Verifica si un libro ya existe en Weaviate para evitar duplicados."""
    result = (
        client.query
        .get("Book", ["title", "author"])
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
    books = result.get("data", {}).get("Get", {}).get("Book", [])
    return len(books) > 0


def upload_book(client: weaviate.Client, txt_path: str) -> dict:
    """
    Lee un archivo .txt de Gutenberg, extrae metadata, crea el objeto Book
    y sube todos sus chunks como objetos BookChunk en Weaviate.

    Devuelve un dict con el resultado de la operación.
    """
    with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    text, metadata = _clean_gutenberg_text(raw)

    # Evitar duplicados
    if book_exists(client, metadata["title"], metadata["author"]):
        return {
            "status": "skipped",
            "title": metadata["title"],
            "reason": "Ya existe en la base de datos",
        }

    # Crear objeto Book
    book_id = str(uuid.uuid4())
    client.data_object.create(
        data_object={
            "title": metadata["title"],
            "author": metadata["author"],
            "year": metadata["year"],
            "language": metadata["language"],
        },
        class_name="Book",
        uuid=book_id,
    )

    # Crear objetos BookChunk 
    chunks = _split_into_chunks(text)
    chunk_ids = []

    with client.batch as batch:
        batch.batch_size = 50  # Enviar de 50 en 50 para no saturar

        for idx, chunk_text in enumerate(chunks):
            chunk_id = str(uuid.uuid4())
            batch.add_data_object(
                data_object={
                    "content": chunk_text,
                    "chunk_index": idx,
                },
                class_name="BookChunk",
                uuid=chunk_id,
            )
            chunk_ids.append((chunk_id, idx))

    # Crear referencias BookChunk a Book
    for chunk_id, _ in chunk_ids:
        client.data_object.reference.add(
            from_class_name="BookChunk",
            from_uuid=chunk_id,
            from_property_name="book",
            to_class_name="Book",
            to_uuid=book_id,
        )

    return {
        "status": "uploaded",
        "title": metadata["title"],
        "author": metadata["author"],
        "year": metadata["year"],
        "language": metadata["language"],
        "book_id": book_id,
        "total_chunks": len(chunks),
    }


def upload_all_books(client: weaviate.Client, books_folder: str = "books") -> list[dict]:
    """
    Recorre la carpeta `books_folder`, carga todos los archivos .txt
    y los sube a Weaviate.

    Devuelve una lista con el resultado de cada archivo.
    """
    if not os.path.isdir(books_folder):
        raise FileNotFoundError(f"La carpeta '{books_folder}' no existe.")

    txt_files = [f for f in os.listdir(books_folder) if f.lower().endswith(".txt")]

    if not txt_files:
        return [{"status": "empty", "reason": f"No se encontraron archivos .txt en '{books_folder}'"}]

    results = []
    for filename in txt_files:
        filepath = os.path.join(books_folder, filename)
        print(f"  Procesando: {filename} ...")
        try:
            result = upload_book(client, filepath)
            result["file"] = filename
            results.append(result)
            print(f"    → {result['status'].upper()}: {result.get('title', '?')}")
        except Exception as e:
            results.append({"status": "error", "file": filename, "error": str(e)})
            print(f"    → ERROR en {filename}: {e}")

    return results


def search_books(client: weaviate.Client, query: str, limit: int = 5) -> list[dict]:
    """
    Realiza una búsqueda vectorial sobre los chunks de libros.
    Devuelve los chunks más relevantes con su referencia al libro.
    """
    result = (
        client.query
        .get("BookChunk", ["content", "chunk_index", "book { ... on Book { title author } }"])
        .with_near_text({"concepts": [query]})
        .with_limit(limit)
        .with_additional(["distance"])
        .do()
    )

    chunks = result.get("data", {}).get("Get", {}).get("BookChunk", [])
    return chunks


def list_books(client: weaviate.Client) -> list[dict]:
    """Lista todos los libros almacenados en Weaviate."""
    result = (
        client.query
        .get("Book", ["title", "author", "year", "language"])
        .with_additional(["id"])
        .with_limit(100)
        .do()
    )
    return result.get("data", {}).get("Get", {}).get("Book", [])


def delete_book(client: weaviate.Client, book_id: str) -> bool:
    """Elimina un libro y todos sus chunks asociados."""
    # Primero buscar los chunks de este libro
    result = (
        client.query
        .get("BookChunk", ["chunk_index"])
        .with_where({
            "path": ["book", "Book", "id"],
            "operator": "Equal",
            "valueText": book_id,
        })
        .with_additional(["id"])
        .with_limit(10000)
        .do()
    )
    chunks = result.get("data", {}).get("Get", {}).get("BookChunk", [])

    # Eliminar chunks
    for chunk in chunks:
        client.data_object.delete(chunk["_additional"]["id"], class_name="BookChunk")

    # Eliminar el libro
    client.data_object.delete(book_id, class_name="Book")
    return True