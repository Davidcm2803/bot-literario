import weaviate

def create_schema(client: weaviate.Client):
    """ Crear o verificar schema en Weaviate """

    # Obtener todas las clases existentes
    existing_schema = client.schema.get()
    existing_classes = [c["class"] for c in existing_schema.get("classes", [])]

    # Coleccion de libros
    if "Book" not in existing_classes:
        book_class = {
            "class": "Book",  # Nombre de la clase
            "description": "Almacena libros del bot literario",
            "vectorizer": "text2vec-transformers",  # Genera embeddings
            "properties": [
                {"name": "title", "description": "Título del libro", "dataType": ["text"]},
                {"name": "author", "description": "Autor del libro", "dataType": ["text"]},
                {"name": "year", "description": "Año de publicación", "dataType": ["int"]},
                {"name": "language", "description": "Idioma del libro", "dataType": ["text"]},
            ]
        }
        client.schema.create_class(book_class)  # Crear clase en Weaviate

    #  Chuncks esto divide en chuncks para buscar por vectores
    if "BookChunk" not in existing_classes:
        chunk_class = {
            "class": "BookChunk",  # Nombre de la clase
            "description": "Chunks de los libros para búsquedas vectoriales",
            "vectorizer": "text2vec-transformers",  # Genera embeddings
            "properties": [
                {"name": "content", "description": "Contenido del chunk", "dataType": ["text"]},
                {"name": "chunk_index", "description": "Índice del chunk", "dataType": ["int"]},
                {"name": "book", "description": "Referencia al libro", "dataType": ["Book"]},
            ]
        }
        client.schema.create_class(chunk_class) 

    # Usarios
    if "User" not in existing_classes:
        user_class = {
            "class": "User",  # Nombre de la clase
            "description": "Usuarios registrados del bot",
            "vectorizer": None,  # Usuarios no necesitan embeddings
            "properties": [
                {"name": "username", "description": "Nombre de usuario", "dataType": ["text"]},
                {"name": "email", "description": "Email del usuario", "dataType": ["text"]},
                {"name": "password_hash", "description": "Hash de contraseña", "dataType": ["text"]},
                {"name": "is_active", "description": "Usuario activo?", "dataType": ["boolean"]},
            ]
        }
        client.schema.create_class(user_class)

    print("Schema creado/verificado correctamente en Weaviate")
