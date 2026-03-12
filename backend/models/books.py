import os
import re
import uuid
import weaviate

# tamano de cada chunk y cuanto texto se repite entre chunks
CHUNK_SIZE = 400
CHUNK_OVERLAP = 100


def _clean_text(raw: str) -> tuple[str, dict]:
    # extrae metadata del encabezado de los libros de project gutenberg

    metadata = {
        "title": "Desconocido",
        "author": "Desconocido",
        "language": "Desconocido",
        "year": 0,
    }

    # busca campos clave en el encabezado usando expresiones regulares
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

    # marcadores que indican donde empieza el contenido real del libro
    start_markers = [
        r"\*\*\* START OF (THE|THIS) PROJECT GUTENBERG",
        r"\*\*\* BEGIN OF (THE|THIS) PROJECT GUTENBERG",
    ]
    # marcadores que indican donde termina el contenido real del libro
    end_markers = [
        r"\*\*\* END OF (THE|THIS) PROJECT GUTENBERG",
        r"\*\*\* END OF THE PROJECT GUTENBERG",
    ]

    text = raw

    # recorta el encabezado de gutenberg, se queda solo con lo que viene despues del marcador de inicio
    for marker in start_markers:
        parts = re.split(marker, text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text = parts[-1]
            break

    # recorta el pie de pagina de gutenberg, se queda solo con lo que viene antes del marcador de fin
    for marker in end_markers:
        parts = re.split(marker, text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text = parts[0]
            break

    # normaliza saltos de linea para evitar espacios en blanco excesivos
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text, metadata


def _split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    # divide el texto en fragmentos para vectorizarlos en weaviate

    words = text.split()
    chunks = []
    start = 0

    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)

        # el overlap hace que cada chunk comparta palabras con el anterior
        # esto evita que el contexto se pierda en los bordes de los chunks
        start += chunk_size - overlap

    return chunks


def book_exists(client: weaviate.Client, title: str, author: str) -> bool:
    # verifica si el libro ya esta guardado en la base

    # filtra por titulo Y autor para evitar falsos positivos con titulos repetidos
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
    # carga un libro txt en weaviate y crea sus chunks

    with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    text, metadata = _clean_text(raw)

    # si el libro ya existe se omite para no duplicar chunks en weaviate
    if book_exists(client, metadata["title"], metadata["author"]):
        return {
            "status": "skipped",
            "title": metadata["title"],
            "reason": "Ya existe en la base de datos",
        }

    book_id = str(uuid.uuid4())

    # crea el objeto libro principal sin el contenido, solo los metadatos
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

    chunks = _split_into_chunks(text)

    # el batch agrupa multiples operaciones en una sola peticion a weaviate
    # es mucho mas rapido que insertar chunk por chunk de forma individual
    with client.batch as batch:
        batch.batch_size = 20

        for idx, chunk_text in enumerate(chunks):

            chunk_id = str(uuid.uuid4())

            # inserta el chunk con su texto y posicion dentro del libro
            batch.add_data_object(
                data_object={
                    "content": chunk_text,
                    "chunk_index": idx,
                },
                class_name="BookChunk",
                uuid=chunk_id,
            )

            # la referencia enlaza cada chunk con su libro padre
            # esto permite recuperar los metadatos del libro al buscar chunks
            batch.add_reference(
                from_object_class_name="BookChunk",
                from_object_uuid=chunk_id,
                from_property_name="book",
                to_object_class_name="Book",
                to_object_uuid=book_id,
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
    # carga todos los libros txt de una carpeta

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
            print(f"    -> {result['status'].upper()}: {result.get('title', '?')}")

        except Exception as e:
            results.append({"status": "error", "file": filename, "error": str(e)})
            print(f"    -> ERROR en {filename}: {e}")

    return results


def search_books(client: weaviate.Client, query: str, limit: int = 5) -> list[dict]:
    # busqueda semantica de chunks usando vectores

    # with_near_text convierte la query en un vector y busca los chunks mas cercanos
    # la distancia resultante indica que tan relevante es cada chunk (menor = mejor)
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
    # lista los libros almacenados en la base

    result = (
        client.query
        .get("Book", ["title", "author", "year", "language"])
        .with_additional(["id"])
        .with_limit(100)
        .do()
    )

    return result.get("data", {}).get("Get", {}).get("Book", [])


def delete_book(client: weaviate.Client, book_id: str) -> bool:
    # elimina un libro y todos sus chunks asociados

    # primero busca todos los chunks que pertenecen a este libro
    # hay que borrarlos antes que el libro para no dejar huerfanos en weaviate
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

    # borra cada chunk individualmente antes de eliminar el libro padre
    for chunk in chunks:
        client.data_object.delete(chunk["_additional"]["id"], class_name="BookChunk")

    client.data_object.delete(book_id, class_name="Book")

    return True