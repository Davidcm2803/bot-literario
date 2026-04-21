"""
weaviate_service.py — Pipeline RAG escalable v2.4

Cambios vs v2.3:
  - force_global rediseñado: en vez de buscar todos los libros en una sola
    llamada _search_parallel (donde Weaviate normaliza scores entre libros y
    aplasta los chunks relevantes), ahora se hacen DOS búsquedas separadas:
      1. Búsqueda en los libros del scope original (DUNE)
      2. Búsqueda en el resto de libros (DUNE MESSIAH, etc.)
    Cada búsqueda tiene su propio espacio de ranking. Luego se mergean
    poniendo el scope primero. Así idx=174 de DUNE MESSIAH no queda
    aplastado por chunks de The Antichrist o The Prince.
"""

import os, re, json, unicodedata, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import current_app
from dotenv import load_dotenv
from models.books import (
    search_chunks_hybrid, search_summaries_hybrid,
    expand_chunks_with_neighbors, list_books,
)

load_dotenv("key.env")

GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL   = "llama-3.3-70b-versatile"

MAX_CHUNKS_LLM    = 8
MAX_CONTEXT_CHARS = 18_000
SEARCH_LIMIT      = 10
NEIGHBOR_SCORE    = 0.85
NEIGHBOR_TOP_N    = 3
MIN_SCORE         = 0.25
FOREIGN_CAP       = 2


# ─────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────

def _normalize(text):
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", " ", text)

def _score(chunk):
    try:
        return float(chunk.get("_additional", {}).get("score", 0) or 0)
    except (TypeError, ValueError):
        return 0.0

def _chunk_id(chunk):
    return chunk.get("_additional", {}).get("id", "")

def _book_id(chunk):
    return chunk.get("book_id", "")

def _merge(base, extra):
    seen = {_chunk_id(c) for c in base}
    return base + [c for c in extra if _chunk_id(c) not in seen]

def _limit(chunks):
    result, total = [], 0
    for c in chunks:
        n = len(c.get("content", ""))
        if len(result) >= MAX_CHUNKS_LLM or total + n > MAX_CONTEXT_CHARS:
            break
        result.append(c)
        total += n
    print(f"  -> {len(result)} chunks al LLM ({total} chars)")
    return result


# ─────────────────────────────────────────────
# Filtro de junk
# ─────────────────────────────────────────────

_JUNK_HEADERS = re.compile(
    r'\b(glossary|glosario|appendix|ap[eé]ndice|bibliography|'
    r'index|[íi]ndice|copyright|all\s+rights\s+reserved|'
    r'publishing\s+group|printed\s+in)\b',
    re.IGNORECASE,
)
_GLOSSARY_LINE = re.compile(r'^[A-Z\s\'\-]{3,40}:\s', re.MULTILINE)

def _is_junk(chunk):
    if chunk.get("chunk_index", 1) == 0:
        return True
    content = chunk.get("content", "")
    if _JUNK_HEADERS.search(content[:300]):
        return True
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    if not lines:
        return False
    glossary = sum(1 for l in lines if _GLOSSARY_LINE.match(l)) / len(lines)
    chapters = sum(1 for l in lines if re.match(r"^Chapter\s+\d+", l, re.I)) / len(lines)
    return glossary > 0.5 or chapters > 0.5


# ─────────────────────────────────────────────
# Groq helpers
# ─────────────────────────────────────────────

_translation_cache: dict = {}
_IS_ENGLISH = re.compile(
    r'\b(what|who|how|when|where|does|did|is|are|the|of|in|and|his|her)\b',
    re.IGNORECASE,
)

def _groq(messages, max_tokens=120, temperature=0.0):
    if not GROQ_API_KEY:
        return None
    try:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": GROQ_MODEL, "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature},
            timeout=8,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        print(f"  Groq {r.status_code}")
    except Exception as e:
        print(f"  Groq error: {e}")
    return None

def _to_english(query):
    if _IS_ENGLISH.search(query):
        return query
    if query in _translation_cache:
        return _translation_cache[query]
    result = _groq([
        {"role": "system", "content":
            "Translate the user's short Spanish search query to English. "
            "Reply ONLY with the translation, nothing else."},
        {"role": "user", "content": query},
    ], max_tokens=60)
    translated = result if (result and result.lower() != query.lower()) else query
    _translation_cache[query] = translated
    if translated != query:
        print(f"  Traduccion: '{query}' -> '{translated}'")
    return translated

def _enrich_query(query, history):
    if not history:
        return query
    context = "\n".join(
        f"Q: {t['question']}"
        for t in history[-4:] if t.get("question", "").strip()
    )
    if not context:
        return query
    result = _groq([
        {"role": "system", "content":
            "You are a query rewriter for a book Q&A system.\n"
            "Rewrite the follow-up question to be self-contained by replacing pronouns "
            "(he, she, it, they, el, ella, su, sus) with the actual character/book names "
            "from the previous questions.\n"
            "Rules:\n"
            "- Only use names/facts from the previous QUESTIONS, never from answers.\n"
            "- Do NOT add book titles.\n"
            "- Keep it short (max 10 words).\n"
            "- Reply ONLY with the rewritten query, nothing else."},
        {"role": "user", "content":
            f"Previous questions:\n{context}\n\nFollow-up: {query}"},
    ], max_tokens=60)
    if result and result.strip() and result.lower() != query.lower():
        result = re.sub(r'^[¡¿"\']+|[!"\']+$', '', result).strip()
        print(f"  Query enriquecida: '{query}' -> '{result}'")
        return result
    return query

def _rerank(query_en, chunks, preferred_ids=None):
    if len(chunks) <= 2:
        return chunks
    snippets = "\n\n".join(
        f"[{i}] ({(c.get('book') or [{}])[0].get('title', '?')}) "
        f"{c.get('content', '')[:250]}"
        for i, c in enumerate(chunks)
    )
    preferred_hint = ""
    if preferred_ids:
        titles = list({
            (c.get("book") or [{}])[0].get("title", "")
            for c in chunks if _book_id(c) in preferred_ids
            if (c.get("book") or [{}])[0].get("title")
        })
        if titles:
            preferred_hint = (
                f"IMPORTANT: The question is most likely about: {', '.join(titles)}. "
                f"Rank passages from those books FIRST if relevant.\n"
            )
    raw = _groq([{"role": "user", "content":
        f"Query: {query_en}\n\n"
        f"{preferred_hint}"
        f"Rank these {len(chunks)} passages by relevance. "
        f"Passages that DIRECTLY answer the question go first. "
        f"Reply ONLY with a JSON integer array like [2,0,3,1]. No other text.\n\n"
        f"{snippets}\n\nJSON array:"
    }], max_tokens=200)
    if not raw:
        return chunks
    match = re.search(r'\[[\d,\s]+\]', raw)
    if not match:
        return chunks
    indices = json.loads(match.group())
    valid  = [i for i in indices if isinstance(i, int) and 0 <= i < len(chunks)]
    seen   = set(valid)
    valid += [i for i in range(len(chunks)) if i not in seen]
    print(f"  Re-rank top5: {valid[:5]}")
    return [chunks[i] for i in valid]


# ─────────────────────────────────────────────
# Detección de libros
# ─────────────────────────────────────────────

def _levenshtein(a, b):
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j+1]+1, curr[j]+1, prev[j]+(ca != cb)))
        prev = curr
    return prev[-1]

def _fuzzy_word(word, targets):
    if word in targets:
        return True
    n = len(word)
    if n < 4:
        return False
    max_dist = 2 if n >= 6 else 1
    return any(_levenshtein(word, t) <= max_dist for t in targets)

def _get_all_books(client):
    try:
        result = (
            client.query.get("Book", ["title", "author"])
            .with_additional(["id"]).with_limit(500).do()
        )
        return result.get("data", {}).get("Get", {}).get("Book", []) or []
    except Exception as e:
        print(f"  Error listando libros: {e}")
        return []

def _detect_books(text, all_books):
    words, matched, consumed = _normalize(text).split(), [], set()
    for book in sorted(all_books, key=lambda b: len(b.get("title", "")), reverse=True):
        title = book.get("title", "")
        bid   = book.get("_additional", {}).get("id", "")
        if not title or not bid:
            continue
        title_words = _normalize(title).split()
        available   = [w for w in words if w not in consumed]
        hits = sum(1 for w in title_words if _fuzzy_word(w, available))
        if hits / len(title_words) >= 0.6:
            matched.append(book)
            print(f"  Libro detectado: '{title}'")
            for w in title_words:
                consumed.add(w)
    return matched

_SEQUEL_PATTERNS = re.compile(
    r'\b(siguiente|secuela|continuaci[oó]n|pr[oó]ximo|despu[eé]s|next|sequel|after)\b',
    re.IGNORECASE,
)

def _resolve_scope(query, history, client):
    all_books = _get_all_books(client)
    in_query  = _detect_books(query, all_books)
    if in_query:
        print("  Scope: libro en query actual")
        return in_query, False
    if _SEQUEL_PATTERNS.search(query):
        print("  Scope: intención de secuela -> búsqueda global")
        return [], False
    for turn in reversed((history or [])[-6:]):
        q = turn.get("question", "").strip()
        if not q:
            continue
        in_history = _detect_books(q, all_books)
        if in_history:
            print("  Scope: libro inferido del historial (hint_only)")
            return in_history, True
    return [], False


# ─────────────────────────────────────────────
# Clasificador
# ─────────────────────────────────────────────

_SUMMARY_PATTERNS = re.compile(
    r'\b('
    r'qu[eé]\s+(pas[oó]|ocurri[oó]|le\s+pas[oó]|sucedi[oó]|hace|hizo|pasa)|'
    r'(trata|habla|cuenta)\s+(el|la|los)?\s*(libro|historia|novela)?|'
    r'resumen|resume|summarize|summary|synopsis|sinopsis|'
    r'de\s+qu[eé]\s+(trata|va)|what\s+is\s+.*\s+about|'
    r'cu[eé]ntame|tell\s+me\s+about|'
    r'qu[eé]\s+rol|what\s+role|'
    r'tema\s+principal|main\s+theme|temas?|themes?|'
    r'qu[eé]\s+pasa\s+(en|al|con)|what\s+happens'
    r')\b',
    re.IGNORECASE,
)

_CHARACTER_PATTERNS = re.compile(
    r'\b('
    r'hijos?|sons?|daughters?|children|'
    r'nombre|llama[mn]?|called|named|'
    r'qui[eé]n\s+es|who\s+is|'
    r'esposa|esposo|wife|husband|'
    r'hermano|hermana|brother|sister|'
    r'padre|madre|father|mother'
    r')\b',
    re.IGNORECASE,
)

_OVERVIEW_PATTERNS = re.compile(
    r'\b('
    r'de\s+qu[eé]\s+(trata|va)|what\s+is\s+.*\s+about|'
    r'resumen\s+general|overview|qu[eé]\s+es\s+(el|la)\s+(libro|novela)'
    r')\b',
    re.IGNORECASE,
)

_POSITION_PATTERNS = {
    "beginning": re.compile(
        r'\b(al\s+inicio|al\s+principio|al\s+comienzo|'
        r'at\s+the\s+(beginning|start)|primera\s+parte|first\s+part)\b',
        re.IGNORECASE,
    ),
    "end": re.compile(
        r'\b(al\s+final|at\s+the\s+end|c[oó]mo\s+termina|'
        r'desenlace|ending|[uú]ltima\s+parte|last\s+part)\b',
        re.IGNORECASE,
    ),
}

_QUOTED_TERM = re.compile(r'"[^"]+"')


def _classify(query: str) -> dict:
    is_summary  = bool(_SUMMARY_PATTERNS.search(query))
    is_char     = bool(_CHARACTER_PATTERNS.search(query))
    is_overview = bool(_OVERVIEW_PATTERNS.search(query))
    has_quoted  = bool(_QUOTED_TERM.search(query))

    if is_summary:
        query_type = "summary"
    elif is_char:
        query_type = "character_lookup"
    else:
        query_type = "specific"

    if query_type == "summary":
        alpha = 0.85
    elif query_type == "character_lookup":
        alpha = 0.75
    elif has_quoted:
        alpha = 0.30
    else:
        alpha = 0.65

    position = None
    for pos, pattern in _POSITION_PATTERNS.items():
        if pattern.search(query):
            position = pos
            break

    print(f"  Tipo: {query_type} alpha={alpha}"
          + (f" pos:{position}" if position else "")
          + (" overview" if is_overview else "")
          + (" [quoted]" if has_quoted else ""))

    return {
        "type":        query_type,
        "alpha":       alpha,
        "position":    position,
        "is_overview": is_overview,
    }


# ─────────────────────────────────────────────
# Sinónimos de evento
# ─────────────────────────────────────────────

_EVENT_SYNONYMS: list[tuple[re.Pattern, list[str]]] = [
    (
        re.compile(
            r'cieg[oa]|blind|pierde\s+la\s+vista|queda\s+ciego|goes?\s+blind',
            re.IGNORECASE,
        ),
        [
            "blind stone burner blindsight vision eyes",
            "ciego ojos visión stone burner",
        ],
    ),
    (
        re.compile(
            r'muer[et][eo]|murio|muere|muerte|dies?|killed|assassin',
            re.IGNORECASE,
        ),
        [
            "death dies killed assassination poison",
            "muerte asesinato muere veneno",
        ],
    ),
    (
        re.compile(r'traicion|betray', re.IGNORECASE),
        [
            "betrayal traitor conspiracy plot",
            "traición traidor conspiración",
        ],
    ),
    (
        re.compile(r'herido|wounded|injur', re.IGNORECASE),
        [
            "wounded injured battle fight",
            "herido batalla pelea",
        ],
    ),
    (
        re.compile(r'cas[ao]|marr(ies?|iage)|boda|matrimonio', re.IGNORECASE),
        [
            "marriage wedding ceremony wife husband",
            "boda matrimonio esposa esposo",
        ],
    ),
]


def _get_event_synonyms(query: str) -> list[str]:
    for pattern, synonyms in _EVENT_SYNONYMS:
        if pattern.search(query):
            print(f"  Evento detectado: +{len(synonyms)} queries BM25 extra")
            return synonyms
    return []


# ─────────────────────────────────────────────
# Búsqueda híbrida
# ─────────────────────────────────────────────

def _search_one(client, bid, query_en, query_orig, alpha, extra_bm25=None):
    vector = search_chunks_hybrid(
        client, query_en, limit=SEARCH_LIMIT, book_id=bid, alpha=alpha)
    bm25 = search_chunks_hybrid(
        client, query_en, limit=SEARCH_LIMIT, book_id=bid, alpha=0.0)
    result = _merge(vector, bm25)

    if query_en != query_orig:
        orig_v = search_chunks_hybrid(
            client, query_orig, limit=SEARCH_LIMIT, book_id=bid, alpha=alpha)
        orig_b = search_chunks_hybrid(
            client, query_orig, limit=SEARCH_LIMIT, book_id=bid, alpha=0.0)
        result = _merge(result, _merge(orig_v, orig_b))

    if extra_bm25:
        for synonym_query in extra_bm25:
            extra = search_chunks_hybrid(
                client, synonym_query, limit=6, book_id=bid, alpha=0.0)
            result = _merge(result, extra)

    return result


def _search_parallel(client, book_ids, query_en, query_orig, alpha, extra_bm25=None):
    """
    Busca en múltiples libros en paralelo. Cada libro se busca con filtro
    por book_id, así cada uno tiene su propio espacio de ranking independiente.
    Los scores NO se normalizan entre libros.
    """
    all_chunks = []
    with ThreadPoolExecutor(max_workers=min(len(book_ids), 8)) as pool:
        futures = {
            pool.submit(
                _search_one, client, bid, query_en, query_orig, alpha, extra_bm25
            ): bid
            for bid in book_ids
        }
        for future in as_completed(futures):
            bid = futures[future]
            try:
                chunks = future.result()
                print(f"  [{bid[:8]}]: {len(chunks)} chunks")
                all_chunks = _merge(all_chunks, chunks)
            except Exception as e:
                print(f"  Error {bid[:8]}: {e}")
    all_chunks.sort(key=_score, reverse=True)
    return all_chunks


def _search_event_global(client, scope_ids, other_ids,
                         query_en, query_orig, alpha, extra_bm25):
    """
    Búsqueda en dos fases independientes para queries con evento concreto.

    El problema que resuelve: cuando Weaviate busca sin filtro de book_id
    (o en un _search_parallel con todos los libros mezclados en el merge),
    los scores se normalizan entre todos los chunks devueltos. Un chunk de
    DUNE MESSIAH con score=1.0 en búsqueda aislada puede quedar en posición
    15 cuando compite contra 141 chunks de otros libros.

    La solución: cada grupo (scope vs otros) tiene su propia pasada de
    _search_parallel donde cada libro ya busca con book_id filter. Los
    scores dentro de cada grupo son comparables. Luego se mergean poniendo
    el scope primero para que _cap_foreign funcione correctamente.
    """
    print(f"  Búsqueda evento — fase 1: scope ({len(scope_ids)} libro(s))")
    scope_chunks = _search_parallel(
        client, list(scope_ids), query_en, query_orig, alpha, extra_bm25)

    other_chunks = []
    if other_ids:
        print(f"  Búsqueda evento — fase 2: otros ({len(other_ids)} libro(s))")
        other_chunks = _search_parallel(
            client, list(other_ids), query_en, query_orig, alpha, extra_bm25)

    merged = _merge(scope_chunks, other_chunks)
    print(f"  Merge evento: {len(scope_chunks)} scope + {len(other_chunks)} otros "
          f"= {len(merged)} total")
    return merged


def _cap_foreign(chunks, scope_ids):
    result, counts = [], {}
    for c in chunks:
        bid = _book_id(c)
        if bid in scope_ids:
            result.append(c)
        else:
            counts[bid] = counts.get(bid, 0) + 1
            if counts[bid] <= FOREIGN_CAP:
                result.append(c)
    removed = len(chunks) - len(result)
    if removed:
        print(f"  Cap foreign: -{removed} chunks fuera de scope")
    return result


def _expand_scope_ids(raw, scope_ids, top_n=3):
    """
    Si alguno de los top-N chunks viene de un libro fuera del scope
    con score >= 0.8, lo incorpora al scope antes de _cap_foreign.
    """
    top_books = {
        _book_id(c) for c in raw[:top_n]
        if _score(c) >= 0.8 and _book_id(c) not in scope_ids
    }
    if top_books:
        print(f"  Scope expandido con: {top_books}")
    return scope_ids | top_books


# ─────────────────────────────────────────────
# Expansión de vecinos controlada
# ─────────────────────────────────────────────

def _expand_top_chunks(client, chunks):
    candidates = [c for c in chunks if _score(c) >= NEIGHBOR_SCORE][:NEIGHBOR_TOP_N]
    if not candidates:
        print("  Sin vecinos (ningún chunk supera umbral)")
        return chunks
    expanded = expand_chunks_with_neighbors(client, candidates, window=1)
    expanded = [c for c in expanded if not _is_junk(c)]
    result   = _merge(chunks, expanded)
    print(f"  Vecinos: +{len(result) - len(chunks)} chunks "
          f"(de {len(candidates)} chunks con score >= {NEIGHBOR_SCORE})")
    return result


# ─────────────────────────────────────────────
# Pipeline principal
# ─────────────────────────────────────────────

def search_chunks(query: str, history: list | None = None) -> list[dict]:
    """
    Pipeline RAG v2.4

    1.  Scope:         libro en query → historial → pedir aclaración
    2.  Enriquecer:    reescribir con contexto de preguntas anteriores
    3.  Clasificar:    summary | character_lookup | specific + alpha
    4.  Sinónimos:     detectar evento y preparar BM25 extra
    5.  Traducir:      solo para retrieval vectorial
    6.  Chunks:
          · Normal:        búsqueda directa en scope
          · hint_only:     búsqueda en todos los libros (una pasada)
          · force_global:  _search_event_global — DOS pasadas independientes:
              Fase 1: scope (DUNE) → scores propios, no contaminados
              Fase 2: otros libros (DUNE MESSIAH…) → scores propios
              Merge: scope primero, luego otros
              Así idx=174 de DUNE MESSIAH rankea 1.0 en su espacio propio
              y no queda aplastado por The Prince o The Antichrist
    7.  Summary-first: si es summary → BookSummary al frente
    8.  Filtrar:       junk + score mínimo
                       + expand_scope_ids si force_global
                       + cap foráneos
    9.  Vecinos:       solo top-3 con score >= 0.85, no para summaries
    10. Re-rank:       LLM sobre max 11 candidatos
    11. Retornar:      <= 8 chunks al LLM final
    """
    client  = current_app.config["WEAVIATE_CLIENT"]
    history = history or []

    # ── 1. Scope ──────────────────────────────────────────
    matched_books, hint_only = _resolve_scope(query, history, client)

    if not matched_books:
        all_books = _get_all_books(client)
        if len(all_books) > 1:
            print("  Sin libro detectado -> pidiendo aclaración")
            return [{"__ask_user__": True}]
        matched_books = all_books

    scope_ids = {
        b.get("_additional", {}).get("id")
        for b in matched_books
        if b.get("_additional", {}).get("id")
    }

    # ── 2. Enriquecer ─────────────────────────────────────
    enriched = _enrich_query(query, history)

    # ── 3. Clasificar ─────────────────────────────────────
    classification = _classify(enriched)
    alpha = classification["alpha"]

    # ── 4. Sinónimos de evento ────────────────────────────
    extra_bm25 = _get_event_synonyms(enriched)

    # ── 5. Traducir ───────────────────────────────────────
    query_en = _to_english(enriched)

    # ── 6. Chunks ─────────────────────────────────────────
    force_global = bool(extra_bm25) and hint_only

    if force_global:
        all_books_list = _get_all_books(client)
        all_ids = {
            b.get("_additional", {}).get("id")
            for b in all_books_list
            if b.get("_additional", {}).get("id")
        }
        other_ids = all_ids - scope_ids
        raw = _search_event_global(
            client, scope_ids, other_ids, query_en, enriched, alpha, extra_bm25)

    elif hint_only:
        all_ids = [
            b.get("_additional", {}).get("id")
            for b in _get_all_books(client)
            if b.get("_additional", {}).get("id")
        ]
        print(f"  Búsqueda global hint_only ({len(all_ids)} libros) alpha={alpha}")
        raw = _search_parallel(client, all_ids, query_en, enriched, alpha, extra_bm25)

    else:
        print(f"  Búsqueda directa ({len(scope_ids)} libro(s)) alpha={alpha}")
        raw = _search_parallel(
            client, list(scope_ids), query_en, enriched, alpha, extra_bm25)

    # ── 7. Summary-first ──────────────────────────────────
    summaries_raw = []
    if classification["type"] == "summary":
        book_id_for_summary = next(iter(scope_ids), None) if not hint_only else None

        summaries_raw = search_summaries_hybrid(
            client, query_en, limit=4,
            book_id=book_id_for_summary,
            position=classification["position"],
            alpha=0.85,
        )

        if classification["is_overview"] and book_id_for_summary:
            overviews = search_summaries_hybrid(
                client, query_en, limit=2,
                book_id=book_id_for_summary,
                position="overview", alpha=0.85,
            )
            summaries_raw = _merge(overviews, summaries_raw)

        if summaries_raw:
            print(f"  Summaries: {len(summaries_raw)} traídos")
            raw = _merge(summaries_raw, raw)

    if not raw:
        print("  Sin resultados")
        return []

    # ── 8. Filtrar ────────────────────────────────────────
    raw = [c for c in raw if not _is_junk(c)]
    raw = [c for c in raw if _score(c) >= MIN_SCORE]

    if force_global:
        scope_ids = _expand_scope_ids(raw, scope_ids)

    raw = _cap_foreign(raw, scope_ids)
    raw.sort(key=_score, reverse=True)

    print(f"  Tras filtros: {len(raw)} chunks")
    for c in raw[:5]:
        title = (c.get("book") or [{}])[0].get("title", "?")
        idx   = c.get("chunk_index", c.get("summary_index", "?"))
        src   = "SUM" if c.get("summary_index") is not None else "CHK"
        print(f"    [{src}][{title}] idx={idx} score={_score(c):.4f} | "
              f"{c.get('content', '')[:60]}")

    if not raw:
        return []

    # ── 9. Vecinos controlados ────────────────────────────
    chunks_only = [c for c in raw if c.get("summary_index") is None]

    if classification["type"] != "summary":
        chunks_only = _expand_top_chunks(client, chunks_only)

    if summaries_raw:
        expanded = _merge(summaries_raw, chunks_only)
    else:
        expanded = chunks_only

    # Garantizar representación del scope si hint_only
    if hint_only and scope_ids:
        in_exp = {_book_id(c) for c in expanded}
        for sid in scope_ids:
            if sid not in in_exp:
                fallback = sorted(
                    [c for c in raw if _book_id(c) == sid],
                    key=_score, reverse=True
                )[:2]
                expanded = _merge(expanded, fallback)
                if fallback:
                    print(f"  Representación forzada: +{len(fallback)} chunks de {sid[:8]}")

    expanded.sort(key=_score, reverse=True)

    # ── 10. Re-rank ───────────────────────────────────────
    preferred = scope_ids if hint_only else None
    reranked  = _rerank(query_en, expanded[:MAX_CHUNKS_LLM + 3], preferred_ids=preferred)

    # ── 11. Retornar ──────────────────────────────────────
    return _limit(reranked)