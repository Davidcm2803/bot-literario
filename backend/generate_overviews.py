"""
Script de migración: genera overviews para libros ya cargados.

Uso:
    python generate_overviews.py

Requiere que la app Flask esté configurada o que el cliente Weaviate
esté disponible. Ajusta la conexión según tu setup.
"""

import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv("key.env")

import weaviate
from models.books import generate_missing_overviews


def init_weaviate() -> weaviate.Client:
    for i in range(10):
        try:
            client = weaviate.Client(
                url="http://localhost:8080",
                timeout_config=(5, 240)
            )
            client.schema.get()
            print("✅ Weaviate conectado")
            return client
        except Exception as e:
            print(f"⏳ Weaviate no listo, reintentando ({i+1}/10)... {e}")
            time.sleep(5)
    raise Exception("❌ No se pudo conectar a Weaviate después de 10 intentos")


if __name__ == "__main__":
    print("🚀 Generando overviews para libros existentes...")
    client = init_weaviate()

    results = generate_missing_overviews(client)

    print("\n📊 Resultados:")
    for r in results:
        status = r.get("status")
        title  = r.get("title", "?")
        icon   = "✅" if status == "created" else ("⏭️" if status == "skipped" else "❌")
        print(f"  {icon} [{status}] {title}")

    created  = sum(1 for r in results if r.get("status") == "created")
    skipped  = sum(1 for r in results if r.get("status") == "skipped")
    errors   = sum(1 for r in results if r.get("status") == "error")
    print(f"\n✅ Creados: {created} | ⏭️  Saltados: {skipped} | ❌ Errores: {errors}")
