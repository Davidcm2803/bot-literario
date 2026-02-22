import sys
import os

# Agrega el directorio raíz (backend/) al path de Python
# para que pueda encontrar los módulos del proyecto
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import weaviate

# Conexión al cliente de Weaviate corriendo en Docker
client = weaviate.Client("http://localhost:8080")

# Texto de búsqueda — la búsqueda es vectorial (por significado, no por palabras exactas)
# Podés cambiar esto por cualquier tema que quieras buscar
query = "Antichrist chapter"

# Realizamos la búsqueda vectorial sobre la colección BookChunk
# BookChunk son los fragmentos en los que dividimos cada libro al subirlo
result = (
    client.query
    .get(
        "BookChunk",  # Clase donde están los fragmentos de los libros
        [
            "content",       # Texto del fragmento
            "chunk_index",   # Número de fragmento dentro del libro
            "book { ... on Book { title author } }"  # Referencia al libro padre
        ]
    )
    .with_near_text({"concepts": [query]})  # Búsqueda semántica con el query
    .with_limit(3)                          # Devuelve los 3 fragmentos más relevantes
    .with_additional(["distance"])          # Incluye la distancia vectorial (qué tan cerca está del query)
    .do()
)

# Extraemos la lista de chunks del resultado
chunks = result["data"]["Get"]["BookChunk"]

# Mostramos cada chunk encontrado con su info
for chunk in chunks:
    # El campo "book" es una lista de referencias, tomamos el primero
    book = chunk["book"][0] if chunk["book"] else {}

    print(f"\n {book.get('title')} — Chunk #{chunk['chunk_index']}")
    print(f"Distancia vectorial: {chunk['_additional']['distance']}")  # Mientras más bajo, más relevante
    print("-" * 60)
    print(chunk["content"][:500])  # Mostramos solo los primeros 500 caracteres del fragmento
    print()