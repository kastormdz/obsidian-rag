# Plugin de Hermes para obsidian-rag

Siete tools para que un agente de Hermes busque, lea y escriba en el vault, con `obsn`
como motor. Sin dependencias de Python: el plugin es un adaptador sobre el binario.

```
plugins/obsidian-rag/
├── plugin.yaml      # manifiesto (tools, config, versión)
├── __init__.py      # register(ctx)
├── schemas.py       # lo que ve el modelo
├── tools.py         # handlers
├── client.py        # subprocess → obsn
└── skills/vault/SKILL.md   # cuándo usar cada tool (se carga como obsidian-rag:vault)
```

## Los tools

| Tool | Qué hace | Motor |
|---|---|---|
| `vault_search` | Búsqueda híbrida (`hybrid`) o substring (`exact`), con folder/tag | `obsn rag` · `obsn search` |
| `vault_read` | Lee una nota entera | `obsn read` |
| `vault_write` | Crea, agrega, prepende o reemplaza texto | `obsn write` · `obsn edit` |
| `vault_list` | Notas, carpetas con conteo o tags con uso | `obsn list` · `folders` · `tags` |
| `vault_move` | Mueve o renombra; no pisa el destino | `obsn move` |
| `vault_delete` | A la papelera, o permanente con `permanent: true` | `obsn delete` |
| `vault_status` | Vault activo, notas, chunks, modelo | `obsn status` |

## Instalar

```bash
# desde el repo (recomendado: queda pinneado y actualizable)
hermes plugins install kastormdz/obsidian-rag#plugins/obsidian-rag --no-enable
hermes plugins enable obsidian-rag
```

Para desarrollo, sin instalar:

```bash
ln -s "$PWD/plugins/obsidian-rag" ~/.hermes/plugins/obsidian-rag
hermes plugins doctor plugins/obsidian-rag --ci   # valida sin instalar
```

Los tools aparecen en la sesión siguiente (`/new`), no en la que está corriendo.

## Configurar

```yaml
plugins:
  entries:
    obsidian-rag:
      obsn: ""            # vacío = buscarlo en PATH
      vault: ""           # carpeta con .obsidian/; vacío = OBS_RAG_VAULT
      timeout: 30         # segundos por llamada
```

## Por qué un subprocess y no importar `rag`

Medido en este equipo:

| | |
|---|---|
| `obsn rag` completo | **73 ms** |
| de eso, arranque del intérprete | 24 ms |
| `import rag.index` en el proceso del gateway | 33 ms **+ numpy/onnxruntime residentes** |

Ganar 24 ms no paga meter numpy y un modelo ONNX de 241 MB dentro del proceso del agente,
ni acoplar la versión del plugin al esquema del índice. Además, un fallo en la búsqueda no
se lleva puesto al agente, y `obsn` sigue siendo la única API: lo que se agregue al CLI
funciona acá sin tocar el plugin.

## Pitfalls que costaron sangre

- **`obsn edit <path> replace "" <viejo> --file <tmp>`**: el cuerpo va de `--file`, pero
  `content` tiene que existir como positional (aunque sea vacío) porque `old` es el
  positional siguiente. Con `--file` primero, argparse se come el viejo como contenido y el
  reemplazo no ocurre. Está en `tools.py::write` con el comentario.
- **`OBS_RAG_VAULT` exportado en la shell** apunta todos los tools a otro vault, incluso si
  el que querés es el default. Lo mismo que ya documenta `bench/gate.sh`.
- **En un vault de 3 notas** una paráfrasis sin ninguna palabra en común queda debajo del
  piso semántico y devuelve vacío. Es del motor, no del plugin: en el vault real la misma
  consulta devuelve 5. Si escribís tests, usá una consulta con un término que exista.
- **`vault_status` justo después de escribir** puede mostrar el estado anterior: el reindex
  es asíncrono (~1,5 s de debounce). Esperá unos segundos antes de afirmar que no indexó.

## Verificar

```bash
hermes plugins doctor plugins/obsidian-rag --ci   # manifiesto + registro + drift
python3 tests/e2e_plugin.py                       # 24 chequeos contra un vault temporal
```
