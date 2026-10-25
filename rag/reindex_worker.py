"""Reindexado en segundo plano: lo lanza el CLI después de escribir y se va solo.

POR QUÉ EXISTE
──────────────
Escribir una nota y que el RAG no la vea hasta el próximo timer es un agujero: un
agente escribe, busca en el mismo turno y no encuentra lo que acaba de crear. Pero
reindexar en el camino crítico cuesta ~1,5 s (cargar el modelo para embeber), y eso
sí se nota.

Solución: el CLI hace fork+exec de este worker y sigue. Acá se resuelven los dos
problemas de hacerlo ingenuamente:

- **Coalescencia**: si escribís cinco notas seguidas, no querés cinco reindexados
  peleándose por el lock de SQLite. El worker toma un `flock` no bloqueante: si ya
  hay otro trabajando, se retira sin hacer nada — el que está corriendo va a listar
  el vault DESPUÉS del debounce, así que ve también esta escritura.
- **Debounce**: duerme un instante antes de listar, para que aterricen las
  escrituras de la ráfaga (un `edit` seguido de un `move`, por ejemplo).

Salida: código 0 siempre que el reindex haya corrido o se haya retirado por lock.
El log vive en $XDG_STATE_HOME/obsidian-rag/reindex.log (o ~/.local/state/...).
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import time
from pathlib import Path

DEBOUNCE_POR_DEFECTO = 1.5      # segundos para que aterrice la ráfaga de escrituras
LOG_MAX_BYTES = 1_000_000       # se rota una vez, sin ceremonia


def estado_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    destino = base / "obsidian-rag"
    destino.mkdir(parents=True, exist_ok=True)
    return destino


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
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] reindex async", file=log, flush=True)
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                try:
                    index.build()
                except SystemExit as salida:      # build() sale con 1 si falta indexar algo
                    print(f"  build terminó con código {salida.code}", file=log, flush=True)
                    return int(salida.code or 0)
            print("  ok", file=log, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
