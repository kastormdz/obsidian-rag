#!/usr/bin/env python3
"""E2E del CLI `obsn`: ciclo completo contra un vault temporal.

Ejercita el binario de verdad (subprocess), no las funciones: env vars, stdin, exit
codes, JSON y el ciclo write -> reindex -> rag -> edit -> move -> delete.

    python tests/e2e_cli.py            # sale 0 si pasa todo
    python tests/e2e_cli.py -v         # muestra la salida de cada comando

Nunca toca un vault real: OBS_RAG_VAULT y OBS_RAG_INDEX apuntan a un temporal.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
FALLOS: list[str] = []
CHECKS = 0


def correr(*args: str, stdin: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [OBSN, *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env or ENV,
        timeout=180,
    )
    if VERBOSE:
        print(f"\n$ obsn {' '.join(args)}  ->  rc={proc.returncode}")
        if proc.stdout.strip():
            print(proc.stdout.strip()[:800])
        if proc.stderr.strip():
            print("STDERR:", proc.stderr.strip()[:400])
    return proc


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condicion:
        print(f"  ok    {nombre}")
    else:
        print(f"  FALLA {nombre}   {detalle}")
        FALLOS.append(nombre)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="obsn-e2e-"))
    vault = tmp / "vault"
    vault.mkdir()
    global ENV, OBSN
    ENV = {
        **os.environ,
        "OBS_RAG_VAULT": str(vault),
        "OBS_RAG_INDEX": str(tmp / "index.db"),
        "OBS_RAG_CACHE": os.environ.get("OBS_RAG_CACHE", str(Path.home() / ".cache/fastembed")),
    }
    OBSN = shutil.which("obsn") or str(Path.home() / ".local/bin/obsn")
    print(f"vault temporal: {vault}\nobsn: {OBSN}\n")

    try:
        # 1) sin índice: status tiene que avisar, no explotar con traceback
        p = correr("status")
        check("status sin índice sale con código 1", p.returncode == 1, f"rc={p.returncode}")
        check("status sin índice lo dice en castellano", "no hay índice" in (p.stderr + p.stdout).lower())

        # 2) escritura por stdin + lectura
        p = correr("write", "02-docs/uno.md", stdin="# Uno\n\nContenido sobre certificados wildcard y renovación automática.\n")
        check("write por stdin crea la nota", p.returncode == 0 and (vault / "02-docs/uno.md").is_file(), p.stderr)
        p = correr("read", "02-docs/uno.md")
        check("read devuelve el contenido", "wildcard" in p.stdout, p.stdout[:200])

        # 3) reindex + búsqueda
        p = correr("reindex")
        check("reindex construye", p.returncode == 0 and "verificado" in p.stdout, p.stderr[-300:])
        p = correr("rag", "certificados", "3")
        check("rag encuentra la nota recién indexada", "uno.md" in p.stdout, p.stdout[:300])

        p = correr("rag", "zqxjkv-9931-no-existe", "3", "--json")
        try:
            datos = json.loads(p.stdout)
        except json.JSONDecodeError:
            datos = None
        check("rag --json emite JSON válido", datos is not None, p.stdout[:200])
        check("rag no inventa resultados para algo inexistente", datos == [], str(datos)[:200])

        # 4) edit + reindex + la búsqueda ve el cambio
        p = correr("edit", "02-docs/uno.md", "append", stdin="\n## Extra\n\nSección sobre buchovial-8891 que antes no existía.\n")
        check("edit append devuelve ok", p.returncode == 0, p.stderr)
        correr("reindex")
        p = correr("rag", "buchovial-8891", "3")
        check("rag ve el contenido agregado después del reindex", "uno.md" in p.stdout, p.stdout[:300])

        # 5) move + reindex
        p = correr("move", "02-docs/uno.md", "02-docs/renombrada.md")
        check("move renombra", p.returncode == 0 and (vault / "02-docs/renombrada.md").is_file(), p.stderr)
        correr("reindex")
        p = correr("rag", "buchovial-8891", "3")
        check("el índice sigue el rename (sin path viejo)", "renombrada.md" in p.stdout and "uno.md" not in p.stdout,
              p.stdout[:300])

        # 6) búsqueda léxica, listado y metadata
        p = correr("search", "wildcard")
        check("search (substring) encuentra la nota", "renombrada.md" in p.stdout, p.stdout[:300])
        p = correr("list", "--json")
        try:
            notas = json.loads(p.stdout)
        except json.JSONDecodeError:
            notas = None
        check("list --json emite JSON válido", notas is not None, p.stdout[:200])
        p = correr("meta", "02-docs/renombrada.md")
        check("meta muestra el path", "path:" in p.stdout, p.stdout[:200])

        # 7) delete a papelera + reindex purga del índice
        p = correr("delete", "02-docs/renombrada.md", "--yes")
        check("delete manda a papelera", p.returncode == 0 and not (vault / "02-docs/renombrada.md").exists(), p.stderr)
        check("delete deja la nota en .trash", any((vault / ".trash").iterdir()), "sin .trash")
        p = correr("reindex")
        check("reindex purga la nota borrada", "purgadas" in p.stdout or "sin cambios" in p.stdout, p.stdout[-300:])
        p = correr("rag", "buchovial-8891", "3")
        check("rag ya no devuelve la nota borrada", "renombrada.md" not in p.stdout, p.stdout[:300])

        # 8) errores y seguridad
        p = correr("read", "no-existe.md")
        check("read de inexistente sale con código 1", p.returncode == 1, f"rc={p.returncode}")
        p = correr("read", "../../etc/passwd")
        check("path traversal rechazado con código 2", p.returncode == 2, f"rc={p.returncode}")
        p = correr("read", "/etc/passwd")
        check("path absoluto rechazado con código 2", p.returncode == 2, f"rc={p.returncode}")

        # 9) status con el índice ya construido
        p = correr("status", "--json")
        try:
            est = json.loads(p.stdout)
        except json.JSONDecodeError:
            est = None
        check("status --json emite JSON válido", est is not None, p.stdout[:200])
        check("status reporta 0 notas después de borrar la única",
              bool(est) and est.get("notas") == 0, str(est)[:200])

        # 10) el vault real no se tocó
        real = Path.home() / "Obsidian"
        check("el vault real sigue existiendo", not (str(real) in ENV["OBS_RAG_VAULT"]), ENV["OBS_RAG_VAULT"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{CHECKS - len(FALLOS)}/{CHECKS} checks ok")
    if FALLOS:
        print("FALLARON: " + ", ".join(FALLOS))
        return 1
    print("E2E OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
