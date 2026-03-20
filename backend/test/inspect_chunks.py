import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv("key.env")

import weaviate

client = weaviate.Client("http://localhost:8080")

result = client.query.get(
    "BookChunk",
    ["content", "chunk_index", "book_id"]
).with_where({
    "operator": "And",
    "operands": [
        {
            "path": ["chunk_index"],
            "operator": "GreaterThanEqual",
            "valueInt": 200
        },
        {
            "path": ["chunk_index"],
            "operator": "LessThanEqual",
            "valueInt": 245
        }
    ]
}).with_limit(200).do()

chunks = result["data"]["Get"]["BookChunk"]

KEYWORDS = ["blind", "stone burner", "eyes", "socket", "explosion",
            "bomb", "sight", "vision", "darkness", "ciego", "bomba",
            "ojos", "oscuridad", "cegó", "atomics"]

print(f"Total chunks encontrados: {len(chunks)}\n")

for c in sorted(chunks, key=lambda x: (x["book_id"], x["chunk_index"])):
    if not c["book_id"].startswith("a19dcbc7"):
        continue
    content_lower = c["content"].lower()
    if any(w in content_lower for w in KEYWORDS):
        print(f"\n{'='*60}")
        print(f"chunk_index: {c['chunk_index']}")
        print(c["content"][:600])
        print("...")