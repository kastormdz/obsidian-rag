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

## Configurar el vault

Por defecto busca el vault en `~/Obsidian`. Si el tuyo está ahí, no hay nada que hacer.

Si está en otro lado, apuntá `OBS_RAG_VAULT` a la carpeta que contiene `.obsidian/`, es
decir la raíz del vault. No es una subcarpeta y no es el nombre del vault: es el directorio.

```bash
export OBS_RAG_VAULT="$HOME/Documentos/mi-vault"                     # esta sesión
echo 'export OBS_RAG_VAULT="$HOME/Documentos/mi-vault"' >> ~/.bashrc # permanente
```

Para comprobar que quedó bien, `obsn status` imprime el vault y el índice que está usando:

```
$ obsn status
notas=199 chunks=7651 con_embedding=7651 sin_chunks=3
vault=/home/vos/Documentos/mi-vault
indice=/home/vos/.local/share/obsidian-rag/index.db
```

Dos cosas que se olvidan y cuestan una tarde:

- **El índice pertenece a un vault.** Los paths del índice son relativos a la raíz del vault.
  Si cambiás `OBS_RAG_VAULT`, el índice viejo queda apuntando a notas que ya no existen: corré
  `obsn reindex`, que purga lo que sobra e indexa lo nuevo. Va a tardar lo que tarda embeber
  un vault entero, porque para el índice son todas notas nuevas.
- **Un vault, un índice.** Para dos vaults, dale un archivo a cada uno:
  `OBS_RAG_VAULT=~/otro OBS_RAG_INDEX=~/.local/share/obsidian-rag/otro.db obsn reindex`.
- **Si el path está mal, no te vacía el índice.** `reindex` se niega cuando el vault no
  existe, o cuando no tiene ninguna nota y el índice sí: te avisa para que revises
  `OBS_RAG_VAULT` en vez de purgar 200 notas por un typo. Si querés vaciarlo a propósito,
  `obsn reindex --force`.

Si el reindex corre desde systemd, el servicio no hereda tu shell: la variable va en la
unidad.

```ini
[Service]
Environment=OBS_RAG_VAULT=/home/vos/Documentos/mi-vault
```

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

## El modelo de embeddings

`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. fastembed lo sirve como ONNX
cuantizado (`qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q`) y son 241 MB en
`~/.cache/fastembed`.

| | |
|---|---|
| Dimensiones | 384 (`float32`, 1.536 B por chunk) |
| Capas | 12 |
| Idiomas | multilingüe, entrenado para español e inglés entre otros |
| Motor | ONNX Runtime en CPU, sin torch |
| En disco | 241 MB (224 del grafo + 16 del tokenizer) |
| Cargarlo | ~1,5 s, y sólo cuando la consulta no se resuelve por léxico |
| Embeber | ~38 chunks/s: un vault de 7.600 chunks, ~3 minutos |
| Costo en el índice | 11,2 MB de vectores para 7.651 chunks |

### Por qué éste

1. **Entiende español.** El vault está en español con términos técnicos en inglés. Un modelo
   sólo-inglés pierde justo las paráfrasis, que es lo que un RAG tiene que resolver.
2. **Corre en CPU sin GPU.** ONNX cuantizado y 12 capas: carga en 1,5 s y embebe el vault
   entero en ~3 minutos. Un modelo más grande multiplica ese costo sin tocar el problema real.
3. **384 dimensiones alcanzan.** Cada vector son 1.536 B y los 7.651 chunks ocupan 11,2 MB. Con
   768 el índice duplica esa parte y la búsqueda tarda más, sin mejorar las consultas que
   importan.
4. **El techo está en el ranking, no en el modelo.** Con una paráfrasis que no comparte ninguna
   palabra, el top-1 acierta 3 de 10; cuando la consulta trae el término exacto, 4 de 5. Por eso
   la precisión la hace la cobertura IDF y el coseno es sólo una parte del score.
5. **Probamos uno más grande y no entró.** `paraphrase-multilingual-mpnet-base-v2` (1,1 GB, 768
   dims) murió por memoria (SIGKILL) antes de terminar el benchmark en esta máquina, así que
   quedó afuera.

Para probar otro modelo sin tocar el índice:

```bash
python bench/compare_models.py sentence-transformers/paraphrase-multilingual-mpnet-base-v2
```

Mide sobre los chunks ya indexados: top-1 y top-5 en las paráfrasis, y el coseno máximo de las
consultas fuera de dominio. Para adoptarlo, `OBS_RAG_MODEL=<modelo> obsn reindex --force`: el
índice guarda el modelo en `meta` y se rehace solo, y la caché de vectores incluye el modelo,
así que no reusa nada viejo.

## El índice

Se arma por hash del contenido. Un `touch` no lo engaña y una edición del mismo largo
tampoco. `reindex` sin `--force` sólo toca lo que cambió, y tarda 0,1 s si no cambió nada.

Trocea por bloque: una tabla entera o una lista entera es un chunk. Antes era una fila por
chunk, y con micro-trozos de una línea el ranking premiaba filas de tabla en lugar de las
secciones que explican el tema. El mismo vault bajó a un tercio de los chunks.

Guarda los términos, un rango de líneas por chunk y el vector. Cuando mostrás un resultado,
relee esas líneas del vault. Son ~1,5 KB por chunk, casi todo el vector de 384 floats: un
vault de 200 notas queda en ~45 MB, y no es una copia legible del vault.

Los vectores se cachean por hash del texto y del modelo. Por eso `reindex --force` con la
caché caliente tarda 7 s en vez de 198.

## Con ovaultd

Esto no reemplaza al sync. Si querés el vault en varias máquinas y que el servidor no pueda
leerlo, ese es otro proyecto: [ovaultd](https://github.com/kastormdz/ovaultd), un servidor de
sincronización self-hosted que guarda blobs cifrados y no conoce ni el contenido ni las rutas.

Se complementan y ninguno necesita al otro:

| | ovaultd | obsidian-rag |
|---|---|---|
| Qué resuelve | llevar el vault a todas tus máquinas sin que el servidor lo lea | encontrar y editar notas en la máquina donde estás |
| Dónde vive el dato | blobs cifrados en tu servidor | markdown en claro en tu disco |
| Qué ve el índice | nada, no lee blobs | el texto local |

Esa división es justamente lo que hace que funcione: ovaultd nunca tiene el texto, así que el
índice no puede alimentarse del servidor. Indexa el vault de la máquina donde corre `obsn`. Si
ovaultd escribe un cambio en disco, el próximo `reindex` lo levanta; si escribís con `obsn`, se
dispara solo en segundo plano.

Ninguno de los dos te va a pedir que instales el otro.

## Reindexado en segundo plano

`write`, `edit`, `delete` y `move` disparan un reindex aparte: `fork+exec`, 1,5 s de espera
para agrupar ráfagas y un `flock` para que dos corridas no se pisen. El comando te devuelve
el control en ~60 ms. Para lo que cambia otro proceso (tu editor, un sync) conviene un
`obsn reindex` periódico: un timer de systemd cada 2 minutos cuesta 0,2% de CPU.

Se apaga con `OBS_RAG_NO_ASYNC=1`.

## Configuración

| Variable | Default | Qué hace |
|---|---|---|
| `OBS_RAG_VAULT` | `~/Obsidian` | vault a leer e indexar (ver [Configurar el vault](#configurar-el-vault)) |
| `OBS_RAG_INDEX` | `$XDG_DATA_HOME/obsidian-rag/index.db` | archivo del índice |
| `OBS_RAG_CACHE` | `$XDG_CACHE_HOME/fastembed` | caché del modelo |
| `OBS_RAG_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | modelo de embeddings (ver [El modelo de embeddings](#el-modelo-de-embeddings)) |
| `OBS_RAG_NO_ASYNC` | sin definir | `1` apaga el reindex en segundo plano |

## Actualizar

```bash
obsn upgrade --check   # 0 al día · 10 hay actualización · 1 error
obsn upgrade           # fetch + fast-forward, reinstala si cambió pyproject.toml, reindexa
obsn upgrade --how     # qué hace, paso a paso
```

Se niega a seguir si el árbol está sucio y nunca crea un merge commit. Si la instalación no
es editable (no hay `.git` al lado del paquete), te manda a `pip install -U`.

## Versiones

El paquete sigue [SemVer](https://semver.org/lang/es/) y el historial está en
[CHANGELOG.md](CHANGELOG.md), con el formato de Keep a Changelog. Mientras estemos en 0.x, un
cambio de esquema del índice o del ranking sube la minor, porque obliga a reconstruir; un fix
sube la patch.

```bash
obsn --version          # 0.2.0
obsn upgrade --check    # 0 al día · 10 hay actualización · 1 error
```

`--check` mira los commits nuevos y el último tag publicado, así que un commit suelto no
cuenta como versión.

Para cortar una versión está `scripts/release.sh`:

```bash
scripts/release.sh --check      # valida main limpio, el gate y qué se publicaría
scripts/release.sh 0.3.0        # bump, changelog, tag, push y release en GitHub
```

Corre el gate completo antes de tocar nada, mueve lo que está en `[Unreleased]` a la versión
nueva con la fecha, crea el tag anotado y el release, y reinstala el paquete editable para que
`obsn --version` lo refleje. Si `[Unreleased]` está vacío, se niega: no hay nada que publicar.

## Plugin de Hermes

`plugins/obsidian-rag/` convierte el vault en siete tools que el agente llama solo:
`vault_search`, `vault_read`, `vault_write`, `vault_list`, `vault_move`, `vault_delete` y
`vault_status`. Trae además una skill embebida (`obsidian-rag:vault`) con el orden correcto:
buscar antes de responder, leer antes de citar, buscar la nota canónica antes de crear una
nueva.

```bash
hermes plugins install kastormdz/obsidian-rag#plugins/obsidian-rag --no-enable
hermes plugins enable obsidian-rag
```

El plugin **no importa el motor**: cada llamada ejecuta `obsn` en un subprocess (73 ms
medidos, 24 de ellos de arranque del intérprete). Así el gateway no carga numpy ni el modelo
de 241 MB, un fallo de búsqueda no se lleva puesto al agente, y el CLI sigue siendo la única
API. Configuración en `plugins.entries.obsidian-rag`: `obsn`, `vault` y `timeout`.

El único tool que pregunta antes de actuar es `vault_delete` con `permanent: true`: borra sin
papelera, así que el plugin lo frena con un hook y el usuario ve el path. El resto escribe de
forma atómica o manda a `.trash`.

## Para agentes

`skills/obsidian-rag/SKILL.md` es una skill con frontmatter YAML y protocolo: cuándo usar
`rag` y cuándo `search`, qué hacer si devuelve vacío, cómo citar la nota y las trampas de
argv. Apuntá al agente ahí en vez de explicarle la herramienta. Si además instalás el plugin
de Hermes, la skill viaja con él y no hace falta copiarla.

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

72 tests contra vaults temporales. No tocan ningún vault real.

El plugin de Hermes tiene su propio E2E, que lo carga como lo hace Hermes y ejercita los
siete tools contra un vault temporal:

```bash
python tests/e2e_plugin.py    # 24 chequeos
hermes plugins doctor plugins/obsidian-rag --ci
```

Para medir recuperación y latencia antes de tocar el ranking, el chunker o el modelo:

```bash
python bench/bench_search.py --verbose
python bench/compare_models.py
```

Números de referencia del banco: top-1 en paráfrasis 3/10, top-5 5/10, cero falsos positivos
en 8 consultas fuera de dominio, y 5/5 cuando el término es exacto. La
recuperación puramente semántica es lo más flojo del conjunto: un modelo de 384 dimensiones
y 12 capas en CPU no resuelve bien una paráfrasis sin ninguna palabra en común. Si eso te
importa más que la velocidad, `bench/compare_models.py` compara alternativas sobre el índice
ya armado.

## Licencia

MIT.
