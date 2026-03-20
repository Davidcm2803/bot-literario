import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import current_app
from models.books import search_books, search_books_bm25, expand_chunks_with_neighbors

import re
import unicodedata
from dotenv import load_dotenv

# Carga variables de entorno
load_dotenv("key.env")


# Normaliza texto (lowercase + sin acentos)
def _normalize(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# Calcula distancia de Levenshtein entre dos strings
def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)

    if not b:
        return len(a)

    prev = list(range(len(b) + 1))

    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,
                curr[j] + 1,
                prev[j] + (ca != cb)
            ))
        prev = curr

    return prev[-1]


# Verifica si una palabra coincide con tolerancia a typos
def _word_matches_tolerant(word: str, query_words: list[str]) -> bool:
    if word in query_words:
        return True

    n = len(word)
    if n < 4:
        return False

    max_dist = 2 if n >= 6 else 1
    return any(_levenshtein(word, qw) <= max_dist for qw in query_words)


# Detecta libros mencionados en la query (soporta errores de escritura)
def detect_mentioned_book_ids(query: str, client) -> list[str]:
    try:
        result = (
            client.query
            .get("BookChunk", ["book_id", "book { ... on Book { title } }"])
            .with_limit(500)
            .do()
        )

        chunks = result.get("data", {}).get("Get", {}).get("BookChunk", [])

        # Mapa único book_id → title
        seen: dict[str, str] = {}
        for c in chunks:
            book_info = (c.get("book") or [{}])[0]
            title   = book_info.get("title", "")
            book_id = c.get("book_id", "")

            if title and book_id:
                seen[book_id] = title

        # Ordena títulos largos primero (evita conflictos tipo "Dune" vs "Dune Messiah")
        sorted_books = sorted(seen.items(), key=lambda x: len(x[1]), reverse=True)

        query_words = _normalize(query).split()
        mentioned   = []

        for book_id, title in sorted_books:
            title_words = _normalize(title).split()

            matches = sum(
                1 for w in title_words
                if _word_matches_tolerant(w, query_words)
            )

            # Coincidencia parcial (60%)
            if title_words and matches / len(title_words) >= 0.6:
                mentioned.append(book_id)

                # Evita duplicar matches
                for w in title_words:
                    query_words = [
                        qw for qw in query_words
                        if not _word_matches_tolerant(w, [qw])
                    ]

                print(f"  📌 Libro en query: '{title}' ({book_id[:8]}...)")

        return mentioned

    except Exception as e:
        print(f"  ⚠️ Error detectando libros: {e}")
        return []


# Obtiene todos los book_id únicos de la base
def _get_all_book_ids(client) -> list[str]:
    try:
        result = client.query.get("BookChunk", ["book_id"]).with_limit(500).do()
        chunks = result.get("data", {}).get("Get", {}).get("BookChunk", [])

        seen, ids = set(), []

        for c in chunks:
            bid = c.get("book_id", "")
            if bid and bid not in seen:
                ids.append(bid)
                seen.add(bid)

        return ids

    except Exception:
        return []


# Mapa de conceptos → términos útiles para BM25
_CONCEPT_MAP = {
    r'\b(cieg[oa]s?|blind|blindness|sight|vision)\b': "blind eyes darkness sockets",
    r'\b(muer[et][eo]|murio|kill|died|death|dies|killed)\b': "died death killed body blood",
    r'\b(traici[oó]n|betray|traitor|betrayal)\b': "betrayed traitor treachery",
    r'\b(amor|love|romance|enamorado)\b': "love beloved heart embrace",
    r'\b(batalla|battle|war|fight|combat|guerra)\b': "battle sword attack fight soldiers",
    r'\b(explosi[oó]n|bomb|explosion|blast|bomba)\b': "explosion blast fire destroyed burning",
}


# Genera queries adicionales para BM25 basadas en conceptos detectados
def _generate_bm25_variants(query: str) -> list[str]:
    query_lower = query.lower()
    variants    = []

    for pattern, terms in _CONCEPT_MAP.items():
        if re.search(pattern, query_lower, re.IGNORECASE):
            variants.append(terms)
            print(f"  🔤 Variante BM25: '{terms}'")

    return variants


# Detecta preguntas causales y genera versiones simplificadas
CAUSE_WORDS_RE = re.compile(
    r'\b(why|how|reason|cause|because|por\s+qu[eé]|porque|c[oó]mo|raz[oó]n|motivo|'
    r'qu[eé]\s+pas[oó]|qu[eé]\s+caus[oó]|qu[eé]\s+provoc[oó]|what\s+caused|what\s+happened)\b',
    re.IGNORECASE
)

def _expand_cause_query(query: str) -> list[str]:
    queries = [query]

    clean = CAUSE_WORDS_RE.sub('', query).strip()
    clean = re.sub(r'\s{2,}', ' ', clean).strip()

    if clean and clean.lower() != query.lower():
        queries.append(clean)
        print(f"  🔀 Query expandida: '{clean}'")

    return queries


# Busca libros mencionados en el historial reciente
def _get_book_ids_from_history(history: list, client) -> list[str]:
    if not history:
        return []

    recent_questions = " ".join(
        turn.get("question", "") for turn in history[-3:]
    )

    if not recent_questions.strip():
        return []

    ids = detect_mentioned_book_ids(recent_questions, client)

    if ids:
        print(f"  📚 Libro del historial: {ids[0][:8]}...")

    return ids


# Pipeline principal de búsqueda (semántico + BM25 + expansión)
def search_chunks(query: str, history: list | None = None) -> list[dict]:
    client = current_app.config["WEAVIATE_CLIENT"]

    # Detecta contexto de libro
    mentioned_ids = detect_mentioned_book_ids(query, client)

    if not mentioned_ids and history:
        mentioned_ids = _get_book_ids_from_history(history, client)

    # Genera variaciones de búsqueda
    search_queries = _expand_cause_query(query)
    bm25_variants  = _generate_bm25_variants(query)

    raw_chunks = []
    seen_keys  = set()

    # Evita duplicados
    def add_chunks(new_chunks: list[dict]) -> int:
        added = 0
        for c in new_chunks:
            key = (c.get("book_id"), c.get("chunk_index"))

            if key not in seen_keys:
                raw_chunks.append(c)
                seen_keys.add(key)
                added += 1

        return added

    if mentioned_ids:
        # Modo enfocado a un libro específico
        print("  🎯 Modo libro específico")

        for book_id in mentioned_ids:
            # Búsqueda semántica
            for q in search_queries:
                add_chunks(search_books(client, q, limit=25, book_id=book_id))

            # BM25 directo
            for q in search_queries:
                add_chunks(search_books_bm25(client, q, limit=30, book_id=book_id))

            # BM25 con variantes
            for v in bm25_variants:
                add_chunks(search_books_bm25(client, v, limit=15, book_id=book_id))

        # Refuerzo global mínimo
        add_chunks(search_books(client, query, limit=5))

    else:
        # Modo global sin contexto de libro
        print("  🌐 Modo global")

        for i, q in enumerate(search_queries):
            add_chunks(search_books(client, q, limit=20 if i == 0 else 10))

        add_chunks(search_books_bm25(client, query, limit=10))

        for v in bm25_variants:
            add_chunks(search_books_bm25(client, v, limit=8))

        # Cobertura por libro
        all_ids = _get_all_book_ids(client)

        for book_id in all_ids:
            for q in search_queries:
                add_chunks(search_books(client, q, limit=8, book_id=book_id))
                add_chunks(search_books_bm25(client, q, limit=5, book_id=book_id))

            for v in bm25_variants:
                add_chunks(search_books_bm25(client, v, limit=5, book_id=book_id))

    print(f"  📦 Chunks antes de expansión: {len(raw_chunks)}")

    # Expande con chunks vecinos para dar contexto
    expanded = expand_chunks_with_neighbors(client, raw_chunks, window=2)

    print(f"  📚 Chunks tras expansión: {len(expanded)}")

    return expanded