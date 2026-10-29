---
name: obsidian-rag
description: "Use when reading, searching or writing a local Obsidian vault (RAG semántico + CRUD). Busca antes de responder, cita la ruta y reindexa después de escribir."
version: 1.0.0
author: kastormdz
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [rag, obsidian, search, notes, mcp]
    repo: https://github.com/kastormdz/obsidian-rag
---

# obsidian-rag — buscar y escribir en un vault de Obsidian

Índice local (SQLite + embeddings) sobre un vault de Obsidian. **No necesita servidor,
cuenta ni base de datos remota**: camina una carpeta de archivos `.md`, los trocea, guarda
los vectores en un SQLite y responde búsquedas en milisegundos.

Un solo comando para todo: **`obsn`**.

## Instalación

```bash
git clone https://github.com/kastormdz/obsidian-rag.git
cd obsidian-rag
python -m venv .venv && . .venv/bin/activate
pip install -e .
obsn status      # ¿hay índice?
obsn reindex     # construirlo (la primera vez descarga el modelo, ~90 MB)
```

El paquete expone el entrypoint `obsn` (ver `pyproject.toml`). Si preferís un launcher
propio —por ejemplo, para usar el intérprete de un venv concreto sin activarlo—:

```bash
#!/usr/bin/env bash
# ~/.local/bin/obsn
set -euo pipefail
exec "$HOME/.venvs/obsidian-rag/bin/python" -m rag.obsn "$@"
```

Si `obsn` falla con `ModuleNotFoundError: rag`, falta `pip install -e .` **en ese
intérprete**. `python3` del sistema puede no ser el mismo que el del venv.

## Uso rápido

| Comando | Qué hace |
|---|---|
| `obsn rag "consulta" [N]` | búsqueda híbrida (léxica + semántica). `N` default 5, `--json`, `--fresh-only` |
| `obsn stale [--days N]` | notas vencidas según su `stale_after`, con los días de atraso |
| `obsn search "texto"` | substring exacto sobre el contenido, sin modelo. `--folder`, `--tag`, `--limit`, `--json` |
| `obsn reindex [--force]` | indexa/actualiza. Sin `--force` solo toca las notas cuyo **hash** cambió |
| `obsn status` | notas, chunks y ruta del índice |
| `obsn upgrade [--check]` | actualiza el paquete desde su repo y reindexa si hace falta (`--check`: 0 al día, 10 hay update) |
| `obsn read "ruta.md"` | contenido completo |
| `obsn write "ruta.md" --file x.md` | crear/sobrescribir (atómico). También por stdin. Reindexa en segundo plano (`--no-reindex` lo apaga) |
| `obsn edit "ruta.md" append --file x.md` | `append` / `prepend` / `replace` |
| `obsn delete "ruta.md"` | a `.trash/` (reversible). `--hard --yes` borra de verdad |
| `obsn move "a.md" "b.md"` | mover o renombrar (no pisa) |
| `obsn list [carpeta] [--tag T] [--limit N] [--json]` | listar notas |
| `obsn folders` / `obsn tags` | carpetas y etiquetas con conteo |
| `obsn meta "ruta.md"` | frontmatter, tags, backlinks, tamaño |

Los nombres largos del estándar MCP son alias de los cortos: `read_note`, `write_note`,
`edit_note`, `delete_note`, `move_note`, `list_notes`, `list_folders`, `list_tags`,
`search_notes`, `get_note_metadata`. Sirven para que un agente use el mismo contrato sin
adaptar nada.

## Si sos un agente de Hermes

Con el plugin instalado (`hermes plugins install kastormdz/obsidian-rag#plugins/obsidian-rag`),
no ejecutes comandos: hay siete tools que hacen exactamente lo mismo.

| En vez de | Llamá |
|---|---|
| `obsn rag "<tema>" 5` | `vault_search` (`mode: hybrid`) |
| `obsn search "<texto>"` | `vault_search` (`mode: exact`) |
| `obsn read <path>` | `vault_read` |
| `obsn write` / `obsn edit` | `vault_write` (`create`/`append`/`prepend`/`replace`) |
| `obsn list` / `folders` / `tags` | `vault_list` (`notes`/`folders`/`tags`) |
| `obsn move` | `vault_move` |
| `obsn delete` | `vault_delete` |
| `obsn status` | `vault_status` |

El protocolo de abajo aplica igual: es el mismo motor, sin la terminal en el medio. No
reindexes a mano después de escribir; el plugin dispara el mismo worker.

## Protocolo para agentes

1. **Buscar antes de responder.** Toda pregunta sobre el vault empieza con
   `obsn rag "<tema>" 5`. Si es un nombre propio, un host o una sigla, sumá
   `obsn search "<texto>"` (substring exacto, sin modelo).
2. **Citar la ruta** de la nota en la respuesta. El campo `path` que devuelve `rag` es
   relativo al vault: sirve tal cual para `obsn read`.
3. **Si `rag` devuelve vacío**, no concluyas que no existe. En este orden:
   `obsn status` (¿índice vacío o incompleto?) → `obsn reindex` → `obsn search`.
4. **Después de escribir o editar, el índice se actualiza solo.** El CLI lanza un
   reindexado en segundo plano (debounce ~1,5 s, con coalescencia: cinco escrituras
   seguidas producen un solo reindex) y devuelve en ~60 ms. Si necesitás la nota en el
   índice **ahora**, corré `obsn reindex`; si sólo necesitás encontrarla ya, `obsn search`
   lee el disco y no depende del índice.
5. **Antes de crear una nota**, buscá la canónica: `obsn rag "<tema>" 3` +
   `obsn search "<nombre>"`. Si ya existe una nota del mismo sujeto, agregale una sección
   en vez de crear una nota paralela.
6. **Para consumir desde código**, usá `--json` y leé `path`/`text`/`score`. Nunca parsees
   la salida humana.

## Cómo busca

- **Chunking por bloque:** headings, fences, tablas y listas. Un bloque de tabla o de
  lista es **un** chunk (no una fila por chunk); un bloque se corta solo si supera
  ~1.200 caracteres. Prosa muy corta (<120 chars) se fusiona con el bloque anterior y el
  último bloque siempre se indexa: una nota chica nunca queda invisible.
- **Qué se embebe:** `"path :: heading :: texto"`. Sin el path y el heading, un chunk como
  "Primer deploy" no tiene contexto y es indistinguible de miles.
- **Señal léxica:** índice invertido `postings(term, chunk, n, in_meta)`. La cobertura se
  mide por **masa IDF**: cubrir un término raro pesa más que cubrir uno que aparece en
  medio vault.
- **Señal semántica:** un único matmul contra los vectores normalizados del índice.
- **Score:** `0.6 * (tf * peso_idf) + 0.4 * coseno + 0.35 * cobertura`, con `x1.35` si el
  término está en path/heading y `+0.15`/`+0.10` por `h1`/`h2`. Por los boosts, un score
  puede superar 1: **no lo interpretes como probabilidad**, es un orden.
- **Vacío antes que ruido:** si no hay ninguna palabra de la consulta en el chunk, se exige
  coseno ≥ 0.62. El modelo siempre devuelve "algo"; devolver `[]` es una respuesta válida y
  preferible a un falso positivo que el consumidor lea como acierto.

## Limitación conocida (importante)

Con el modelo por defecto (`paraphrase-multilingual-MiniLM-L12-v2`) la recuperación
**puramente semántica es débil**: en un banco de 10 paráfrasis sin palabras en común con la
nota, el top-1 acierta ~1-2 de 10. El sistema rinde cuando la consulta **comparte al menos
un término concreto** con la nota.

En la práctica:

- Usá **2 a 4 palabras clave concretas** (`"certificados wildcard renovación"`), no
  oraciones con muletillas (`"cómo hago para renovar un certificado que está vencido"`).
- Si el resultado no aparece, probá sinónimos y sin acentos.
- Para mejorar la recuperación de paráfrasis hay que cambiar de modelo de embeddings
  (mpnet/E5) y volver a medir con el banco: ver `bench/bench_search.py`.

## Rendimiento medido

Vault de referencia: 196 notas, 7.550 chunks, 384 dimensiones float32 normalizados.

| Operación | Tiempo |
|---|---|
| `obsn search` (léxico, sin modelo) | ~60 ms |
| `obsn rag` con evidencia léxica (el caso común) | **~65 ms** |
| `obsn rag` en proceso, fase léxica | **~3-25 ms** |
| `obsn rag` paráfrasis sin términos (necesita el modelo) | ~1,7 s la primera vez · ~90-180 ms después |
| `obsn reindex` sin cambios | ~240 ms |
| `obsn reindex --force` | ~220 s (7.550 embeddings en CPU) |
| Tamaño del índice | 31 MB (antes 115 MB con chunking por línea y caché vieja) |

La búsqueda va **en dos fases**: si una nota cubre al menos el 70% de la masa IDF de la
consulta, se responde sólo con el índice invertido, sin traer los 11 MB de vectores ni
importar numpy (3-25 ms). El coseno entra únicamente cuando el léxico no alcanza: ahí se
paga el matmul y, si la consulta es nueva, la carga del modelo.

La consulta se cachea por hash en la tabla `emb`, así que **repetir una búsqueda no carga
el modelo**. La primera ejecución de una consulta nueva sí.

## Variables de entorno

| Variable | Default | Qué hace |
|---|---|---|
| `OBS_RAG_VAULT` | `~/Obsidian` | carpeta del vault a leer e indexar |
| `OBS_RAG_INDEX` | `$XDG_DATA_HOME/obsidian-rag/index.db` | archivo SQLite del índice |
| `OBS_RAG_CACHE` | `$XDG_CACHE_HOME/fastembed` | caché del modelo de embeddings |
| `OBS_RAG_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | modelo de embeddings |

Cambiar `OBS_RAG_MODEL` **fuerza un rebuild completo** en el próximo `reindex` (el índice
guarda con qué modelo se construyó, y la caché `emb` está particionada por modelo).

## Problemas y soluciones

| Síntoma | Causa | Qué hacer |
|---|---|---|
| `rag` devuelve `(sin resultados)` para algo que existe | índice vacío, viejo o incompleto | `obsn status` → `obsn reindex` → `obsn search "término"` |
| `ModuleNotFoundError: rag` | el paquete no está instalado en **ese** intérprete | `pip install -e .` en el venv correcto |
| Los backticks del texto desaparecen o el comando hace otra cosa | markdown multilínea por argv: el shell interpreta backticks y `$` | pasarlo por `--file` o por stdin |
| La búsqueda trae resultados que no tienen nada que ver | la consulta es demasiado genérica (`"docker"`, `"servidor"`) | agregá un término específico; `rag` compara contra todo el vault |
| `reindex` sale con código 1 y "N sin indexar" | notas ilegibles o fuera del índice | mirar la lista que imprime; revisar permisos de esos archivos |
| `obsn rag` tarda ~2 s cada vez | la consulta no está cacheada: carga el modelo (~1,3 s) | normal; repetir la misma consulta baja a ~180 ms |
| `rag` devuelve vacío en 0 ms y `status` muestra un vault raro | quedaron `OBS_RAG_VAULT`/`OBS_RAG_INDEX` exportados de una prueba | `unset` de esas variables, o usá `env VAR=... obsn ...` por comando |
| `error: fuera del vault` (exit 2) | path con `..`, absoluto o symlink que apunta afuera | usar siempre rutas relativas al vault |

## Seguridad

- Todo path se resuelve y se verifica **dentro del vault**: `../..`, rutas absolutas y
  symlinks que apunten afuera se rechazan.
- Las escrituras son **atómicas** (temporal + `rename`), así que un corte no deja la nota a
  medias.
- `delete` manda a `.trash/` con fecha; solo borra de verdad con `--hard`.
- **El índice ya no guarda el texto**: guarda términos, un rango de líneas por chunk y el
  vector. Igual tratalo como dato sensible, porque el vocabulario completo y los vectores
  están ahí. Si el índice se comparte o se respalda, no viaja el vault entero pero viaja de
  qué habla.

## Cómo medir un cambio

Cualquier cambio de chunker, ranking o modelo se juzga con el banco, no a ojo:

```bash
python bench/bench_search.py --verbose     # top-1 / top-5 / vacíos / latencia
python -m unittest discover -s tests -t .  # tests (vault temporal, no toca el real)
```

El banco tiene tres grupos: `POS` (paráfrasis con nota esperada), `NEG` (consultas fuera de
dominio, lo correcto es **vacío**) y `EXACT` (término exacto, tiene que salir top-1). Si un
cambio sube la latencia pero baja los aciertos, no entra.
