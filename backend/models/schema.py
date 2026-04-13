import weaviate


def create_schema(client: weaviate.Client):
    # Obtiene el schema actual y lista las clases que ya existen
    existing_schema  = client.schema.get()
    existing_classes = [c["class"] for c in existing_schema.get("classes", [])]

    # Configuracion del vectorizador para todas las clases
    # Usa snowflake arctic-embed-l-v2.0 con 1024 dimensiones y ventana de 8192 tokens
    # Cubre chunks de 500 palabras y resumenes de 200 palabras sin recorte
    VECTORIZER_CONFIG = {
        "text2vec-transformers": {
            "vectorizeClassName": False,
        }
    }

    # Crea la clase principal del libro si no existe todavia
    if "Book" not in existing_classes:
        client.schema.create_class({
            "class": "Book",
            "description": "Almacena libros del bot literario",
            "vectorizer": "text2vec-transformers",
            "moduleConfig": VECTORIZER_CONFIG,
            "properties": [
                {"name": "title",    "dataType": ["text"]},
                {"name": "author",   "dataType": ["text"]},
                {"name": "year",     "dataType": ["int"]},
                {"name": "language", "dataType": ["text"]},
            ]
        })
        print("  Clase Book creada")

    # Crea la clase para chunks pequenos de texto usados en busqueda detallada
    if "BookChunk" not in existing_classes:
        client.schema.create_class({
            "class": "BookChunk",
            "description": "Chunks pequeños (500 palabras) para búsqueda detallada",
            "vectorizer": "text2vec-transformers",
            "moduleConfig": VECTORIZER_CONFIG,
            "properties": [
                {"name": "content",     "dataType": ["text"]},
                {"name": "chunk_index", "dataType": ["int"]},
                {"name": "book_id",     "dataType": ["text"]},
                {"name": "book",        "dataType": ["Book"]},
            ]
        })
        print("  Clase BookChunk creada")

    # Crea la clase para resumenes generados por LLM de bloques de unas 50 paginas
    # Cada resumen tiene unas 200 palabras, lo que equivale a 300 tokens
    # Este tamano esta muy por debajo del limite de 8192 tokens del vectorizador
    if "BookSummary" not in existing_classes:
        client.schema.create_class({
            "class": "BookSummary",
            "description": "Resúmenes condensados por LLM de secciones de ~50 páginas",
            "vectorizer": "text2vec-transformers",
            "moduleConfig": VECTORIZER_CONFIG,
            "properties": [
                {"name": "content",       "dataType": ["text"]},
                {"name": "summary_index", "dataType": ["int"]},
                {"name": "book_id",       "dataType": ["text"]},
                # Indica si el resumen viene del inicio, mitad o final del libro
                {"name": "position",      "dataType": ["text"]},
                {"name": "book",          "dataType": ["Book"]},
            ]
        })
        print("  Clase BookSummary creada")

    # Crea la clase de usuarios del bot sin vectorizacion ya que no se busca por contenido
    if "User" not in existing_classes:
        client.schema.create_class({
            "class": "User",
            "description": "Usuarios registrados del bot",
            "vectorizer": "none",
            "properties": [
                {"name": "username",      "dataType": ["text"]},
                {"name": "email",         "dataType": ["text"]},
                {"name": "password_hash", "dataType": ["text"]},
                {"name": "is_active",     "dataType": ["boolean"]},
            ]
        })
        print("  Clase User creada")

    print("Schema creado/verificado en Weaviate")