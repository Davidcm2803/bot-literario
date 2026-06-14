import os, re, json, unicodedata, requests
from collections import defaultdict
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


def _normalize(text):
    """
    Toma cualquier texto y lo estandariza: lo pasa a minúsculas, 
    le quita las tildes y elimina los signos de puntuación. 
    Esto es súper útil para comparar palabras sin que una tilde o una coma arruinen el match.
    """
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", " ", text)

def _score(chunk):
    """
    Extrae de forma segura la puntuación (score) que Weaviate le dio a un fragmento de texto.
    Si el fragmento no tiene puntuación o hay algún error, devuelve 0.0 por defecto.
    """
    try:
        return float(chunk.get("_additional", {}).get("score", 0) or 0)
    except (TypeError, ValueError):
        return 0.0

def _chunk_id(chunk):
    """
    Saca el ID único de un fragmento desde sus metadatos ocultos (_additional).
    """
    return chunk.get("_additional", {}).get("id", "")

def _book_id(chunk):
    """
    Saca el ID del libro al que pertenece este fragmento de texto.
    """
    return chunk.get("book_id", "")

def _merge(base, extra):
    """
    Une dos listas de fragmentos de texto (chunks) asegurándose de no meter duplicados.
    Revisa los IDs de la lista base y solo agrega los de la lista extra que sean nuevos.
    """
    seen = {_chunk_id(c) for c in base}
    return base + [c for c in extra if _chunk_id(c) not in seen]

def _limit(chunks):
    """
    Recorta la lista de fragmentos para no saturar al LLM. 
    Se detiene cuando llega al límite máximo de fragmentos permitidos o 
    cuando la cantidad total de caracteres supera el límite de contexto.
    """
    result, total = [], 0
    for c in chunks:
        n = len(c.get("content", ""))
        if len(result) >= MAX_CHUNKS_LLM or total + n > MAX_CONTEXT_CHARS:
            break
        result.append(c)
        total += n
    print(f"  -> {len(result)} chunks al LLM ({total} chars)")
    return result


_JUNK_HEADERS = re.compile(
    r'\b(glossary|glosario|appendix|ap[eé]ndice|bibliography|'
    r'index|[íi]ndice|copyright|all\s+rights\s+reserved|'
    r'publishing\s+group|printed\s+in)\b',
    re.IGNORECASE,
)
_GLOSSARY_LINE = re.compile(r'^[A-Z\s\'\-]{3,40}:\s', re.MULTILINE)

def _is_junk(chunk):
    """
    Evalúa si un fragmento de texto es 'basura' para el contexto del LLM.
    Descarta cosas como el glosario, el índice, la página de copyright 
    o listas de capítulos que no aportan valor narrativo para responder preguntas.
    """
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


_translation_cache: dict = {}
_IS_ENGLISH = re.compile(
    r'\b(what|who|how|when|where|does|did|is|are|the|of|in|and|his|her)\b',
    re.IGNORECASE,
)

def _groq(messages, max_tokens=120, temperature=0.0):
    """
    Se encarga de hacer la llamada HTTP a la API de Groq con los mensajes proporcionados.
    Maneja el timeout y devuelve el texto de la respuesta, o None si algo falla.
    """
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
    """
    Traduce de manera rápida la pregunta del usuario al inglés usando Groq.
    Si detecta que ya está en inglés, se salta la traducción. 
    Además guarda un caché de traducciones para no repetir la misma petición.
    """
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

def _enrich_query(query, history, all_books=None):
    """
    Reescribe la pregunta del usuario dándole contexto basado en el historial.
    Por ejemplo, si el usuario dice "qué le pasó a él", y antes hablaban de Paul,
    lo cambia a "qué le pasó a Paul". Tiene un seguro integrado: si la reescritura 
    accidentalmente borra el título de un libro que sí estaba en la pregunta original, 
    se descarta la reescritura para no arruinar la búsqueda.
    """
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
            "- NEVER remove or replace book titles already present in the follow-up.\n"
            "- Do NOT add new book titles that are not already in the follow-up.\n"
            "- Keep it short (max 12 words).\n"
            "- Reply ONLY with the rewritten query, nothing else."},
        {"role": "user", "content":
            f"Previous questions:\n{context}\n\nFollow-up: {query}"},
    ], max_tokens=60)
    if result and result.strip() and result.lower() != query.lower():
        result = re.sub(r'^[¡¿"\']+|[!"\']+$', '', result).strip()

        # Validación: si la query original tenía un libro conocido y
        # la enriquecida lo perdió, descartar el enriquecimiento.
        if all_books:
            books_in_original  = _detect_books(query, all_books)
            books_in_enriched  = _detect_books(result, all_books)
            orig_ids = {b.get("_additional", {}).get("id") for b in books_in_original}
            enr_ids  = {b.get("_additional", {}).get("id") for b in books_in_enriched}
            lost = orig_ids - enr_ids
            if lost:
                lost_titles = [
                    b.get("title", "?") for b in books_in_original
                    if b.get("_additional", {}).get("id") in lost
                ]
                print(f"  Enriquecimiento descartado: perdía libro(s) {lost_titles}")
                return query

        print(f"  Query enriquecida: '{query}' -> '{result}'")
        return result
    return query

def _rerank(query_en, chunks, preferred_ids=None):
    """
    Usa el LLM para evaluar los fragmentos encontrados y reordenarlos.
    Pone primero los fragmentos que responden de forma más directa a la pregunta.
    También puede darle prioridad a fragmentos que pertenezcan a libros específicos (preferred_ids).
    """
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


def _levenshtein(a, b):
    """
    Calcula la distancia de Levenshtein entre dos palabras.
    En términos humanos: cuenta cuántas letras tienes que cambiar, agregar o quitar 
    para transformar la palabra 'a' en la palabra 'b'. Ideal para tolerar errores ortográficos.
    """
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
    """
    Verifica si una palabra se parece lo suficiente a alguna de las palabras en una lista destino.
    Da un margen de error dinámico: permite un error si la palabra es mediana y hasta dos si es larga.
    """
    if word in targets:
        return True
    n = len(word)
    if n < 4:
        return False
    max_dist = 2 if n >= 6 else 1
    return any(_levenshtein(word, t) <= max_dist for t in targets)

def _get_all_books(client):
    """
    Se conecta a Weaviate y descarga la lista completa de todos los libros disponibles 
    con su título y su ID interno. Esto sirve de catálogo base para las funciones de búsqueda.
    """
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
    """
    Busca dentro de un texto si el usuario mencionó el título de algún libro de nuestra base de datos.
    Usa tolerancia a errores ortográficos y verifica que al menos el 60% de las palabras del título coincidan.
    """
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

def _resolve_scope(query, history, client, all_books=None):
    """
    Define en qué libro(s) debemos enfocar la búsqueda. 
    Prioriza si el usuario nombró un libro en su pregunta actual. 
    Si está pidiendo por la 'secuela', obliga a buscar de forma global.
    Si no menciona nada, revisa el historial reciente para ver de qué libro venían hablando.
    """
    all_books = all_books or _get_all_books(client)
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


_SUMMARY_PATTERNS = re.compile(
    r'\b('
    r'qu[eé]\s+(pas[oó]|ocurri[oó]|le\s+pas[oó]|sucedi[oó]|hace|hizo|pasa)|'
    r'(trata|habla|cuenta)\s+(el|la|los)?\s*(libro|historia|novela)?|'
    r'resumen|resume|summarize|summary|synopsis|sinopsis|'
    r'de\s+qu[eé]\s+(trata|va)|what\s+is\s+.*\s+about|'
    r'cu[eé]ntame|tell\s+me\s+about|'
    r'qu[eé]\s+rol|what\s+role|'
    r'tema\s+principal|main\s+theme|temas?|themes?|'
    r'qu[eé]\s+pasa\s+(en|al|con)|what\s+happens|'
    r'c[oó]mo\s+(termina|finaliza|acaba)'
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


def _classify(query: str, original: str | None = None) -> dict:
    """
    Clasifica la pregunta del usuario en categorías (resumen, personaje, específica).
    Evalúa tanto la pregunta enriquecida como la original del usuario para no perder el sentido.
    También determina si el usuario pregunta por el 'inicio' o el 'final' del libro, 
    y ajusta un peso (alpha) que balancea la búsqueda vectorial frente a la búsqueda por palabras clave.
    """
    # Combinar ambas queries para clasificar: se evalúan en paralelo
    # y se toma la clasificación más informativa (summary > character > specific)
    queries_to_check = [query]
    if original and original.strip() and original.strip().lower() != query.strip().lower():
        queries_to_check.append(original)

    is_summary  = any(bool(_SUMMARY_PATTERNS.search(q))  for q in queries_to_check)
    is_char     = any(bool(_CHARACTER_PATTERNS.search(q)) for q in queries_to_check)
    is_overview = any(bool(_OVERVIEW_PATTERNS.search(q))  for q in queries_to_check)
    has_quoted  = any(bool(_QUOTED_TERM.search(q))        for q in queries_to_check)

    # Posición: buscar en ambas queries, la original suele tener "al final", "al inicio"
    position = None
    for q in queries_to_check:
        for pos, pattern in _POSITION_PATTERNS.items():
            if pattern.search(q):
                position = pos
                break
        if position:
            break

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
    """
    Busca patrones de eventos comunes en la pregunta (muertes, bodas, traiciones)
    y devuelve una lista de sinónimos clave en español e inglés.
    Esto ayuda a Weaviate a encontrar fragmentos aunque el libro use palabras distintas al usuario.
    """
    for pattern, synonyms in _EVENT_SYNONYMS:
        if pattern.search(query):
            print(f"  Evento detectado: +{len(synonyms)} queries BM25 extra")
            return synonyms
    return []


def _search_one(client, bid, query_en, query_orig, alpha, extra_bm25=None):
    """
    Realiza todas las búsquedas necesarias para UN solo libro. 
    Hace búsquedas híbridas combinando la pregunta en inglés, la original en español, 
    y opcionalmente listas de sinónimos extra. Luego junta todos los resultados quitando duplicados.
    """
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
    Toma una lista de libros y ejecuta la función '_search_one' para todos ellos al mismo tiempo 
    usando hilos (ThreadPoolExecutor). Agrupa todos los fragmentos encontrados y los ordena 
    según su puntuación de mayor a menor. Al filtrar libro por libro de entrada, se garantiza
    que el ranking se calcule adecuadamente para cada contexto separado.
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
    Orquesta una búsqueda de alcance global dividida en dos grupos paralelos: 
    los libros principales del contexto actual (scope_ids) y el resto del catálogo (other_ids).
    Sirve especialmente cuando se está buscando un evento clave y no estamos seguros en qué libro ocurre.
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
    """
    Controla el "ruido" de libros que no pertenecen al contexto original.
    Permite que pasen todos los fragmentos de los libros principales (scope_ids),
    pero pone un límite estricto de cuántos fragmentos pueden colarse de libros secundarios.
    """
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


def _expand_scope_ids(raw, scope_ids, top_n=6):
    """
    Revisa si entre los primeros resultados aparece un fragmento excepcional (con score mayor a 0.75) 
    de un libro que no estaba inicialmente en nuestro enfoque principal.
    Si lo encuentra, "expande" el alcance añadiendo ese nuevo libro a nuestro contexto oficial.
    """
    top_books = {
        _book_id(c) for c in raw[:top_n]
        if _score(c) >= 0.75 and _book_id(c) not in scope_ids
    }
    if top_books:
        print(f"  Scope expandido con: {top_books}")
    return scope_ids | top_books


def _expand_top_chunks(client, chunks):
    """
    Toma los fragmentos con las puntuaciones más altas y busca en la base de datos 
    el fragmento inmediatamente anterior y el inmediatamente posterior.
    Esto permite entregarle al LLM un "bloque" de contexto narrativo más grueso y continuo.
    """
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


_COMPARISON_PATTERNS = re.compile(
    r'\b('
    # Similitudes
    r'en\s+qu[eé]\s+se\s+parece[n]?|'
    r'qu[eé]\s+tienen\s+en\s+com[uú]n|'
    r'similitudes?|similarit(?:y|ies)|'
    r'parecido[s]?|parecida[s]?|similar(?:es)?|'
    r'se\s+asemeja[n]?|resembl(?:e|es|ance)|'
    r'compara[r]?\s+con|compare[sd]?\s+to|'
    r'al\s+igual\s+que|just\s+like|'
    # Diferencias
    r'en\s+qu[eé]\s+se\s+diferencia[n]?|'
    r'diferencia[s]?\s+entre|difference[s]?\s+between|'
    r'a\s+diferencia\s+de|unlike|'
    r'contrario\s+a|contrast(?:s|ed)?\s+with|'
    r'mejor\s+que|worse\s+than|m[aá]s\s+\w+\s+que|'
    # Comparación general
    r'comparaci[oó]n|comparison|'
    r'versus|vs\.?|'
    r'frente\s+a|compared?\s+to|'
    r'tanto\s+\w+\s+como|both\s+\w+\s+and'
    r')\b',
    re.IGNORECASE,
)

_ENTITY_SEPARATORS = re.compile(
    r'\s+(?:y|e|and|vs\.?|versus|con|with|or|o|compared?\s+to|frente\s+a|'
    r'al\s+igual\s+que|tanto\s+como|as\s+well\s+as)\s+',
    re.IGNORECASE,
)

_COMPARISON_STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "the", "a", "an", "of", "in", "on", "at", "to", "for",
    "que", "de", "en", "con", "por", "para", "se", "su", "sus",
    "es", "son", "era", "eran", "fue", "were", "was", "is", "are",
    "esto", "este", "esta", "estos", "estas",
    "this", "that", "these", "those",
    "como", "as", "like",
}

_INTRO_PHRASES = re.compile(
    r'^(en\s+qu[eé]\s+se\s+parece[n]?\s+|'
    r'qu[eé]\s+tienen\s+en\s+com[uú]n\s+|'
    r'cu[aá]les\s+son\s+las\s+similitudes\s+(?:entre\s+)?|'
    r'cu[aá]les\s+son\s+las\s+diferencias\s+(?:entre\s+)?|'
    r'compara\s+|compare\s+|'
    r'diferencias?\s+entre\s+|'
    r'similitudes?\s+entre\s+|'
    r'what\s+(?:do|does|are|is)\s+\w+\s+(?:and\s+\w+\s+)?have\s+in\s+common\s*|'
    r'how\s+(?:is|are)\s+\w+\s+similar\s+to\s+)',
    re.IGNORECASE,
)

_SIMILARITY_PATTERNS = re.compile(
    r'\b(similar|parecido|parece|resembl|en\s+com[uú]n|'
    r'alike|same|igual|tambi[eé]n|also|both|ambos)\b',
    re.IGNORECASE,
)
_DIFFERENCE_PATTERNS = re.compile(
    r'\b(diferencia|difference|distinto|unlike|contrario|'
    r'contrast|versus|vs|mejor\s+que|worse|opuesto)\b',
    re.IGNORECASE,
)


def _detect_comparison_type(query: str) -> str:
    """
    Revisa si una pregunta comparativa está buscando similitudes específicas, 
    diferencias específicas, o si es una comparación general sin un sesgo claro.
    """
    if _SIMILARITY_PATTERNS.search(query):
        return "similarity"
    if _DIFFERENCE_PATTERNS.search(query):
        return "difference"
    return "general"


def _extract_entity_names(query: str) -> list[str]:
    """
    Limpia la pregunta comparativa eliminando frases introductorias (como "en qué se parece")
    y usa separadores (como "y", "vs") para extraer únicamente los nombres de los sujetos
    o cosas que se están intentando comparar.
    """
    clean = _INTRO_PHRASES.sub("", query).strip()
    parts = _ENTITY_SEPARATORS.split(clean)
    entities = []
    for part in parts:
        part = re.sub(r'[?¿!¡.,;:]+$', '', part).strip()
        words = part.split()
        filtered = [w for w in words if _normalize(w) not in _COMPARISON_STOPWORDS]
        if filtered:
            entities.append(part)
    return [e for e in entities if len(e) > 1]


def _match_entity_to_book(entity_name: str, all_books: list[dict]) -> dict | None:
    """
    Intenta relacionar una de las entidades extraídas de la comparación con algún 
    libro conocido de la base de datos usando un algoritmo que tolera errores al escribir.
    Devuelve el libro encontrado o None si no hace match.
    """
    entity_words = _normalize(entity_name).split()
    best_match, best_score = None, 0.0

    for book in sorted(all_books, key=lambda b: len(b.get("title", "")), reverse=True):
        title = book.get("title", "")
        if not title:
            continue
        title_words = _normalize(title).split()
        hits = 0
        for ew in entity_words:
            for tw in title_words:
                n = max(len(ew), len(tw))
                if n < 4:
                    if ew == tw:
                        hits += 1
                        break
                else:
                    max_dist = 2 if n >= 6 else 1
                    if _levenshtein(ew, tw) <= max_dist:
                        hits += 1
                        break
        if title_words:
            score = hits / len(title_words)
            if score >= 0.6 and score > best_score:
                best_score = score
                best_match = book

    return best_match


def _detect_comparison(query: str, enriched: str, all_books: list[dict]) -> dict | None:
    """
    Evalúa la pregunta completa para determinar si efectivamente el usuario está haciendo una comparación.
    Si encuentra al menos 2 entidades, empaqueta todos los metadatos (tipo de comparación, nombres detectados,
    libros relacionados) en un diccionario especial para que el resto del sistema lo procese.
    """
    use_query = enriched or query
    if not _COMPARISON_PATTERNS.search(use_query):
        return None

    print(f"  [Comparison] Detectada en: '{use_query}'")

    entity_names = _extract_entity_names(use_query)
    print(f"  [Comparison] Entidades: {entity_names}")

    if len(entity_names) < 2:
        print("  [Comparison] < 2 entidades → pipeline normal")
        return None

    comparison_type = _detect_comparison_type(use_query)
    entities = []

    for name in entity_names[:4]:  # máximo 4 entidades
        matched_book = _match_entity_to_book(name, all_books)
        book_id      = matched_book.get("_additional", {}).get("id") if matched_book else None
        book_title   = matched_book.get("title", "") if matched_book else ""

        search_queries = [name]
        if book_title and book_title.lower() not in name.lower():
            search_queries.append(f"{name} {book_title}")

        entities.append({
            "name":           name,
            "book":           matched_book,
            "book_id":        book_id,
            "book_title":     book_title,
            "search_queries": search_queries,
        })
        print(f"  [Comparison] '{name}' → '{book_title or 'libro no detectado'}'")

    book_ids = {e["book_id"] for e in entities if e["book_id"]}
    cross_book = len(book_ids) > 1

    return {
        "type":      comparison_type,
        "entities":  entities,
        "cross_book": cross_book,
    }


def _search_one_entity(client, entity: dict, query_en: str, query_orig: str,
                       alpha: float, extra_bm25: list[str] | None,
                       limit: int = 8) -> list[dict]:
    """
    Realiza la búsqueda de fragmentos para una única entidad dentro de una comparación.
    Filtra los resultados estrictamente por el libro al que pertenece la entidad (si lo hay)
    y luego añade etiquetas ocultas a los fragmentos devueltos indicando a qué entidad pertenecen,
    para que posteriormente no se mezclen sin control en el resultado final.
    """
    book_id    = entity.get("book_id")
    entity_name = entity["name"]
    chunks     = []

    for sq in entity["search_queries"]:
        v = search_chunks_hybrid(client, sq, limit=limit, book_id=book_id, alpha=alpha)
        b = search_chunks_hybrid(client, sq, limit=limit, book_id=book_id, alpha=0.0)
        chunks = _merge(chunks, v)
        chunks = _merge(chunks, b)

    # Query original enriquecida con nombre de entidad
    if query_en != query_orig:
        combined = f"{entity_name} {query_orig}"
        v2 = search_chunks_hybrid(
            client, combined, limit=limit // 2, book_id=book_id, alpha=alpha)
        chunks = _merge(chunks, v2)

    # Sinónimos de evento si los hay
    if extra_bm25:
        for synonym in extra_bm25:
            sq_syn = f"{entity_name} {synonym}"
            s = search_chunks_hybrid(client, sq_syn, limit=4, book_id=book_id, alpha=0.0)
            chunks = _merge(chunks, s)

    # Etiquetar chunks con la entidad de origen para que el LLM sepa de quién habla
    for chunk in chunks:
        chunk["__comparison_entity__"] = entity_name
        chunk["__comparison_book__"]   = entity.get("book_title", "")

    print(f"  [Comparison] '{entity_name}': {len(chunks)} chunks")
    return chunks


def _interleave_comparison(results_by_entity: dict[str, list[dict]],
                           max_total: int) -> list[dict]:
    """
    Mezcla equitativamente los fragmentos encontrados para las distintas entidades.
    Usa un sistema por turnos (round-robin) para asegurar que si se busca a "Batman y Superman", 
    el resultado final contenga fragmentos balanceados de ambos, empezando por el que 
    haya sacado un fragmento con mayor nivel de relevancia.
    """
    entity_order = sorted(
        results_by_entity.keys(),
        key=lambda n: _score(results_by_entity[n][0]) if results_by_entity[n] else 0,
        reverse=True,
    )
    pointers = {name: 0 for name in entity_order}
    result, seen_ids = [], set()

    while len(result) < max_total:
        added = False
        for name in entity_order:
            if len(result) >= max_total:
                break
            idx    = pointers[name]
            chunks = results_by_entity[name]
            if idx >= len(chunks):
                continue
            chunk = chunks[idx]
            pointers[name] += 1
            cid = _chunk_id(chunk)
            if cid and cid in seen_ids:
                continue
            seen_ids.add(cid)
            result.append(chunk)
            added = True
        if not added:
            break

    return result


def _search_comparison(comparison: dict, client, query_en: str, query_orig: str,
                       alpha: float, extra_bm25: list[str] | None,
                       max_per_entity: int = 4) -> list[dict]:
    """
    Es el coordinador maestro para responder a preguntas comparativas. 
    Lanza hilos paralelos para buscar cada entidad por su cuenta (_search_one_entity),
    luego filtra la 'basura' de cada resultado, y finalmente los intercala limpiamente 
    (_interleave_comparison) para que el LLM reciba una lista de contextos balanceada y etiquetada.
    """
    entities = comparison["entities"]
    print(f"\n  [Comparison] Buscando {len(entities)} entidades en paralelo")

    results_by_entity: dict[str, list[dict]] = {}

    with ThreadPoolExecutor(max_workers=min(len(entities), 4)) as pool:
        futures = {
            pool.submit(
                _search_one_entity,
                client, entity, query_en, query_orig, alpha, extra_bm25
            ): entity["name"]
            for entity in entities
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                chunks = future.result()
                # Filtrar junk y score mínimo dentro de cada entidad
                chunks = [c for c in chunks if not _is_junk(c) and _score(c) >= MIN_SCORE]
                chunks.sort(key=_score, reverse=True)
                results_by_entity[name] = chunks[:max_per_entity]
            except Exception as e:
                print(f"  [Comparison] Error en '{name}': {e}")
                results_by_entity[name] = []

    # Log resumen por entidad
    print(f"\n  [Comparison] Chunks por entidad tras filtros:")
    for name, chunks in results_by_entity.items():
        scores = [f"{_score(c):.3f}" for c in chunks]
        print(f"    '{name}': {len(chunks)} chunks — scores: {scores}")

    # Intercalar para balancear
    interleaved = _interleave_comparison(results_by_entity, max_total=MAX_CHUNKS_LLM)

    print(f"  [Comparison] Total intercalado: {len(interleaved)} chunks")
    for c in interleaved:
        entity = c.get("__comparison_entity__", "?")
        book   = c.get("__comparison_book__", "?")
        idx    = c.get("chunk_index", "?")
        sc     = _score(c)
        print(f"    [{entity}][{book}] idx={idx} score={sc:.4f} | "
              f"{c.get('content', '')[:60]}")

    return interleaved


def search_chunks(query: str, history: list | None = None) -> list[dict]:
    """
    Pipeline RAG v2.5

    Esta es la función principal que gobierna todo el proceso de recuperación de información.
    Los pasos principales que orquesta son:
    1. Define el alcance (qué libro o si debe pedir aclaración al usuario).
    2. Enriquece la consulta (resuelve pronombres apoyándose en el historial reciente).
    3. Detecta si es una pregunta comparativa (si lo es, despacha el flujo hacia el orquestador de comparaciones y termina).
    4. Clasifica la pregunta si no fue comparativa (busca si es un resumen o datos específicos).
    5. Carga listas de sinónimos de eventos y traduce la consulta al inglés para vectorización.
    6. Lanza las búsquedas necesarias en los libros adecuados de forma paralela.
    7. Pone los resúmenes en primer lugar si el usuario estaba pidiendo de qué se trata un libro.
    8. Filtra la basura y limita la injerencia de libros que no forman parte del tema central.
    9. Expande los fragmentos de mayor puntaje agregando contexto adyacente del libro original.
    10. Usa un LLM externo para reordenar (rerank) la selección final con más precisión.
    11. Corta la lista final y la devuelve lista para procesar en la respuesta de la IA.
    """
    client  = current_app.config["WEAVIATE_CLIENT"]
    history = history or []

    # Fetching all_books una sola vez y reusar en scope, enrich y comparison.
    all_books = _get_all_books(client)
    matched_books, hint_only = _resolve_scope(query, history, client, all_books=all_books)

    if not matched_books:
        if len(all_books) > 1:
            print("  Sin libro detectado -> pidiendo aclaración")
            return [{"__ask_user__": True}]
        matched_books = all_books

    scope_ids = {
        b.get("_additional", {}).get("id")
        for b in matched_books
        if b.get("_additional", {}).get("id")
    }

    # Pasar all_books para que _enrich_query detecte si el resultado
    # elimina un título de libro que estaba en la query original.
    enriched = _enrich_query(query, history, all_books=all_books)

    # Se evalúa DESPUÉS de enriquecer (pronombres ya resueltos)
    # y ANTES del resto del pipeline (tiene su propio return).
    comparison = _detect_comparison(query, enriched, all_books)
    if comparison:
        _extra_bm25_cmp = _get_event_synonyms(enriched)
        _query_en_cmp   = _to_english(enriched)
        _alpha_cmp      = _classify(enriched, original=query)["alpha"]
        return _search_comparison(
            comparison, client,
            _query_en_cmp, enriched,
            _alpha_cmp, _extra_bm25_cmp,
        )

    # Se pasa también la query original para no perder señales que
    # el enriquecedor haya eliminado (ej: "al final", "qué pasa")
    classification = _classify(enriched, original=query)
    alpha = classification["alpha"]

    # Sinónimos de evento
    extra_bm25 = _get_event_synonyms(enriched)

    # Traducir
    query_en = _to_english(enriched)

    # Chunks
    force_global = bool(extra_bm25) and hint_only

    if force_global:
        all_ids = {
            b.get("_additional", {}).get("id")
            for b in all_books
            if b.get("_additional", {}).get("id")
        }
        other_ids = all_ids - scope_ids
        raw = _search_event_global(
            client, scope_ids, other_ids, query_en, enriched, alpha, extra_bm25)

    elif hint_only:
        all_ids = [
            b.get("_additional", {}).get("id")
            for b in all_books
            if b.get("_additional", {}).get("id")
        ]
        print(f"  Búsqueda global hint_only ({len(all_ids)} libros) alpha={alpha}")
        raw = _search_parallel(client, all_ids, query_en, enriched, alpha, extra_bm25)

    else:
        print(f"  Búsqueda directa ({len(scope_ids)} libro(s)) alpha={alpha}")
        raw = _search_parallel(
            client, list(scope_ids), query_en, enriched, alpha, extra_bm25)

    # Summary-first
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

        # Fallback posicional: cuando hay position (end/beginning) y tenemos
        # book_id concreto, añadir summaries sin filtro de posición para
        # cubrir el caso donde el summary "end" no tiene la info específica.
        # También añadir chunks del libro sin filtro para que el reranker elija.
        if classification["position"] and book_id_for_summary and not summaries_raw:
            fallback_sums = search_summaries_hybrid(
                client, query_en, limit=3,
                book_id=book_id_for_summary,
                position=None,
                alpha=0.85,
            )
            if fallback_sums:
                print(f"  Summaries fallback (sin posición): {len(fallback_sums)}")
                raw = _merge(fallback_sums, raw)

    if not raw:
        print("  Sin resultados")
        return []

    # Filtrar
    raw = [c for c in raw if not _is_junk(c)]
    raw = [c for c in raw if _score(c) >= MIN_SCORE]

    if force_global:
        scope_ids = _expand_scope_ids(raw, scope_ids)

    # Después de expand_scope_ids, los libros incorporados al scope
    # ya no están limitados por FOREIGN_CAP — se tratan como scope propio.
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

    # Vecinos controlados
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

    # Re-rank (reemplaza tu sección 10 y 11 con esto)
    preferred = scope_ids if hint_only else None
    
    # Separar summaries y chunks para proteger los summaries del reranker
    sums_to_keep = [c for c in expanded if c.get("summary_index") is not None]
    chunks_to_rank = [c for c in expanded if c.get("summary_index") is None]
    
    reranked_chunks = _rerank(query_en, chunks_to_rank[:MAX_CHUNKS_LLM + 3], preferred_ids=preferred)
    
    # Poner los summaries siempre primero, luego los chunks rerankeados
    final_list = _merge(sums_to_keep, reranked_chunks)
    
    # Retornar
    return _limit(final_list)