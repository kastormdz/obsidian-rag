"""Rutas y constantes del paquete. Todo configurable por entorno, sin rutas personales.

    OBS_RAG_VAULT   vault a indexar          (default: ~/Obsidian)
    OBS_RAG_INDEX   archivo SQLite del índice (default: $XDG_DATA_HOME/obsidian-rag/index.db)
    OBS_RAG_CACHE   caché del modelo         (default: $XDG_CACHE_HOME/fastembed)
"""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()


def _xdg(variable: str, default: str) -> Path:
    return Path(os.environ.get(variable) or (HOME / default))


VAULT = Path(os.path.expanduser(os.environ.get("OBS_RAG_VAULT") or str(HOME / "Obsidian"))).resolve()
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "obsidian-rag"
DB = Path(os.path.expanduser(os.environ["OBS_RAG_INDEX"]) if os.environ.get("OBS_RAG_INDEX") else DATA_DIR / "index.db")
EMBEDDINGS_CACHE = Path(
    os.path.expanduser(os.environ["OBS_RAG_CACHE"])
    if os.environ.get("OBS_RAG_CACHE")
    else _xdg("XDG_CACHE_HOME", ".cache") / "fastembed"
)

MODELO = os.environ.get("OBS_RAG_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

# Cuánto pierde una nota vencida frente a una vigente en el ranking. 1.0 = no penaliza;
# 0.0 = la esconde (peor: devuelve "no encontrado"). Se sube si el banco muestra que
# esconde notas buenas.
STALE_PENALTY = max(0.0, min(1.0, float(os.environ.get("OBS_RAG_STALE_PENALTY", "0.75"))))
