# Changelog

Lo que cambió, por versión. El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el versionado es [SemVer](https://semver.org/lang/es/).

Mientras estemos en 0.x, un cambio de esquema del índice o del ranking sube la minor, porque
puede obligar a reconstruir el índice. Un fix que no rompe nada sube la patch.

## [Unreleased]

### Added

- Banner de cabecera en `docs/banner.webp` (1600×533, 38 KB en WebP), arriba del título en los
  dos READMEs, con un borde tenue que lo separa del fondo oscuro de GitHub.

### Changed

- **`key_terms()` eliminada**: quedó muerta cuando el ranking empezó a sacar los términos con
  contenido del propio índice (ARG-08). Sin cambio de comportamiento: `search()` devuelve lo mismo
  y la extracción no cuesta un pase extra.

### Fixed

- **Fixtures de los tests**: los ejemplos usan `alpha`/`beta`/`gamma` sobre `192.0.2.0/24`, el
  rango reservado para documentación (RFC 5737), y notas de contenido genérico. Dos consultas del
  banco se reformularon; los números de referencia quedaron en top-1 4/10 y top-5 5/10.
- **Filtros del plugin en híbrido**: `vault_search` ignoraba `folder` y `tag` en silencio, así
  que el modelo creía haber filtrado. `folder` ahora filtra de verdad (prefijo de path, con
  sobre-muestreo porque el filtro va después de deduplicar) y `tag` se rechaza con un mensaje
  accionable: el índice no guarda tags.
- **Conexiones sqlite**: `build`, `search`, `_main` y `cmd_status` las dejaban abiertas
  (`with sqlite3.connect(...)` commitea, no cierra). Ahora hay un `_conexion()` que cierra en
  `finally`; la suite corre con `-W error::ResourceWarning` sin fallar.

## [0.4.0] - 2026-10-28

### Fixed

- **Chunker (ARG-01)**: un encabezado seguido de línea en blanco se fusionaba con la sección
  ANTERIOR, así que la sección nueva perdía su `heading` y sus términos no entraban como
  `in_meta`. En el vault real eran **366 secciones en 104 de 203 notas**; ahora quedan 13 en
  una sola nota (un índice con encabezados dentro de listas). Sube `CHUNKER_VERSION` a 3, así
  que el próximo `reindex` re-trocea solo.
- **`edit replace` (ARG-06)**: `old` va como parámetro y no serializado con `\n`. Un bloque
  viejo multilínea se partía en el primer salto y corrompía la nota. `replace-old-new` se
  eliminó y falla con un mensaje que dice qué usar.
- **`.md` en todas las operaciones (ARG-11)**: `move_note("a.md","b")` dejaba un archivo sin
  extensión, invisible para el vault y el índice. Ahora read/edit/delete/move normalizan igual
  que write, y un `alias.md` interno se lista con su propio nombre.
- **Confinamiento (ARG-02)**: `_recorrer()` no filtraba symlinks ni ocultos, y un solo
  `nota.md -> ~/.ssh/id_x` hacía que `list`, `search` y `meta` murieran con `ValueError` (o
  leyeran afuera). Usa el mismo confinamiento que `notas_del_vault()`.
- **Metadatos (ARG-12)**: `tags: infra` se iteraba por carácter (`a, f, i, n, r`) y los
  backlinks se matcheaban por prefijo (`[[uno-dos]]` contaba para `uno.md`).
- **Wikilinks (ARG-05)**: `.rstrip(".md")` mutilaba los nombres terminados en `m`/`d`
  (`systemd -> syste`, `ovaultd -> ovault`), marcando links válidos como rotos. `removesuffix`.
- **Grafo y links (ARG-04/PERF-04)**: `vecinos()` borraba las DOS direcciones de cada par
  recíproco (los pares más parecidos no se proponían) y la canonicidad contaba salientes como
  entrantes. El grafo resuelve wikilinks por `by_name`, que ya construía y no usaba.
- **Índice (ARG-03)**: `emb` declara `modelo`; los logs de migración van a stderr (rompían el
  `--json` en un índice nuevo) y `search` no crea una DB vacía cuando no hay índice.
- **Plugin (ARG-10/ARG-14)**: `vault_search` pide `limit*3` chunks antes de deduplicar por nota
  (pedía 5 y devolvía 1), y los posicionales van después de `--`, así una búsqueda de
  `--force` no se interpreta como flag.
- **`bench/compare_models.py` (ARG-07)**: pedía la columna `text` eliminada; reconstruye por
  offsets. **`link` (ARG-15)**: no explota con `np.vstack` si no hay vectores.

### Changed

- **Cobertura por nota (ARG-08)**: era `Σ idf(términos de la nota) / Σ idf(términos clave)`, así
  que las stopwords la empujaban por encima de 1.0 y daban ganadoras falsas (y un `de` en el
  path daba 1.35x). Ahora es la **fracción de términos clave presentes**, acotada en [0,1]. Con
  eso el banco da top-1 3/10 y top-5 4/10: se pierde un top-5 que antes entraba por cobertura
  inflada, a cambio de cero falsos positivos y un score que significa algo.
- **Performance**: `conn()` ya no re-verifica el esquema en cada consulta (11,7x → 1,5x sobre
  un connect pelado), `build()` lee el estado previo de una vez en lugar de un SELECT por nota,
  `_con_texto` cachea las líneas por nota, `embed_all` arma el mapa hash→texto (era O(N²)),
  `listar_notas` resuelve la raíz una vez, y `postings` se recorre en una sola pasada. El banco
  bajó la mediana de 31 a 22 ms y el p95 de 144 a 36 ms.

## [0.3.0] - 2026-10-28

### Added

- **`obsn status` dice cuándo se indexó**: `indexado` (ISO con offset) y `antiguedad_min`. El
  dato ya estaba en `meta`, pero no se exponía; sin él un agente no puede distinguir un índice
  fresco de uno de hace tres días. Lo consume `vault_status`.
- **CI en GitHub Actions**: unitarios en Python 3.11 y 3.12, más el E2E del plugin (vault
  temporal, 30 chequeos) en 3.12 con el modelo cacheado.
- **Plugin de Hermes** en `plugins/obsidian-rag/`: siete tools (`vault_search`, `vault_read`,
  `vault_write`, `vault_list`, `vault_move`, `vault_delete`, `vault_status`) sobre el binario
  `obsn`, más una skill embebida. Sin dependencias de Python; el gateway no carga el modelo.
  `tests/e2e_plugin.py` lo ejercita contra un vault temporal.
- Sección "El modelo de embeddings" en los dos READMEs: cuál es, cuánto pesa, cuánto tarda,
  cuánto ocupa en el índice y las cinco razones por las que se eligió (incluido el modelo de
  1,1 GB que no entró por memoria).
- Sección "Configurar el vault" en los dos READMEs: la variable, cómo verificarla con
  `obsn status`, qué pasa al cambiar de vault y cómo se configura en systemd.

### Changed

- **`vault_delete` con `permanent: true` pide aprobación.** Es el único tool irreversible: el
  plugin registra un hook `pre_tool_call` que devuelve `{"action": "approve"}` con
  `rule_key: vault_delete_permanent`, así que el usuario ve el path antes del borrado sin
  vuelta. El borrado normal va a `.trash` y no pregunta.
- Los README ya no clavan el tamaño del vault: los conteos de notas y chunks envejecían y se
  contradecían con el índice real. Quedan las proporciones y el costo por chunk, que no caducan.
- `scripts/release.sh` calla los avisos de dependencias del venv anfitrión y sólo los
  muestra si `pip` falla.

### Fixed

- Un `OBS_RAG_VAULT` mal apuntado vaciaba el índice sin avisar: ahora `reindex` se niega
  cuando el vault no existe o queda sin notas mientras el índice tiene, y pide `--force`
  para vaciarlo a propósito.

## [0.2.0] - 2026-10-25

### Added

- `obsn upgrade` con `--check` (0 al día, 10 hay actualización, 1 error), `--how` y
  `--no-reindex`. Fast-forward only y se niega a tocar nada con el árbol sucio.
- Reindexado en segundo plano después de `write`, `edit`, `delete` y `move`: `fork+exec`,
  espera de 1,5 s para agrupar ráfagas, `flock` y log rotativo. El comando vuelve en ~60 ms.
- `obsn graph` con `--stats`, `--orphans`, `--clusters` y `--broken`.
- `obsn link --neighbors`, `--propose` y `--apply` para proponer wikilinks por similitud.
- `skills/obsidian-rag/SKILL.md`, una skill portable para que use el RAG un agente que no
  conoce la herramienta.
- `bench/bench_search.py` (grupos POS, NEG y EXACT, con `--json`), `bench/compare_models.py`
  y `bench/gate.sh` con umbrales numéricos.
- 66 tests contra vaults temporales.
- README en español y en inglés.

### Changed

- Chunking por bloque: una tabla entera o una lista entera es un chunk. El vault de
  referencia pasó de 21.592 micro-trozos a 7.550 chunks.
- Ranking híbrido `0.6·tf·idf + 0.4·coseno + 0.35·cobertura`, con cobertura por nota y boost
  cuando el término aparece en el path o el heading.
- Búsqueda en dos fases: el índice invertido resuelve antes de tocar vectores, así que el CLI
  no importa numpy cuando no hace falta.
- El índice ya no guarda el texto de las notas: `chunks` tiene `off_start`/`off_end` y el
  texto se relee del vault al mostrar los resultados.
- `df` se deriva de `postings` en vez de tokenizar el vault completo.
- Los nombres del estándar MCP (`read_note`, `write_note`, `search_notes`, etc.) funcionan
  como alias.

### Fixed

- `write` con path vacío resolvía a la raíz del vault y escribía fuera del vault.
- Dos notas homónimas borradas en el mismo segundo se pisaban en la papelera.
- `link --apply` importaba un cliente MCP que ya no existe y moría con `ModuleNotFoundError`.
- `graph --stats` y `link --neighbors` no aceptaban sus propios flags.
- `build()` no era transaccional por nota: un fallo podía dejar el `DELETE` confirmado por el
  commit de otra nota y perder los chunks.
- Las escrituras no hacían `fsync` antes del `rename`.
- `status` tiraba un traceback con un índice de formato viejo.
- `--limit` negativo se aceptaba en silencio.
- `_rel()` fallaba si el vault vivía bajo un symlink.

### Security

- Todo path se resuelve y se verifica dentro del vault: `../..`, rutas absolutas y symlinks
  que apunten afuera se rechazan.
- Las escrituras son atómicas y con `fsync`.
- `delete` manda la nota a `.trash/` con fecha y carpeta de origen.

## [0.1.0]

Primera versión que funcionó, nunca publicada. CRUD sobre el vault, búsqueda con embeddings,
chunking por línea, TF-IDF más coseno sin índice invertido y nombres compatibles con MCP.
