# Changelog

Lo que cambió, por versión. El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el versionado es [SemVer](https://semver.org/lang/es/).

Mientras estemos en 0.x, un cambio de esquema del índice o del ranking sube la minor, porque
puede obligar a reconstruir el índice. Un fix que no rompe nada sube la patch.

## [Unreleased]

### Added

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
