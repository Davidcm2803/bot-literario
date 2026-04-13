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

MAX_CHUNKS_TO_LLM    = 10
MAX_CONTEXT_CHARS    = 20_000
MIN_CHUNKS_THRESHOLD = 4
MIN_RELEVANCE_SCORE  = 0.55

GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")


# Detecta chunks que no aportan contenido util como indices de capitulos o chunk inicial
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


# Patron para detectar si la query ya esta en ingles y evitar traducirla
_EN_PATTERN = re.compile(
    r'\b(what|who|how|when|where|does|did|is|are|the|of|in|to|and|his|her)\b',
    re.IGNORECASE,
)


# Traduce la query al ingles para mejorar la busqueda vectorial
# Usa system prompt explicito para que el modelo no confunda verbos con nombres propios
def _translate_query_llm(query: str) -> str:
    if _EN_PATTERN.search(query):
        return query
    if not GROQ_API_KEY:
        return query
    try:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a translator. The user sends short Spanish search queries about books. "
                            "These are always questions or phrases, never names. "
                            "Translate to English. Reply ONLY with the translation, nothing else."
                        )
                    },
                    {
                        "role": "user",
                        "content": query
                    }
                ],
                "max_tokens": 80,
                "temperature": 0,
            },
            timeout=5,
        )
        if r.status_code == 200:
            translated = r.json()["choices"][0]["message"]["content"].strip()
            if translated and translated.lower() != query.lower():
                print(f"  Query traducido: '{query}' a '{translated}'")
                return translated
    except Exception as e:
        print(f"  Traduccion fallida: {e}")
    return query


# Enriquece queries vagas con contexto del historial reciente
# Reemplaza pronombres y referencias por las entidades reales mencionadas antes
# Esto mejora la busqueda vectorial cuando el usuario usa frases como esa habilidad o el personaje
def _enrich_query_with_history(query: str, history: list) -> str:
    if not history or not GROQ_API_KEY:
        return query

    recent  = history[-3:]
    context = "\n".join(
        f"Q: {t.get('question', '')} A: {t.get('answer', '')[:200]}"
        for t in recent
    )

    try:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a search query optimizer. "
                            "Given a conversation history and a vague follow-up question, "
                            "rewrite the question to be self-contained and specific. "
                            "Replace pronouns and references with the actual entities from the context. "
                            "Reply ONLY with the rewritten question, nothing else."
                        )
                    },
                    {
                        "role": "user",
                        "content": f"History:\n{context}\n\nFollow-up question: {query}"
                    }
                ],
                "max_tokens": 60,
                "temperature": 0,
            },
            timeout=5,
        )
        if r.status_code == 200:
            enriched = r.json()["choices"][0]["message"]["content"].strip()
            if enriched and enriched.lower() != query.lower():
                print(f"  Query enriquecida: '{query}' a '{enriched}'")
                return enriched
    except Exception as e:
        print(f"  Enriquecimiento fallido: {e}")
    return query


# Normaliza texto a minusculas sin tildes ni puntuacion para comparaciones fuzzy
def _normalize(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s]", " ", s)
    return s


# Calcula distancia de edicion entre dos cadenas para detectar palabras similares
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


# Verifica si una palabra del titulo coincide con alguna palabra de la query con tolerancia a errores
def _word_matches(word: str, query_words: list[str]) -> bool:
    if word in query_words:
        return True
    n = len(word)
    if n < 4:
        return False
    max_dist = 2 if n >= 6 else 1
    return any(_levenshtein(word, qw) <= max_dist for qw in query_words)


# Une dos listas de chunks eliminando duplicados por ID
def _merge_unique(base: list[dict], extra: list[dict]) -> list[dict]:
    seen_ids = {c.get("_additional", {}).get("id") for c in base}
    for c in extra:
        if c.get("_additional", {}).get("id") not in seen_ids:
            base.append(c)
    return base


# Detecta que libros se mencionan en el texto usando fuzzy match contra los titulos en Weaviate
# Consulta los titulos dinamicamente para escalar a cualquier cantidad de libros
def detect_mentioned_book_ids(query: str, client) -> list[str]:
    try:
        result = (
            client.query
            .get("Book", ["title"])
            .with_additional(["id"])
            .with_limit(100)
            .do()
        )
        books = result.get("data", {}).get("Get", {}).get("Book", [])

        seen: dict[str, str] = {}
        for b in books:
            title   = b.get("title", "")
            book_id = b.get("_additional", {}).get("id", "")
            if title and book_id:
                seen[book_id] = title

        sorted_books = sorted(seen.items(), key=lambda x: len(x[1]), reverse=True)
        query_words  = _normalize(query).split()
        mentioned    = []

        for book_id, title in sorted_books:
            title_words = _normalize(title).split()
            if len(title_words) == 1:
                matches = sum(1 for w in title_words if w in query_words)
            else:
                matches = sum(1 for w in title_words if _word_matches(w, query_words))

            if title_words and matches / len(title_words) >= 0.6:
                mentioned.append(book_id)
                for w in title_words:
                    query_words = [qw for qw in query_words if not _word_matches(w, [qw])]
                print(f"  Libro detectado: '{title}' id {book_id[:8]}...")

        return mentioned
    except Exception as e:
        print(f"  Error detectando libros: {e}")
        return []


# Busca el libro en el historial de mas reciente a mas antiguo
# Devuelve una tupla (ids, confirmado) donde confirmado es True si el libro
# aparece en el ultimo turno permitiendo tratarlo como libro fijo sin hint_only
def _get_book_ids_from_history(history: list, client, turns: int = 5) -> tuple[list[str], bool]:
    if not history:
        return [], False

    # Primero busca solo en el ultimo turno para confirmar el libro activo
    last      = history[-1]
    last_text = " ".join(filter(None, [last.get("question"), last.get("answer")]))
    if last_text.strip():
        found = detect_mentioned_book_ids(last_text, client)
        if found:
            print(f"  Libro confirmado del ultimo turno")
            return found, True

    # Si no hay coincidencia en el ultimo turno busca en los anteriores como pista
    for turn in reversed(history[-turns:-1]):
        parts = []
        if turn.get("question"):
            parts.append(turn["question"])
        if turn.get("answer"):
            parts.append(turn["answer"])

        text = " ".join(parts)
        if not text.strip():
            continue

        found = detect_mentioned_book_ids(text, client)
        if found:
            print(f"  Libro inferido del historial: turno reciente")
            return found, False

    return [], False


# Patrones para detectar queries que necesitan resumen narrativo en vez de busqueda especifica
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

# Patrones para detectar queries que buscan un hecho puntual o detalle especifico
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

# Patrones para detectar si la query hace referencia a una parte concreta del libro
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

# Patrones para detectar preguntas de alto nivel sobre temas generales o premisa del libro
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


# Determina el tipo de query, posicion narrativa y si es una pregunta de overview general
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

    print(f"  Tipo: {query_type}" + (f" posicion: {position}" if position else "") + (f" overview: {is_overview}" if is_overview else ""))
    return {"type": query_type, "position": position, "is_overview": is_overview}


# Recorta la lista de chunks al maximo permitido por numero y por caracteres totales
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


# Usa el LLM para reordenar los chunks por relevancia directa a la query
# El prompt fuerza JSON puro para evitar que el modelo devuelva texto adicional
def _rerank_chunks(query: str, chunks: list[dict], top_n: int = 10) -> list[dict]:
    if not chunks or not GROQ_API_KEY:
        return chunks

    snippets = "\n\n".join(
        f"[{i}] (Book: {(c.get('book') or [{}])[0].get('title', '?')}) {c.get('content', '')[:300]}"
        for i, c in enumerate(chunks)
    )

    prompt = (
        f"Query: {query}\n\n"
        f"Rank these {len(chunks)} passages by relevance to the query.\n"
        f"You MUST respond with ONLY a JSON array of integers. No text before or after.\n"
        f"Example for 4 passages: [2,0,3,1]\n\n"
        f"{snippets}\n\n"
        f"JSON array:"
    )

    try:
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0,
            },
            timeout=8,
        )

        if r.status_code != 200:
            print(f"  Re-rank fallido status {r.status_code}, usando orden original")
            return chunks

        raw   = r.json()["choices"][0]["message"]["content"].strip()
        match = re.search(r'\[[\d,\s]+\]', raw)
        if not match:
            print(f"  Re-rank sin array JSON, usando orden original")
            return chunks

        indices       = json.loads(match.group())
        valid_indices = [i for i in indices if isinstance(i, int) and 0 <= i < len(chunks)]

        # Agrega indices omitidos por el LLM para no perder chunks
        seen = set(valid_indices)
        for i in range(len(chunks)):
            if i not in seen:
                valid_indices.append(i)

        reranked = [chunks[i] for i in valid_indices]
        print(f"  Re-ranked top 5: {valid_indices[:5]}")
        return reranked[:top_n]

    except Exception as e:
        print(f"  Re-rank error: {e}, usando orden original")
        return chunks


# Devuelve el score mas alto entre los primeros cinco chunks
def _top_score(chunks: list[dict]) -> float:
    best = 0.0
    for c in chunks[:5]:
        try:
            s = float(c.get("_additional", {}).get("score", 0) or 0)
            if s > best:
                best = s
        except (TypeError, ValueError):
            pass
    return best


# Pipeline para queries especificas que busca en BookChunk con expansion y re-ranking
# Si el score con el libro confirmado es bajo amplia a todos los libros
# Esto cubre casos donde la respuesta esta en un libro anterior de la misma saga
# En modo hint_only busca en todos los libros desde el inicio y ordena por score
def _search_specific(client, query: str, book_id: str | None,
                     hint_only: bool = False) -> list[dict]:
    query_en = _translate_query_llm(query)

    if hint_only:
        books = list_books(client)
        raw   = []
        for book in books:
            bid      = book.get("_additional", {}).get("id")
            title    = book.get("title", "?")
            per_book = _merge_unique(
                search_chunks_hybrid(client, query_en, limit=8, book_id=bid, alpha=0.5),
                search_chunks_hybrid(client, query_en, limit=8, book_id=bid, alpha=0.0),
            )
            if query_en != query:
                per_book = _merge_unique(per_book,
                    search_chunks_hybrid(client, query, limit=5, book_id=bid, alpha=0.5))
            print(f"  [{title}]: {len(per_book)} chunks")
            raw = _merge_unique(raw, per_book)

        # Ordena por score descendente para que top_raw tenga los mejores chunks
        # sin importar el libro ni el orden de insercion del loop
        raw.sort(
            key=lambda c: float(c.get("_additional", {}).get("score", 0) or 0),
            reverse=True,
        )
        print(f"  Total global: {len(raw)} chunks")

    else:
        raw = search_chunks_hybrid(
            client, query_en,
            limit=15 if book_id else 20,
            book_id=book_id, alpha=0.5,
        )
        raw = _merge_unique(raw, search_chunks_hybrid(
            client, query_en,
            limit=15 if book_id else 20,
            book_id=book_id, alpha=0.0,
        ))
        if query_en != query:
            raw = _merge_unique(raw, search_chunks_hybrid(
                client, query,
                limit=10 if book_id else 15,
                book_id=book_id, alpha=0.5,
            ))

        # Si el score es bajo con el libro confirmado amplia a todos los libros
        # Cubre casos donde la respuesta esta en otro libro de la misma saga o coleccion
        if book_id and _top_score(raw) < MIN_RELEVANCE_SCORE:
            print("  Score bajo con libro confirmado, ampliando a todos los libros...")
            raw = _merge_unique(raw, search_chunks_hybrid(client, query_en, limit=20, alpha=0.5))
            raw = _merge_unique(raw, search_chunks_hybrid(client, query_en, limit=20, alpha=0.0))
        elif len(raw) < MIN_CHUNKS_THRESHOLD and book_id:
            print("  Pocos chunks, ampliando a todos los libros...")
            raw = _merge_unique(raw, search_chunks_hybrid(client, query_en, limit=20, alpha=0.5))
            raw = _merge_unique(raw, search_chunks_hybrid(client, query_en, limit=20, alpha=0.0))

    before = len(raw)
    raw    = [c for c in raw if not _is_junk_chunk(c)]
    print(f"  Junk filtrados: {before - len(raw)}, quedan: {len(raw)}")

    before    = len(raw)
    scores    = [float(c.get("_additional", {}).get("score", 0) or 0) for c in raw]
    top_score = max(scores) if scores else 0

    def _keep(c):
        s = float(c.get("_additional", {}).get("score", 0) or 0)
        if s == 0.5:
            return False
        if top_score >= 0.6 and s < 0.35:
            return False
        return True

    filtered = [c for c in raw if _keep(c)]
    if len(filtered) >= MIN_CHUNKS_THRESHOLD:
        raw = filtered
        if len(raw) < before:
            print(f"  Score filtrados: {before - len(raw)}, quedan: {len(raw)}")
    else:
        raw = [c for c in raw if float(c.get("_additional", {}).get("score", 0) or 0) != 0.5]
        print(f"  Solo score 0.5 filtrado, quedan: {len(raw)}")

    for c in raw[:10]:
        score     = c.get("_additional", {}).get("score", "?")
        score_str = f"{float(score):.4f}" if score != "?" else "?"
        book_info = (c.get("book") or [{}])[0]
        print(f"     [{book_info.get('title','?')}] idx={c.get('chunk_index')} score={score_str} | {c.get('content','')[:60]}")

    relevance_order = {c.get("_additional", {}).get("id"): i for i, c in enumerate(raw)}

    top_raw  = raw[:10 if hint_only else 6]
    expanded = expand_chunks_with_neighbors(client, top_raw, window=1)

    def sort_key(c):
        cid  = c.get("_additional", {}).get("id")
        rank = relevance_order.get(cid, 9999)
        idx  = c.get("chunk_index", 0)
        return (rank, idx)

    expanded.sort(key=sort_key)
    print(f"  Total tras expandir: {len(expanded)} chunks")

    reranked = _rerank_chunks(query, expanded, top_n=MAX_CHUNKS_TO_LLM + 2)
    return _limit_chunks(reranked)


# Pipeline para queries de resumen que busca en BookSummary
# Prioriza el overview cuando la pregunta es general y hay un libro identificado
# Si el score es bajo con el libro confirmado amplia a todos igual que en specific
def _search_summary(client, query: str, book_id: str | None,
                    position: str | None, hint_only: bool = False,
                    is_overview: bool = False) -> list[dict]:
    query_en = _translate_query_llm(query)

    if book_id and (is_overview or not position):
        overview_chunks = search_summaries_hybrid(
            client, query_en,
            limit=2,
            book_id=book_id,
            position="overview",
            alpha=0.75,
        )
        if overview_chunks:
            print(f"  Overview encontrado, combinando con summaries normales")
            raw = _merge_unique(
                overview_chunks,
                search_summaries_hybrid(
                    client, query_en,
                    limit=4,
                    book_id=book_id,
                    position=position,
                    alpha=0.75,
                )
            )
            return _limit_chunks(raw)
        else:
            print(f"  Sin overview para este libro, usando busqueda normal")

    raw = search_summaries_hybrid(
        client, query_en,
        limit=6,
        book_id=book_id,
        position=position,
        alpha=0.75,
    )

    if len(raw) < MIN_CHUNKS_THRESHOLD and position:
        print("  Pocos summaries con posicion, buscando sin filtro...")
        raw = _merge_unique(raw, search_summaries_hybrid(
            client, query_en, limit=6, book_id=book_id,
            position=None, alpha=0.75))

    # Si el score es bajo con libro confirmado o es hint_only amplia a todos los libros
    top = _top_score(raw)
    if book_id and (hint_only or top < MIN_RELEVANCE_SCORE):
        print(f"  Top score: {top:.4f}, ampliando summaries a todos los libros...")
        raw = _merge_unique(raw, search_summaries_hybrid(
            client, query_en, limit=6, position=position, alpha=0.75))

    if len(raw) < MIN_CHUNKS_THRESHOLD and book_id:
        print("  Buscando summaries en todos los libros...")
        raw = _merge_unique(raw, search_summaries_hybrid(
            client, query_en, limit=6, position=position, alpha=0.75))

    if not raw:
        print("  Sin summaries, fallback a BookChunk...")
        raw = search_chunks_hybrid(client, query_en, limit=15,
                                   book_id=book_id, alpha=0.5)
        raw = _merge_unique(raw, search_chunks_hybrid(
            client, query_en, limit=15, book_id=book_id, alpha=0.0))
        raw = [c for c in raw if not _is_junk_chunk(c)]
        raw = expand_chunks_with_neighbors(client, raw, window=1)

    return _limit_chunks(raw)


# Punto de entrada principal del pipeline RAG
# Enriquece la query con historial, clasifica, detecta el libro y decide el modo de busqueda
def search_chunks(query: str, history: list | None = None) -> list[dict]:
    client = current_app.config["WEAVIATE_CLIENT"]

    # Enriquece la query con contexto del historial antes de cualquier clasificacion
    # Esto convierte preguntas vagas como esa habilidad en queries especificas y buscables
    enriched_query = _enrich_query_with_history(query, history or [])

    classification = classify_query(enriched_query)
    query_type     = classification["type"]
    position       = classification["position"]
    is_overview    = classification["is_overview"]

    mentioned_ids = detect_mentioned_book_ids(enriched_query, client)
    hint_only     = False

    if not mentioned_ids and history:
        mentioned_ids, confirmed = _get_book_ids_from_history(history, client, turns=5)
        if mentioned_ids:
            # Libro confirmado significa que aparecio en el ultimo turno y se busca directo
            # Libro como pista significa que viene de turnos anteriores y se busca en todos
            hint_only = not confirmed
            if hint_only:
                print(f"  Libro inferido del historial como pista")
            else:
                print(f"  Libro confirmado del historial, busqueda directa")

    if not mentioned_ids:
        available = list_books(client)
        if len(available) > 1:
            print("  Sin libro detectado en query ni historial, solicitando aclaracion")
            return [{"__ask_user__": True}]

    book_id = mentioned_ids[0] if len(mentioned_ids) == 1 else None

    if query_type == "summary":
        print("  Modo summary usando BookSummary")
        return _search_summary(
            client, enriched_query, book_id, position,
            hint_only=hint_only,
            is_overview=is_overview,
        )
    else:
        print("  Modo specific usando BookChunk")
        return _search_specific(client, enriched_query, book_id, hint_only=hint_only)