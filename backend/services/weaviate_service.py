import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import json
import requests
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import current_app
from dotenv import load_dotenv

from models.books import (
    search_chunks_hybrid,
    search_summaries_hybrid,
    expand_chunks_with_neighbors,
    list_books,
)

load_dotenv(".env")

MAX_CHUNKS_TO_LLM    = 10
MAX_CONTEXT_CHARS    = 20_000
MIN_CHUNKS_THRESHOLD = 4
MIN_RELEVANCE_SCORE  = 0.55

LIMIT_VECTOR         = 5
LIMIT_BM25           = 4
BM25_SKIP_THRESHOLD  = 0.75

TOP_N_EXPAND_GLOBAL  = 7
TOP_N_EXPAND_DIRECT  = 5

GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Detecta si la query ya esta en ingles para saltar la traduccion
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
    r'muer[et][eo]|murio|muere|muerte|dies?|killed|assassinat|'
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
        r'desenlace|ending|[uú]ltima\s+parte|last\s+part|'
        r'muer[et][eo]|murio|muere|muerte|dies?|killed|'
        r'sobrevive|survive[sd]?|c[oó]mo\s+acaba|'
        r'destino\s+de|fate\s+of)\b',
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

# Detecta preguntas sobre nombres propios para usar BM25 en vez de vectorial
_NAME_QUERY_PATTERNS = re.compile(
    r'\b('
    r'c[oó]mo\s+se\s+llama[mn]?|nombres?\s+de|how\s+are\s+.*\s+called|'
    r'what\s+(are\s+the\s+)?names?\s+(of|are)|'
    r'qui[eé]n(es)?\s+son|who\s+are\s+the|'
    r'c[oó]mo\s+llaman|llamado[s]?|named?\s+after'
    r')\b',
    re.IGNORECASE
)

# Cache de traducciones en memoria para no repetir llamadas a Groq
_translation_cache: dict[str, str] = {}


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


_GLOSSARY_PATTERNS = re.compile(
    r'^[A-Z\s\'\-]{3,40}:\s',
    re.MULTILINE
)
_APPENDIX_HEADERS = re.compile(
    r'\b(glossary|glosario|appendix|ap[eé]ndice|bibliography|bibliograf[ií]a|'
    r'index|[íi]ndice|terminology|terminolog[íi]a|'
    r'publishing\s+group|orion\s+publishing|gollancz|copyright|'
    r'all\s+rights\s+reserved|printed\s+in)\b',
    re.IGNORECASE
)


def _is_junk_chunk(chunk: dict) -> bool:
    """Descarta chunks que son glosario, indice, apendice o tabla de contenidos."""
    if chunk.get("chunk_index", 1) == 0:
        return True
    content = chunk.get("content", "")
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    if not lines:
        return False
    chapter_lines = sum(1 for l in lines if re.match(r"^Chapter\s+\d+", l, re.IGNORECASE))
    if chapter_lines / len(lines) > 0.5:
        return True
    glossary_lines = sum(1 for l in lines if _GLOSSARY_PATTERNS.match(l))
    if len(lines) >= 3 and glossary_lines / len(lines) > 0.5:
        return True
    if _APPENDIX_HEADERS.search(content[:300]):
        return True
    return False


# Score artificial para tail chunks: no deben competir con hits vectoriales reales
_TAIL_CHUNK_SCORE = 0.4


def _apply_score_filter(raw: list[dict]) -> list[dict]:
    """
    Filtra chunks de baja relevancia.
    Garantiza al menos un chunk por libro para evitar que un libro con score alto
    elimine completamente a otro libro que puede tener la respuesta correcta.
    """
    if not raw:
        return raw

    tail = [c for c in raw if _chunk_score(c) == _TAIL_CHUNK_SCORE]
    hits = [c for c in raw if _chunk_score(c) != _TAIL_CHUNK_SCORE]

    scores    = [_chunk_score(c) for c in hits] if hits else [0.0]
    top_score = max(scores) if scores else 0
    before    = len(hits)

    # Guardar el mejor score de cada libro para protegerlo del filtro
    book_best_score: dict[str, float] = {}
    for c in hits:
        bid = c.get("book_id", "")
        s   = _chunk_score(c)
        if bid not in book_best_score or s > book_best_score[bid]:
            book_best_score[bid] = s

    def _keep(c):
        s   = _chunk_score(c)
        bid = c.get("book_id", "")
        if s == 0.5:
            return False
        # El mejor chunk de cada libro siempre pasa
        if book_best_score.get(bid) == s:
            return True
        if top_score >= 0.6 and s < 0.35:
            return False
        return True

    filtered = [c for c in hits if _keep(c)]
    if len(filtered) < MIN_CHUNKS_THRESHOLD:
        filtered = [c for c in hits if _chunk_score(c) != 0.5]
        print(f"  Solo score 0.5 filtrado en hits, quedan: {len(filtered)}")
    elif len(filtered) < before:
        print(f"  Score filtrados: {before - len(filtered)}, quedan: {len(filtered)}")

    result = filtered + [c for c in tail if c not in filtered]
    if tail:
        print(f"  Tail chunks preservados: {len(tail)}")
    return result


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


def _log_chunks(chunks: list[dict]) -> None:
    for c in chunks[:10]:
        book_info = (c.get("book") or [{}])[0]
        print(
            f"     [{book_info.get('title','?')}] idx={c.get('chunk_index')} "
            f"score={_chunk_score(c):.4f} | {c.get('content','')[:60]}"
        )


def _call_groq(messages: list[dict], max_tokens: int = 100,
               temperature: float = 0, timeout: int = 6) -> str | None:
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
    """Traduce la query al ingles para mejorar la busqueda vectorial. Solo para retrieval, nunca para el LLM final."""
    if _EN_PATTERN.search(query):
        return query
    if query in _translation_cache:
        cached = _translation_cache[query]
        print(f"  Query traducida (cache): '{query}' -> '{cached}'")
        return cached
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
        print(f"  Query traducida: '{query}' -> '{result}'")
        _translation_cache[query] = result
        return result
    return query


def _enrich_query_with_history(query: str, history: list, hint_only: bool = False) -> str:
    """
    Reemplaza pronombres vagos por nombres reales usando solo las preguntas del historial.
    Nunca usa las respuestas anteriores porque pueden estar mal y contaminar la busqueda.
    hint_only=True: solo resuelve pronombres, no agrega nombre de libro para no sesgar.
    """
    if not history:
        return query

    recent = history[-3:]
    context = "\n".join(
        f"Q: {t.get('question', '')}"
        for t in recent
        if t.get("question", "").strip()
    )

    if not context.strip():
        return query

    if hint_only:
        system_msg = (
            "You are a search query optimizer for a book RAG system. "
            "Rewrite the follow-up question using ONLY information present in the previous questions.\n"
            "Rules:\n"
            "1. Replace pronouns (he, she, it, el, ella, su, his, her, sus) with the character "
            "name that appears in the previous questions.\n"
            "2. For 'why' or 'how' questions about an event explicitly mentioned in a previous "
            "question (e.g. Q: 'does Paul go blind?' -> follow-up: 'why does he go blind?'), "
            "rewrite to include the event: e.g. 'Paul Atreides blindness cause'.\n"
            "3. CRITICAL: Do NOT add any names, facts, or details that do not appear verbatim "
            "in the previous questions. If unsure, just replace the pronoun and nothing else.\n"
            "4. Do NOT add book titles.\n"
            "5. Output ONLY the rewritten query, max 10 words, no explanation."
        )
    else:
        system_msg = (
            "You are a search query optimizer. "
            "Given a list of previous questions and a vague follow-up question, "
            "rewrite the follow-up to be self-contained by replacing pronouns and vague "
            "references with the actual entities from the context. "
            "CRITICAL: If the question refers to a continuation (e.g., 'el siguiente libro', "
            "'la secuela', 'next book', 'despues'), DO NOT replace these terms with the name "
            "of the previous book. Keep the continuation reference intact. "
            "Do NOT add extra context, dates, or narrative details. "
            "Keep the rewritten question as short as possible. "
            "Reply ONLY with the rewritten question, nothing else."
        )

    result = _call_groq(
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"Previous questions:\n{context}\n\nFollow-up question: {query}"}
        ],
        max_tokens=60,
    )
    if result and result.lower() != query.lower():
        result = re.sub(r'^[¡¿]+|[!]+$', '', result).strip()
        print(f"  Query enriquecida: '{query}' -> '{result}'")
        return result
    return query


def _rerank_chunks(query_en: str, chunks: list[dict], top_n: int = 10,
                   preferred_book_id: str | None = None) -> list[dict]:
    """
    Reordena chunks por relevancia usando el LLM.
    Recibe siempre la query en ingles para consistencia.
    preferred_book_id: libro del historial a priorizar cuando hay contaminacion semantica
    de otros libros con vocabulario similar (ej: The Antichrist vs DUNE MESSIAH).
    """
    if not chunks:
        return chunks

    preferred_title = None
    if preferred_book_id:
        for c in chunks:
            if c.get("book_id") == preferred_book_id:
                preferred_title = (c.get("book") or [{}])[0].get("title")
                break

    snippets = "\n\n".join(
        f"[{i}] (Book: {(c.get('book') or [{}])[0].get('title', '?')}) {c.get('content', '')[:300]}"
        for i, c in enumerate(chunks)
    )
    preferred_hint = (
        f"IMPORTANT: The question is most likely answered in '{preferred_title}'. "
        f"Rank passages from that book first IF they are relevant to the query. "
        f"Ignore passages from other books if they don't directly answer the question.\n"
        if preferred_title else ""
    )
    prompt = (
        f"Query: {query_en}\n\n"
        f"{preferred_hint}"
        f"Rank these {len(chunks)} passages by relevance to the query. "
        f"Prioritize passages that DIRECTLY answer the question. "
        f"If the query asks about a character's death, blindness, or fate, "
        f"rank passages that explicitly describe that event FIRST, even if they come "
        f"from the end of the book or have lower retrieval scores. "
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


def _get_all_books(client) -> list[dict]:
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


# Mapea referencias numericas a titulos reales (ej: "dune 2" -> "dune messiah")
_ORDINAL_SEQUEL_MAP = re.compile(
    r'\bdune\s+(?:2|dos|ii)\b',
    re.IGNORECASE
)


def _normalize_ordinal_titles(text: str) -> str:
    text = _ORDINAL_SEQUEL_MAP.sub('dune messiah', text)
    return text


def detect_mentioned_book_ids(text: str, client) -> list[str]:
    """
    Detecta libros mencionados en el texto usando fuzzy match.
    Ordena por longitud descendente para que titulos largos (DUNE MESSIAH) matcheen antes que cortos (DUNE).
    """
    books = _get_all_books(client)
    query_words = _normalize(text).split()
    mentioned   = []

    books_sorted = sorted(books, key=lambda b: len(b.get("title", "")), reverse=True)

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
            for w in title_words:
                query_words = [qw for qw in query_words if not _word_matches(w, [qw])]

    return mentioned


def _resolve_book_context(query: str, history: list, client) -> tuple[list[str], bool]:
    """
    Determina que libro es relevante para la query.
    Retorna (book_ids, hint_only).
    hint_only=False: libro mencionado en la query actual, busqueda directa.
    hint_only=True: libro inferido del historial, buscar en todos los libros.
    """
    query_normalized = _normalize_ordinal_titles(query)

    ids_in_query = detect_mentioned_book_ids(query_normalized, client)
    if ids_in_query:
        print(f"  Libro confirmado en query actual, busqueda directa")
        return ids_in_query, False

    if _SEQUEL_PATTERNS.search(query_normalized):
        print("  Intencion de secuela, busqueda global sin filtro de libro")
        return [], False

    if history:
        for turn in reversed(history[-5:]):
            question = turn.get("question", "").strip()
            if not question:
                continue
            ids_in_history = detect_mentioned_book_ids(question, client)
            if ids_in_history:
                print(f"  Libro inferido de historial, hint_only, busqueda en todos los libros")
                return ids_in_history, True

    return [], False


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


def _expand_and_sort_by_score(client, raw: list[dict], top_n: int,
                               window: int = 1) -> list[dict]:
    """Expande los top_n chunks con sus vecinos y reordena por score."""
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


def _search_book(client, book: dict, query_en: str, query_orig: str,
                 search_fn, limit: int, **kwargs) -> list[dict]:
    """Busca en un unico libro. Se ejecuta en un thread del pool paralelo."""
    bid     = book.get("_additional", {}).get("id")
    results = search_fn(client, query_en, limit=limit, book_id=bid, **kwargs)
    if query_en != query_orig:
        results = _merge_unique(
            results,
            search_fn(client, query_orig, limit=max(1, limit // 2), book_id=bid, **kwargs)
        )
    return results


def _search_all_books(client, query_en: str, query_orig: str,
                      search_fn, limit: int = LIMIT_VECTOR, **kwargs) -> list[dict]:
    """Busca en todos los libros en paralelo y combina resultados por score."""
    books = list_books(client)
    raw   = []

    with ThreadPoolExecutor(max_workers=min(len(books), 8)) as executor:
        futures = {
            executor.submit(
                _search_book, client, book, query_en, query_orig, search_fn, limit, **kwargs
            ): book.get("title", "?")
            for book in books
        }
        for future in as_completed(futures):
            title = futures[future]
            try:
                results = future.result()
                print(f"  [{title}]: {len(results)} chunks")
                raw = _merge_unique(raw, results)
            except Exception as e:
                print(f"  [{title}] error en busqueda paralela: {e}")

    raw.sort(key=_chunk_score, reverse=True)
    print(f"  Total global: {len(raw)}")
    return raw


def _fetch_tail_chunks(client, book_id: str, n: int = 15) -> list[dict]:
    """
    Recupera los ultimos N chunks narrativos de un libro ordenados por indice descendente.
    Pide n*4 al backend para tener margen despues de filtrar junk (glosario, apendice).
    Asigna score artificial 0.4 para no desplazar hits vectoriales reales.
    """
    gql = f"""
    {{
      Get {{
        BookChunk(
          where: {{
            path: ["book_id"]
            operator: Equal
            valueText: "{book_id}"
          }}
          sort: [{{ path: ["chunk_index"], order: desc }}]
          limit: {n * 4}
        ) {{
          content
          chunk_index
          book_id
          book {{ ... on Book {{ title }} }}
          _additional {{ id }}
        }}
      }}
    }}
    """
    try:
        result = client.query.raw(gql)
        chunks = result.get("data", {}).get("Get", {}).get("BookChunk", [])
        before = len(chunks)
        chunks = [c for c in chunks if not _is_junk_chunk(c)]
        if before != len(chunks):
            print(f"  Tail junk filtrados: {before - len(chunks)}")
        chunks = chunks[:n]
        for c in chunks:
            c.setdefault("_additional", {})["score"] = 0.4
        if chunks:
            max_idx = max(c.get("chunk_index", 0) for c in chunks)
            min_idx = min(c.get("chunk_index", 0) for c in chunks)
            print(f"  Tail chunks recuperados: {len(chunks)} (idx {min_idx}-{max_idx})")
        else:
            print("  Tail chunks: 0 resultados (todo era glosario/apendice)")
        return chunks
    except Exception as e:
        print(f"  Error fetching tail chunks: {e}")
        return []


def _search_specific(client, query: str, book_id: str | None,
                     hint_only: bool = False,
                     position: str | None = None) -> list[dict]:
    """
    Pipeline para preguntas especificas sobre eventos, personajes o hechos concretos.
    hint_only=True: busca en todos los libros en paralelo.
    hint_only=False: busca directamente en el libro confirmado.
    position='end': inyecta los ultimos chunks del libro para cubrir desenlaces.
    """
    query_en = _translate_query_llm(query)

    if hint_only or not book_id:
        raw = _search_all_books(
            client, query_en, query,
            search_fn=search_chunks_hybrid,
            limit=LIMIT_VECTOR,
            alpha=0.5,
        )
        if _top_score(raw) < BM25_SKIP_THRESHOLD:
            raw = _merge_unique(raw, _search_all_books(
                client, query_en, query,
                search_fn=search_chunks_hybrid,
                limit=LIMIT_BM25,
                alpha=0.0,
            ))
            raw.sort(key=_chunk_score, reverse=True)
        else:
            print(f"  BM25 omitida (top score={_top_score(raw):.4f} >= {BM25_SKIP_THRESHOLD})")

        # Si el libro del historial no aparece en los top-5, forzar busqueda directa en el.
        # Previene que libros con vocabulario filosofico similar (The Antichrist) dominen
        # queries sobre eventos narrativos de DUNE MESSIAH.
        if hint_only and book_id and raw:
            top5_books = {c.get("book_id") for c in raw[:5]}
            if book_id not in top5_books:
                print(f"  Libro del historial ausente del top-5, busqueda directa en {book_id[:8]}...")
                fallback = search_chunks_hybrid(client, query_en, limit=10, book_id=book_id, alpha=0.5)
                fallback = _merge_unique(fallback, search_chunks_hybrid(
                    client, query_en, limit=10, book_id=book_id, alpha=0.0))
                if query_en != query:
                    fallback = _merge_unique(fallback, search_chunks_hybrid(
                        client, query, limit=8, book_id=book_id, alpha=0.5))
                print(f"  Fallback directo: {len(fallback)} chunks adicionales")
                raw = _merge_unique(raw, fallback)
                raw.sort(key=_chunk_score, reverse=True)

        if position == "end" and raw:
            char_words = [
                w for w in _normalize(query).split()
                if len(w) >= 4 and w not in {
                    "muere", "murio", "muerte", "dies", "dead", "killed",
                    "last", "libro", "book", "saga", "final", "pasa", "what",
                    "does", "happen", "happens", "the", "end", "fate", "Ultimo"
                }
            ]
            top_hits = raw[:15]
            saga_books: dict[str, float] = {}
            for c in top_hits:
                bid = c.get("book_id")
                if not bid:
                    continue
                content_lower = c.get("content", "").lower()
                char_present = not char_words or any(w in content_lower for w in char_words)
                if char_present:
                    score = _chunk_score(c)
                    if bid not in saga_books or score > saga_books[bid]:
                        saga_books[bid] = score
            best_saga_book = max(saga_books, key=saga_books.get) if saga_books else None
            if best_saga_book:
                print(f"  position=end global, tail chunks de {best_saga_book[:8]}...")
                raw = _merge_unique(raw, _fetch_tail_chunks(client, best_saga_book, n=30))
                raw.sort(key=_chunk_score, reverse=True)

    else:
        # Para nombres propios BM25 es mejor que vectorial porque hace match exacto de tokens
        is_name_query = bool(_NAME_QUERY_PATTERNS.search(query))
        if is_name_query:
            print("  Query de nombre, BM25 primero para match exacto")
            raw = search_chunks_hybrid(client, query, limit=15, book_id=book_id, alpha=0.0)
            raw = _merge_unique(raw, search_chunks_hybrid(
                client, query_en, limit=15, book_id=book_id, alpha=0.0))
            raw = _merge_unique(raw, search_chunks_hybrid(
                client, query_en, limit=10, book_id=book_id, alpha=0.5))
        else:
            raw = search_chunks_hybrid(client, query_en, limit=15, book_id=book_id, alpha=0.5)
            raw = _merge_unique(raw, search_chunks_hybrid(
                client, query_en, limit=15, book_id=book_id, alpha=0.0))
            if query_en != query:
                raw = _merge_unique(raw, search_chunks_hybrid(
                    client, query, limit=12, book_id=book_id, alpha=0.5))
                raw = _merge_unique(raw, search_chunks_hybrid(
                    client, query, limit=12, book_id=book_id, alpha=0.0))

        raw.sort(key=_chunk_score, reverse=True)

        if _top_score(raw) < MIN_RELEVANCE_SCORE or len(raw) < MIN_CHUNKS_THRESHOLD:
            print(f"  Score bajo o pocos chunks ({len(raw)}), ampliando a todos los libros...")
            raw = _merge_unique(raw, _search_all_books(
                client, query_en, query,
                search_fn=search_chunks_hybrid,
                limit=LIMIT_VECTOR,
                alpha=0.5,
            ))
            raw.sort(key=_chunk_score, reverse=True)

        if position == "end" or _top_score(raw) < MIN_RELEVANCE_SCORE:
            reason = "posicion end" if position == "end" else f"score bajo ({_top_score(raw):.4f})"
            print(f"  Inyectando tail chunks ({reason})...")
            raw = _merge_unique(raw, _fetch_tail_chunks(client, book_id, n=30))
            raw.sort(key=_chunk_score, reverse=True)

    before = len(raw)
    raw    = [c for c in raw if not _is_junk_chunk(c)]
    print(f"  Junk filtrados: {before - len(raw)}, quedan: {len(raw)}")
    raw = _apply_score_filter(raw)
    _log_chunks(raw)

    has_tail = any(_chunk_score(c) == _TAIL_CHUNK_SCORE for c in raw)
    if has_tail:
        top_n_expand = min(len(raw), MAX_CHUNKS_TO_LLM + 5)
        print(f"  Tail chunks detectados, expand top_n={top_n_expand}")
    else:
        top_n_expand = TOP_N_EXPAND_GLOBAL if (hint_only or not book_id) else TOP_N_EXPAND_DIRECT

    expanded = _expand_and_sort_by_score(client, raw, top_n=top_n_expand, window=1)

    # Garantizar al menos 4 chunks del libro del historial en el pool del re-ranker.
    # Sin esto, libros con scores altos monopolizan los slots del expand y el libro
    # correcto nunca llega al LLM.
    MIN_BOOK_CHUNKS = 4
    if hint_only and book_id:
        book_chunks_in_expanded = [c for c in expanded if c.get("book_id") == book_id]
        if len(book_chunks_in_expanded) < MIN_BOOK_CHUNKS:
            history_book_chunks = sorted(
                [c for c in raw if c.get("book_id") == book_id],
                key=_chunk_score, reverse=True
            )[:MIN_BOOK_CHUNKS]
            expanded_ids = {c.get("_additional", {}).get("id") for c in expanded}
            missing = [c for c in history_book_chunks
                       if c.get("_additional", {}).get("id") not in expanded_ids]
            if missing:
                print(f"  Garantia de representacion: +{len(missing)} chunks de libro del historial")
                expanded = expanded + missing

    preferred = book_id if hint_only else None
    reranked = _rerank_chunks(query_en, expanded, top_n=MAX_CHUNKS_TO_LLM + 2,
                              preferred_book_id=preferred)
    return _limit_chunks(reranked)


def _search_summary(client, query: str, book_id: str | None,
                    position: str | None, hint_only: bool = False,
                    is_overview: bool = False) -> list[dict]:
    """Pipeline para preguntas de resumen usando BookSummary."""
    query_en = _translate_query_llm(query)

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

    raw = search_summaries_hybrid(
        client, query_en, limit=6, book_id=book_id,
        position=position, alpha=0.75,
    )

    if len(raw) < MIN_CHUNKS_THRESHOLD and position:
        print("  Pocos summaries con posicion, buscando sin filtro...")
        raw = _merge_unique(raw, search_summaries_hybrid(
            client, query_en, limit=6, book_id=book_id,
            position=None, alpha=0.75))

    top = _top_score(raw)
    if hint_only or not book_id or top < MIN_RELEVANCE_SCORE:
        print(f"  Top score: {top:.4f} o hint_only, ampliando summaries a todos los libros...")
        extra = search_summaries_hybrid(
            client, query_en, limit=6, position=position, alpha=0.75)
        raw = _merge_unique(raw, extra)
        raw.sort(key=_chunk_score, reverse=True)

    if not raw:
        print("  Sin summaries, fallback a BookChunk...")
        raw = search_chunks_hybrid(client, query_en, limit=15, book_id=book_id, alpha=0.5)
        raw = _merge_unique(raw, search_chunks_hybrid(
            client, query_en, limit=15, book_id=book_id, alpha=0.0))
        raw = [c for c in raw if not _is_junk_chunk(c)]
        raw = expand_chunks_with_neighbors(client, raw, window=1)
    elif position == "end" and book_id:
        print("  Posicion end, complementando summaries con chunks del final...")
        end_chunks = search_chunks_hybrid(client, query_en, limit=8, book_id=book_id, alpha=0.5)
        end_chunks = [c for c in end_chunks if not _is_junk_chunk(c)]
        raw = _merge_unique(raw, end_chunks)
        raw.sort(key=_chunk_score, reverse=True)

    return _limit_chunks(raw)


def search_chunks(query: str, history: list | None = None) -> list[dict]:
    """
    Punto de entrada principal del pipeline RAG.
    1. Detecta el libro en la query actual.
    2. Si no hay libro en la query, busca en el historial (hint_only=True).
    3. hint_only=True: busca en todos los libros en paralelo.
    4. Sin libro detectado y multiples libros disponibles: pide aclaracion al usuario.
    """
    client = current_app.config["WEAVIATE_CLIENT"]

    mentioned_ids, hint_only = _resolve_book_context(query, history or [], client)
    enriched_query = _enrich_query_with_history(query, history or [], hint_only=hint_only)

    classification = classify_query(enriched_query)
    query_type     = classification["type"]
    position       = classification["position"]
    is_overview    = classification["is_overview"]

    if not mentioned_ids:
        available = list_books(client)
        if len(available) > 1:
            print("  Sin libro detectado en query ni historial, solicitando aclaracion")
            return [{"__ask_user__": True}]

    book_id = mentioned_ids[0] if len(mentioned_ids) == 1 else None

    if query_type == "summary":
        print("  Modo summary, usando BookSummary")
        return _search_summary(
            client, enriched_query, book_id, position,
            hint_only=hint_only,
            is_overview=is_overview,
        )
    else:
        print("  Modo specific, usando BookChunk")
        return _search_specific(client, enriched_query, book_id,
                                hint_only=hint_only, position=position)