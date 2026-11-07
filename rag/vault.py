"""Vault operations against the FILESYSTEM, with no daemon.

The MCP client talked to `localhost:8787` for all CRUD; that daemon was retired and the
operations died with it. This module reimplements them against the files, which is what the
vault really is, and keeps each function's contract.

Safety rules:
  - Every path is resolved and checked to stay INSIDE the vault (no `..`, no symlinks
    pointing out). An agent can never touch `~/.ssh` by passing `../../`.
  - Writes are atomic (temp file + rename on the same filesystem).
  - `delete` goes to `.trash/` with a timestamp: reversible. `hard=True` is explicit.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Iterable

from .config import VAULT  # single source for the vault path
from .vigencia import fecha_local, vigencia

EXCLUIDAS = {".obsidian", ".trash", ".git", ".stfolder", ".DS_Store"}


class FueraDelVault(Exception):
    """The requested path leaves the vault: rejected before touching anything."""


def _resolver(path: str) -> Path:
    """Absolute path inside the vault. Rejects `..`, external absolutes and symlinks that leave."""
    crudo = (path or "").strip()
    # An absolute path is NOT reinterpreted as relative: `write /etc/x.md` has to fail,
    # not create `<vault>/etc/x.md` (the user would believe it wrote to /etc).
    if crudo.startswith("/"):
        raise FueraDelVault(f"absolute path: use a path relative to the vault ({crudo})")
    limpio = crudo
    # BOTH sides resolved: if the vault lives under a symlink (TMPDIR, /tmp → /private/tmp),
    # resolving only the destination makes `relative_to` fail for paths that ARE inside.
    raiz = VAULT.resolve()
    destino = (raiz / limpio).resolve()
    try:
        destino.relative_to(raiz)
    except ValueError as error:
        raise FueraDelVault(f"outside the vault: {path}") from error
    return destino


def _rel(destino: Path) -> str:
    """Path relative to the vault, for display. Does NOT follow the last symlink if not needed.

    An `alias.md -> real.md` is a note with its own name: resolving it made `list` show
    `real.md` twice and `alias.md` never.
    """
    d = destino if destino.is_absolute() else (VAULT / destino)
    for base in (VAULT, VAULT.resolve()):
        # Both bases are tried: `_recorrer` walks the RESOLVED root, and `_resolver` returns
        # resolved paths; comparing against only one failed depending on where it came from.
        try:
            return d.relative_to(base).as_posix()
        except ValueError:
            continue
    return destino.resolve().relative_to(VAULT.resolve()).as_posix()


def _con_md(path: str) -> str:
    """Append `.md` when missing. Same rule for read, edit, move and delete.

    Before, only `write_note` did it: `move_note("a.md", "b")` left a file with no
    extension, invisible to the vault and to the index.
    """
    limpio = (path or "").strip().rstrip("/")
    if limpio and not limpio.lower().endswith(".md"):
        limpio += ".md"
    return limpio


def _dentro(destino: Path, pedido: str) -> Path:
    """Re-check that an ALREADY transformed path is still inside the vault.

    It is needed because transforming the name can move it out: `write "."` resolves to the
    vault root and `with_suffix(".md")` turns it into `<vault>.md`, a file NEXT TO the vault.
    Measured 2026-10-24: it created the file outside before failing.
    """
    raiz = VAULT.resolve()
    resuelto = destino.resolve()
    if not resuelto.is_relative_to(raiz):
        raise FueraDelVault(f"outside the vault: {pedido!r}")
    return resuelto


def dentro_del_vault(abs_: Path | str, raiz: Path | str | None = None) -> bool:
    """Is the RESOLVED path still inside the vault?

    `Path.resolve()` follows symlinks, so a `note.md -> ~/.ssh/id_ed25519` yields `False`.
    It is the only check that works: comparing the unresolved path lets any link pointing
    outside through.

    `raiz` is passed explicitly when the caller has its own copy of `VAULT` (the index does,
    and in tests the vault is temporary): validating against another root would reject
    legitimate notes.
    """
    base = Path(raiz if raiz is not None else VAULT).resolve()
    try:
        Path(abs_).resolve().relative_to(base)
        return True
    except (ValueError, OSError):
        return False


def notas_del_vault() -> dict[str, str]:
    """Every note in the vault, confined, with its text.

    The SINGLE entry point for the code that walks the vault on its own (index, graph,
    links). Before, each one did its own `os.walk`/`rglob` and none filtered symlinks: a
    link to a file outside was read and ended up indexed.
    """
    raiz = VAULT.resolve()
    notas: dict[str, str] = {}
    for actual, dirs, archivos in os.walk(raiz, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUIDAS and not d.startswith("."))
        for nombre in sorted(archivos):
            if nombre.startswith(".") or not nombre.lower().endswith(".md"):
                continue
            destino = Path(actual) / nombre
            if not dentro_del_vault(destino):
                continue
            try:
                notas[destino.relative_to(raiz).as_posix()] = _leer(destino)
            except OSError:
                continue
    return notas


def _recorrer() -> Iterable[Path]:
    """Visible notes in the vault, confined.

    It is the entry point used by `list`, `search`, `tags` and `meta`. Before, it walked the
    vault without filtering symlinks or hidden files: a `note.md -> ~/.ssh/id_ed25519` was
    read, and `_rel` died with `ValueError`, aborting the whole command. Now it uses the same
    confinement as `notas_del_vault()`.
    """
    raiz = VAULT.resolve()
    for actual, dirs, archivos in os.walk(raiz, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUIDAS and not d.startswith("."))
        for nombre in sorted(archivos):
            if nombre.startswith(".") or not nombre.lower().endswith(".md"):
                continue
            destino = Path(actual) / nombre
            if dentro_del_vault(destino, raiz):
                yield destino


def _leer(destino: Path) -> str:
    return destino.read_text(encoding="utf-8", errors="replace")


def _escribir_atomico(destino: Path, contenido: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(f".{destino.name}.tmp-{os.getpid()}")
    # fsync BEFORE the rename: without it, a power cut can leave the rename done and the
    # content still in the page cache (empty or truncated file). Costs ~1 ms.
    with open(temporal, "w", encoding="utf-8") as fh:
        fh.write(contenido)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporal, destino)


def _titulo(texto: str, destino: Path) -> str:
    m = re.search(r"^#\s+(.+)$", texto, re.MULTILINE)
    return m.group(1).strip() if m else destino.stem


def _frontmatter(texto: str) -> dict[str, Any]:
    if not texto.startswith("---"):
        return {}
    fin = texto.find("\n---", 3)
    if fin == -1:
        return {}
    datos: dict[str, Any] = {}
    clave = None
    for linea in texto[3:fin].splitlines():
        if not linea.strip():
            continue
        if linea.lstrip().startswith("- ") and clave:
            datos.setdefault(clave, [])
            if isinstance(datos[clave], list):
                datos[clave].append(linea.lstrip()[2:].strip().strip("'\""))
            continue
        if ":" in linea:
            clave, _, valor = linea.partition(":")
            clave = clave.strip()
            valor = valor.strip().strip("'\"")
            # INLINE list (`tags: [alpha, beta]`): without this the value stays a string and
            # whoever iterates it ends up iterating CHARACTERS.
            if valor.startswith("[") and valor.endswith("]"):
                datos[clave] = [
                    x.strip().strip("'\"") for x in valor[1:-1].split(",") if x.strip()
                ]
            else:
                datos[clave] = valor if valor else []
    return datos


def frontmatter(texto: str) -> dict[str, Any]:
    """Frontmatter as a dict. Public alias of `_frontmatter` for other modules."""
    return _frontmatter(texto)


def _tags_del_texto(texto: str) -> set[str]:
    """Inline tags, without the `#` of code blocks (`#!/bin/bash`, YAML comments).

    An `#r` in a script is indistinguishable from a tag unless the context is checked: fenced
    blocks are skipped and a minimum of 2 characters is required.
    """
    encontrados: set[str] = set()
    en_bloque = False
    for linea in texto.splitlines():
        limpia = linea.lstrip()
        if limpia.startswith("```"):
            en_bloque = not en_bloque
            continue
        # A heading is not a tag, and inside a code block `#` is a comment.
        if en_bloque or re.match(r"#{1,6}\s", limpia):
            continue
        for etiqueta in re.findall(r"(?<![\w/\[\]{},:])(?<!#)#([A-Za-z][A-Za-z0-9_/-]{1,})", linea):
            encontrados.add(etiqueta.lower())
    return encontrados


# --------------------------------------------------------------------- operations


def list_notes(folder: str | None = None, limit: int | None = None, tag: str | None = None,
               sort_by: str = "modified", **_: Any) -> list[dict[str, str]]:
    """Vault notes: `[{name, url}]` like the MCP client. Filters by folder and tag."""
    base = _resolver(folder) if folder else VAULT
    encontradas: list[tuple[float, Path]] = []
    for destino in _recorrer():
        if folder and not destino.is_relative_to(base):
            continue
        if tag:
            texto = _leer(destino)
            etiquetas = _tags_frontmatter(texto)
            etiquetas |= _tags_del_texto(texto)
            if tag.lstrip("#").lower() not in etiquetas:
                continue
        encontradas.append((destino.stat().st_mtime, destino))
    encontradas.sort(key=lambda par: par[0], reverse=(sort_by == "modified"))
    if limit:
        encontradas = encontradas[: max(1, int(limit))]
    return [{"name": _rel(d), "url": f"obsidian://open?path={_rel(d)}"} for _, d in encontradas]


def read_note(path: str) -> str:
    """Full note content."""
    destino = _resolver(_con_md(path))
    if not destino.is_file():
        raise FileNotFoundError(f"no such note: {path}")
    return _leer(destino)


def write_note(path: str, content: str) -> str:
    """Create or overwrite (atomic). Returns one line with the result."""
    destino = _resolver(path)
    if destino.is_dir():
        raise ValueError(f"that is a folder, not a note: {path!r}")
    if destino.suffix.lower() != ".md":
        # Re-validate ALWAYS after changing the name: with `path=""` or `"."`, `with_suffix`
        # points next to the vault, not inside it.
        destino = _dentro(destino.with_suffix(".md"), path)
    existia = destino.is_file()
    _escribir_atomico(destino, content)
    accion = "updated" if existia else "created"
    return f"{accion}: {_rel(destino)} ({len(content)} bytes)"


def edit_note(path: str, op: str, content: str, old: str | None = None) -> str:
    """append | prepend | replace. `replace` changes the first occurrence of `old`.

    `old` is a parameter and not serialized inside `content`: with the previous `\n`
    separator, an old multiline block was split at the first newline and corrupted the note.
    """
    destino = _resolver(_con_md(path))
    if not destino.is_file():
        raise FileNotFoundError(f"no such note: {path}")
    actual = _leer(destino)
    if op == "append":
        nuevo = actual.rstrip("\n") + "\n" + content
    elif op == "prepend":
        nuevo = content + actual
    elif op == "replace":
        if not old:
            raise ValueError(
                "replace needs the old text: obsn edit <path> replace <new> --old <old>"
            )
        if old not in actual:
            raise ValueError("replace: the old text is not in the note")
        nuevo = actual.replace(old, content, 1)
    elif op == "replace-old-new":
        raise ValueError(
            "replace-old-new was removed: use `replace` and pass the old text in --old"
        )
    else:
        raise ValueError(f"unknown operation: {op} (append|prepend|replace)")
    _escribir_atomico(destino, nuevo)
    return f"edited ({op}): {_rel(destino)}"


def delete_note(path: str, hard: bool = False) -> str:
    """Delete: by default sends it to `.trash/` with a timestamp (reversible)."""
    destino = _resolver(_con_md(path))
    if not destino.is_file():
        raise FileNotFoundError(f"no such note: {path}")
    if hard:
        destino.unlink()
        return f"DELETED (permanent): {_rel(destino)}"
    papelera = VAULT / ".trash"
    papelera.mkdir(exist_ok=True)
    sello = time.strftime("%Y%m%d-%H%M%S")
    rel = _rel(destino)
    # Two same-named notes in different folders, deleted in the same second, went to the
    # SAME file: `shutil.move` overwrote the first and that note was lost. The source folder
    # is kept in the name and, if it still collides, a counter is appended.
    nombre = f"{rel.replace('/', '__')} ({sello})"
    destino_movido = papelera / f"{nombre}{destino.suffix}"
    n = 1
    while destino_movido.exists():
        destino_movido = papelera / f"{nombre}-{n}{destino.suffix}"
        n += 1
    shutil.move(str(destino), str(destino_movido))
    return f"to trash: {rel} -> .trash/{destino_movido.name}"


def move_note(from_path: str, to_path: str) -> str:
    """Move or rename. Never overwrites an existing file."""
    origen = _resolver(_con_md(from_path))
    destino = _resolver(_con_md(to_path))
    if not origen.is_file():
        raise FileNotFoundError(f"no such note: {from_path}")
    if destino.exists():
        raise FileExistsError(f"already exists: {to_path}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(origen), str(destino))
    return f"moved: {_rel(origen)} -> {_rel(destino)}"


def folder_counts() -> list[dict[str, Any]]:
    """Folders with their note count, root as `(root)`.

    A note in `sub/` counts for `sub` AND for every ancestor: the number answers "how many
    notes live under this folder", which is what someone browsing the vault wants. Root
    notes used to show up as a separate `.` row while the ancestor was `(root)`: two names
    for the same folder, and the root count only appeared when there was a subfolder.
    """
    raiz = Path(VAULT).resolve()
    conteo: dict[str, int] = {}
    for destino in _recorrer():
        carpeta = destino.parent.relative_to(raiz).as_posix()
        if carpeta == ".":
            carpeta = ""
        while True:
            conteo[carpeta] = conteo.get(carpeta, 0) + 1
            if not carpeta:
                break
            carpeta = Path(carpeta).parent.as_posix()
            if carpeta == ".":
                carpeta = ""
    return [{"folder": c or "(root)", "count": n} for c, n in sorted(conteo.items())]


def list_folders() -> str:
    """Folders with their note count, sorted. Human-readable form of `folder_counts`."""
    return "\n".join(f"{r['count']:5d}  {r['folder']}" for r in folder_counts())


def _tags_frontmatter(texto: str) -> set[str]:
    """Tags declared in the frontmatter, always as a set of valid strings.

    The frontmatter may hold a list (`[a, b]`), a block list (`- a`) or a bare string.
    Centralizing it here avoids the bug of iterating the CHARACTERS of the string.
    """
    valor = _frontmatter(texto).get("tags", [])
    crudos = valor if isinstance(valor, list) else [valor]
    return {
        str(t).lstrip("#").lower()
        for t in crudos
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_/-]*", str(t).lstrip("#"))
    }


def tag_counts() -> list[dict[str, Any]]:
    """Tags from the frontmatter and the body, with their frequency.

    The `#` is kept in the value so the JSON mirrors the human output and the plugin's
    contract does not change. Ties are broken by name, not by filesystem order.
    """
    frecuencia: dict[str, int] = {}
    for destino in _recorrer():
        texto = _leer(destino)
        etiquetas = _tags_frontmatter(texto)
        etiquetas |= _tags_del_texto(texto)
        for etiqueta in etiquetas:
            frecuencia[etiqueta] = frecuencia.get(etiqueta, 0) + 1
    return [
        {"tag": f"#{t}", "count": n}
        for t, n in sorted(frecuencia.items(), key=lambda p: (-p[1], p[0]))
    ]


def list_tags() -> str:
    """Tags with their frequency. Human-readable form of `tag_counts`."""
    return "\n".join(f"{r['count']:5d}  {r['tag']}" for r in tag_counts())


def get_note_metadata(path: str) -> str:
    """Frontmatter, tags, size, dates and backlinks."""
    destino = _resolver(_con_md(path))
    if not destino.is_file():
        raise FileNotFoundError(f"no such note: {path}")
    texto = _leer(destino)
    datos = _frontmatter(texto)
    stat = destino.stat()
    enlaces = re.findall(r"\[\[([^\]|#]+)", texto)
    # Obsidian resolves a wikilink by file name, by TITLE or by alias. Searching only by
    # `stem` misses `[[Note One]]` when the file is called `one.md`.
    nombres = {destino.stem, _titulo(texto, destino)}
    alias = datos.get("aliases") or datos.get("alias") or []
    nombres |= {str(a) for a in (alias if isinstance(alias, list) else [alias])}
    backlinks: list[str] = []
    for otra in _recorrer():
        if otra.resolve() == destino:
            continue
        contenido = _leer(otra)
        # EXACT wikilink: `[[one` is not a link to `one.md` if the link says `[[one-two]]`.
        # It used to match by prefix and any note was a backlink of its homonyms.
        if any(re.search(r"\[\[" + re.escape(str(n)) + r"(?:\]\]|\||#)", contenido)
               for n in nombres if n):
            backlinks.append(_rel(otra))
    # `tags: infra` (scalar) is not a list: `sorted("infra")` returned a, f, i, n, r.
    tags = datos.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    lineas = [
        f"path: {_rel(destino)}",
        f"title: {_titulo(texto, destino)}",
        f"bytes: {stat.st_size}",
        f"modified: {time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))}",
        f"words: {len(texto.split())}",
        f"tags: {', '.join(sorted(str(t) for t in tags)) if tags else '(none)'}",
    ]
    for clave, valor in datos.items():
        if clave not in ("tags", "stale_after", "updated"):
            lineas.append(f"{clave}: {valor}")
    v = vigencia(datos)
    if v["stale_after"]:
        estado = (f"STALE, {v['days_stale']} days ago" if v["stale"] else "fresh")
        lineas.append(f"freshness: {estado} (expires {fecha_local(v['stale_after'])})")
    else:
        lineas.append("freshness: no date declared")
    if v["updated"]:
        lineas.append(f"updated: {fecha_local(v['updated'])}")
    lineas.append(f"outgoing links: {len(enlaces)}")
    lineas.append(f"backlinks: {len(backlinks)}" + (f" -> {', '.join(backlinks[:8])}" if backlinks else ""))
    return "\n".join(lineas)


def search_notes(terms: Iterable[str] | str, folder: str | None = None, tag: str | None = None,
                 limit: int = 20, **_: Any) -> list[dict[str, Any]]:
    """Case-insensitive substring over CONTENT (`rag` is the semantic one).

    Ranks path or title hits first, then by number of terms. Returns one line of context per
    hit, never the whole body.
    """
    lista = [terms] if isinstance(terms, str) else list(terms)
    lista = [t for t in (str(x).strip().lower() for x in lista) if t]
    if not lista:
        return []
    base = _resolver(folder) if folder else VAULT
    hits: list[dict[str, Any]] = []
    for destino in _recorrer():
        if folder and not destino.is_relative_to(base):
            continue
        texto = _leer(destino)
        bajo = texto.lower()
        if tag:
            etiquetas = _tags_frontmatter(texto)
            etiquetas |= _tags_del_texto(texto)
            if tag.lstrip("#").lower() not in etiquetas:
                continue
        rel = _rel(destino).lower()
        en_path = any(t in rel for t in lista)
        encontrados = [t for t in lista if t in bajo]
        if not encontrados and not en_path:
            continue
        contexto = ""
        for termino in encontrados:
            i = bajo.find(termino)
            if i >= 0:
                inicio = max(0, bajo.rfind("\n", 0, i) + 1)
                fin = bajo.find("\n", i)
                contexto = texto[inicio: fin if fin > 0 else len(texto)].strip()[:240]
                break
        hits.append({
            "path": _rel(destino),
            "matches": len(encontrados),
            "path_hit": en_path,
            "context": contexto,
        })
    hits.sort(key=lambda h: (not h["path_hit"], -h["matches"], h["path"]))
    return hits[: max(1, int(limit))]
