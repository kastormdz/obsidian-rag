"""Operaciones de vault sobre el FILESYSTEM, sin daemon.

El cliente MCP hablaba con `localhost:8787` para todo el CRUD; ese daemon se retiró y
las operaciones quedaron muertas. Este módulo las reimplementa contra los archivos, que
es lo que el vault realmente es, y mantiene el contrato de cada función.

Reglas de seguridad:
  - Todo path se resuelve y se verifica que quede DENTRO del vault (no `..`, no symlinks
    que salgan). Un agente nunca puede tocar `~/.ssh` por pasar `../../`.
  - Las escrituras son atómicas (temporal + rename en el mismo filesystem).
  - `borrar()` manda a `.trash/` con timestamp: reversible. `hard=True` es explícito.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Iterable

from .config import VAULT  # una sola fuente de la ruta del vault

EXCLUIDAS = {".obsidian", ".trash", ".git", ".stfolder", ".DS_Store"}


class FueraDelVault(Exception):
    """El path pedido sale del vault: se rechaza antes de tocar nada."""


def _resolver(path: str) -> Path:
    """Path absoluto dentro del vault. Rechaza `..`, absolutos externos y symlinks que salgan."""
    crudo = (path or "").strip()
    # Un path absoluto NO se reinterpreta como relativo: `write /etc/x.md` tiene que fallar,
    # no crear `<vault>/etc/x.md` (el usuario creería que escribió en /etc).
    if crudo.startswith("/"):
        raise FueraDelVault(f"path absoluto: usá una ruta relativa al vault ({crudo})")
    limpio = crudo
    # Los DOS lados resueltos: si el vault vive bajo un symlink (TMPDIR, /tmp → /private/tmp),
    # resolver solo el destino hace que `relative_to` falle con paths que SÍ están adentro.
    raiz = VAULT.resolve()
    destino = (raiz / limpio).resolve()
    try:
        destino.relative_to(raiz)
    except ValueError as error:
        raise FueraDelVault(f"fuera del vault: {path}") from error
    return destino


def _rel(destino: Path) -> str:
    """Path relativo al vault, para mostrar. NO sigue el último symlink si no hace falta.

    Un `alias.md -> real.md` es una nota con nombre propio: resolverla hacía que `list`
    mostrara `real.md` dos veces y `alias.md` nunca.
    """
    d = destino if destino.is_absolute() else (VAULT / destino)
    for base in (VAULT, VAULT.resolve()):
        # Se prueban las dos bases: `_recorrer` camina la raíz RESUELTA, y `_resolver`
        # devuelve paths resueltos; comparar contra una sola fallaba según de dónde viniera.
        try:
            return d.relative_to(base).as_posix()
        except ValueError:
            continue
    return destino.resolve().relative_to(VAULT.resolve()).as_posix()


def _con_md(path: str) -> str:
    """Agrega `.md` si falta. La misma regla para leer, editar, mover y borrar.

    Antes sólo `write_note` lo hacía: `move_note("a.md", "b")` dejaba un archivo sin
    extensión, invisible para el vault y para el índice.
    """
    limpio = (path or "").strip().rstrip("/")
    if limpio and not limpio.lower().endswith(".md"):
        limpio += ".md"
    return limpio


def _dentro(destino: Path, pedido: str) -> Path:
    """Revalida que un path YA transformado siga dentro del vault.

    Hace falta porque transformar el nombre puede sacarlo: `write "."` resuelve a la raíz
    del vault y `with_suffix(".md")` la convierte en `<vault>.md`, es decir un archivo AL
    LADO del vault. Medido 2026-10-24: creaba el archivo fuera antes de fallar.
    """
    raiz = VAULT.resolve()
    resuelto = destino.resolve()
    if not resuelto.is_relative_to(raiz):
        raise FueraDelVault(f"fuera del vault: {pedido!r}")
    return resuelto


def dentro_del_vault(abs_: Path | str, raiz: Path | str | None = None) -> bool:
    """¿El path RESUELTO sigue dentro del vault?

    `Path.resolve()` sigue los symlinks, así que un `nota.md -> ~/.ssh/id_ed25519` da
    `False`. Es el único chequeo que sirve: comparar el path sin resolver deja pasar
    cualquier link que apunte afuera.

    `raiz` se pasa explícita cuando el llamador tiene su propia copia de `VAULT` (el índice
    la tiene, y en los tests el vault es temporal): validar contra otra raíz rechazaría
    notas legítimas.
    """
    base = Path(raiz if raiz is not None else VAULT).resolve()
    try:
        Path(abs_).resolve().relative_to(base)
        return True
    except (ValueError, OSError):
        return False


def notas_del_vault() -> dict[str, str]:
    """Todas las notas del vault, confinadas, con su texto.

    Punto ÚNICO para los caminos que caminan el vault por su cuenta (índice, grafo,
    enlaces). Antes cada uno hacía su propio `os.walk`/`rglob` y ninguno filtraba
    symlinks: un link a un archivo de afuera se leía y quedaba indexado.
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
    """Notas visibles del vault, confinadas.

    Es el punto que usan `list`, `search`, `tags` y `meta`. Antes caminaba el vault sin
    filtrar symlinks ni ocultos: un `nota.md -> ~/.ssh/id_ed25519` se leía, y `_rel`
    moría con `ValueError` abortando el comando entero. Ahora usa el mismo confinamiento
    que `notas_del_vault()`.
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
    # fsync ANTES del rename: sin esto, un corte de luz puede dejar el rename hecho y el
    # contenido todavía en el page cache (archivo vacío o truncado). Cuesta ~1 ms.
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
            # Lista INLINE (`tags: [alfa, beta]`): sin esto el valor queda como string y
            # quien lo recorra termina iterando CARACTERES.
            if valor.startswith("[") and valor.endswith("]"):
                datos[clave] = [
                    x.strip().strip("'\"") for x in valor[1:-1].split(",") if x.strip()
                ]
            else:
                datos[clave] = valor if valor else []
    return datos


def frontmatter(texto: str) -> dict[str, Any]:
    """Frontmatter como dict. Alias público de `_frontmatter` para otros módulos."""
    return _frontmatter(texto)


def _tags_del_texto(texto: str) -> set[str]:
    """Tags inline, sin los `#` de bloques de código (`#!/bin/bash`, comentarios de YAML).

    Un `#r` de un script es indistinguible de un tag si no se mira el contexto: se saltan
    los bloques cercados y se exige un mínimo de 2 caracteres.
    """
    encontrados: set[str] = set()
    en_bloque = False
    for linea in texto.splitlines():
        limpia = linea.lstrip()
        if limpia.startswith("```"):
            en_bloque = not en_bloque
            continue
        # Un heading no es un tag, y dentro de un bloque de código `#` es un comentario.
        if en_bloque or re.match(r"#{1,6}\s", limpia):
            continue
        for etiqueta in re.findall(r"(?<![\w/\[\]{},:])(?<!#)#([A-Za-z][A-Za-z0-9_/-]{1,})", linea):
            encontrados.add(etiqueta.lower())
    return encontrados


# --------------------------------------------------------------------- operaciones


def list_notes(folder: str | None = None, limit: int | None = None, tag: str | None = None,
               sort_by: str = "modified", **_: Any) -> list[dict[str, str]]:
    """Notas del vault: `[{name, url}]` como el cliente MCP. Filtra carpeta y tag."""
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
    """Contenido completo de la nota."""
    destino = _resolver(_con_md(path))
    if not destino.is_file():
        raise FileNotFoundError(f"no existe la nota: {path}")
    return _leer(destino)


def write_note(path: str, content: str) -> str:
    """Crea o sobreescribe (atómico). Devuelve una línea con el resultado."""
    destino = _resolver(path)
    if destino.is_dir():
        raise ValueError(f"es una carpeta, no una nota: {path!r}")
    if destino.suffix.lower() != ".md":
        # Revalidar SIEMPRE después de cambiar el nombre: con `path=""` o `"."` el
        # `with_suffix` apunta al lado del vault, no adentro.
        destino = _dentro(destino.with_suffix(".md"), path)
    existia = destino.is_file()
    _escribir_atomico(destino, content)
    accion = "actualizada" if existia else "creada"
    return f"{accion}: {_rel(destino)} ({len(content)} bytes)"


def edit_note(path: str, op: str, content: str, old: str | None = None) -> str:
    """append | prepend | replace. `replace` cambia la primera aparición de `old`.

    `old` va como parámetro y no serializado dentro de `content`: con el separador `\n`
    anterior, un bloque viejo multilínea se partía en el primer salto y corrompía la nota.
    """
    destino = _resolver(_con_md(path))
    if not destino.is_file():
        raise FileNotFoundError(f"no existe la nota: {path}")
    actual = _leer(destino)
    if op == "append":
        nuevo = actual.rstrip("\n") + "\n" + content
    elif op == "prepend":
        nuevo = content + actual
    elif op == "replace":
        if not old:
            raise ValueError(
                "replace necesita el texto viejo: obsn edit <path> replace <nuevo> --old <viejo>"
            )
        if old not in actual:
            raise ValueError("replace: el texto viejo no está en la nota")
        nuevo = actual.replace(old, content, 1)
    elif op == "replace-old-new":
        raise ValueError(
            "replace-old-new se eliminó: usá `replace` y pasá el texto viejo en --old"
        )
    else:
        raise ValueError(f"operación desconocida: {op} (append|prepend|replace)")
    _escribir_atomico(destino, nuevo)
    return f"editada ({op}): {_rel(destino)}"


def delete_note(path: str, hard: bool = False) -> str:
    """Borra: por defecto manda a `.trash/` con timestamp (reversible)."""
    destino = _resolver(_con_md(path))
    if not destino.is_file():
        raise FileNotFoundError(f"no existe la nota: {path}")
    if hard:
        destino.unlink()
        return f"BORRADA (definitivo): {_rel(destino)}"
    papelera = VAULT / ".trash"
    papelera.mkdir(exist_ok=True)
    sello = time.strftime("%Y%m%d-%H%M%S")
    rel = _rel(destino)
    # Dos notas homónimas en carpetas distintas, borradas en el mismo segundo, iban al
    # MISMO archivo: `shutil.move` pisaba la primera y esa nota se perdía. Se conserva la
    # carpeta de origen en el nombre y, si aun así colisiona, se numera.
    nombre = f"{rel.replace('/', '__')} ({sello})"
    destino_movido = papelera / f"{nombre}{destino.suffix}"
    n = 1
    while destino_movido.exists():
        destino_movido = papelera / f"{nombre}-{n}{destino.suffix}"
        n += 1
    shutil.move(str(destino), str(destino_movido))
    return f"a la papelera: {rel} -> .trash/{destino_movido.name}"


def move_note(from_path: str, to_path: str) -> str:
    """Mueve o renombra. No pisa un archivo existente."""
    origen = _resolver(_con_md(from_path))
    destino = _resolver(_con_md(to_path))
    if not origen.is_file():
        raise FileNotFoundError(f"no existe la nota: {from_path}")
    if destino.exists():
        raise FileExistsError(f"ya existe: {to_path}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(origen), str(destino))
    return f"movida: {_rel(origen)} -> {_rel(destino)}"


def list_folders() -> str:
    """Carpetas con cantidad de notas, ordenadas."""
    conteo: dict[str, int] = {}
    for destino in _recorrer():
        carpeta = destino.parent.relative_to(VAULT).as_posix()
        while True:
            conteo[carpeta] = conteo.get(carpeta, 0) + 1
            if carpeta in ("", "."):
                break
            carpeta = str(Path(carpeta).parent.as_posix())
            if carpeta == ".":
                carpeta = ""
    lineas = [f"{n:5d}  {c or '(raíz)'}" for c, n in sorted(conteo.items())]
    return "\n".join(lineas)


def _tags_frontmatter(texto: str) -> set[str]:
    """Tags declarados en el frontmatter, siempre como set de strings válidos.

    El frontmatter puede traer una lista (`[a, b]`), una lista en bloque (`- a`) o un
    string suelto. Centralizarlo acá evita el bug de iterar los CARACTERES del string.
    """
    valor = _frontmatter(texto).get("tags", [])
    crudos = valor if isinstance(valor, list) else [valor]
    return {
        str(t).lstrip("#").lower()
        for t in crudos
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_/-]*", str(t).lstrip("#"))
    }


def list_tags() -> str:
    """Tags del frontmatter y del cuerpo, con frecuencia."""
    frecuencia: dict[str, int] = {}
    for destino in _recorrer():
        texto = _leer(destino)
        etiquetas = _tags_frontmatter(texto)
        etiquetas |= _tags_del_texto(texto)
        for etiqueta in etiquetas:
            frecuencia[etiqueta] = frecuencia.get(etiqueta, 0) + 1
    return "\n".join(f"{n:5d}  #{t}" for t, n in sorted(frecuencia.items(), key=lambda p: -p[1]))


def get_note_metadata(path: str) -> str:
    """Frontmatter, tags, tamaño, fechas y backlinks."""
    destino = _resolver(_con_md(path))
    if not destino.is_file():
        raise FileNotFoundError(f"no existe la nota: {path}")
    texto = _leer(destino)
    datos = _frontmatter(texto)
    stat = destino.stat()
    enlaces = re.findall(r"\[\[([^\]|#]+)", texto)
    # Obsidian resuelve un wikilink por nombre de archivo, por TÍTULO o por alias. Buscar solo
    # por `stem` pierde `[[Nota Uno]]` cuando el archivo se llama `uno.md`.
    nombres = {destino.stem, _titulo(texto, destino)}
    alias = datos.get("aliases") or datos.get("alias") or []
    nombres |= {str(a) for a in (alias if isinstance(alias, list) else [alias])}
    backlinks: list[str] = []
    for otra in _recorrer():
        if otra.resolve() == destino:
            continue
        contenido = _leer(otra)
        # Wikilink EXACTO: `[[uno` no es un link a `uno.md` si el link dice `[[uno-dos]]`.
        # Antes se matcheaba por prefijo y cualquier nota era backlink de sus homónimas.
        if any(re.search(r"\[\[" + re.escape(str(n)) + r"(?:\]\]|\||#)", contenido)
               for n in nombres if n):
            backlinks.append(_rel(otra))
    # `tags: infra` (escalar) no es una lista: `sorted("infra")` devolvía a, f, i, n, r.
    tags = datos.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    lineas = [
        f"path: {_rel(destino)}",
        f"titulo: {_titulo(texto, destino)}",
        f"bytes: {stat.st_size}",
        f"modificado: {time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))}",
        f"palabras: {len(texto.split())}",
        f"tags: {', '.join(sorted(str(t) for t in tags)) if tags else '(ninguno)'}",
    ]
    for clave, valor in datos.items():
        if clave != "tags":
            lineas.append(f"{clave}: {valor}")
    lineas.append(f"enlaces salientes: {len(enlaces)}")
    lineas.append(f"backlinks: {len(backlinks)}" + (f" -> {', '.join(backlinks[:8])}" if backlinks else ""))
    return "\n".join(lineas)


def search_notes(terms: Iterable[str] | str, folder: str | None = None, tag: str | None = None,
                 limit: int = 20, **_: Any) -> list[dict[str, Any]]:
    """Substring case-insensitive por CONTENIDO (el `rag` es el semántico).

    Rankea primero los aciertos en path o título, después por cantidad de términos.
    Devuelve una línea de contexto por hit, nunca el cuerpo entero.
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
