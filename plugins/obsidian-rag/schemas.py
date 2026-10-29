"""Tool schemas — this is what the model reads. The description is the whole contract."""

from __future__ import annotations

_PATH_HELP = "Ruta relativa a la raíz del vault, con o sin .md (ej. '01-Technical/docker.md')."

SEARCH = {
    "name": "vault_search",
    "description": (
        "Busca en el vault de Obsidian del usuario. Arranca siempre por acá antes de "
        "responder algo que pueda estar en sus notas. `mode: hybrid` (default) entiende "
        "paráfrasis y devuelve pasajes con su heading; `mode: exact` es substring literal, "
        "para cuando sabés la palabra exacta y querés velocidad. Devuelve paths: leé el que "
        "interese con vault_read."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Qué buscar, en lenguaje natural o el término literal según el modo."},
            "limit": {"type": "integer", "description": "Máximo de resultados (default 8, tope 25)."},
            "mode": {"type": "string", "enum": ["hybrid", "exact"], "description": "hybrid = semántica + léxica; exact = substring."},
            "folder": {"type": "string", "description": "Sólo en modo exact: limitar a una carpeta (ej. '03-Personal')."},
            "tag": {"type": "string", "description": "Sólo en modo exact: limitar a un tag (sin #)."},
        },
        "required": ["query"],
    },
}

READ = {
    "name": "vault_read",
    "description": ("Lee una nota entera del vault. Usalo después de vault_search para ver el contexto completo antes de citar o editar."),
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": _PATH_HELP}},
        "required": ["path"],
    },
}

WRITE = {
    "name": "vault_write",
    "description": (
        "Crea o modifica una nota. `mode: create` (default) escribe el contenido completo "
        "y pisa si existe; `append` y `prepend` agregan; `replace` sustituye `old` por el "
        "contenido nuevo. Antes de crear una nota nueva, buscá la canónica con vault_search: "
        "si ya existe una del mismo tema, va como sección adentro, no como nota paralela. "
        "El índice se actualiza solo en segundo plano."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _PATH_HELP},
            "content": {"type": "string", "description": "Markdown a escribir."},
            "mode": {"type": "string", "enum": ["create", "append", "prepend", "replace"], "description": "create pisa el archivo; append/prepend agregan; replace necesita `old`."},
            "old": {"type": "string", "description": "Sólo para mode=replace: el texto exacto a reemplazar."},
        },
        "required": ["path", "content"],
    },
}

LIST = {
    "name": "vault_list",
    "description": (
        "Explora la estructura del vault sin buscar contenido. `mode: notes` lista rutas "
        "(con folder y/o tag opcionales), `mode: folders` las carpetas con su conteo, "
        "`mode: tags` los tags ordenados por uso. Sirve para saber dónde archivar algo."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["notes", "folders", "tags"], "description": "Qué listar (default notes)."},
            "folder": {"type": "string", "description": "Sólo mode=notes: prefijo de carpeta."},
            "tag": {"type": "string", "description": "Sólo mode=notes: tag exacto, sin #."},
            "limit": {"type": "integer", "description": "Máximo de filas (default 40)."},
        },
    },
}

MOVE = {
    "name": "vault_move",
    "description": "Mueve o renombra una nota dentro del vault. No pisa el destino: si existe, falla.",
    "parameters": {
        "type": "object",
        "properties": {
            "from": {"type": "string", "description": _PATH_HELP},
            "to": {"type": "string", "description": "Ruta nueva, relativa a la raíz del vault."},
        },
        "required": ["from", "to"],
    },
}

DELETE = {
    "name": "vault_delete",
    "description": (
        "Borra una nota. Por defecto va a la papelera del vault (.trash), así que se puede "
        "recuperar; `permanent: true` la borra de verdad y no se deshace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _PATH_HELP},
            "permanent": {"type": "boolean", "description": "Borrado real, sin papelera. Default false."},
        },
        "required": ["path"],
    },
}

STATUS = {
    "name": "vault_status",
    "description": (
        "Estado del índice: vault activo, cantidad de notas y chunks, modelo de embeddings, "
        "notas sin contenido indexable y cuándo se indexó (antiguedad_min). Usalo cuando una "
        "búsqueda no devuelve nada que debería existir, para descartar un índice viejo o "
        "apuntado a otro vault."
    ),
    "parameters": {"type": "object", "properties": {}},
}

SCHEMAS = {s["name"]: s for s in (SEARCH, READ, WRITE, LIST, MOVE, DELETE, STATUS)}
