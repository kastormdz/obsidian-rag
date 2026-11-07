"""Background reindex: the CLI launches it after writing and it exits on its own.

WHY IT EXISTS
─────────────
Writing a note and having the RAG not see it until the next timer is a hole: an agent
writes, searches in the same turn, and does not find what it just created. But reindexing
on the critical path costs ~1.5 s (loading the model to embed), and that is noticeable.

Solution: the CLI forks+execs this worker and moves on. Three problems of doing it naively
are solved here:

- **Coalescing**: if you write five notes in a row, you do not want five reindexes
  fighting over the SQLite lock. The worker takes a non-blocking `flock`: if another one
  is already working, it leaves without doing anything — the running one will list the
  vault AFTER the debounce, so it sees this write too.
- **Debounce**: it sleeps for a moment before listing, so the writes of the burst land
  (an `edit` followed by a `move`, for example).
- **Changes during the build**: the CLI leaves a `dirty` marker before spawning. It is
  cleared right before building, so a write that lands while the build runs recreates it
  and the worker does one more pass instead of losing that note until the next write.

Output: exit code 0 whenever the reindex ran or stepped aside for the lock.
The log lives in $XDG_STATE_HOME/obsidian-rag/reindex.log (or ~/.local/state/...).
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import time
from pathlib import Path

DEBOUNCE_POR_DEFECTO = 1.5      # segundos para que aterrice la ráfaga de escrituras
LOG_MAX_BYTES = 1_000_000       # se rota una vez, sin ceremonia
MAX_PASADAS = 3                 # una por ráfaga; más que esto es un vault que no para


def estado_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    destino = base / "obsidian-rag"
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def marca_sucia() -> Path:
    """Marker the CLI touches before spawning: "there is a write this build may not see"."""
    return estado_dir() / "dirty"


def marcar_sucio() -> None:
    """Never fatal: if the state dir is not writable the write still reached the vault."""
    try:
        marca_sucia().touch()
    except OSError:
        pass


def _rotar_si_grande(log: Path) -> None:
    try:
        if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
            log.replace(log.with_suffix(".log.1"))
    except OSError:
        pass


def main() -> int:
    debounce = float(os.environ.get("OBS_RAG_DEBOUNCE", DEBOUNCE_POR_DEFECTO))
    base = estado_dir()
    log_path = base / "reindex.log"
    _rotar_si_grande(log_path)
    marca = marca_sucia()

    # El lock se toma ANTES del debounce a propósito: así una ráfaga de escrituras
    # deja un solo worker esperando, y los demás se retiran en silencio.
    with open(base / "reindex.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0        # ya hay un worker en camino: él va a ver este cambio

        time.sleep(max(0.0, debounce))

        from . import index
        with open(log_path, "a", encoding="utf-8") as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                for pasada in range(1, MAX_PASADAS + 1):
                    # Se borra ANTES de listar: lo que llegue mientras corre el build
                    # vuelve a crear la marca y pide otra pasada.
                    try:
                        marca.unlink()
                    except OSError:
                        pass
                    sello = time.strftime("%Y-%m-%d %H:%M:%S")
                    extra = f" (pass {pasada})" if pasada > 1 else ""
                    print(f"[{sello}] reindex async{extra}", file=log, flush=True)
                    try:
                        index.build()
                    except SystemExit as salida:   # build() sale con 1 si falta indexar algo
                        print(f"  build finished with code {salida.code}", file=log, flush=True)
                        return int(salida.code or 0)
                    if not marca.exists():
                        print("  ok", file=log, flush=True)
                        return 0
                    print("  a write landed during the build: running once more",
                          file=log, flush=True)
                print("  gave up after several passes: the next write or the timer catches up",
                      file=log, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
