import weaviate

def init_weaviate() -> weaviate.Client:
    return weaviate.Client(
        url="http://localhost:8080",
        timeout_config=(5, 60) 
    )