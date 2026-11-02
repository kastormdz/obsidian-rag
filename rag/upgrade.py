"""upgrade — update obsn from its git repo and leave the index consistent.

Why it exists: the package is installed in editable mode, so "updating" means
fetching commits, reinstalling only if the dependencies changed, and reindexing if the
chunking criteria or the model changed. Doing it by hand is easy to forget halfway
(someone runs `git pull` and nobody reindexes).

Outputs:
    obsn upgrade --check    # 0 = up to date, 10 = update available, 1 = error
    obsn upgrade            # fetches the commits, reinstalls if needed and reindexes
    obsn upgrade --how      # explains the procedure
"""
from __future__ import annotations

import os
import re
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
    """Repo directory when the install is editable (the package lives in it)."""
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


def _ver(texto: str) -> tuple[int, int, int]:
    """(mayor, menor, patch) de algo como 'v0.2.0'. Sin `packaging`: no vale una dependencia."""
    numeros = [int(n) for n in re.findall(r"\d+", texto)[:3]]
    while len(numeros) < 3:
        numeros.append(0)
    return numeros[0], numeros[1], numeros[2]


def ultima_version(repo: Path) -> str | None:
    """Highest version published as a tag that origin/main reaches, without the `v`."""
    salida = _git(["tag", "--list", "v*", "--merged", "origin/main", "--sort=-v:refname"], repo)
    if salida.returncode != 0:
        return None
    for linea in salida.stdout.splitlines():
        tag = linea.strip()
        if re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            return tag[1:]
    return None


def estado(repo: Path, fetch: bool = True) -> dict[str, Any]:
    """Repo state against its remote. Modifies nothing."""
    datos: dict[str, Any] = {"repo": str(repo), "version": version_instalada()}

    rama = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    if rama.returncode != 0:
        raise RuntimeError(f"could not read the branch: {rama.stderr.strip()}")
    datos["rama"] = rama.stdout.strip()
    datos["head"] = _git(["rev-parse", "--short", "HEAD"], repo).stdout.strip()
    datos["sucio"] = bool(_git(["status", "--porcelain"], repo).stdout.strip())
    datos["archivos_sucios"] = [
        l[3:] for l in _git(["status", "--porcelain"], repo).stdout.splitlines() if l.strip()
    ]

    upstream = f"origin/{datos['rama']}"
    datos["upstream"] = upstream
    if fetch:
        traido = _git(["fetch", "--quiet", "--prune", "--tags", "origin"], repo)
        if traido.returncode != 0:
            raise RuntimeError(f"git fetch failed: {traido.stderr.strip()[:300]}")

    # Los tags cuentan: un commit sin tag no es una versión publicada.
    datos["ultima_version"] = ultima_version(repo)

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
    print("[upgrade] pyproject.toml changed: reinstalling in this interpreter", flush=True)
    pip = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
        cwd=repo, capture_output=True, text=True, timeout=600,
    )
    if pip.returncode != 0:
        print(f"[upgrade] pip failed:\n{pip.stderr[-800:]}", file=sys.stderr)
        return False
    print("[upgrade] reinstalled", flush=True)
    return True


def main(check: bool = False, how: bool = False, reindexar: bool = True,
         repo: Path | None = None) -> int:
    if how:
        print(
            "obsn upgrade updates the package from its git repo and leaves the index consistent.\n\n"
            "1. It checks that the install is editable: if `rag` lives in site-packages and not\n"
            "   in a repo with .git, there is nothing to update from here (use pip).\n"
            "2. It refuses to continue with uncommitted local changes: a fast-forward with a\n"
            "   dirty tree can overwrite work.\n"
            "3. `git fetch` + fast-forward to the remote (never creates a merge commit).\n"
            "4. If pyproject.toml changed, it reinstalls in the interpreter running obsn.\n"
            "5. It reindexes: incremental when nothing structural changed; if the chunker or the\n"
            "   model changed, the index rebuilds itself (meta.chunker/meta.modelo decide).\n\n"
            "`--check` looks at two things: new commits on the remote and the latest version tag\n"
            "(vX.Y.Z) that origin/main reaches. A commit without a tag does not count as a\n"
            "published version.\n\n"
            "Exit codes for --check: 0 = up to date, 10 = update available, 1 = error."
        )
        return 0

    repo = repo or repo_dir()
    if repo is None:
        print(
            "this install is not editable (no .git next to the package).\n"
            "Update with: pip install -U git+https://github.com/kastormdz/obsidian-rag.git",
            file=sys.stderr,
        )
        return EXIT_ERROR

    try:
        est = estado(repo, fetch=True)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"[upgrade] could not query the remote: {exc}", file=sys.stderr)
        return EXIT_ERROR

    marca = f"{est['rama']} @ {est['head']}  ·  version {est['version']}"
    publicada = est.get("ultima_version")
    cola = f" · latest published {publicada}" if publicada else " · no published versions"
    hay_version = bool(publicada) and _ver(str(publicada)) > _ver(est["version"])
    if check:
        if est["atras"] == 0 and not hay_version:
            print(f"up to date: {marca}{cola}")
            return EXIT_AL_DIA
        motivos = []
        if hay_version:
            motivos.append(f"version {publicada} available")
        if est["atras"]:
            motivos.append(f"{est['atras']} new commit{'s' if est['atras'] != 1 else ''}")
        print(f"update available: {marca}{cola} -> {', '.join(motivos)}")
        for linea in _commit_log(repo, est["head"], est["upstream"]):
            print(f"  {linea}")
        return EXIT_HAY_UPDATE

    if est["atras"] == 0:
        print(f"[upgrade] nothing to do: {marca}")
        return EXIT_AL_DIA

    if est["sucio"]:
        print("[upgrade] uncommitted local changes; not touching anything:", file=sys.stderr)
        for archivo in est["archivos_sucios"][:10]:
            print(f"  {archivo}", file=sys.stderr)
        return EXIT_ERROR

    previo, nuevo = est["head"], est["remoto"]
    print(f"[upgrade] {est['rama']}: {previo} -> {nuevo} ({est['atras']} commits)")
    ff = _git(["merge", "--ff-only", est["upstream"]], repo)
    if ff.returncode != 0:
        # Happens with unpushed local commits: ff-only cannot and we do not invent merges.
        print(f"[upgrade] could not fast-forward (unpushed local commits?):\n{ff.stderr.strip()}",
              file=sys.stderr)
        return EXIT_ERROR
    for linea in _commit_log(repo, previo, nuevo):
        print(f"  {linea}")

    _reinstalar_si_cambio_pyproject(repo, previo, nuevo)

    # Reimport in case the upgrade changed the engine: this process still holds the old module.
    if reindexar:
        print("[upgrade] reindexing (incremental; full rebuild only if the engine changed)", flush=True)
        import importlib

        from . import index
        importlib.reload(index)
        index.build(force=False)
    print(f"[upgrade] done: {est['rama']} @ {_git(['rev-parse', '--short', 'HEAD'], repo).stdout.strip()}")
    return EXIT_AL_DIA
