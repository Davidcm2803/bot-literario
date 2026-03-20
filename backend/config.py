import weaviate

def init_weaviate() -> weaviate.Client:
    return weaviate.Client(
        url="http://localhost:8080",
        #5 intentos 240 segundos para subir
        timeout_config=(5, 240) 
    )