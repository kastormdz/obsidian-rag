"""
index — índice local de chunks del vault, con embeddings.

POR QUÉ EXISTE
─────────────
Buscar leyendo y embebiendo las notas en cada consulta cuesta minutos: este módulo
mantiene el texto chunkeado + los vectores en un SQLite local, así una búsqueda es
`query → SQLite` en milisegundos.

SINCRONIZACIÓN
──────────────
- `build()` camina el vault en disco y actualiza solo las notas cuyo HASH cambió.
- `build(force=True)` reindexa todo.
- El índice es un archivo local: se puede borrar y reconstruir sin pérdida.

USO
───
    obsn reindex            # indexa/actualiza
    obsn status             # stats
    obsn rag "consulta"     # búsqueda semántica
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Optional

from .config import DB as _CONFIG_DB, LEGACY_DB, MODELO, VAULT as _CONFIG_VAULT, EMBEDDINGS_CACHE

DB = str(_CONFIG_DB)
# El vault vive descifrado en disco. El índice se construye desde el filesystem:
# no hay API que liste notas (y con sync E2EE no puede haberla).
VAULT = str(_CONFIG_VAULT)
# Directorios que no son contenido: config del editor, papelera, VCS.
_EXCLUIDOS = {".obsidian", ".trash", ".git", ".stfolder", ".stversions"}

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS notes (
    path        TEXT PRIMARY KEY,
    mtime       REAL,
    size        INTEGER,
    hash        TEXT,        -- sha1 del contenido: señal fiable de "cambió"
    n_chunks    INTEGER DEFAULT 0,
    indexed_at  REAL
);

CREATE TABLE IF NOT EXISTS chunks (
    id      INTEGER PRIMARY KEY,
    path    TEXT NOT NULL,
    ord     INTEGER,
    heading TEXT,
    hlevel  INTEGER,
    ctype   TEXT,
    text    TEXT,
    vec     BLOB,
    n_tok   INTEGER
);

CREATE INDEX IF NOT EXISTS ix_chunks_path ON chunks(path);

-- Índice invertido: término → chunk, con frecuencia y si aparece en path/heading.
-- Sin esto, cada consulta tokeniza TODO el vault con regex (~100 ms con 7.550
-- chunks): la parte léxica pasa a ser una consulta indexada sobre candidatos.
CREATE TABLE IF NOT EXISTS postings (
    term    TEXT NOT NULL,
    chunk   INTEGER NOT NULL,
    n       INTEGER NOT NULL,
    in_meta INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (term, chunk)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_postings_chunk ON postings(chunk);
CREATE TABLE IF NOT EXISTS df (
    term TEXT PRIMARY KEY,
    n    INTEGER
);

-- Cache de embeddings por hash. Comparte formato con la DB histórica
-- (~/.local/share/obsidian-rag/legacy-embeddings.db) para poder reusarla/migrarla.
CREATE TABLE IF NOT EXISTS emb (
    h   TEXT PRIMARY KEY,
    dim INTEGER,
    v   BLOB
);

-- Estado y versión del índice. Sin esto, cambiar el criterio de troceado o el
-- modelo deja un índice que dice estar al día pero responde con datos viejos.
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""

# Versión del criterio de troceado. Si cambia `chunk()`, subir esto: el hash de la
# NOTA no cambia, así que sin este marcador un `reindex` incremental no re-trocea
# nada y el índice viejo sobrevive mintiendo.
CHUNKER_VERSION = 2

# Versión del índice invertido. Se puede poblar sin re-embeber (sale del texto ya
# guardado), así que subirla NO obliga a un rebuild de 4 minutos.
POSTINGS_VERSION = 1


def conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.executescript(SCHEMA)
    _ensure_columns(c)
    _migrate_legacy_embeddings(c)
    return c


def _ensure_columns(c: sqlite3.Connection) -> None:
    """Agregar columnas faltantes a tablas ya creadas.

    `CREATE TABLE IF NOT EXISTS` no altera una tabla existente: si el esquema
    crece, la DB vieja se queda sin la columna nueva y el primer SELECT revienta
    con "no such column". Por eso el esquema NO alcanza — hay que migrar.
    """
    expected = {
        "notes": [("hash", "TEXT"), ("mtime", "REAL"), ("size", "INTEGER"),
                  ("n_chunks", "INTEGER"), ("indexed_at", "REAL")],
        "chunks": [("n_tok", "INTEGER")],
        "emb": [("modelo", "TEXT")],
    }
    for table, cols in expected.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue
        for name, decl in cols:
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                print(f"[index] migracion: {table}.{name} agregada", flush=True)
    c.commit()


_LEGACY_DB = str(LEGACY_DB)


def _migrate_legacy_embeddings(c: sqlite3.Connection) -> None:
    """Copiar la cache histórica de embeddings a la DB del índice (una vez).

    La DB vieja tiene 4.400+ vectores ya calculados; recalcularlos cuesta
    minutos. Solo corre si la tabla emb local está vacía.
    """
    try:
        row = c.execute("SELECT COUNT(*) FROM emb").fetchone()
        if row and row[0] > 0:
            return
        if not os.path.exists(_LEGACY_DB) or os.path.abspath(_LEGACY_DB) == os.path.abspath(DB):
            return
        src = sqlite3.connect(f"file:{_LEGACY_DB}?mode=ro", uri=True, timeout=10)
        n = src.execute("SELECT COUNT(*) FROM emb").fetchone()[0]
        if not n:
            src.close()
            return
        c.execute("ATTACH DATABASE ? AS legacy", (_LEGACY_DB,))
        c.execute("INSERT OR IGNORE INTO emb (h, dim, v) SELECT h, dim, v FROM legacy.emb")
        c.commit()
        c.execute("DETACH DATABASE legacy")
        src.close()
        print(f"[index] migrados {n} embeddings historicos desde obsidian-embeddings.db", flush=True)
    except Exception as exc:
        print(f"[index] no se migraron los embeddings historicos: {exc}", file=sys.stderr)


# ── Chunking (mismo criterio que el cliente: headings, fences, tablas) ───────
def chunk(content: str, min_chars: int = 120, max_chars: int = 1200) -> list[dict[str, Any]]:
    """Divide en chunks respetando estructura markdown. Devuelve lista de dicts.

    Un bloque de tabla (header + separador + filas) y un bloque de lista (bullets
    consecutivos) son UN chunk, no una fila por chunk. Con un chunk por línea, el
    vault real quedaba en 19.007 micro-trozos de ~8 tokens, y la normalización por
    densidad (tf = apariciones / tokens) hacía que una fila de tabla le ganara a la
    sección que explica el tema. Un bloque se corta sólo si supera `max_chars`.
    """
    if not content or len(content.strip()) < 20:
        return []
    lines = content.split("\n")
    out: list[dict[str, Any]] = []
    buf: list[str] = []
    heading, hlevel, ctype = None, 0, "prose"
    in_fence = False

    def flush(final: bool = False) -> None:
        nonlocal buf, heading, hlevel, ctype
        text = "\n".join(buf).strip()
        # min_chars es un piso de calidad, NO un filtro: una sección corta pero
        # densa ("## Backup → /srv/...") es exactamente lo que se busca. Si se
        # descarta, el término queda invisible y el RAG devuelve notas ajenas
        # con confianza falsa. Medido 2026-09-29: el token de una nota de prueba
        # vivía en un chunk de 100 chars y el RAG no la encontraba NUNCA.
        if text:
            if len(text) < min_chars and out and ctype == "prose":
                # Bloque corto de prosa: se FUSIONA con el anterior en vez de
                # descartarse. Descartarlo es cómo una nota corta queda invisible
                # al RAG (medido 2026-10-19: cuerpo de 62 chars perdido).
                out[-1]["text"] = (out[-1]["text"] + "\n\n" + text).strip()
            elif final or len(text) >= min_chars or not out or ctype in ("code", "table", "list"):
                out.append({"heading": heading, "hlevel": hlevel, "ctype": ctype, "text": text})
        buf = []

    for ln in lines:
        if ln.strip().startswith("```"):
            if in_fence:
                buf.append(ln)
                flush()
                in_fence = False
                ctype = "prose"
            else:
                flush()
                in_fence = True
                ctype = "code"
                buf.append(ln)
            continue
        if in_fence:
            buf.append(ln)
            # Sub-chunking de fences enormes (un solo chunk de 16KB no es buscable)
            if sum(len(b) for b in buf) > 3000:
                buf.append("")
                flush()
                ctype = "code"
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", ln)
        if m:
            flush()
            heading, hlevel = m.group(2).strip(), len(m.group(1))
            ctype = "prose"
            buf.append(ln)
            continue
        if not ln.strip():
            flush()
            continue
        # Tipo del bloque según la línea. Mientras el tipo no cambie, las líneas se
        # acumulan: así una tabla entera (o una lista entera) es un solo chunk.
        if ln.lstrip().startswith("|"):
            nuevo = "table"
        elif re.match(r"^\s*[-*+]\s|^\s*\d+[.)]\s", ln):
            nuevo = "list"
        else:
            nuevo = "prose"
        # Cambio de tipo → cortar. Bloque al tope → cortar y seguir con el mismo
        # tipo (sin esto, una tabla de 400 filas es un chunk de 40 KB que ningún
        # embedding representa bien).
        if nuevo != ctype or sum(len(b) + 1 for b in buf) + len(ln) > max_chars:
            flush()
            ctype = nuevo
        buf.append(ln)
    # El ÚLTIMO bloque va siempre, aunque sea corto: si no, una nota entera de
    # <120 chars se indexa solo por su heading (medido 2026-10-19: "# Prueba
    # RAG" quedó como único chunk y el cuerpo de la nota era invisible).
    flush(final=True)

    # Un chunk de fence puede haber quedado partido sin heading: agregale el del padre
    last_h, last_l = None, 0
    for c in out:
        if c["hlevel"]:
            last_h, last_l = c["heading"], c["hlevel"]
        elif c["heading"] is None:
            c["heading"], c["hlevel"] = last_h, last_l
    return out


# ── Embeddings (fastembed, cacheados en la DB de embeddings existente) ──────
def embedder():
    try:
        from fastembed import TextEmbedding
        return TextEmbedding(
            model_name=MODELO,
            # cache_dir EXPLICITO. Sin esto fastembed usa su default
            # (fastembed/common/utils.py: tempfile.gettempdir()/fastembed_cache) y en Hermes
            # TMPDIR apuntaba a una caché temporal que un pruner barre a
            # las 24h y duplicaba el cache vivo del RAG. Medido 2026-10-17: 305 MB duplicados
            # byte a byte contra la caché del modelo.
            cache_dir=os.path.expanduser(
                os.environ.get("FASTEMBED_CACHE_PATH", str(EMBEDDINGS_CACHE))
            ),
        )
    except Exception as exc:  # pragma: no cover
        print(f"[warn] fastembed no disponible ({exc}); RAG degradará a TF-IDF", file=sys.stderr)
        return None


def _hash(t: str) -> str:
    return hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()


def _hash_emb(t: str) -> str:
    """Clave de caché de embeddings: incluye el modelo.

    Sin el modelo en la clave, cambiar `OBS_RAG_MODEL` devuelve los vectores del
    modelo anterior (otra dimensión, otro espacio) y el coseno compara peras con
    manzanas. `dim` en la tabla no alcanza: dos modelos pueden compartir 384.
    """
    return hashlib.sha1(f"{MODELO}\x00{t}".encode("utf-8", "replace")).hexdigest()


def _meta(c: sqlite3.Connection, k: str, default: Optional[str] = None) -> Optional[str]:
    fila = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return fila[0] if fila else default


def _set_meta(c: sqlite3.Connection, k: str, v: Any) -> None:
    c.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?,?)", (k, str(v)))


def _fila_postings(texto: str, metadatos: str) -> list[tuple[str, int, int]]:
    """(término, frecuencia, en_metadatos) de un chunk. Una vez por build, no por consulta."""
    cuenta: dict[str, int] = {}
    for t in tok(texto):
        cuenta[t] = cuenta.get(t, 0) + 1
    en_meta = set(tok(metadatos))
    return [(t, n, 1 if t in en_meta else 0) for t, n in cuenta.items()]


def _postings_de_nota(c: sqlite3.Connection, path: str, chunks: list[dict], ids: list[int]) -> None:
    filas = []
    for ch, cid in zip(chunks, ids):
        filas.extend((t, cid, n, im)
                     for t, n, im in _fila_postings(ch["text"], f"{path} {ch['heading'] or ''}"))
    if filas:
        c.executemany(
            "INSERT OR REPLACE INTO postings (term, chunk, n, in_meta) VALUES (?,?,?,?)", filas
        )


def _poblar_postings(c: sqlite3.Connection) -> None:
    """Recorre los chunks ya guardados y arma el índice invertido (sin re-embeber)."""
    c.execute("DELETE FROM postings")
    t0 = time.perf_counter()
    pendientes: list[tuple] = []
    total = 0
    for cid, path, h, txt in c.execute("SELECT id, path, heading, text FROM chunks").fetchall():
        filas = _fila_postings(txt, f"{path} {h or ''}")
        pendientes.extend((t, cid, n, im) for t, n, im in filas)
        c.execute("UPDATE chunks SET n_tok=? WHERE id=?", (sum(n for _, n, _ in filas), cid))
        total += 1
        if len(pendientes) > 20000:
            c.executemany(
                "INSERT OR REPLACE INTO postings (term, chunk, n, in_meta) VALUES (?,?,?,?)",
                pendientes,
            )
            pendientes = []
    if pendientes:
        c.executemany(
            "INSERT OR REPLACE INTO postings (term, chunk, n, in_meta) VALUES (?,?,?,?)", pendientes
        )
    _set_meta(c, "postings", POSTINGS_VERSION)
    c.commit()
    print(f"[build] indice invertido: {total} chunks en {time.perf_counter()-t0:.1f} s", flush=True)


def _purgar_emb_viejos(c: sqlite3.Connection) -> None:
    """Borra vectores cacheados de otro modelo (o de antes de que se guardara el modelo).

    La caché `emb` es lo que más pesa en la DB (81 MB con 41.000 filas, de las cuales
    la mayoría eran del esquema de hash viejo). No afecta al índice: se puede
    recalcular. Se purga y se compacta.
    """
    viejos = c.execute(
        "SELECT COUNT(*) FROM emb WHERE modelo IS NULL OR modelo != ?", (MODELO,)
    ).fetchone()[0]
    if not viejos:
        return
    c.execute("DELETE FROM emb WHERE modelo IS NULL OR modelo != ?", (MODELO,))
    c.commit()
    print(f"[build] purgados {viejos} embeddings de otro modelo", flush=True)
    try:
        c.execute("VACUUM")
        print("[build] DB compactada", flush=True)
    except sqlite3.Error as exc:
        print(f"[build] no se pudo compactar: {exc}", file=sys.stderr, flush=True)


def embed_all(texts: list[str], c: sqlite3.Connection, model=None) -> list[Optional[Any]]:
    """Embeddings con cache por hash (modelo incluido) en la tabla emb.

    `model` puede venir en None: en ese caso se resuelve perezosamente, y sólo si
    falta embeber algo. Un `reindex` sin cambios no tiene por qué cargar 1,3 s de
    modelo para no usarlo.
    """
    import numpy as np
    hashes = [_hash_emb(t) for t in texts]
    got: dict[str, Any] = {}
    unicos = list(dict.fromkeys(hashes))
    for i in range(0, len(unicos), 500):
        lote = unicos[i : i + 500]
        q = "SELECT h, dim, v FROM emb WHERE h IN (%s)" % ",".join("?" * len(lote))
        for h, dim, v in c.execute(q, lote):
            got[h] = np.frombuffer(v, dtype="float32").reshape(dim)
    faltan = [h for h in unicos if h not in got]
    if faltan:
        if model is None:
            model = _MODEL_HOLDER[0] or embedder()
            _MODEL_HOLDER[0] = model
        if model is not None:
            nuevos = list(model.embed(missing_texts := [texts[hashes.index(h)] for h in faltan]))
            c.executemany(
                "INSERT OR REPLACE INTO emb (h, dim, v, modelo) VALUES (?,?,?,?)",
                [(_hash_emb(t), v.shape[0], v.astype("float32").tobytes(), MODELO)
                 for t, v in zip(missing_texts, nuevos)],
            )
            c.commit()
            for h, v in zip(faltan, nuevos):
                got[h] = v
    return [got.get(h) for h in hashes]


# ── Indexado ─────────────────────────────────────────────────────────────────
def listar_notas() -> list[dict[str, Any]]:
    """Camina el vault en disco: (name relativo, mtime, path absoluto)."""
    salida: list[dict[str, Any]] = []
    if not os.path.isdir(VAULT):
        return salida
    for raiz, dirs, files in os.walk(VAULT):
        dirs[:] = sorted(d for d in dirs if d not in _EXCLUIDOS and not d.startswith("."))
        for nombre in sorted(files):
            if nombre.startswith(".") or not nombre.endswith(".md"):
                continue
            abs_ = os.path.join(raiz, nombre)
            try:
                mt = os.stat(abs_).st_mtime
            except OSError:
                continue
            rel = os.path.relpath(abs_, VAULT).replace(os.sep, "/")
            salida.append({"name": rel, "mtime": mt, "abs": abs_})
    return salida


def leer_nota(abs_: str) -> str:
    with open(abs_, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def build(force: bool = False) -> None:
    c = conn()
    # El índice sabe con qué criterio se troceó y con qué modelo se embebió. Si
    # cambió cualquiera de los dos, el índice viejo NO sirve aunque el hash de la
    # nota sea idéntico: hay que reconstruirlo entero.
    if not force:
        if (_meta(c, "chunker") != str(CHUNKER_VERSION)) or (_meta(c, "modelo") != MODELO):
            print("[build] criterio de indexado o modelo distinto al del índice: rebuild completo",
                  flush=True)
            force = True
    _set_meta(c, "estado", "building")
    c.commit()
    notes = listar_notas()
    # El índice invertido se arma desde el texto ya guardado: no hace falta
    # re-embeber para tenerlo, así que esta migración es barata.
    if _meta(c, "postings") != str(POSTINGS_VERSION) and _meta(c, "chunker") == str(CHUNKER_VERSION):
        _poblar_postings(c)
    _purgar_emb_viejos(c)

    changed, same, failed = [], 0, []
    for n in notes:
        path = n["name"]
        try:
            body = leer_nota(n["abs"])
        except OSError:
            failed.append(path)
            continue
        mt = float(n.get("mtime") or 0.0)
        # El HASH DEL CONTENIDO es la única señal fiable de "cambió".
        # mtime solo: la cache de metadata del server tiene TTL y puede venir
        # vieja → el write nuevo pasa inadvertido. size solo: una edición que
        # mantiene el largo (un caracter por otro) pasa inadvertida igual.
        # Medido 2026-09-29: una nota recién escrita con el MISMO largo no
        # reindexaba y el RAG seguía sin encontrarla.
        ch = hashlib.sha1(body.encode("utf-8", "replace")).hexdigest()
        prev = c.execute("SELECT size, mtime, hash FROM notes WHERE path=?", (path,)).fetchone()
        if prev and not force and len(prev) > 2 and prev[2] == ch:
            same += 1
            continue
        changed.append((path, mt, body))

    print(f"[build] vault={len(notes)}  sin cambios={same}  a reindexar={len(changed)}  fallidas={len(failed)}", flush=True)

    # PURGAR lo que ya no está en el vault. Sin esto el índice solo crece: una
    # nota borrada por MCP queda en el índice para siempre y el RAG puede
    # devolver un path que no existe — el peor modo de falla para un consumidor
    # que después va a read_note() y recibe "Note not found".
    # Medido 2026-09-29: 7 notas de prueba borradas seguían en el índice.
    server_paths = {n["name"] for n in notes}
    stale = [r[0] for r in c.execute("SELECT path FROM notes") if r[0] not in server_paths]
    if stale:
        for p in stale:
            ids = [r[0] for r in c.execute("SELECT id FROM chunks WHERE path=?", (p,))]
            if ids:
                c.executemany("DELETE FROM postings WHERE chunk=?", [(i,) for i in ids])
        c.executemany("DELETE FROM chunks WHERE path=?", [(p,) for p in stale])
        c.executemany("DELETE FROM notes WHERE path=?", [(p,) for p in stale])
        print(f"[build] purgadas {len(stale)} notas que ya no estan en el vault", flush=True)

    for i, (path, mt, body) in enumerate(changed, 1):
        # Los postings viejos apuntan a chunks que se van a borrar: hay que sacarlos
        # ANTES, si no quedan huérfanos apuntando a ids que ya no existen.
        ids_viejos = [r[0] for r in c.execute("SELECT id FROM chunks WHERE path=?", (path,))]
        if ids_viejos:
            c.executemany("DELETE FROM postings WHERE chunk=?", [(i2,) for i2 in ids_viejos])
        c.execute("DELETE FROM chunks WHERE path=?", (path,))
        chunks = chunk(body)
        # El texto embebido incluye path + heading: sin el nombre de la nota,
        # un chunk como "Primer deploy" no tiene ningún contexto semántico y se
        # vuelve indistinguible de miles. Medido 2026-09-29: incluir el path
        # devolvió al top las notas correctas de CouchDB.
        vecs = (embed_all([f"{path} :: {ch['heading'] or ''} :: {ch['text']}" for ch in chunks], c)
                if chunks else [])
        # Los vectores se guardan NORMALIZADOS: así la búsqueda es un único matmul
        # y no recalcula 5.000 normas por consulta.
        import numpy as np
        empaquetados = []
        for v in vecs:
            if v is None:
                empaquetados.append(None)
            else:
                norma = float(np.linalg.norm(v)) or 1.0
                empaquetados.append((v / norma).astype("float32").tobytes())
        filas_postings: list[list[tuple[str, int, int]]] = []
        tuplas = []
        for j, ch in enumerate(chunks):
            filas = _fila_postings(ch["text"], f"{path} {ch['heading'] or ''}")
            filas_postings.append(filas)
            tuplas.append((path, j, ch["heading"], ch["hlevel"], ch["ctype"], ch["text"],
                           empaquetados[j], sum(n for _, n, _ in filas)))
        c.executemany(
            "INSERT INTO chunks (path, ord, heading, hlevel, ctype, text, vec, n_tok)"
            " VALUES (?,?,?,?,?,?,?,?)",
            tuplas,
        )
        ids_nuevos = [r[0] for r in
                      c.execute("SELECT id FROM chunks WHERE path=? ORDER BY ord", (path,))]
        pendientes = [(t, cid, n, im)
                      for filas, cid in zip(filas_postings, ids_nuevos)
                      for t, n, im in filas]
        if pendientes:
            c.executemany(
                "INSERT OR REPLACE INTO postings (term, chunk, n, in_meta) VALUES (?,?,?,?)",
                pendientes,
            )
        chash = hashlib.sha1(body.encode("utf-8", "replace")).hexdigest()
        c.execute(
            "INSERT OR REPLACE INTO notes (path, mtime, size, hash, n_chunks, indexed_at)"
            " VALUES (?,?,?,?,?,?)",
            (path, mt, len(body), chash, len(chunks), time.time()),
        )
        if i % 10 == 0:
            c.commit(); print(f"  … {i}/{len(changed)}", flush=True)
    c.commit()

    # df (document frequency para TF-IDF)
    c.execute("DELETE FROM df")
    df: dict[str, int] = {}
    for (txt,) in c.execute("SELECT text FROM chunks"):
        for t in set(tok(txt)):
            df[t] = df.get(t, 0) + 1
    c.executemany("INSERT INTO df (term, n) VALUES (?,?)", df.items())
    c.commit()

    total = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    withvec = c.execute("SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]
    print(f"[build] listo: {len(notes)} notas, {total} chunks ({withvec} con embedding)", flush=True)
    if failed:
        print(f"[build] no leídas: {len(failed)} -> {failed[:5]}", flush=True)

    # Un build NUNCA vuelve a pasar en silencio con notas afuera del índice:
    # verifica que cada nota listada del vault tenga su fila en `notes`.
    indexed = {r[0] for r in c.execute("SELECT path FROM notes")}
    faltan = sorted(server_paths - indexed)
    if failed or faltan:
        _set_meta(c, "estado", "failed")
        c.commit()
        print(
            f"[build] ERROR: {len(failed)} ilegibles, {len(faltan)} sin indexar",
            file=sys.stderr, flush=True,
        )
        for p in faltan[:10]:
            print(f"[build]   sin indexar: {p}", file=sys.stderr, flush=True)
        raise SystemExit(1)
    # Sólo un build que verificó todo se declara listo. Cualquier consumidor
    # (agente incluido) puede leer `meta.estado` y saber si el índice está entero.
    _set_meta(c, "chunker", CHUNKER_VERSION)
    _set_meta(c, "modelo", MODELO)
    _set_meta(c, "normalizado", "1")
    _set_meta(c, "indexado", time.time())
    _set_meta(c, "estado", "ready")
    c.commit()
    print(f"[build] verificado: {len(indexed)}/{len(server_paths)} notas del índice", flush=True)


_TOK = re.compile(r"[a-z0-9áéíóúüñ]+")

# Stopwords: sin esto, "zqxjkv-9931-no-existe" toma "no" como su término más
# específico (aparece en la mitad del español escrito) y el piso de relevancia
# deja de filtrar ruido. Son las que nunca son el sujeto de una búsqueda.
_STOP = {
    "de", "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o", "u",
    "en", "con", "por", "para", "del", "al", "que", "se", "su", "sus", "es",
    "son", "como", "mas", "más", "pero", "the", "of", "to", "in", "and", "or",
    "a", "an", "at", "on", "for", "no", "si", "sí", "ya", "muy", "sin", "sobre",
    "entre", "desde", "hasta", "este", "esta", "esto", "ese", "esa", "todo",
    "todos", "cada", "mi", "tu", "va", "van", "es", "hay", "the",
}


def tok(s: str) -> list[str]:
    return _TOK.findall(s.lower())


def key_terms(s: str) -> list[str]:
    """Tokens con contenido (sin stopwords). Si todo es stopword, usa todos."""
    t = _TOK.findall(s.lower())
    keep = [w for w in t if w not in _STOP and len(w) > 1]
    return keep or t


def search(query: str, k: int = 5, alpha: float = 0.6) -> list[dict]:
    c = conn()
    import numpy as np

    total_chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    if not total_chunks:
        return []
    qset = set(tok(query))
    if not qset:
        return []
    # Los términos CON contenido (sin stopwords) son los que definen el sujeto de la
    # búsqueda: si no, "no" puede ser el más específico y la cobertura deja de filtrar.
    key_set = set(key_terms(query))

    # ── IDF de la consulta, una fila por término
    idf: dict[str, float] = {}
    for t in qset:
        fila = c.execute("SELECT n FROM df WHERE term=?", (t,)).fetchone()
        idf[t] = float(np.log1p(total_chunks / (1 + (fila[0] if fila else 0))))
    idf_max = max(idf.values()) if idf else 1.0
    w = {t: (idf[t] / idf_max if idf_max else 0.0) for t in qset}
    lex_w = (sum(w.values()) / len(w)) if w else 1.0
    masa_total = sum(idf.get(t, 0.0) for t in key_set) or 1.0

    # ── Señal léxica: índice invertido. Nada de tokenizar el vault por consulta.
    marcas = ",".join("?" * len(qset))
    por_chunk: dict[int, dict[str, float]] = {}
    for term, cid, n, im in c.execute(
        f"SELECT term, chunk, n, in_meta FROM postings WHERE term IN ({marcas})", tuple(qset)
    ):
        d = por_chunk.setdefault(cid, {"tf": 0.0, "masa": 0.0, "meta": 0})
        d["tf"] += n
        d["masa"] += idf.get(term, 0.0)
        if im:
            d["meta"] = 1

    # ── Señal semántica: un matmul, sin traer el texto de todo el vault
    qv = _query_vector(query, c)
    if qv is None:
        return []
    qv = qv / (np.linalg.norm(qv) or 1.0)
    filas = c.execute(
        "SELECT id, path, ord, heading, hlevel, ctype, vec, n_tok FROM chunks"
        " WHERE vec IS NOT NULL"
    ).fetchall()
    if not filas:  # sin embeddings → TF-IDF puro sobre texto
        puntajes = []
        for path, o, h, hl, ct, txt in c.execute(
                "SELECT path, ord, heading, hlevel, ctype, text FROM chunks"):
            tt = tok(txt)
            s = sum(tt.count(t) for t in qset)
            if s:
                puntajes.append((s, path, o, h, hl, ct, txt))
        puntajes.sort(key=lambda x: -x[0])
        return [{"path": r[1], "ord": r[2], "heading": r[3], "chunk_type": r[5],
                 "score": r[0] / 100, "text": r[6][:6000]} for r in puntajes[:k]]
    try:
        mat = np.frombuffer(b"".join(r[6] for r in filas), dtype=np.float32).reshape(len(filas), -1)
    except ValueError as exc:
        raise ValueError(
            "el índice tiene vectores de distinto tamaño (¿cambiaste de modelo?): "
            "corré `obsn reindex --force`"
        ) from exc
    # Si el índice ya se construyó con vectores normalizados, no se normaliza de
    # nuevo: esa división costaba ~45 ms por consulta para no cambiar nada.
    if _meta(c, "normalizado") != "1":
        nrm = np.linalg.norm(mat, axis=1, keepdims=True)
        nrm[nrm == 0] = 1.0
        mat = mat / nrm
    sems = np.clip(mat @ qv, 0.0, 1.0)

    # ── Candidatos: los que tienen señal léxica + el top semántico. El resto no
    # puede ganar (sin léxico su score es 0.4*coseno y nunca pasa el piso), así que
    # no se puntúa: eso es lo que hace que la consulta no dependa del tamaño del vault.
    por_id = {r[0]: i for i, r in enumerate(filas)}
    tope = max(50, 20 * k)
    if len(sems) > tope:
        top_sem = np.argpartition(-sems, tope - 1)[:tope]
    else:
        top_sem = range(len(sems))
    candidatos = set(por_chunk) | {int(filas[i][0]) for i in top_sem}

    out = []
    for cid in candidatos:
        pos = por_id.get(cid)
        if pos is None:
            continue
        _cid, path, o, h, hl, ct, _vec, n_tok = filas[pos]
        sem = float(sems[pos])
        d = por_chunk.get(cid)
        tf = min(1.0, d["tf"] / n_tok) if (d and n_tok) else 0.0
        cobertura = (d["masa"] / masa_total) if d else 0.0
        # La cobertura SUMA al score, no lo reemplaza: ramificar por cobertura
        # (devolver sólo los chunks con match léxico) tiraba a la basura la señal
        # semántica y coronaba coincidencias flojas de una palabra común.
        score = alpha * (tf * lex_w) + (1 - alpha) * sem + _PESO_COBERTURA * cobertura
        # Boost por nombre/heading (paridad con el cliente)
        if d and d["meta"]:
            score *= 1.35
        if hl == 1:
            score += 0.15
        elif hl == 2:
            score += 0.10
        out.append({"path": path, "ord": o, "heading": h, "hlevel": hl,
                    "chunk_type": ct, "score": score, "sem": sem,
                    "_cob": cobertura, "_id": cid})
    out.sort(key=lambda x: -x["score"])

    # 1) Evidencia léxica FUERTE (el chunk cubre casi todo lo específico de la
    #    consulta): esos chunks son la respuesta. Se ordenan por score, que ya
    #    incluye la cobertura — ordenar sólo por cobertura premiaba chunks que
    #    repetían una palabra común en un texto irrelevante.
    fuertes = [r for r in out if r["_cob"] >= _COBERTURA_FUERTE]
    if fuertes:
        fuertes.sort(key=lambda r: -r["score"])
        elegidos = fuertes[:k]
    # 2) Sin evidencia léxica: es una paráfrasis. El piso se aplica al mejor chunk,
    #    pero NO se vacía el resultado por culpa de un único chunk flojo (bug medido
    #    2026-10-24: `rag "como funciona el reindex y los embeddings"` devolvía []
    #    aunque un chunk posterior sí tenía la palabra "reindex").
    elif out and out[0]["sem"] >= _SEM_FLOOR:
        elegidos = out[:k]
    else:
        return []

    # El texto se lee SÓLO para los k ganadores: el índice no lo necesita para
    # puntuar, así que la búsqueda no arrastra los 3 MB del vault a memoria.
    ids = [r["_id"] for r in elegidos]
    textos = dict(c.execute(
        f"SELECT id, text FROM chunks WHERE id IN ({','.join('?' * len(ids))})", ids
    ))
    for r in elegidos:
        r.pop("_cob", None)
        r["text"] = textos.get(r.pop("_id"), "")
    return elegidos


_MODEL_HOLDER = [None]

# Cobertura léxica por masa IDF. `_PESO_COBERTURA` la suma al score (no reemplaza
# al coseno): así un chunk que contiene los términos de la consulta se ordena mejor
# sin anular la señal semántica. `_COBERTURA_FUERTE` es el umbral para considerarlo
# respuesta directa (cubre casi todo lo específico de lo que se preguntó).
_PESO_COBERTURA = 0.35
_COBERTURA_FUERTE = 0.7

# Piso de relevancia del camino PURO SEMÁNTICO (sin ninguna palabra de la consulta
# en el chunk). La métrica es el COSENO CRUDO, no el score combinado: cuando no hay
# match léxico `lex = 0`, así que `score = 0.4 * coseno` y jamás podría llegar a
# 0.62 — comparar el score acá sería una puerta cerrada.
#
# Calibrado 2026-10-24 contra el vault real (196 notas, 21.592 chunks), 10
# paráfrasis con nota esperada vs 8 consultas fuera de dominio:
#   positivas   coseno 0.486 – 0.738   (top-1 correcto sólo 3/10 antes de arreglar
#                                       el ranking léxico)
#   negativas   coseno 0.309 – 0.598
# El coseno NO separa los grupos: hay negativas por encima de positivas. Por eso el
# trabajo de precisión lo hace la cobertura IDF de arriba, y este piso queda como
# último recurso para no devolver ruido cuando no hay ninguna evidencia léxica.
# Si el ranking vuelve a devolver basura, recalibrar acá con números, no a ojo.
_SEM_FLOOR = 0.62


def _query_vector(query: str, c: sqlite3.Connection):
    """Vector de la consulta, desde la caché `emb` si ya se calculó alguna vez.

    Cargar el modelo cuesta ~1,3 s y el vector se cachea por hash: mirar la caché
    primero evita pagar el modelo para una consulta ya vista.
    """
    import numpy as np
    texto = f":: {query}"
    fila = c.execute("SELECT dim, v FROM emb WHERE h=?", (_hash_emb(texto),)).fetchone()
    if fila:
        return np.frombuffer(fila[1], dtype="float32").reshape(fila[0])
    vecs = embed_all([texto], c)
    return vecs[0] if vecs else None


def _main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "build":
        build(force="--force" in sys.argv)
    elif cmd == "status":
        c = conn()
        n = c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        ch = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        vec = c.execute("SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]
        last = c.execute("SELECT MAX(indexed_at) FROM notes").fetchone()[0]
        print(f"notas={n} chunks={ch} con_embedding={vec} "
              f"ultimo_index={time.strftime('%Y-%m-%d %H:%M', time.localtime(last)) if last else 'nunca'}")
    elif cmd == "search":
        q = " ".join(sys.argv[2:-1]) if sys.argv[-1].isdigit() else " ".join(sys.argv[2:])
        k = int(sys.argv[-1]) if sys.argv[-1].isdigit() else 5
        t0 = time.perf_counter()
        res = search(q, k)
        print(f"[{time.perf_counter()-t0:.3f}s] {len(res)} resultados para {q!r}")
        for r in res:
            print(f"  {r['score']:.3f}  {r['path']} :: {r.get('heading')} ({r['chunk_type']})")
    else:
        print("uso: obsn {reindex [--force]|status|rag 'consulta' [N]}")


if __name__ == "__main__":
    _main()
