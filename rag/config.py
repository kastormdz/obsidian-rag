"""Package paths and constants. Everything configurable through the environment, no personal paths.

    OBS_RAG_VAULT   vault to index            (default: ~/Obsidian)
    OBS_RAG_INDEX   index SQLite file         (default: $XDG_DATA_HOME/obsidian-rag/index.db)
    OBS_RAG_CACHE   model cache               (default: $XDG_CACHE_HOME/fastembed)
    OBS_RAG_MODEL   embedding model           (default: see `_MODELO_POR_DEFECTO`)
    OBS_RAG_STALE_PENALTY  stale score factor (default: 0.75, clamped to [0, 1])

Every path is resolved to an absolute one, so a relative `OBS_RAG_INDEX` means "relative to
the working directory where obsn was launched" instead of depending on a later `chdir`. A
bad numeric value falls back to the default with a warning, not a traceback at import time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HOME = Path.home()

_MODELO_POR_DEFECTO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def _xdg(variable: str, default: str) -> Path:
    return Path(os.environ.get(variable) or (HOME / default))


def _ruta(variable: str, default: Path) -> Path:
    """Environment path, expanded and absolute. Empty = default."""
    crudo = (os.environ.get(variable) or "").strip()
    if not crudo:
        return default.resolve()
    return Path(os.path.expanduser(crudo)).resolve()


def _numero(variable: str, default: float, bajo: float, alto: float) -> float:
    """Float from the environment, clamped to [bajo, alto]. A bad value is a warning, not a crash.

    `obsn --version` should not die because someone left `OBS_RAG_STALE_PENALTY=0,75` in
    their shell: the default is a valid answer and the warning names the variable.
    """
    crudo = (os.environ.get(variable) or "").strip()
    if not crudo:
        return default
    try:
        valor = float(crudo)
    except ValueError:
        print(f"[config] {variable}={crudo!r} is not a number: using {default}",
              file=sys.stderr, flush=True)
        return default
    acotado = max(bajo, min(alto, valor))
    if acotado != valor:
        print(f"[config] {variable}={valor} is outside [{bajo}, {alto}]: using {acotado}",
              file=sys.stderr, flush=True)
    return acotado


VAULT = _ruta("OBS_RAG_VAULT", HOME / "Obsidian")
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "obsidian-rag"
DB = _ruta("OBS_RAG_INDEX", DATA_DIR / "index.db")
EMBEDDINGS_CACHE = _ruta("OBS_RAG_CACHE", _xdg("XDG_CACHE_HOME", ".cache") / "fastembed")

MODELO = (os.environ.get("OBS_RAG_MODEL") or "").strip() or _MODELO_POR_DEFECTO

# Cuánto pierde una nota vencida frente a una vigente en el ranking. 1.0 = no penaliza;
# 0.0 = la esconde (peor: devuelve "no encontrado"). Se sube si el banco muestra que
# esconde notas buenas.
STALE_PENALTY = _numero("OBS_RAG_STALE_PENALTY", 0.75, 0.0, 1.0)
