import weaviate
import time

def init_weaviate() -> weaviate.Client:
    for i in range(10):
        try:
            client = weaviate.Client(
                url="http://localhost:8080",
                timeout_config=(5, 240)
            )
            client.schema.get()  # prueba que esté listo
            print("✅ Weaviate conectado")
            return client
        except Exception as e:
            print(f"⏳ Weaviate no listo, reintentando ({i+1}/10)... {e}")
            time.sleep(5)
    raise Exception("❌ No se pudo conectar a Weaviate después de 10 intentos")