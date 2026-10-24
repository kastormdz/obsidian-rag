"""upgrade — actualizar obsn desde su repo git y dejar el índice consistente.

Por qué existe: el paquete se instala en modo editable, así que "actualizar" es
traer commits, reinstalar sólo si cambiaron las dependencias y reindexar si cambió
el criterio de troceado o el modelo. Hacerlo a mano es fácil de olvidar a medias
(se tira `git pull` y nadie reindexa).

Salidas:
    obsn upgrade --check    # 0 = al día, 10 = hay actualización, 1 = error
    obsn upgrade            # trae los commits, reinstala si hace falta y reindexa
    obsn upgrade --how      # explica el procedimiento
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

EXIT_AL_DIA = 0
EXIT_ERROR = 1
EXIT_HAY_UPDATE = 10


def version_instalada() -> str:
    try:
        from importlib.metadata import version
        return version("obsidian-rag")
    except Exception:
        return "desconocida"


def repo_dir() -> Path | None:
    """Directorio del repo cuando la instalación es editable (el paquete vive en él)."""
    try:
        import rag
    except ImportError:
        return None
    raiz = Path(rag.__file__).resolve().parent.parent
    return raiz if (raiz / ".git").exists() else None


def _git(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        # Sin prompts: un upgrade no puede quedarse esperando una password en un cron.
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "true",
        "GIT_SSH_COMMAND": "ssh -o IdentityAgent=none -o BatchMode=yes",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout
    )


def estado(repo: Path, fetch: bool = True) -> dict[str, Any]:
    """Estado del repo frente a su remoto. No modifica nada."""
    datos: dict[str, Any] = {"repo": str(repo), "version": version_instalada()}

    rama = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    if rama.returncode != 0:
        raise RuntimeError(f"no pude leer la rama: {rama.stderr.strip()}")
    datos["rama"] = rama.stdout.strip()
    datos["head"] = _git(["rev-parse", "--short", "HEAD"], repo).stdout.strip()
    datos["sucio"] = bool(_git(["status", "--porcelain"], repo).stdout.strip())
    datos["archivos_sucios"] = [
        l[3:] for l in _git(["status", "--porcelain"], repo).stdout.splitlines() if l.strip()
    ]

    upstream = f"origin/{datos['rama']}"
    datos["upstream"] = upstream
    if fetch:
        traido = _git(["fetch", "--quiet", "--prune", "origin"], repo)
        if traido.returncode != 0:
            raise RuntimeError(f"git fetch falló: {traido.stderr.strip()[:300]}")

    existe = _git(["rev-parse", "--verify", "--quiet", upstream], repo)
    if existe.returncode != 0:
        datos["atras"] = 0
        datos["adelante"] = 0
        datos["remoto"] = None
        return datos

    datos["remoto"] = _git(["rev-parse", "--short", upstream], repo).stdout.strip()
    atras = _git(["rev-list", "--count", f"HEAD..{upstream}"], repo).stdout.strip()
    adelante = _git(["rev-list", "--count", f"{upstream}..HEAD"], repo).stdout.strip()
    datos["atras"] = int(atras or 0)
    datos["adelante"] = int(adelante or 0)
    return datos


def _commit_log(repo: Path, desde: str, hasta: str) -> list[str]:
    salida = _git(["log", "--oneline", "--no-decorate", f"{desde}..{hasta}"], repo).stdout
    return [l for l in salida.splitlines() if l.strip()]


def _reinstalar_si_cambio_pyproject(repo: Path, previo: str, nuevo: str) -> bool:
    cambio = _git(["diff", "--name-only", previo, nuevo], repo).stdout
    if "pyproject.toml" not in cambio:
        return False
    print("[upgrade] cambió pyproject.toml: reinstalando en este intérprete", flush=True)
    pip = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
        cwd=repo, capture_output=True, text=True, timeout=600,
    )
    if pip.returncode != 0:
        print(f"[upgrade] pip falló:\n{pip.stderr[-800:]}", file=sys.stderr)
        return False
    print("[upgrade] reinstalado", flush=True)
    return True


def main(check: bool = False, how: bool = False, reindexar: bool = True,
         repo: Path | None = None) -> int:
    if how:
        print(
            "obsn upgrade actualiza el paquete desde su repo git y deja el índice consistente.\n\n"
            "1. Verifica que la instalación sea editable: si `rag` vive en site-packages y no\n"
            "   en un repo con .git, no hay nada que actualizar por acá (usá pip).\n"
            "2. Se niega a seguir si hay cambios locales sin commitear: un fast-forward con el\n"
            "   árbol sucio puede pisar trabajo.\n"
            "3. `git fetch` + fast-forward al remoto (nunca crea un merge commit).\n"
            "4. Si cambió pyproject.toml, reinstala en el intérprete que corre obsn.\n"
            "5. Reindexa: incremental si no cambió nada estructural; si cambió el chunker o el\n"
            "   modelo, el índice se reconstruye solo (lo decide meta.chunker/meta.modelo).\n\n"
            "Códigos de salida de --check: 0 = al día, 10 = hay actualización, 1 = error."
        )
        return 0

    repo = repo or repo_dir()
    if repo is None:
        print(
            "esta instalación no es editable (no hay .git junto al paquete).\n"
            "Actualizá con: pip install -U git+https://github.com/kastormdz/obsidian-rag.git",
            file=sys.stderr,
        )
        return EXIT_ERROR

    try:
        est = estado(repo, fetch=True)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"[upgrade] no pude consultar el remoto: {exc}", file=sys.stderr)
        return EXIT_ERROR

    marca = f"{est['rama']} @ {est['head']}  ·  versión {est['version']}"
    if check:
        if est["atras"] == 0:
            print(f"al día: {marca}")
            return EXIT_AL_DIA
        print(f"hay actualización: {marca} -> {est['upstream']} @ {est['remoto']}"
              f"  ({est['atras']} commit{'s' if est['atras'] != 1 else ''})")
        for linea in _commit_log(repo, est["head"], est["upstream"]):
            print(f"  {linea}")
        return EXIT_HAY_UPDATE

    if est["atras"] == 0:
        print(f"[upgrade] nada que hacer: {marca}")
        return EXIT_AL_DIA

    if est["sucio"]:
        print("[upgrade] hay cambios locales sin commitear; no toco nada:", file=sys.stderr)
        for archivo in est["archivos_sucios"][:10]:
            print(f"  {archivo}", file=sys.stderr)
        return EXIT_ERROR

    previo, nuevo = est["head"], est["remoto"]
    print(f"[upgrade] {est['rama']}: {previo} -> {nuevo} ({est['atras']} commits)")
    ff = _git(["merge", "--ff-only", est["upstream"]], repo)
    if ff.returncode != 0:
        # Pasa si hay commits locales sin pushear: ff-only no puede y no inventamos merges.
        print(f"[upgrade] no se pudo avanzar (¿commits locales sin pushear?):\n{ff.stderr.strip()}",
              file=sys.stderr)
        return EXIT_ERROR
    for linea in _commit_log(repo, previo, nuevo):
        print(f"  {linea}")

    _reinstalar_si_cambio_pyproject(repo, previo, nuevo)

    # Reimportar por si el upgrade cambió el motor: el proceso ya tiene el módulo viejo.
    if reindexar:
        print("[upgrade] reindexando (incremental; rebuild solo si cambió el motor)", flush=True)
        import importlib

        from . import index
        importlib.reload(index)
        index.build(force=False)
    print(f"[upgrade] listo: {est['rama']} @ {_git(['rev-parse', '--short', 'HEAD'], repo).stdout.strip()}")
    return EXIT_AL_DIA
