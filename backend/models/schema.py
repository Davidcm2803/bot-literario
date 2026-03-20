import weaviate

def create_schema(client: weaviate.Client):
    """ Crear o verificar schema en Weaviate """
    # Obtener todas las clases existentes
    existing_schema  = client.schema.get()
    existing_classes = [c["class"] for c in existing_schema.get("classes", [])]

    # Coleccion de libros
    if "Book" not in existing_classes:
        book_class = {
            "class": "Book",
            "description": "Almacena libros del bot literario",
            "vectorizer": "text2vec-transformers",
            "properties": [
                {"name": "title",    "description": "Título del libro",       "dataType": ["text"]},
                {"name": "author",   "description": "Autor del libro",        "dataType": ["text"]},
                {"name": "year",     "description": "Año de publicación",     "dataType": ["int"]},
                {"name": "language", "description": "Idioma del libro",       "dataType": ["text"]},
            ]
        }
        client.schema.create_class(book_class)

    # Chunks por book_id agregado para poder recuperar vecinos
    if "BookChunk" not in existing_classes:
        chunk_class = {
            "class": "BookChunk",
            "description": "Chunks de los libros para búsquedas vectoriales",
            "vectorizer": "text2vec-transformers",
            "properties": [
                {"name": "content",      "description": "Contenido del chunk",                    "dataType": ["text"]},
                {"name": "chunk_index",  "description": "Índice del chunk dentro del libro",      "dataType": ["int"]},
                {"name": "book_id",      "description": "UUID del libro padre para expansión de contexto", "dataType": ["text"]},
                {"name": "book",         "description": "Referencia al libro",                    "dataType": ["Book"]},
            ]
        }
        client.schema.create_class(chunk_class)

    # Usuarios
    if "User" not in existing_classes:
        user_class = {
            "class": "User",
            "description": "Usuarios registrados del bot",
            "vectorizer": None,
            "properties": [
                {"name": "username",      "description": "Nombre de usuario",   "dataType": ["text"]},
                {"name": "email",         "description": "Email del usuario",   "dataType": ["text"]},
                {"name": "password_hash", "description": "Hash de contraseña",  "dataType": ["text"]},
                {"name": "is_active",     "description": "Usuario activo?",     "dataType": ["boolean"]},
            ]
        }
        client.schema.create_class(user_class)

    print("Schema creado/verificado correctamente en Weaviate")