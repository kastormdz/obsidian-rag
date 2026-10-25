# obsidian-rag

Busca y edita un vault de [Obsidian](https://obsidian.md) desde la terminal, con un solo
binario: `obsn`. Lee las notas del disco y arma un índice SQLite con embeddings. No hace
falta servidor ni cuenta: el índice es un archivo. El vault sigue siendo markdown en tu
disco.

[English version](README.en.md)

## Instalación

```bash
git clone https://github.com/kastormdz/obsidian-rag.git
cd obsidian-rag
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

La primera búsqueda baja el modelo (241 MB) a `~/.cache/fastembed`. Cargarlo cuesta 1,5 s y
sólo pasa cuando la consulta no se resuelve por léxico.

## Comandos

Búsqueda:

```bash
obsn rag "certificados wildcard"    # híbrida: léxica + semántica
obsn rag "backup" 10 --json         # cantidad de resultados y salida JSON
obsn search certificados            # substring sobre el contenido, sin modelo
obsn status                         # notas, chunks, vectores, modelo
obsn reindex                        # incremental: sólo lo que cambió
obsn reindex --force                # todo de nuevo
```

Archivos:

```bash
obsn read  "02-docs/nota.md"
obsn write "02-docs/nueva.md" --file ~/tmp/nota.md
obsn edit  "02-docs/nota.md" append --file ~/tmp/cola.md
obsn delete "02-docs/vieja.md"      # a .trash; --hard borra de verdad
obsn move  "02-docs/a.md" "02-docs/b.md"
obsn list  "02-docs" --limit 20
obsn folders
obsn tags
obsn meta  "02-docs/nota.md"        # frontmatter, tags, backlinks
```

Grafo:

```bash
obsn graph --stats                  # resumen en una línea
obsn graph --orphans                # notas sin links entrantes
obsn graph --clusters               # islas y componentes
obsn graph --broken                 # wikilinks que no resuelven
obsn link --neighbors docker        # notas parecidas a un tema
obsn link --propose                 # propone links nuevos, no escribe
obsn link --apply                   # los escribe en las notas
```

`link --apply` modifica tus notas. Corré `--propose` primero.

Los nombres del estándar MCP (`read_note`, `write_note`, `search_notes`, `list_tags` y el
resto) funcionan como alias, así un agente no tiene que traducir nada.

## Cómo busca

`rag` mezcla dos señales en un solo score:

```
0.6 · tf·idf  +  0.4 · coseno  +  0.35 · cobertura
```

La cobertura se calcula por nota, no por chunk. Si tu consulta nombra cosas que están en
secciones distintas de la misma nota, esa nota sube entera. También hay un empujón cuando el
término aparece en el path o en el heading, porque un chunk que sólo dice "Primer deploy" no
dice nada por sí solo.

Antes de tocar un vector, la búsqueda intenta resolver con el índice invertido. Casi todas
las consultas reales nombran algo que está escrito en alguna nota, y en ese caso no hay que
traer 11 MB de vectores ni importar numpy: 70 ms de punta a punta contra 1,5 s si hay que
cargar el modelo.

Cuando no hay ninguna palabra de la consulta en el fragmento se exige coseno ≥ 0.62, y si no
llega devuelve vacío. Una lista vacía es mejor que un resultado inventado.

`search` es otra cosa: substring sobre el texto, sin modelo. Sirve cuando sabés la palabra
exacta y querés velocidad.

## El índice

Se arma por hash del contenido. Un `touch` no lo engaña y una edición del mismo largo
tampoco. `reindex` sin `--force` sólo toca lo que cambió, y tarda 0,1 s si no cambió nada.

Trocea por bloque: una tabla entera o una lista entera es un chunk. Antes era una fila por
chunk, y con 21.592 micro-trozos de 8 tokens el ranking premiaba filas de tabla en lugar de
las secciones que explican el tema. El mismo vault quedó en 7.550 chunks.

No guarda el texto de las notas. Guarda los términos, un rango de líneas por chunk y el
vector. Cuando mostrás un resultado, relee esas líneas del vault. Son 41 MB y no son una
copia legible del vault.

Los vectores se cachean por hash del texto y del modelo. Por eso `reindex --force` con la
caché caliente tarda 7 s en vez de 198.

## Reindexado en segundo plano

`write`, `edit`, `delete` y `move` disparan un reindex aparte: `fork+exec`, 1,5 s de espera
para agrupar ráfagas y un `flock` para que dos corridas no se pisen. El comando te devuelve
el control en ~60 ms. Para lo que cambia otro proceso (tu editor, un sync) conviene un
`obsn reindex` periódico: un timer de systemd cada 2 minutos cuesta 0,2% de CPU.

Se apaga con `OBS_RAG_NO_ASYNC=1`.

## Configuración

| Variable | Default | Qué hace |
|---|---|---|
| `OBS_RAG_VAULT` | `~/Obsidian` | vault a leer e indexar |
| `OBS_RAG_INDEX` | `$XDG_DATA_HOME/obsidian-rag/index.db` | archivo del índice |
| `OBS_RAG_CACHE` | `$XDG_CACHE_HOME/fastembed` | caché del modelo |
| `OBS_RAG_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | modelo de embeddings |
| `OBS_RAG_NO_ASYNC` | sin definir | `1` apaga el reindex en segundo plano |

## Actualizar

```bash
obsn upgrade --check   # 0 al día · 10 hay actualización · 1 error
obsn upgrade           # fetch + fast-forward, reinstala si cambió pyproject.toml, reindexa
obsn upgrade --how     # qué hace, paso a paso
```

Se niega a seguir si el árbol está sucio y nunca crea un merge commit. Si la instalación no
es editable (no hay `.git` al lado del paquete), te manda a `pip install -U`.

## Para agentes

`skills/obsidian-rag/SKILL.md` es una skill con frontmatter YAML y protocolo: cuándo usar
`rag` y cuándo `search`, qué hacer si devuelve vacío, cómo citar la nota y las trampas de
argv. Apuntá al agente ahí en vez de explicarle la herramienta.

## Seguridad

- Todo path se resuelve y se verifica dentro del vault. `../..`, rutas absolutas y symlinks
  que apunten afuera se rechazan.
- Las escrituras son atómicas: archivo temporal, `fsync` y `rename`.
- `delete` manda a `.trash/` con fecha y carpeta de origen. Sólo borra de verdad con
  `--hard`.
- El índice no guarda el texto, pero sí el vocabulario completo y los vectores. Tratalo como
  dato sensible, con el mismo cuidado que el vault.

## Tests

```bash
python -m unittest discover -s tests -t .
```

62 tests contra vaults temporales. No tocan ningún vault real.

Para medir recuperación y latencia antes de tocar el ranking, el chunker o el modelo:

```bash
python bench/bench_search.py --verbose
python bench/compare_models.py
```

Números de referencia con 196 notas y 7.550 chunks: top-1 en paráfrasis 3/10, top-5 5/10,
cero falsos positivos en 8 consultas fuera de dominio, y 5/5 cuando el término es exacto. La
recuperación puramente semántica es lo más flojo del conjunto: un modelo de 384 dimensiones
y 12 capas en CPU no resuelve bien una paráfrasis sin ninguna palabra en común. Si eso te
importa más que la velocidad, `bench/compare_models.py` compara alternativas sobre el índice
ya armado.

## Licencia

MIT.
