"""
diagnostico_dune_messiah.py

Corre esto y pegame el output completo.
Hace tres cosas en orden:
  1. Busca con BM25 puro las queries más obvias sobre la ceguera
  2. Escanea TODOS los chunks buscando "blind" / "stone" en el texto crudo
  3. Muestra el contenido exacto de los chunks candidatos
"""
import weaviate, os, re
from dotenv import load_dotenv
load_dotenv("key.env")

client  = weaviate.Client("http://localhost:8080")
BOOK_ID = "7eeeddd4-94a5-4b84-989b-ba7bac37e904"  # Dune Messiah

BATCH = 100


# ─── Helpers ────────────────────────────────────────────────────────────────

def get_all_chunks():
    """Trae todos los chunks de Dune Messiah ordenados por chunk_index."""
    all_chunks, offset = [], 0
    while True:
        result = (
            client.query
            .get("BookChunk", ["content", "chunk_index"])
            .with_where({"path": ["book_id"], "operator": "Equal", "valueText": BOOK_ID})
            .with_limit(BATCH)
            .with_offset(offset)
            .do()
        )["data"]["Get"]["BookChunk"]
        if not result:
            break
        all_chunks.extend(result)
        offset += BATCH
    all_chunks.sort(key=lambda c: c.get("chunk_index", 0))
    return all_chunks


def bm25_search(query, limit=8):
    return (
        client.query
        .get("BookChunk", ["content", "chunk_index"])
        .with_where({"path": ["book_id"], "operator": "Equal", "valueText": BOOK_ID})
        .with_hybrid(query=query, alpha=0.0, properties=["content"])
        .with_limit(limit)
        .with_additional(["score", "id"])
        .do()
    ).get("data", {}).get("Get", {}).get("BookChunk", [])


def hybrid_search(query, alpha=0.65, limit=8):
    return (
        client.query
        .get("BookChunk", ["content", "chunk_index"])
        .with_where({"path": ["book_id"], "operator": "Equal", "valueText": BOOK_ID})
        .with_hybrid(query=query, alpha=alpha, properties=["content"])
        .with_limit(limit)
        .with_additional(["score", "id"])
        .do()
    ).get("data", {}).get("Get", {}).get("BookChunk", [])


def show_chunks(chunks, label):
    print(f"\n{'─'*60}")
    print(f"  {label}  ({len(chunks)} resultados)")
    print(f"{'─'*60}")
    for c in chunks:
        score = c.get("_additional", {}).get("score", "?")
        idx   = c.get("chunk_index", "?")
        text  = c.get("content", "")
        print(f"  [idx={idx}] score={score}")
        print(f"  {text[:300]}")
        print()


# ─── 1. BM25 con varias queries ─────────────────────────────────────────────

print("\n" + "="*60)
print("  PASO 1 — BM25 puro con distintas queries")
print("="*60)

queries_bm25 = [
    "Paul blind",
    "Paul goes blind",
    "stone burner blind",
    "stone burner",
    "blind eyes Paul",
    "blindness Atreides",
    "ciego Paul",
    "Paul pierde la vista",
]

for q in queries_bm25:
    results = bm25_search(q, limit=3)
    show_chunks(results, f"BM25: '{q}'")


# ─── 2. Hybrid con alpha bajo (más BM25) ────────────────────────────────────

print("\n" + "="*60)
print("  PASO 2 — Hybrid alpha=0.2 (casi todo BM25)")
print("="*60)

queries_hybrid = [
    "Paul Atreides goes blind stone burner",
    "stone burner weapon blindness",
]

for q in queries_hybrid:
    results = hybrid_search(q, alpha=0.2, limit=5)
    show_chunks(results, f"Hybrid alpha=0.2: '{q}'")


# ─── 3. Scan manual de todos los chunks ─────────────────────────────────────

print("\n" + "="*60)
print("  PASO 3 — Scan manual buscando 'blind' / 'stone' / 'ciego' en el texto")
print("="*60)

print("  Descargando todos los chunks...")
all_chunks = get_all_chunks()
print(f"  Total chunks: {len(all_chunks)}")

# Patrones a buscar en el texto crudo
patterns = [
    re.compile(r'blind',        re.IGNORECASE),
    re.compile(r'stone\s+burn', re.IGNORECASE),
    re.compile(r'cieg',         re.IGNORECASE),
    re.compile(r'lost.{0,20}(eye|sight|vision)', re.IGNORECASE),
    re.compile(r'(eye|sight|vision).{0,20}lost',  re.IGNORECASE),
]

hits = []
for chunk in all_chunks:
    content = chunk.get("content", "")
    for pat in patterns:
        if pat.search(content):
            hits.append(chunk)
            break  # no duplicar si matchea varios patrones

print(f"\n  Chunks con menciones de ceguera/blind/stone burner: {len(hits)}")
print()

for chunk in hits:
    idx     = chunk.get("chunk_index", "?")
    content = chunk.get("content", "")

    # Resaltar el match en el texto
    marked = content
    for pat in patterns:
        marked = pat.sub(lambda m: f">>>>{m.group()}<<<<", marked)

    print(f"  ── chunk_index={idx} ──────────────────────────────")
    print(f"  {marked[:500]}")
    print()


# ─── 4. Resumen final ───────────────────────────────────────────────────────

print("\n" + "="*60)
print("  RESUMEN")
print("="*60)
print(f"  Total chunks en Dune Messiah : {len(all_chunks)}")
print(f"  Chunks con 'blind'/'stone'   : {len(hits)}")
if hits:
    indices = [c.get("chunk_index") for c in hits]
    print(f"  chunk_index de esos chunks  : {indices}")
else:
    print("  NINGÚN chunk contiene esas palabras -> problema de encoding en los datos")
print()