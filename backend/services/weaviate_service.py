import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import json
import requests
import unicodedata
from flask import current_app
from dotenv import load_dotenv

from models.books import (
    search_chunks_hybrid,
    search_summaries_hybrid,
    expand_chunks_with_neighbors,
    list_books,
)

load_dotenv("key.env")

# ---------------------------------------------------------------------------
# CONSTANTES
# ---------------------------------------------------------------------------

MAX_CHUNKS_TO_LLM    = 10
MAX_CONTEXT_CHARS    = 20_000
MIN_CHUNKS_THRESHOLD = 4
MIN_RELEVANCE_SCORE  = 0.55

GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

_EN_PATTERN = re.compile(
    r'\b(what|who|how|when|where|does|did|is|are|the|of|in|to|and|his|her)\b',
    re.IGNORECASE,
)

_SEQUEL_PATTERNS = re.compile(
    r'\b(siguiente|secuela|continuaci[oó]n|pr[oó]ximo|despu[eé]s|next|sequel|after)\b',
    re.IGNORECASE
)

_SUMMARY_PATTERNS = re.compile(
    r'\b('
    r'qu[eé]\s+(pas[oó]|ocurri[oó]|le\s+pas[oó]|sucedi[oó]|hace|hizo)|'
    r'(trata|habla|cuenta)\s+(el|la|los)?\s*(libro|historia|novela)?|'
    r'resumen|resume|summarize|summary|synopsis|sinopsis|'
    r'al\s+final|how\s+does\s+.*\s+end|c[oó]mo\s+termina|'
    r'qu[eé]\s+le\s+pasa\s+a|what\s+happens\s+to|'
    r'qui[eé]n\s+es|who\s+is|who\s+are|'
    r'de\s+qu[eé]\s+(trata|va)|what\s+is\s+.*\s+about|'
    r'cu[aá]l\s+es\s+(la\s+)?trama|what\s+is\s+the\s+plot|'
    r'cu[eé]ntame|tell\s+me\s+about|'
    r'qu[eé]\s+rol|what\s+role|'
    r'termina|ends?|outcome|desenlace|'
    r'tema\s+principal|main\s+theme|temas?|themes?'
    r')\b',
    re.IGNORECASE
)

_SPECIFIC_PATTERNS = re.compile(
    r'\b('
    r'cu[aá]ndo|when\s+exactly|d[oó]nde\s+exactamente|'
    r'c[oó]mo\s+funciona|how\s+does\s+.*\s+work|'
    r'exact(amente|ly)|textualmente|literally|'
    r'cu[aá]l\s+es\s+la\s+frase|what\s+did\s+.*\s+say|'
    r'describe\s+exactamente|explica\s+en\s+detalle|'
    r'qu[eé]\s+es\s+(el|la|un|una)|what\s+is\s+(the\s+)?\w+\s*\?|'
    r'cieg[oa]|blind|pierde\s+la\s+vista|loses?\s+(his|her)?\s+sight|'
    r'muer[et][eo]|murio|dies?|killed|assassinat|'
    r'herido|wounded|injur|'
    r'traicion|betray|'
    r'cas[ao]|marr(ies?|iage)|'
    r'nac[eió]|born|gives?\s+birth'
    r')\b',
    re.IGNORECASE
)

_POSITION_PATTERNS = {
    "beginning": re.compile(
        r'\b(al\s+inicio|al\s+principio|al\s+comienzo|'
        r'at\s+the\s+(beginning|start)|how\s+(does|did)\s+.*\s+(start|begin)|'
        r'primera\s+parte|first\s+part)\b',
        re.IGNORECASE
    ),
    "end": re.compile(
        r'\b(al\s+final|al\s+t[eé]rmino|at\s+the\s+end|'
        r'how\s+does\s+.*\s+end|c[oó]mo\s+termina|'
        r'desenlace|ending|[uú]ltima\s+parte|last\s+part)\b',
        re.IGNORECASE
    ),
}

_OVERVIEW_PATTERNS = re.compile(
    r'\b('
    r'tema\s+principal|main\s+theme|temas?|themes?|'
    r'de\s+qu[eé]\s+(trata|va)|what\s+is\s+.*\s+about|'
    r'cu[eé]ntame\s+(sobre|acerca)|tell\s+me\s+about|'
    r'resumen\s+general|general\s+summary|overview|'
    r'qu[eé]\s+es\s+(el|la)\s+(libro|novela)|what\s+is\s+the\s+book|'
    r'de\s+qu[eé]\s+habla|what\s+does\s+.*\s+talk\s+about'
    r')\b',
    re.IGNORECASE
)


# ---------------------------------------------------------------------------
# UTILIDADES GENERALES
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s]", " ", s)
    return s


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _word_matches(word: str, query_words: list[str]) -> bool:
    if word in query_words:
        return True
    n = len(word)
    if n < 4:
        return False
    max_dist = 2 if n >= 6 else 1
    return any(_levenshtein(word, qw) <= max_dist for qw in query_words)


def _chunk_score(c: dict) -> float:
    try:
        return float(c.get("_additional", {}).get("score", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _top_score(chunks: list[dict]) -> float:
    return max((_chunk_score(c) for c in chunks[:5]), default=0.0)


def _merge_unique(base: list[dict], extra: list[dict]) -> list[dict]:
    seen_ids = {c.get("_additional", {}).get("id") for c in base}
    for c in extra:
        if c.get("_additional", {}).get("id") not in seen_ids:
            base.append(c)
    return base


def _is_junk_chunk(chunk: dict) -> bool:
    if chunk.get("chunk_index", 1) == 0:
        return True
    content = chunk.get("content", "")
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    if lines:
        chapter_lines = sum(
            1 for l in lines if re.match(r"^Chapter\s+\d+", l, re.IGNORECASE)
        )
        if chapter_lines / len(lines) > 0.5:
            return True
    return False


def _apply_score_filter(raw: list[dict]) -> list[dict]:
    if not raw:
        return raw
    scores    = [_chunk_score(c) for c in raw]
    top_score = max(scores) if scores else 0
    before    = len(raw)

    def _keep(c):
        s = _chunk_score(c)
        if s == 0.5:
            return False
        if top_score >= 0.6 and s < 0.35:
            return False
        return True

    filtered = [c for c in raw if _keep(c)]
    if len(filtered) >= MIN_CHUNKS_THRESHOLD:
        if len(filtered) < before:
            print(f"  Score filtrados: {before - len(filtered)}, quedan: {len(filtered)}")
        return filtered

    fallback = [c for c in raw if _chunk_score(c) != 0.5]
    print(f"  Solo score 0.5 filtrado, quedan: {len(fallback)}")
    return fallback


def _limit_chunks(chunks: list[dict]) -> list[dict]:
    selected, total = [], 0
    for c in chunks:
        content = c.get("content", "")
        if len(selected) >= MAX_CHUNKS_TO_LLM:
            break
        if total + len(content) > MAX_CONTEXT_CHARS:
            break
        selected.append(c)
        total += len(content)
    print(f"  Chunks al LLM: {len(selected)} con {total} chars")
    return selected


def _log_chunks(chunks: list[dict], label: str = "") -> None:
    for c in chunks[:10]:
        book_info = (c.get("book") or [{}])[0]
        print(
            f"     [{book_info.get('title','?')}] idx={c.get('chunk_index')} "
            f"score={_chunk_score(c):.4f} | {c.get('content','')[:60]}"
        )


# ---------------------------------------------------------------------------
# LLM: traduccion, enriquecimiento, re-ranking
# ---------------------------------------------------------------------------

def _call_groq(messages: list[dict], max_tokens: int = 100,
               temperature: float = 0, timeout: int = 6) -> str | None:
    """Helper centralizado para llamar a Groq. Devuelve el texto o None si falla."""
    if not GROQ_API_KEY:
        return None
    try:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=timeout,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        print(f"  Groq error status {r.status_code}")
    except Exception as e:
        print(f"  Groq call failed: {e}")
    return None


def _translate_query_llm(query: str) -> str:
    """Traduce la query al inglés para mejorar la búsqueda vectorial."""
    if _EN_PATTERN.search(query):
        return query
    result = _call_groq(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a translator. The user sends short Spanish search queries about books. "
                    "These are always questions or phrases, never names. "
                    "Translate to English. Reply ONLY with the translation, nothing else."
                )
            },
            {"role": "user", "content": query}
        ],
        max_tokens=80,
    )
    if result and result.lower() != query.lower():
        print(f"  Query traducido: '{query}' → '{result}'")
        return result
    return query


def _enrich_query_with_history(query: str, history: list) -> str:
    """
    Reemplaza pronombres y referencias vagas por entidades reales del historial.
    Solo actúa si hay historial. No agrega contexto narrativo ni fechas.
    """
    if not history:
        return query

    recent  = history[-3:]
    context = "\n".join(
        f"Q: {t.get('question', '')} A: {t.get('answer', '')[:200]}"
        for t in recent
    )
    result = _call_groq(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a search query optimizer. "
                    "Given a conversation history and a vague follow-up question, "
                    "rewrite the question to be self-contained by replacing pronouns and vague "
                    "references with the actual entities from the context. "
                    "CRITICAL: If the question refers to a continuation (e.g., 'el siguiente libro', "
                    "'la secuela', 'next book', 'después'), DO NOT replace these terms with the name "
                    "of the previous book. Keep the continuation reference intact. "
                    "Do NOT add extra context, dates, or narrative details. "
                    "Keep the rewritten question as short as possible. "
                    "Reply ONLY with the rewritten question, nothing else."
                )
            },
            {
                "role": "user",
                "content": f"History:\n{context}\n\nFollow-up question: {query}"
            }
        ],
        max_tokens=60,
    )
    if result and result.lower() != query.lower():
        print(f"  Query enriquecida: '{query}' → '{result}'")
        return result
    return query


def _rerank_chunks(query: str, chunks: list[dict], top_n: int = 10) -> list[dict]:
    """Re-ordena chunks por relevancia directa a la query usando el LLM."""
    if not chunks:
        return chunks

    snippets = "\n\n".join(
        f"[{i}] (Book: {(c.get('book') or [{}])[0].get('title', '?')}) {c.get('content', '')[:300]}"
        for i, c in enumerate(chunks)
    )
    prompt = (
        f"Query: {query}\n\n"
        f"Rank these {len(chunks)} passages by relevance to the query. "
        f"Prioritize passages that DIRECTLY answer the question. "
        f"If passages from different books are present, rank those that contain "
        f"the answer first, regardless of which book was previously discussed.\n"
        f"You MUST respond with ONLY a JSON array of integers. No text before or after.\n"
        f"Example for 4 passages: [2,0,3,1]\n\n"
        f"{snippets}\n\nJSON array:"
    )

    raw = _call_groq(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150,
        timeout=8,
    )
    if not raw:
        print("  Re-rank fallido, usando orden original")
        return chunks

    match = re.search(r'\[[\d,\s]+\]', raw)
    if not match:
        print("  Re-rank sin array JSON, usando orden original")
        return chunks

    indices       = json.loads(match.group())
    valid_indices = [i for i in indices if isinstance(i, int) and 0 <= i < len(chunks)]
    seen = set(valid_indices)
    for i in range(len(chunks)):
        if i not in seen:
            valid_indices.append(i)

    print(f"  Re-ranked top 5: {valid_indices[:5]}")
    return [chunks[i] for i in valid_indices][:top_n]


# ---------------------------------------------------------------------------
# DETECCIÓN DE LIBROS
# ---------------------------------------------------------------------------

def _get_all_books(client) -> list[dict]:
    """Devuelve todos los libros cargados en Weaviate (escalable a N libros)."""
    try:
        result = (
            client.query
            .get("Book", ["title"])
            .with_additional(["id"])
            .with_limit(200)
            .do()
        )
        return result.get("data", {}).get("Get", {}).get("Book", [])
    except Exception as e:
        print(f"  Error obteniendo libros: {e}")
        return []


def detect_mentioned_book_ids(text: str, client) -> list[str]:
    """
    Detecta qué libros se mencionan en el texto usando fuzzy match.
    Escala dinámicamente a cualquier número de libros en la base de datos.
    """
    books = _get_all_books(client)
    query_words = _normalize(text).split()
    mentioned   = []

    # Orden descendente por largo de título: evita que "DUNE" se active antes de "DUNE MESSIAH"
    books_sorted = sorted(
        books,
        key=lambda b: len(b.get("title", "")),
        reverse=True
    )

    for b in books_sorted:
        title   = b.get("title", "")
        book_id = b.get("_additional", {}).get("id", "")
        if not title or not book_id:
            continue

        title_words = _normalize(title).split()
        if not title_words:
            continue

        if len(title_words) == 1:
            matched = sum(1 for w in title_words if w in query_words)
        else:
            matched = sum(1 for w in title_words if _word_matches(w, query_words))

        ratio = matched / len(title_words)
        if ratio >= 0.6:
            mentioned.append(book_id)
            print(f"  Libro detectado: '{title}' id {book_id[:8]}...")
            # Consumir las palabras ya usadas para evitar doble detección
            for w in title_words:
                query_words = [qw for qw in query_words if not _word_matches(w, [qw])]

    return mentioned


# ---------------------------------------------------------------------------
# RESOLUCIÓN DE CONTEXTO DE CONVERSACIÓN
# ---------------------------------------------------------------------------

def _resolve_book_context(query: str, history: list, client) -> tuple[list[str], bool]:
    """
    Determina qué libro(s) son relevantes para la query actual.

    Retorna (book_ids, is_hint_only):
    - book_ids     : lista de IDs detectados (puede ser vacía)
    - is_hint_only : True  → el libro es una pista del historial, buscar en TODOS
                     False → el libro fue mencionado explícitamente en la query actual,
                             se puede restringir la búsqueda a ese libro

    REGLA CLAVE (escalabilidad multi-libro):
    El historial SIEMPRE produce hint_only=True.
    Solo la query actual del usuario puede dar hint_only=False.
    Esto garantiza que si el usuario pregunta sobre DUNE MESSIAH tras hablar de
    DUNE, el sistema no se queda atrapado buscando solo en DUNE.
    """

    # 1. Detectar en la query actual (máxima prioridad, hint_only=False)
    ids_in_query = detect_mentioned_book_ids(query, client)
    if ids_in_query:
        print(f"  Libro confirmado en query actual → búsqueda directa")
        return ids_in_query, False

    # 2. Si es pregunta de secuela, no limitamos por historial
    if _SEQUEL_PATTERNS.search(query):
        print("  Intención de secuela → búsqueda global sin filtro de libro")
        return [], False

    # 3. Buscar en el historial (SIEMPRE hint_only=True)
    if history:
        # Solo miramos las PREGUNTAS del usuario, nunca las respuestas del bot.
        # Las respuestas siempre mencionan el libro y generarían falsos positivos.
        for turn in reversed(history[-5:]):
            question = turn.get("question", "").strip()
            if not question:
                continue
            ids_in_history = detect_mentioned_book_ids(question, client)
            if ids_in_history:
                print(f"  Libro inferido de historial → hint_only (búsqueda en todos los libros)")
                return ids_in_history, True

    return [], False


# ---------------------------------------------------------------------------
# CLASIFICACIÓN DE QUERIES
# ---------------------------------------------------------------------------

def classify_query(query: str) -> dict:
    is_summary  = bool(_SUMMARY_PATTERNS.search(query))
    is_specific = bool(_SPECIFIC_PATTERNS.search(query))
    is_overview = bool(_OVERVIEW_PATTERNS.search(query))
    query_type  = "specific" if is_specific else ("summary" if is_summary else "specific")

    position = None
    for pos, pattern in _POSITION_PATTERNS.items():
        if pattern.search(query):
            position = pos
            break

    print(f"  Tipo: {query_type}"
          + (f" posicion: {position}" if position else "")
          + (f" overview: {is_overview}" if is_overview else ""))

    return {"type": query_type, "position": position, "is_overview": is_overview}


# ---------------------------------------------------------------------------
# EXPANSIÓN CON VECINOS ORDENADA POR SCORE
# ---------------------------------------------------------------------------

def _expand_and_sort_by_score(client, raw: list[dict], top_n: int,
                               window: int = 1) -> list[dict]:
    """
    Expande los top_n chunks con sus vecinos y reordena por score descendente.
    Los vecinos heredan el score del padre con penalización leve (×0.95).
    """
    score_map = {
        c.get("_additional", {}).get("id"): _chunk_score(c)
        for c in raw
    }

    top_raw  = raw[:top_n]
    expanded = expand_chunks_with_neighbors(client, top_raw, window=window)

    for c in expanded:
        cid = c.get("_additional", {}).get("id")
        if cid not in score_map:
            book_id   = c.get("book_id")
            chunk_idx = c.get("chunk_index", 0)
            parent_score = 0.0
            for orig in raw:
                if orig.get("book_id") == book_id:
                    diff = abs(orig.get("chunk_index", 0) - chunk_idx)
                    if diff <= window:
                        parent_score = max(parent_score, _chunk_score(orig))
            score_map[cid] = parent_score * 0.95

    expanded.sort(
        key=lambda c: score_map.get(c.get("_additional", {}).get("id"), 0.0),
        reverse=True,
    )
    print(f"  Total tras expandir: {len(expanded)} chunks")
    return expanded


# ---------------------------------------------------------------------------
# BÚSQUEDA GLOBAL EN TODOS LOS LIBROS
# ---------------------------------------------------------------------------

def _search_all_books(client, query_en: str, query_orig: str,
                      search_fn, limit: int = 8, **kwargs) -> list[dict]:
    """
    Busca en cada libro individualmente y combina resultados ordenados por score.
    Útil cuando no hay libro confirmado o en modo hint_only.
    Escalable: funciona igual con 2 libros o con 100.
    """
    books = list_books(client)
    raw   = []
    for book in books:
        bid   = book.get("_additional", {}).get("id")
        title = book.get("title", "?")
        per_book = _merge_unique(
            search_fn(client, query_en, limit=limit, book_id=bid, **kwargs),
            search_fn(client, query_en, limit=limit, book_id=bid, **kwargs),
        )
        if query_en != query_orig:
            per_book = _merge_unique(
                per_book,
                search_fn(client, query_orig, limit=limit // 2 + 1, book_id=bid, **kwargs)
            )
        print(f"  [{title}]: {len(per_book)} chunks")
        raw = _merge_unique(raw, per_book)

    raw.sort(key=_chunk_score, reverse=True)
    print(f"  Total global tras buscar en todos los libros: {len(raw)}")
    return raw


# ---------------------------------------------------------------------------
# PIPELINES DE BÚSQUEDA
# ---------------------------------------------------------------------------

def _search_specific(client, query: str, book_id: str | None,
                     hint_only: bool = False) -> list[dict]:
    """
    Pipeline para queries específicas usando BookChunk.

    hint_only=True  → busca en TODOS los libros y ordena por score vectorial.
    hint_only=False → puede restringir la búsqueda al libro confirmado;
                      amplía automáticamente si el score es bajo.
    """
    query_en = _translate_query_llm(query)

    if hint_only or not book_id:
        # Búsqueda global: deja que el score vectorial decida qué libro responde
        raw = _search_all_books(
            client, query_en, query,
            search_fn=search_chunks_hybrid,
            limit=8,
            alpha=0.5,
        )
        # Segunda pasada BM25 para capturar términos exactos
        raw = _merge_unique(raw, _search_all_books(
            client, query_en, query,
            search_fn=search_chunks_hybrid,
            limit=8,
            alpha=0.0,
        ))
        raw.sort(key=_chunk_score, reverse=True)

    else:
        # Búsqueda directa al libro confirmado
        raw = search_chunks_hybrid(client, query_en, limit=15, book_id=book_id, alpha=0.5)
        raw = _merge_unique(raw, search_chunks_hybrid(
            client, query_en, limit=15, book_id=book_id, alpha=0.0))
        if query_en != query:
            raw = _merge_unique(raw, search_chunks_hybrid(
                client, query, limit=10, book_id=book_id, alpha=0.5))

        # Si el score es bajo, ampliar a todos los libros
        if _top_score(raw) < MIN_RELEVANCE_SCORE or len(raw) < MIN_CHUNKS_THRESHOLD:
            print(f"  Score bajo o pocos chunks ({len(raw)}), ampliando a todos los libros...")
            raw = _merge_unique(raw, _search_all_books(
                client, query_en, query,
                search_fn=search_chunks_hybrid,
                limit=10, alpha=0.5,
            ))
            raw.sort(key=_chunk_score, reverse=True)

    # Filtrar junk y scores irrelevantes
    before = len(raw)
    raw    = [c for c in raw if not _is_junk_chunk(c)]
    print(f"  Junk filtrados: {before - len(raw)}, quedan: {len(raw)}")
    raw = _apply_score_filter(raw)
    _log_chunks(raw)

    # Expandir con vecinos y re-rankear
    top_n_expand = 10 if (hint_only or not book_id) else 6
    expanded     = _expand_and_sort_by_score(client, raw, top_n=top_n_expand, window=1)
    reranked     = _rerank_chunks(query, expanded, top_n=MAX_CHUNKS_TO_LLM + 2)
    return _limit_chunks(reranked)


def _search_summary(client, query: str, book_id: str | None,
                    position: str | None, hint_only: bool = False,
                    is_overview: bool = False) -> list[dict]:
    """
    Pipeline para queries de resumen usando BookSummary.
    Si hint_only o score bajo, amplía a todos los libros.
    """
    query_en = _translate_query_llm(query)

    # Intentar overview primero si hay libro confirmado
    if book_id and not hint_only and (is_overview or not position):
        overview_chunks = search_summaries_hybrid(
            client, query_en, limit=2, book_id=book_id,
            position="overview", alpha=0.75,
        )
        if overview_chunks:
            print("  Overview encontrado, combinando con summaries normales")
            raw = _merge_unique(
                overview_chunks,
                search_summaries_hybrid(
                    client, query_en, limit=4, book_id=book_id,
                    position=position, alpha=0.75,
                )
            )
            return _limit_chunks(raw)

    # Búsqueda normal de summaries
    raw = search_summaries_hybrid(
        client, query_en, limit=6, book_id=book_id,
        position=position, alpha=0.75,
    )

    if len(raw) < MIN_CHUNKS_THRESHOLD and position:
        print("  Pocos summaries con posición, buscando sin filtro...")
        raw = _merge_unique(raw, search_summaries_hybrid(
            client, query_en, limit=6, book_id=book_id,
            position=None, alpha=0.75))

    top = _top_score(raw)
    if hint_only or not book_id or top < MIN_RELEVANCE_SCORE:
        print(f"  Top score: {top:.4f} o hint_only → ampliando summaries a todos los libros...")
        extra = search_summaries_hybrid(
            client, query_en, limit=6, position=position, alpha=0.75)
        raw = _merge_unique(raw, extra)
        raw.sort(key=_chunk_score, reverse=True)

    # Fallback a chunks si no hay summaries
    if not raw:
        print("  Sin summaries, fallback a BookChunk...")
        raw = search_chunks_hybrid(client, query_en, limit=15, book_id=book_id, alpha=0.5)
        raw = _merge_unique(raw, search_chunks_hybrid(
            client, query_en, limit=15, book_id=book_id, alpha=0.0))
        raw = [c for c in raw if not _is_junk_chunk(c)]
        raw = expand_chunks_with_neighbors(client, raw, window=1)

    return _limit_chunks(raw)


# ---------------------------------------------------------------------------
# PUNTO DE ENTRADA PRINCIPAL
# ---------------------------------------------------------------------------

def search_chunks(query: str, history: list | None = None) -> list[dict]:
    """
    Pipeline RAG principal. Flujo:

    1. Detectar libro en la query ORIGINAL (antes de enriquecer).
       - Si se menciona explícitamente → búsqueda directa (hint_only=False).
       - Si es pregunta de secuela → búsqueda global sin restricción de libro.

    2. Si no hay libro en la query, buscarlo en el HISTORIAL (solo preguntas,
       nunca respuestas del bot). El historial SIEMPRE produce hint_only=True.

    3. Con hint_only=True el sistema busca en TODOS los libros y deja que
       el score vectorial decida cuál responde la pregunta. Esto resuelve
       el caso DUNE → DUNE MESSIAH y es escalable a cualquier número de libros.

    4. Si no se detecta ningún libro y hay múltiples libros cargados,
       solicitar aclaración al usuario.
    """
    client = current_app.config["WEAVIATE_CLIENT"]

    # Paso 1: resolver contexto de libro con la query ORIGINAL
    mentioned_ids, hint_only = _resolve_book_context(query, history or [], client)

    # Paso 2: enriquecer la query para mejorar el retrieval vectorial
    enriched_query = _enrich_query_with_history(query, history or [])

    # Paso 3: clasificar la query enriquecida
    classification = classify_query(enriched_query)
    query_type     = classification["type"]
    position       = classification["position"]
    is_overview    = classification["is_overview"]

    # Paso 4: si no hay libro y hay múltiples libros, pedir aclaración
    if not mentioned_ids:
        available = list_books(client)
        if len(available) > 1:
            print("  Sin libro detectado en query ni historial → solicitando aclaración")
            return [{"__ask_user__": True}]

    book_id = mentioned_ids[0] if len(mentioned_ids) == 1 else None

    # Paso 5: ejecutar pipeline según tipo de query
    if query_type == "summary":
        print("  Modo summary → usando BookSummary")
        return _search_summary(
            client, enriched_query, book_id, position,
            hint_only=hint_only,
            is_overview=is_overview,
        )
    else:
        print("  Modo specific → usando BookChunk")
        return _search_specific(client, enriched_query, book_id, hint_only=hint_only)