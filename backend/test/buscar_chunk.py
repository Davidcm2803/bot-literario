import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import init_weaviate

client = init_weaviate()

queries = [
    "stone burner blind Paul eyes",
    "Paul loses eyes explosion",
    "atomics blind Muad'Dib",
    "stone burner Alia",
    "Paul ciego armas nucleares explosion Dune Messiah",
]

for q in queries:
    print(f"\n{'='*50}")
    print(f"Query: {q}")
    result = (
        client.query
        .get("BookChunk", ["content", "chunk_index", "book_id",
                           "book { ... on Book { title author } }"])
        .with_near_text({"concepts": [q]})
        .with_limit(3)
        .with_additional(["distance"])
        .do()
    )
    chunks = result.get("data", {}).get("Get", {}).get("BookChunk", [])
    for c in chunks:
        book = c.get("book", [{}])
        title = book[0].get("title", "?") if book else "?"
        dist = c.get("_additional", {}).get("distance", "?")
        print(f"\n  [{c['chunk_index']}] {title} | dist: {dist}")
        print(f"  {c['content'][:300]}")
        print("  ...")