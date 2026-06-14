"""
regenerate.py -- Regenera summaries de posicion "end" para todos los libros
o para uno especifico, detectando el final narrativo real y saltando
glosarios, apendices y backmatter.

Uso:
    python regenerate.py
    python regenerate.py --title "DUNE MESSIAH"
"""

import sys
import os
import re
import uuid
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "key.env"))

import weaviate

from models.books import (
    list_books,
    get_chunk_by_book_and_index,
    _summarize_block,
    SUMMARY_BLOCK_WORDS,
)

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

WORDS_PER_CHUNK  = 300
END_BLOCK_CHUNKS = SUMMARY_BLOCK_WORDS // WORDS_PER_CHUNK  # ~33 chunks


# ---------------------------------------------------------------------------
# Deteccion de backmatter
# ---------------------------------------------------------------------------

# Palabras clave de seccion que confirman backmatter inmediatamente
_BACKMATTER_KW = re.compile(
    r'\b('
    r'appendix|apendice|'
    r'glossary|glosario|'
    r'terminology|terminologia|'
    r'cartograph|'
    r'bibliography|bibliografia|'
    r'copyright|all rights reserved|isbn|'
    r'published by|publishing group|'
    r'about the author|sobre el autor|'
    r'acknowledgment|agradecimiento|'
    r'also by the author|otros libros|'
    r'notes for map|notas para el mapa|'
    r'table of contents|tabla de contenidos|'
    r'terminology of the|'
    r'imperial calendar|'
    r'spacing guild bank'
    r')\b',
    re.IGNORECASE,
)

# Patron especifico del glosario de Dune:
# "Water of Life. WATER OF LIFE: an illuminating poison"
# Detecta PALABRA(S) mayusculas + punto + PALABRA(S) mayusculas
_DUNE_GLOSSARY_PATTERN = re.compile(
    r'[A-Z]{2,}[\s\'\-]*[A-Z]*\.\s+[A-Z]{2,}',
)

# Entradas de glosario: "Termino: definicion"
_GLOSSARY_ENTRY = re.compile(
    r'^[A-Z][A-Za-z\'\- ]{0,55}:\s+\S',
    re.MULTILINE,
)

# Indice de capitulos
_CHAPTER_INDEX = re.compile(r'^Chapter\s+\d+', re.IGNORECASE | re.MULTILINE)


def _is_backmatter(content: str) -> bool:
    """
    Devuelve True si el chunk parece ser backmatter.
    Evalua el chunk completo con multiples heuristicas.
    """
    # 1. Palabras clave de seccion en TODO el contenido
    if _BACKMATTER_KW.search(content):
        return True

    # 2. Patron especifico del glosario de Dune
    #    "TERM. TERM: definition" en los primeros 600 chars
    if _DUNE_GLOSSARY_PATTERN.search(content[:600]):
        return True

    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not lines:
        return False

    total = len(lines)

    # 3. Densidad de entradas "Termino: definicion"
    glossary_hits = sum(1 for ln in lines if _GLOSSARY_ENTRY.match(ln))
    if total > 0 and (glossary_hits / total) > 0.25:
        return True

    # 4. Densidad de lineas de indice de capitulos
    chapter_hits = sum(1 for ln in lines if _CHAPTER_INDEX.match(ln))
    if total > 0 and (chapter_hits / total) > 0.35:
        return True

    # 5. Alta densidad de lineas cortas con ":" -> glosario denso
    colon_short = sum(
        1 for ln in lines
        if ':' in ln and len(ln.split(':')[0].strip()) < 50
    )
    if total >= 5 and (colon_short / total) > 0.50:
        return True

    return False


# ---------------------------------------------------------------------------
# Helpers Weaviate
# ---------------------------------------------------------------------------

def _get_client():
    url = os.environ.get("WEAVIATE_URL", "http://localhost:8080")
    print(f"Conectando a Weaviate en {url}...")
    client = weaviate.Client(url)
    print("Conexion OK\n")
    return client


def _get_total_chunks(client, book_id):
    result = (
        client.query
        .get("BookChunk", ["chunk_index"])
        .with_where({"path": ["book_id"], "operator": "Equal", "valueText": book_id})
        .with_additional(["id"])
        .with_limit(10_000)
        .do()
    )
    return len(result.get("data", {}).get("Get", {}).get("BookChunk", []) or [])


def _get_all_summaries(client, book_id):
    result = (
        client.query
        .get("BookSummary", ["summary_index", "book_id", "position"])
        .with_where({"path": ["book_id"], "operator": "Equal", "valueText": book_id})
        .with_additional(["id"])
        .with_limit(100)
        .do()
    )
    return result.get("data", {}).get("Get", {}).get("BookSummary", []) or []


def _delete_summaries_by_position(client, summaries, position):
    targets = [s for s in summaries if s.get("position") == position]
    for s in targets:
        sid = s.get("_additional", {}).get("id")
        if not sid:
            continue
        try:
            client.data_object.delete(sid, class_name="BookSummary")
            print(f"  Borrado summary '{position}' idx={s.get('summary_index')} ({sid[:8]}...)")
        except Exception as e:
            print(f"  Error borrando {sid[:8]}: {e}")
    return targets


def _save_summary(client, book_id, content, summary_index, position):
    sid = str(uuid.uuid4())
    client.data_object.create(
        data_object={
            "content":       content,
            "summary_index": summary_index,
            "book_id":       book_id,
            "position":      position,
        },
        class_name="BookSummary",
        uuid=sid,
    )
    client.data_object.reference.add(
        from_class_name="BookSummary", from_uuid=sid,
        from_property_name="book",
        to_class_name="Book",          to_uuid=book_id,
    )
    return sid


# Senales de prosa narrativa real (dialogo, verbos de accion, parrafos largos)
_NARRATIVE_SIGNALS = re.compile(
    r'(\b(said|asked|replied|whispered|shouted|thought|felt|knew|saw|heard'
    r'|walked|ran|turned|looked|spoke|answered|demanded|murmured'
    r'|dijo|pregunto|respondio|susurro|grito|penso|sintio|supo|vio|oyo'
    r'|camino|corrio|miro|hablo|exigio|murmuro)\b)',
    re.IGNORECASE,
)

# Notas academicas y referencias cruzadas — tipicas de apendices
_APPENDIX_NOTE = re.compile(
    r'(^Note:|^\(Note|\bsee also\b|\bcross.reference\b|\bcf\.\b)',
    re.IGNORECASE | re.MULTILINE,
)


def _is_narrative(content):
    """
    True si el chunk es prosa narrativa real de ficcion.
    Descarta glosarios, apendices y notas aunque no tengan keywords de seccion.
    """
    if _is_backmatter(content):
        return False
    if _APPENDIX_NOTE.search(content[:400]):
        return False
    if _NARRATIVE_SIGNALS.search(content):
        return True
    # Heuristica: prosa corrida tiene lineas largas y pocas con ":"
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not lines:
        return False
    avg_words  = sum(len(ln.split()) for ln in lines) / len(lines)
    no_colon   = sum(1 for ln in lines if ":" not in ln) / len(lines)
    return avg_words > 12 and no_colon > 0.60


# ---------------------------------------------------------------------------
# Busqueda del final narrativo real
# ---------------------------------------------------------------------------

def _find_narrative_end(client, book_id, total_chunks):
    """
    Escanea hacia atras buscando el ultimo chunk con prosa narrativa real.
    Requiere N_CONFIRM chunks narrativos consecutivos (escaneando hacia atras)
    para confirmar que salio del backmatter y llego al texto de ficcion.
    """
    N_CONFIRM = 3
    scan_to   = max(0, int(total_chunks * 0.50))
    print(f"  Escaneando chunks {total_chunks - 1} -> {scan_to}...")

    consecutive    = 0
    last_narrative = None

    for idx in range(total_chunks - 1, scan_to, -1):
        chunk = get_chunk_by_book_and_index(client, book_id, idx)
        if not chunk:
            consecutive = 0
            continue
        content = chunk.get("content", "")
        is_narr = _is_narrative(content)

        if (total_chunks - 1 - idx) < 25:
            tag     = "NARRATIVO" if is_narr else "BACKMATTER"
            preview = content[:85].replace("\n", " ")
            print(f"    idx={idx} [{tag}] | {preview}")

        if is_narr:
            consecutive += 1
            last_narrative = idx
            if consecutive >= N_CONFIRM:
                confirmed = last_narrative + N_CONFIRM - 1
                print(f"  -> Final narrativo confirmado: chunk_index={confirmed}")
                return confirmed
        else:
            consecutive = 0

    if last_narrative is not None:
        print(f"  -> Final narrativo (sin confirmar N={N_CONFIRM}): chunk_index={last_narrative}")
        return last_narrative

    fallback = int(total_chunks * 0.80)
    print(f"  No se encontro final narrativo -> fallback chunk={fallback}")
    return fallback

def _read_block(client, book_id, end_idx, n_chunks):
    """
    Lee hasta n_chunks consecutivos terminando en end_idx,
    filtrando chunks de backmatter dentro del bloque.
    Garantiza que el texto enviado al LLM sea 100% narrativo.
    """
    start_idx = max(0, end_idx - n_chunks + 1)
    print(f"  Leyendo chunks {start_idx}-{end_idx} ({end_idx - start_idx + 1} chunks)...")
    texts = []
    skipped = []
    for idx in range(start_idx, end_idx + 1):
        chunk = get_chunk_by_book_and_index(client, book_id, idx)
        if not chunk:
            continue
        content = chunk.get("content", "")
        if _is_backmatter(content):
            skipped.append(idx)
            continue
        texts.append(content)
    if skipped:
        print(f"  Chunks de backmatter omitidos dentro del bloque: {skipped}")
    print(f"  Chunks narrativos usados: {len(texts)}")
    return " ".join(texts)


# ---------------------------------------------------------------------------
# Regenerar summary "end"
# ---------------------------------------------------------------------------

def regenerate_end_summary(client, book):
    book_id = book.get("_additional", {}).get("id")
    title   = book.get("title", "?")
    author  = book.get("author", "?")

    if not book_id:
        return {"title": title, "status": "error", "reason": "sin book_id"}

    print(f"\n{'--' * 28}")
    print(f"Libro : '{title}' por {author}")
    print(f"ID    : {book_id}")

    total_chunks = _get_total_chunks(client, book_id)
    print(f"Chunks totales: {total_chunks}")
    if total_chunks == 0:
        return {"title": title, "status": "error", "reason": "sin chunks"}

    # 1. Encontrar el final narrativo real
    narrative_end = _find_narrative_end(client, book_id, total_chunks)

    # 2. Leer el bloque final
    block_text = _read_block(client, book_id, narrative_end, END_BLOCK_CHUNKS)
    if not block_text.strip():
        return {"title": title, "status": "error", "reason": "bloque vacio"}

    print(f"  Texto del bloque: {len(block_text.split())} palabras")

    # 3. Generar summary
    # Usamos position="middle" porque el bloque ya esta filtrado de backmatter:
    # solo contiene texto narrativo. Tomar las primeras 5000 palabras del bloque
    # limpio es mas fiable que tomar las ultimas (que en position="end" podrian
    # incluir restos del glosario si el filtrado no fue perfecto).
    # El prompt del LLM igual recibe instruccion de resumir el final/desenlace.
    print(f"  Generando summary 'end' con LLM...")
    new_summary = _summarize_block(block_text, title, author, position="middle")

    if not new_summary or not new_summary.strip():
        return {"title": title, "status": "error", "reason": "LLM no genero summary"}

    print(f"  Summary generado: {len(new_summary.split())} palabras")
    print(f"  Preview: {new_summary[:300]}")

    # 4. Borrar summaries "end" existentes
    all_summaries = _get_all_summaries(client, book_id)
    deleted       = _delete_summaries_by_position(client, all_summaries, "end")

    # Reusar el mismo summary_index del borrado; si no habia, usar max+1
    if deleted:
        end_idx = deleted[0].get("summary_index", 1)
    else:
        max_idx = max((s.get("summary_index", 0) for s in all_summaries), default=0)
        end_idx = max_idx + 1

    # 5. Guardar nuevo summary
    sid = _save_summary(client, book_id, new_summary, end_idx, "end")
    print(f"  OK Summary 'end' guardado (summary_index={end_idx}, uuid={sid[:8]}...)")

    return {"title": title, "status": "ok", "summary_index": end_idx}



def regenerate_beginning_summary(client, book):
    """
    Regenera el summary de posicion "beginning" usando los primeros chunks
    narrativos reales, saltando el chunk 0 (tabla de contenidos) y cualquier
    otro frontmatter.
    """
    book_id = book.get("_additional", {}).get("id")
    title   = book.get("title", "?")
    author  = book.get("author", "?")

    if not book_id:
        return {"title": title, "status": "error", "reason": "sin book_id"}

    total_chunks = _get_total_chunks(client, book_id)
    if total_chunks == 0:
        return {"title": title, "status": "error", "reason": "sin chunks"}

    # Buscar el primer chunk narrativo real (saltando frontmatter/indice)
    print(f"  Buscando inicio narrativo...")
    narrative_start = None
    for idx in range(1, min(total_chunks, 30)):
        chunk = get_chunk_by_book_and_index(client, book_id, idx)
        if not chunk:
            continue
        content = chunk.get("content", "")
        if _is_narrative(content):
            if narrative_start is None:
                narrative_start = idx
            # Confirmar con 2 chunks narrativos consecutivos
            next_chunk = get_chunk_by_book_and_index(client, book_id, idx + 1)
            if next_chunk and _is_narrative(next_chunk.get("content", "")):
                narrative_start = idx
                print(f"  Inicio narrativo confirmado: chunk_index={idx}")
                break

    if narrative_start is None:
        narrative_start = 1
        print(f"  Inicio narrativo no encontrado, usando chunk 1")

    # Leer bloque del inicio
    end_idx   = min(narrative_start + END_BLOCK_CHUNKS - 1, total_chunks - 1)
    print(f"  Leyendo chunks {narrative_start}-{end_idx}...")
    texts = []
    for idx in range(narrative_start, end_idx + 1):
        chunk = get_chunk_by_book_and_index(client, book_id, idx)
        if chunk and not _is_backmatter(chunk.get("content", "")):
            texts.append(chunk.get("content", ""))

    block_text = " ".join(texts)
    if not block_text.strip():
        return {"title": title, "status": "error", "reason": "bloque vacio"}

    print(f"  Texto del bloque: {len(block_text.split())} palabras")
    print(f"  Generando summary 'beginning' con LLM...")
    new_summary = _summarize_block(block_text, title, author, position="beginning")

    if not new_summary or not new_summary.strip():
        return {"title": title, "status": "error", "reason": "LLM no genero summary"}

    print(f"  Summary generado: {len(new_summary.split())} palabras")
    print(f"  Preview: {new_summary[:200]}")

    # Borrar el summary "beginning" existente y guardar el nuevo
    all_summaries = _get_all_summaries(client, book_id)
    deleted       = _delete_summaries_by_position(client, all_summaries, "beginning")

    if deleted:
        beg_idx = deleted[0].get("summary_index", 0)
    else:
        beg_idx = 0

    sid = _save_summary(client, book_id, new_summary, beg_idx, "beginning")
    print(f"  OK Summary 'beginning' guardado (summary_index={beg_idx}, uuid={sid[:8]}...)")

    return {"title": title, "status": "ok", "summary_index": beg_idx}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Regenera summaries para libros en Weaviate."
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Titulo exacto del libro a regenerar.",
    )
    parser.add_argument(
        "--position",
        type=str,
        default="end",
        choices=["end", "beginning", "all"],
        help="Posicion del summary a regenerar (default: end).",
    )
    args = parser.parse_args()

    client = _get_client()
    books  = list_books(client)

    print(f"Libros en la base de datos: {len(books)}")
    for b in books:
        bid = b.get("_additional", {}).get("id", "?")[:8]
        print(f"  [{bid}...] '{b.get('title')}' -- {b.get('author')}")

    if not books:
        print("\nNo hay libros indexados.")
        return

    if args.title:
        target   = args.title.strip().lower()
        filtered = [b for b in books if b.get("title", "").strip().lower() == target]
        if not filtered:
            print(f"\nNo se encontro '{args.title}'. Titulos disponibles:")
            for b in books:
                print(f"  '{b.get('title')}'")
            sys.exit(1)
        books = filtered
        print(f"\nRegenerando solo: '{args.title}' (position={args.position})")
    else:
        print(f"\nRegenerando todos los libros (position={args.position})...")

    results = []
    for book in books:
        print(f"\n{'--' * 28}")
        print(f"Libro: '{book.get('title')}'")
        if args.position in ("end", "all"):
            r = regenerate_end_summary(client, book)
            results.append({"pos": "end", **r})
        if args.position in ("beginning", "all"):
            r = regenerate_beginning_summary(client, book)
            results.append({"pos": "beginning", **r})

    print(f"\n{'==' * 28}")
    print("RESUMEN FINAL:")
    ok    = [r for r in results if r.get("status") == "ok"]
    error = [r for r in results if r.get("status") == "error"]
    for r in ok:
        print(f"  OK [{r.get('pos','?')}] '{r['title']}': summary_index={r.get('summary_index')}")
    for r in error:
        print(f"  ERROR [{r.get('pos','?')}] '{r['title']}': {r.get('reason', '?')}")
    print(f"\n  {len(ok)} OK -- {len(error)} errores")


if __name__ == "__main__":
    main()