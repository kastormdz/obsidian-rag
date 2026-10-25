# Correctitud y rendimiento del RAG — plan de implementación

**Meta:** que `obsn rag` devuelva la nota correcta en consultas parafrásticas y que una
búsqueda tarde decenas de milisegundos en vez de dos segundos.

**Arquitectura:** índice SQLite (texto troceado + vectores float32). Se separa el problema en
tres capas: (1) **qué se indexa** (chunking por bloque), (2) **cómo se puntúa** (semántico +
léxico + piso de relevancia), (3) **cómo se recorre** (matmul único + índice invertido en vez
de escanear todo el vault por consulta).

**Stack:** Python 3.13, SQLite (WAL), numpy, fastembed (`paraphrase-multilingual-MiniLM-L12-v2`,
384 dims).

---

## Números de partida (medidos 2026-10-24, vault real)

| Métrica | Antes |
|---|---|
| Notas / chunks | 196 / 21.592 |
| `index.db` | 119 MB |
| Chunks de tabla y lista | 19.007 (88%) |
| `obsn rag` punta a punta | 1,9–2,0 s |
| Búsqueda con modelo ya cargado | 350–400 ms |
| Carga del modelo | 1.309 ms |
| `obsn reindex` sin cambios | 1.852 ms (1,3 s es cargar el modelo al pedo) |
| Top-1 correcto en 10 paráfrasis | **3/10** |
| Separación coseno POS/NEG | **ninguna** (NEG máx 0.598 > POS mín 0.486) |

---

## Fase 1 — Correctitud (sin cambiar el esquema)

### Tarea 1.1 — El piso no puede juzgar todo por el chunk #1

**Archivo:** `rag/index.py` (`search()`, ~líneas 520–550)

Hoy `has_word` se evalúa **solo** contra `out[0]`. Si el top-1 no contiene ninguna palabra de
la consulta y su coseno < 0.62, se descarta **todo** el resultado, incluido un chunk #3 que sí
tenía la palabra.

**Criterio de aceptación:** `obsn rag "como funciona el reindex y los embeddings"` devuelve
`notes/obsidian-rag.md` entre los 5 primeros.

### Tarea 1.2 — El comentario del umbral miente sobre la métrica

**Archivo:** `rag/index.py` (`_SEM_FLOOR`, ~línea 555)

Dice "calibrado con `score = 0.6*lex + 0.4*coseno`" pero compara `best["sem"]` (coseno crudo).
Con `lex ≈ 0`, el score máximo es 0.4 y nunca podría llegar a 0.62.

**Criterio:** el comentario describe la métrica real; el valor queda justificado con la
calibración de la Tarea 1.4.

### Tarea 1.3 — `OBS_RAG_MODEL` se ignora

**Archivo:** `rag/index.py` (`embedder()`, línea 230)

`config.MODELO` lee la variable de entorno, pero `embedder()` **hardcodea** el nombre del
modelo. Documentado en README y en la nota del vault, y no funciona.

**Criterio:** `OBS_RAG_MODEL=intfloat/multilingual-e5-small obsn reindex` usa ese modelo.

### Tarea 1.4 — Recalibrar el piso con datos, no a ojo

Evidencia: el coseno **no separa** (POS 0.486–0.738 vs NEG 0.309–0.598). Combinar señales:

- gate léxico: si el chunk contiene una palabra con contenido → entra (sin piso de coseno);
- camino semántico puro: exigir coseno ≥ umbral **y** que el top-1 esté por encima de la
  media del vault por un margen (z-score), que sí distingue consultas fuera de dominio.

**Criterio:** las 10 paráfrasis dejan de devolver `[]` cuando la nota correcta existe; las 8
consultas fuera de dominio siguen devolviendo vacío o un resultado marcado como débil.

---

## Fase 2 — Chunking por bloque

### Tarea 2.1 — Agrupar tablas y listas

**Archivo:** `rag/index.py` (`chunk()`, líneas 170–222)

Hoy el `flush()` corre en **cada** línea que empieza con `|` o con `-`. Una fila de tabla es un
chunk de ~8 tokens: 19.007 micro-chunks en el vault, y por la normalización por densidad
(`tf = count / len(tokens)`) **ganan siempre** contra las secciones que explican el tema.

Nuevo criterio: un bloque de tabla (header + separador + filas) o de lista (bullets
consecutivos) es **un chunk**, partido solo si supera un tope (`max_chars ≈ 1.200`,
respetando líneas).

**Criterio:** chunks bajan a ~4.000–6.000; `index.db` a ~40 MB; ninguna fila de tabla queda
aislada en su propio chunk.

### Tarea 2.2 — Tests del chunker

**Archivo:** `tests/test_chunk.py` (nuevo)

Casos: tabla completa en un chunk; lista completa en un chunk; tabla gigante se parte; prosa
corta se fusiona; fence > 3.000 chars se parte; el último bloque corto se indexa; heading
heredado.

---

## Fase 3 — Rendimiento de búsqueda

### Tarea 3.1 — Un solo matmul en vez de 21.592 `frombuffer`

**Archivo:** `rag/index.py` (`search()`)

Hoy recorre fila por fila, hace `np.frombuffer` y calcula la norma de **cada** vector por
consulta. Reemplazar por: leer todos los BLOB en un buffer contiguo → `reshape(N, 384)` →
normalizar la matriz de una vez → `mat @ qv` → `argpartition` para el top-k.

**Criterio:** de 350–400 ms a < 100 ms con el modelo ya cargado.

### Tarea 3.2 — No cargar el modelo si el vector de la consulta ya está cacheado

**Archivo:** `rag/index.py` (`search()`), `rag/obsn.py` (`cmd_rag`)

La consulta se cachea por hash en la tabla `emb`, pero el CLI carga el modelo **antes** de
mirar la caché: pagar 1,3 s para no usarlo. Buscar en `emb` primero.

**Criterio:** segunda ejecución idéntica de `obsn rag` < 200 ms de punta a punta.

### Tarea 3.3 — Modelo perezoso en `build()`

**Archivo:** `rag/index.py` (`build()`)

`model = embedder()` corre siempre, aunque las 196 notas estén sin cambios.

**Criterio:** `obsn reindex` sin cambios: de 1.852 ms a < 600 ms.

### Tarea 3.4 — Índice invertido (`postings`)

**Archivo:** `rag/index.py` (esquema + `build()` + `search()`)

Hoy cada consulta tokeniza **todo el texto del vault** con regex (21.592 `tok()`). Nuevo:

```sql
CREATE TABLE postings (
    term    TEXT NOT NULL,
    chunk   INTEGER NOT NULL,
    n       INTEGER NOT NULL,   -- frecuencia en el chunk
    in_meta INTEGER NOT NULL,   -- 1 si está en path o heading
    PRIMARY KEY (term, chunk)
) WITHOUT ROWID;
```

La parte léxica pasa a ser una consulta con índice (`WHERE term IN (...)`), y el puntaje se
calcula solo sobre candidatos: unión de (chunks que contienen los términos) y (top-K por
coseno). El texto completo se lee únicamente para los k resultados finales.

**Criterio:** búsqueda < 60 ms con modelo cargado; deja de importar cuánto crezca el vault en
texto (sí en vectores).

### Tarea 3.5 — No pagar el vector cuando el léxico ya alcanza

**Archivo:** `rag/index.py` (`search()`, fase 1)

Medido después de 3.1–3.4: de los ~29 ms de una búsqueda, **23 ms eran traer 11 MB de vectores**
y 7,7 ms el matmul; y de los ~180 ms del CLI, **77 ms eran importar numpy**. Pero ese trabajo
se descarta cuando la nota ganadora ya está identificada por el índice invertido.

**Cambio:** si alguna nota cubre ≥ `_COBERTURA_FUERTE` de la masa IDF, se responde sólo con
`postings` + `math.log1p` (sin numpy, sin vectores). El coseno entra únicamente cuando el
léxico no alcanza.

**Resultado medido:** CLI 180-194 ms → **64-74 ms**; término exacto in-process 30 ms → **3 ms**;
paráfrasis con nota ganadora ~90 ms. Banco sin cambios (POS 3/10 top-1, 5/10 top-5, NEG 0/8).

**Trade-off:** en la fase léxica no hay coseno que desempate, así que si dos notas cubren la
consulta el orden lo deciden tf/heading. Para recuperar el desempate semántico habría que
pagar numpy + el matmul (o cachear la matriz en un proceso de vida larga).

---

## Fase 4 — Calidad de recuperación (medir antes de decidir)

### Tarea 4.1 — Banco de evaluación reproducible

**Archivo:** `bench/bench_search.py` (nuevo)

10 paráfrasis con nota esperada + 8 consultas fuera de dominio + 5 consultas de término exacto.
Reporta top-1, top-5, latencia, y si devolvió vacío. **Este banco es el gate de las fases
siguientes**: sin él, cualquier cambio de modelo es fe.

### Tarea 4.2 — Modelo con prefijos de tarea (E5)

**A medir, no asumir:** `intfloat/multilingual-e5-small` embebe con `query: ` / `passage: `
y suele subir el top-1 varios puntos sobre MiniLM en retrieval multilingüe. Requiere rebuild
completo (~5 min) y cambia la dimensionalidad del contrato (`emb` por hash de texto no alcanza:
el hash debe incluir el prefijo).

**Criterio de adopción:** sube el top-1 del banco **sin** bajar el top-5 y sin duplicar la
latencia. Si no, se descarta y queda documentado por qué.

### Tarea 4.3 — ANN sólo si el vault crece

Con 5.000 chunks el matmul es ~10 ms y sobra. Por encima de ~200.000 chunks (≈20× el vault
actual) evaluar `sqlite-vec`. **No implementar ahora** (YAGNI).

---

---

## Fase 5 — Arquitectura tomada de `es617/obsidian-sync-mcp`

Ese proyecto **no usa LLM ni embeddings**: su `search_notes` es substring sobre el texto en RAM
(`ARCHITECTURE.md`: *"substring search, no inverted index"*; FlexSearch se sacó por memoria).
Su valor no es la búsqueda, es la sincronización y el endurecimiento. Qué se adopta y qué no:

### 5.1 — Índice sin texto en claro (seguridad + tamaño) — IMPLEMENTADO

**Idea:** ellos mantienen el texto **solo en memoria** y nunca lo escriben a disco. Nuestro
`chunks.text` era una copia del vault en claro (119 MB).

**Cambio aplicado:** `chunks` guarda `off_start`/`off_end` (rango de líneas en la nota) en
vez de `text`; los resultados se releen del vault al mostrarlos; `df` se deriva de
`postings`; `graph` y `link` leen el vault en vez del índice.

**Resultado medido:** `index.db` 115 MB → 41 MB, y la frase del vault ya no aparece en la
DB ni en el WAL. El texto que devuelve `obsn rag` es idéntico al de antes (test de
round-trip del chunker).

### 5.2 — Cifrado del índice (solo si se conserva texto)

`AES-256-GCM` con `scrypt(passphrase, salt, 32)` y formato `salt:iv:tag:ciphertext`
(`src/search.ts`). Portar a Python con `hashlib.scrypt` + `cryptography`.

### 5.3 — `IndexState`: `building | ready | failed`

Hoy `rag` devuelve `[]` sin distinguir "no hay nada" de "el índice está incompleto". Guardar
el estado en `meta` y que `obsn status` / `obsn rag` lo digan explícitamente.

### 5.4 — Salida apta para agentes

`list_notes` abre con totales, filtros activos y orden, y al truncar **nombra las carpetas
omitidas con conteo**. Aplicar a `obsn list` y `obsn rag` (que hoy no dice si truncó).

### 5.5 — Sincronización con ovaultd (event-driven)

**Contexto real:** el vault vive en disco (`~/Obsidian`) y lo sincroniza **ovaultd** (plugin de
Obsidian en el escritorio, o `ovaultd sync --continuous` headless). El server es **ciego**:
guarda blobs cifrados y los paths son HMAC, así que el índice **no puede** ni debe alimentarse
desde la DB del server — se alimenta del plaintext local, como hoy.

Lo que cambia no es la fuente, es el **disparador**. Hoy: timer cada 15 min.

**Opción A — systemd path unit (recomendada).** Un `PathChanged=` sobre el vault con
`OnUnitActiveSec` de debounce (~2 s) dispara `obsn reindex` incremental. Requisito medido
(ovaultd, pitfall 26): los renames externos llegan como `created`+`deleted`, los dotfiles y
`.obsidian/` **no generan eventos**, y un create+rm en <300 ms puede perderse. Por eso el scan
por **hash** sigue siendo la verdad y el timer queda como red de seguridad (p. ej. cada 1 h).

**Opción B — hook después del sync headless.** Si el sync corre en un servicio, un wrapper con
`OnSuccess=` llama `obsn reindex` tras cada pasada. Para saltear el walk haría falta que
`ovaultd list-local` emita **JSON** (hoy imprime texto, sin `--json`): es un cambio chico en
`packages/cli` y convierte el reindex en "aplicá esta lista de paths". A 7.550 chunks el walk
cuesta ~240 ms y no urge; a 10.000+ notas sí.

**Lo que NO se hace:**

- Leer el **bridge local** del CLI (`127.0.0.1:18931`, `/fs/*`): no tiene auth (hallazgo F-02 de
  la auditoría) y es una superficie interna, no un contrato.
- Consumir la DB del server: paths HMAC + contenido E2E ⇒ inútil para indexar.
- Depender de eventos como verdad: los eventos son pista, el hash es la verdad.

**Estado: IMPLEMENTADO (2026-10-24) con una corrección de diseño.** El `PathModified=` de
systemd **no es recursivo**: vigila el directorio que le nombrás, así que un path unit sobre la
raíz del vault no ve los cambios en subcarpetas (donde vive casi todo). Generar una entrada por
subdirectorio es frágil. Lo que se hizo:

1. **Worker async después de escribir** (`rag/reindex_worker.py`): `write/edit/delete/move`
   lanzan un reindexado en segundo plano con `fork+exec` detachado. Debounce (~1,5 s) para que
   aterrice la ráfaga y **coalescencia por `flock`**: cinco escrituras seguidas producen un solo
   reindex. Costo medido para el usuario: **57-62 ms por escritura, igual que sin el worker**.
   Se apaga con `--no-reindex` o `OBS_RAG_NO_ASYNC=1`.
2. **Timer de 2 minutos** (antes 15) como red de seguridad para las escrituras que no pasan por
   `obsn` (Obsidian, sync de ovaultd). Costo: ~240 ms de CPU cada 2 min ≈ 0,2%.

La ventana de ceguera del `rag` pasó de **≤15 min a ≤2 min**, y a ~2 s para lo que escribe el
propio agente. `obsn search` sigue viendo el disco al instante.

### 5.6 — Checklist de exposición como MCP

Si algún día `obsn` se expone por HTTP/MCP: OAuth 2.1 + PKCE (`auth.ts`), rate limit con
backoff exponencial, `READ_ONLY` a nivel backend (`vault-readonly.ts`), `WRITE_FOLDERS`
boundary-aware (`write-scope.ts`), `host-guard` contra DNS rebinding, y
`_meta["anthropic/maxResultSizeChars"]` en `read_note`.

### Lo que NO se adopta

- Búsqueda por substring: nuestro híbrido (cobertura IDF + coseno) es superior en español.
- Texto íntegro en RAM: el vault no entra cómodo en memoria.
- Un vault por instancia: ya resolvemos multi-vault por `OBS_RAG_VAULT`.

---

## Orden de ejecución y rollback

1. Fase 1 → tests → commit.
2. Fase 2 → `reindex --force` → verificar 196/196 → commit.
3. Fase 3.1–3.3 → medir → commit.
4. Fase 3.4 → medir → commit.
5. Fase 4.1 → banco → medir 4.2 → decidir.

El índice se puede borrar y reconstruir sin pérdida: **rollback = `git revert` + `reindex
--force`**. El vault nunca se toca (solo lectura).

## Riesgos

| Riesgo | Mitigación |
|---|---|
| El chunking nuevo cambia rankings esperados | banco de evaluación (4.1) antes de Fase 4; tests del chunker |
| `emb` cacheado con otro modelo devuelve dimensiones mezcladas | hash del texto embebido incluye modelo y prefijo; `dim` ya se guarda |
| Rebuild largo bloquea el timer de 15 min | `reindex` toma lock de escritura SQLite; el timer reintenta (timeout 30 s) |
| Cambiar el piso esconde falsos positivos | test de regresión con las 8 consultas fuera de dominio |
