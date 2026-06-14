"""
diagnostico.py — Muestra contenido completo de summaries de DUNE MESSIAH.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "key.env"))
import weaviate

def main():
    client = weaviate.Client("http://localhost:8080")
    books = client.query.get("Book", ["title"]).with_additional(["id"]).with_limit(20).do()
    for b in books.get("data", {}).get("Get", {}).get("Book", []):
        if "messiah" in b.get("title", "").lower():
            book_id = b["_additional"]["id"]
            result = (
                client.query
                .get("BookSummary", ["summary_index", "position", "content"])
                .with_where({"path": ["book_id"], "operator": "Equal", "valueText": book_id})
                .with_additional(["id"])
                .with_limit(50)
                .do()
            )
            summaries = result.get("data", {}).get("Get", {}).get("BookSummary", []) or []
            for s in sorted(summaries, key=lambda x: x.get("summary_index", 0)):
                print(f"=== idx={s.get('summary_index')} position={s.get('position')} ===")
                print(s.get("content", ""))
                print()

if __name__ == "__main__":
    main()