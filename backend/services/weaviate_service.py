import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import current_app
from models.books import search_books


def search_chunks(query: str, limit: int = 5) -> list[dict]:
    # obtiene el cliente de weaviate guardado en la configuracion de flask
    client = current_app.config["WEAVIATE_CLIENT"]

    # busca chunks relevantes usando la query del usuario
    return search_books(client, query, limit=limit)