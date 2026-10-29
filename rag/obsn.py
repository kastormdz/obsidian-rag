#!/usr/bin/env python3
"""obsn — un solo comando para leer, escribir y buscar en un vault de Obsidian.

Los nombres largos son los del estándar MCP (`read_note`, `write_note`, …) y los cortos
son alias, para que la misma herramienta sirva desde un agente y desde una terminal.

    obsn rag "consulta"          búsqueda semántica (necesita el índice)
    obsn search "texto"          búsqueda léxica por contenido (sin modelo)
    obsn reindex [--force]       construir/actualizar el índice
    obsn status                  estado del índice
    obsn upgrade [--check]       actualizar el paquete desde su repo git

    obsn read  <path>            leer una nota
    obsn write <path>            crear/sobreescribir (contenido por stdin o --file)
    obsn edit  <path> <op>       append | prepend | replace
    obsn delete <path>           a la papelera (--hard para borrar de verdad)
    obsn move  <from> <to>       mover o renombrar
    obsn list  [carpeta]         listar notas
    obsn folders                 carpetas con cantidad de notas
    obsn tags                    tags del vault
    obsn meta  <path>            frontmatter, tags, backlinks

Variables de entorno: OBS_RAG_VAULT, OBS_RAG_INDEX, OBS_RAG_CACHE, OBS_RAG_MODEL.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Sequence

from . import vault


def _encolar_reindex() -> None:
    """Dispara un reindexado en segundo plano y sigue.

    fork+exec de un worker que hace debounce y coalescencia (ver rag/reindex_worker.py):
    el usuario no paga los ~1,5 s de cargar el modelo. Se apaga con `--no-reindex` o
    con `OBS_RAG_NO_ASYNC=1` (scripts que escriben en lote y reindexan una sola vez).
    Si no se puede lanzar, no es un error: el timer lo agarra igual.
    """
    if os.environ.get("OBS_RAG_NO_ASYNC"):
        return
    try:
        subprocess.Popen(
            [sys.executable, "-m", "rag.reindex_worker"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,       # que no lo mate el Ctrl-C de la terminal
            close_fds=True,
        )
    except OSError:
        pass


# --------------------------------------------------------------------- formato


def _json(datos: Any) -> None:
    print(json.dumps(datos, ensure_ascii=False, indent=2))


def _pedir_contenido(args: argparse.Namespace) -> str:
    if getattr(args, "file", None):
        with open(args.file, encoding="utf-8") as archivo:
            return archivo.read()
    if getattr(args, "content", None):
        return args.content
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("falta el contenido: usá --file <ruta> o pasalo por stdin")


def _confirmar(pregunta: str, opciones: argparse.Namespace) -> bool:
    if getattr(opciones, "yes", False):
        return True
    if not sys.stdin.isatty():
        raise SystemExit(f"{pregunta}\nNo hay terminal para confirmar: repetí con --yes.")
    respuesta = input(f"{pregunta} [s/N] ").strip().lower()
    return respuesta in {"s", "si", "sí", "y", "yes"}


# --------------------------------------------------------------------- comandos


def cmd_rag(args: argparse.Namespace) -> None:
    from . import index

    resultados = index.search(args.query, args.n)
    if args.json:
        _json(resultados)
        return
    if not resultados:
        print("(sin resultados: probá otras palabras o corré `obsn reindex`)")
        return
    for i, r in enumerate(resultados, 1):
        print(f"{i}. {r['path']}  ·  {r.get('heading', '')}  ·  score {r.get('score', 0):.3f}")


def cmd_search(args: argparse.Namespace) -> None:
    resultados = vault.search_notes(
        args.terms, folder=args.folder, tag=args.tag, limit=args.limit
    )
    if args.json:
        _json(resultados)
        return
    if not resultados:
        print("(sin resultados)")
        return
    for r in resultados:
        marca = " [path]" if r["path_hit"] else ""
        print(f"{r['path']}{marca}  ({r['matches']})")
        if r["context"]:
            print(f"    {r['context']}")


def cmd_reindex(args: argparse.Namespace) -> None:
    from . import index

    index.build(force=args.force)


def cmd_upgrade(args: argparse.Namespace) -> None:
    from . import upgrade

    raise SystemExit(upgrade.main(check=args.check, how=args.how, reindexar=not args.no_reindex))


def cmd_status(args: argparse.Namespace) -> None:
    import sqlite3
    import time
    from datetime import datetime

    from . import config

    if not config.DB.exists():
        raise SystemExit(f"no hay índice en {config.DB}: corré `obsn reindex`")
    with sqlite3.connect(config.DB) as c:
        # `notes` es la fuente de verdad de lo indexado. Contar paths distintos en
        # `chunks` subcuenta: una nota sin cuerpo (solo frontmatter, o <20 chars) no
        # genera chunks y desaparecía del total (medido: 193 vs 196 reales).
        try:
            notas = c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        except sqlite3.OperationalError as exc:
            # Índice de un formato anterior (sin tabla `notes`) o archivo que no es una DB:
            # sin esto el usuario ve un traceback de sqlite en vez de qué hacer.
            raise SystemExit(
                f"índice incompleto en {config.DB} ({exc}): corré `obsn reindex --force`"
            ) from None
        chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        con_vec = c.execute("SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]
        sin_chunks = c.execute(
            "SELECT COUNT(*) FROM notes WHERE COALESCE(n_chunks, 0) = 0"
        ).fetchone()[0]
        # Cuándo se indexó y en qué estado quedó: los datos ya estaban en `meta`, pero no se
        # exponían, así que un agente no podía distinguir un índice fresco de uno viejo, ni
        # un build fallido de uno que nunca corrió. La tabla es `meta(k, v)`: con nombres
        # inventados el SELECT tira OperationalError y el `except` lo vuelve "sin dato".
        try:
            filas = dict(c.execute("SELECT k, v FROM meta WHERE k IN ('indexado', 'estado')"))
        except sqlite3.OperationalError:
            filas = {}
    indexado = antiguedad = None
    if filas.get("indexado"):
        try:
            sello = float(filas["indexado"])
        except (TypeError, ValueError):
            sello = 0.0
        if sello:
            indexado = datetime.fromtimestamp(sello).astimezone().isoformat(timespec="seconds")
            antiguedad = max(0, int((time.time() - sello) / 60))
    estado = filas.get("estado") or "desconocido"
    datos = {
        "indice": str(config.DB),
        "vault": str(config.VAULT),
        "notas": notas,
        "chunks": chunks,
        "con_embedding": con_vec,
        "sin_chunks": sin_chunks,
        "modelo": config.MODELO,
        "indexado": indexado,
        "antiguedad_min": antiguedad,
        "estado": estado,
    }
    if args.json:
        _json(datos)
        return
    print(
        f"notas={datos['notas']} chunks={datos['chunks']} "
        f"con_embedding={datos['con_embedding']} sin_chunks={datos['sin_chunks']} "
        f"estado={estado}\n"
        f"vault={datos['vault']}\nindice={datos['indice']}"
    )
    if indexado:
        print(f"indexado={indexado} (hace {antiguedad} min)")


def cmd_graph(args: argparse.Namespace) -> None:
    from . import graph

    sys.argv = ["graph", *args.resto]
    graph.main()


def cmd_link(args: argparse.Namespace) -> None:
    from . import link

    sys.argv = ["link", *args.resto]
    link.main()


def cmd_read(args: argparse.Namespace) -> None:
    print(vault.read_note(args.path), end="")


def cmd_write(args: argparse.Namespace) -> None:
    print(vault.write_note(args.path, _pedir_contenido(args)))
    if not args.no_reindex:
        _encolar_reindex()


def cmd_edit(args: argparse.Namespace) -> None:
    viejo = args.old_flag if args.old_flag is not None else args.old
    if args.op == "replace" and not viejo:
        raise SystemExit(
            "replace necesita el texto viejo: obsn edit <path> replace <nuevo> --old <viejo>"
        )
    contenido = _pedir_contenido(args)
    print(vault.edit_note(args.path, args.op, contenido, old=viejo))
    if not args.no_reindex:
        _encolar_reindex()


def cmd_delete(args: argparse.Namespace) -> None:
    if not _confirmar(f"¿Borrar {args.path}?", args):
        print("cancelado")
        return
    print(vault.delete_note(args.path, hard=args.hard))
    if not args.no_reindex:
        _encolar_reindex()


def cmd_move(args: argparse.Namespace) -> None:
    print(vault.move_note(args.desde, args.hasta))
    if not args.no_reindex:
        _encolar_reindex()


def cmd_list(args: argparse.Namespace) -> None:
    if args.limit is not None and args.limit < 0:
        raise SystemExit("--limit no puede ser negativo (0 o sin --limit = todas)")
    notas = vault.list_notes(folder=args.folder, limit=args.limit, tag=args.tag)
    if args.json:
        _json(notas)
        return
    for n in notas:
        print(n["name"])


def cmd_folders(args: argparse.Namespace) -> None:
    print(vault.list_folders())


def cmd_tags(args: argparse.Namespace) -> None:
    print(vault.list_tags())


def cmd_meta(args: argparse.Namespace) -> None:
    print(vault.get_note_metadata(args.path))


# --------------------------------------------------------------------- parser


def construir_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="obsn",
        description="Vault de Obsidian: RAG semántico + operaciones de archivo.",
    )
    try:
        from importlib.metadata import version as _v
        ap.add_argument("--version", action="version", version=f"obsn {_v('obsidian-rag')}")
    except Exception:
        ap.add_argument("--version", action="version", version="obsn (versión desconocida)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rag", help="búsqueda semántica")
    p.add_argument("query")
    p.add_argument("n", nargs="?", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rag)

    p = sub.add_parser("search", aliases=["search_notes"], help="búsqueda léxica por contenido")
    p.add_argument("terms", nargs="+")
    p.add_argument("--folder")
    p.add_argument("--tag")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("reindex", help="construir/actualizar el índice")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("status", help="estado del índice")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("upgrade", help="actualizar el paquete desde su repo git")
    p.add_argument("--check", action="store_true",
                   help="sólo informar: 0 al día, 10 hay actualización, 1 error")
    p.add_argument("--how", action="store_true", help="explicar el procedimiento")
    p.add_argument("--no-reindex", action="store_true", help="no reindexar después de actualizar")
    p.set_defaults(func=cmd_upgrade)

    p = sub.add_parser("graph", help="auditoría del grafo de wikilinks")
    p.add_argument("resto", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("link", help="propone/aplica wikilinks por similitud")
    p.add_argument("resto", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("read", aliases=["read_note"], help="leer una nota")
    p.add_argument("path")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("write", aliases=["write_note"], help="crear/sobreescribir una nota")
    p.add_argument("path")
    p.add_argument("content", nargs="?")
    p.add_argument("--file")
    p.add_argument("--no-reindex", action="store_true",
                   help="no reindexar en segundo plano después de escribir")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("edit", aliases=["edit_note"], help="append | prepend | replace")
    p.add_argument("path")
    p.add_argument("op", choices=["append", "prepend", "replace"])
    p.add_argument("content", nargs="?")
    p.add_argument("old", nargs="?", help="texto viejo a reemplazar (o usá --old)")
    p.add_argument("--old", dest="old_flag",
                   help="texto viejo a reemplazar; pensado para combinar con --file")
    p.add_argument("--file")
    p.add_argument("--no-reindex", action="store_true",
                   help="no reindexar en segundo plano después de editar")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("delete", aliases=["delete_note"], help="borrar (a la papelera)")
    p.add_argument("path")
    p.add_argument("--hard", action="store_true", help="borrar de verdad (sin papelera)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--no-reindex", action="store_true",
                   help="no reindexar en segundo plano después de borrar")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("move", aliases=["move_note"], help="mover o renombrar")
    p.add_argument("desde")
    p.add_argument("hasta")
    p.add_argument("--no-reindex", action="store_true",
                   help="no reindexar en segundo plano después de mover")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("list", aliases=["list_notes"], help="listar notas")
    p.add_argument("folder", nargs="?")
    p.add_argument("--limit", type=int)
    p.add_argument("--tag")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("folders", aliases=["list_folders"], help="carpetas con conteo")
    p.set_defaults(func=cmd_folders)

    p = sub.add_parser("tags", aliases=["list_tags"], help="tags del vault")
    p.set_defaults(func=cmd_tags)

    p = sub.add_parser("meta", aliases=["get_note_metadata"], help="frontmatter, tags, backlinks")
    p.add_argument("path")
    p.set_defaults(func=cmd_meta)

    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = construir_parser()
    # `graph` y `link` tienen su propio argparse: sus flags no están declarados acá, y con
    # `REMAINDER` un `--stats` inicial lo rechaza el parser de arriba. Se aceptan los
    # sobrantes y se pasan al módulo, que es quien valida.
    args, sobrantes = ap.parse_known_args(argv)
    if sobrantes:
        if hasattr(args, "resto"):
            args.resto = [*sobrantes, *args.resto]
        else:
            ap.error(f"argumentos no reconocidos: {' '.join(sobrantes)}")
    try:
        args.func(args)
    except vault.FueraDelVault as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (FileNotFoundError, FileExistsError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
