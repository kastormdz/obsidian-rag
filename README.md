# obsidian-rag

Búsqueda semántica y operaciones de archivo sobre un vault de [Obsidian](https://obsidian.md),
desde la terminal o desde un agente. **Un solo comando: `obsn`.**

No necesita servidor, cuenta ni base de datos: lee el vault del disco y construye un índice
SQLite local con embeddings. El vault sigue siendo tuyo y en texto plano.

## Instalación

```bash
git clone https://github.com/kastormdz/obsidian-rag.git
cd obsidian-rag
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

La primera vez que busques, descarga el modelo de embeddings (~90 MB).

## Uso

```bash
obsn rag "certificados wildcard"        # búsqueda semántica
obsn search certificados                # búsqueda léxica por contenido
obsn reindex                            # construir/actualizar el índice
obsn status                             # qué hay indexado

obsn read  "02-docs/nota.md"            # leer
obsn write "02-docs/nueva.md" --file ~/tmp/nota.md
obsn edit  "02-docs/nota.md" append --file ~/tmp/cola.md
obsn delete "02-docs/vieja.md"          # a .trash (--hard para borrar de verdad)
obsn move  "02-docs/a.md" "02-docs/b.md"
obsn list  "02-docs" --limit 20
obsn folders
obsn tags
obsn meta  "02-docs/nota.md"            # frontmatter, tags, backlinks
```

Los nombres largos del estándar MCP también funcionan: `read_note`, `write_note`,
`edit_note`, `delete_note`, `move_note`, `list_notes`, `list_folders`, `list_tags`,
`search_notes`, `get_note_metadata`.

## Configuración

| Variable | Default | Qué hace |
|---|---|---|
| `OBS_RAG_VAULT` | `~/Obsidian` | vault a leer e indexar |
| `OBS_RAG_INDEX` | `$XDG_DATA_HOME/obsidian-rag/index.db` | archivo del índice |
| `OBS_RAG_CACHE` | `$XDG_CACHE_HOME/fastembed` | caché del modelo |
| `OBS_RAG_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | modelo de embeddings |

## Cómo busca

Búsqueda **híbrida**: índice invertido (léxico, con peso IDF) + embeddings (semántico), en un
solo score.

- **`rag`** combina las dos señales: `0.6 · tf·idf + 0.4 · coseno + 0.35 · cobertura`, con un
  boost si el término aparece en el path o el heading. Si no hay ninguna palabra de la consulta
  en el fragmento, exige coseno ≥ 0.62 y **devuelve vacío** antes que devolver ruido.
- **`search`** es substring sobre el contenido, sin modelo: rápido y exacto, para cuando sabés
  la palabra.
- El chunking respeta la estructura: un bloque de tabla o de lista es **un** chunk, no una fila
  por chunk.
- El índice se arma por **hash del contenido**: `reindex` sin `--force` solo reindexa lo que
  cambió. Un `touch` no lo engaña; una edición del mismo largo tampoco.

**Limitación medida:** con el modelo por defecto la recuperación puramente semántica es débil
—10 paráfrasis sin palabras en común con la nota: acierta el top-1 en 1–2 de 10—. Rinde cuando
la consulta comparte al menos un término concreto. `bench/bench_search.py` mide eso y
`bench/compare_models.py` permite evaluar otro modelo antes de adoptarlo.

## Para agentes

[`skills/obsidian-rag/SKILL.md`](skills/obsidian-rag/SKILL.md) es una skill lista para cargar
(frontmatter YAML + protocolo): cuándo usar `rag` y cuándo `search`, qué hacer si devuelve
vacío, cómo citar la nota, y las trampas de argv. Apuntá al agente ahí en vez de explicarle la
herramienta.

## Seguridad

- Todo path se resuelve y se verifica **dentro del vault**: `../..`, rutas absolutas y symlinks
  que apunten afuera se rechazan.
- Las escrituras son **atómicas** (temporal + `rename`).
- `delete` manda a `.trash/` con fecha; solo borra de verdad con `--hard`.
- El índice contiene el vault **en claro**. Tratalo como dato sensible: mismo cuidado que el
  vault mismo.

## Tests

```bash
python -m unittest discover -s tests -t .
```

Corren contra un vault temporal; no tocan ningún vault real.

Para medir recuperación y latencia (lo que hay que correr antes de cambiar el ranking, el
chunker o el modelo):

```bash
python bench/bench_search.py --verbose
```

## Licencia

MIT.
