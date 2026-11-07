#!/usr/bin/env python3
"""obsn — one command to read, write and search an Obsidian vault.

The long names are the MCP standard ones (`read_note`, `write_note`, …) and the short ones
are aliases, so the same tool works from an agent and from a terminal.

    obsn rag "query"             semantic search (needs the index)
    obsn search "text"           lexical search by content (no model)
    obsn reindex [--force]       build/update the index
    obsn status                  index state
    obsn upgrade [--check]       update the package from its git repo

    obsn read  <path>            read a note
    obsn write <path>            create/overwrite (content via stdin or --file)
    obsn edit  <path> <op>       append | prepend | replace
    obsn delete <path>           to the trash (--hard to delete for real)
    obsn move  <src> <dst>       move or rename
    obsn list  [folder]          list notes
    obsn folders                 folders with note counts
    obsn tags                    vault tags
    obsn meta  <path>            frontmatter, tags, backlinks

Environment variables: OBS_RAG_VAULT, OBS_RAG_INDEX, OBS_RAG_CACHE, OBS_RAG_MODEL.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import subprocess
import sys
from typing import Any, Sequence

from . import vault


def _diagnostico_indice(exc: BaseException, db: object) -> str:
    """Actionable message for a SQLite error: schema problem vs access problem.

    The old code said "incomplete index, run reindex --force" for EVERY OperationalError.
    That is right for a missing table and wrong for "unable to open database file": no
    reindex fixes a permission, a lock or a deleted volume.
    """
    texto = str(exc).lower()
    if "no such table" in texto or "no such column" in texto:
        return f"incomplete index at {db} ({exc}): run `obsn reindex --force`"
    return (
        f"could not read the index at {db} ({exc}): check permissions and that no other "
        "process is holding it; if the file is corrupt, move it away and run `obsn reindex`"
    )


def _encolar_reindex() -> None:
    """Fire a background reindex and move on.

    fork+exec of a worker that debounces and coalesces (see rag/reindex_worker.py): the
    user does not pay the ~1.5 s of loading the model. Turn it off with `--no-reindex` or
    with `OBS_RAG_NO_ASYNC=1` (scripts that write in bulk and reindex once).

    The dirty marker is written BEFORE spawning. If the write lands after the worker already
    listed the vault, the marker is still there and the worker runs one more pass; if the
    spawn fails, the next write (or the systemd timer in contrib/systemd/) picks it up.
    """
    if os.environ.get("OBS_RAG_NO_ASYNC"):
        return
    try:
        from .reindex_worker import marcar_sucio

        marcar_sucio()
    except Exception:  # no fcntl outside POSIX, or a read-only state dir: not fatal
        pass
    try:
        subprocess.Popen(
            [sys.executable, "-m", "rag.reindex_worker"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,       # so the terminal's Ctrl-C does not kill it
            close_fds=True,
        )
    except OSError:
        pass


# --------------------------------------------------------------------- output


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
    raise SystemExit("missing content: use --file <path> or pipe it through stdin")


def _confirmar(pregunta: str, opciones: argparse.Namespace) -> bool:
    if getattr(opciones, "yes", False):
        return True
    if not sys.stdin.isatty():
        raise SystemExit(f"{pregunta}\nNo terminal to confirm: repeat with --yes.")
    respuesta = input(f"{pregunta} [y/N] ").strip().lower()
    return respuesta in {"y", "yes", "s", "si", "sí"}


# --------------------------------------------------------------------- commands


def cmd_rag(args: argparse.Namespace) -> None:
    from . import index

    resultados = index.search(args.query, args.n, fresh_only=args.fresh_only)
    if args.json:
        _json(resultados)
        return
    if not resultados:
        print("(no results: try other words or run `obsn reindex`)")
        return
    for i, r in enumerate(resultados, 1):
        marca = ""
        if r.get("stale"):
            dias = ""
            try:
                from datetime import datetime, timezone
                dias = f", {((datetime.now(timezone.utc) - datetime.fromisoformat(r['stale_after'])).days)} days ago"
            except (TypeError, ValueError):
                pass
            marca = f"  [STALE{dias}]"
        print(f"{i}. {r['path']}  ·  {r.get('heading', '')}  ·  "
              f"score {r.get('score', 0):.3f}{marca}")


def cmd_search(args: argparse.Namespace) -> None:
    resultados = vault.search_notes(
        args.terms, folder=args.folder, tag=args.tag, limit=args.limit
    )
    if args.json:
        _json(resultados)
        return
    if not resultados:
        print("(no results)")
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
    import time
    from datetime import datetime, timezone

    from . import config

    if not config.DB.exists():
        raise SystemExit(f"no index at {config.DB}: run `obsn reindex`")
    try:
        conexion = sqlite3.connect(config.DB)
    except sqlite3.Error as exc:
        raise SystemExit(_diagnostico_indice(exc, config.DB)) from None
    with contextlib.closing(conexion) as c:
        # `notes` is the source of truth for what is indexed. Counting distinct paths in
        # `chunks` undercounts: a note with no body (frontmatter only, or <20 chars) produces
        # no chunks and vanished from the total (measured: 193 vs 196 real).
        try:
            notas = c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            con_vec = c.execute(
                "SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]
            sin_chunks = c.execute(
                "SELECT COUNT(*) FROM notes WHERE COALESCE(n_chunks, 0) = 0"
            ).fetchone()[0]
        except sqlite3.Error as exc:
            # Old format (no `notes` table) or a file that is not a DB: without this the
            # user sees an sqlite traceback instead of what to do.
            raise SystemExit(_diagnostico_indice(exc, config.DB)) from None
        try:
            vigencias = [r[0] for r in c.execute("SELECT stale_after FROM notes")]
        except sqlite3.OperationalError:      # index from before freshness existed
            vigencias = []
        # When it was indexed and what state it ended in: the data was already in `meta`, but
        # was not exposed, so an agent could not tell a fresh index from a stale one, nor a
        # failed build from one that never ran. The table is `meta(k, v)`: with invented names
        # the SELECT raises OperationalError and the `except` turns it into "no data".
        try:
            filas = dict(c.execute(
                "SELECT k, v FROM meta WHERE k IN ('indexado', 'estado', 'embedder')"))
        except sqlite3.OperationalError:
            filas = {}
    # Vectores que faltan: un índice construido sin fastembed los deja en NULL y la
    # búsqueda cae a TF-IDF sin decirlo. `embedded` ya existía; esto lo hace explícito.
    faltan_vec = chunks - con_vec
    embedder = filas.get("embedder")
    if not embedder:
        embedder = "ok" if chunks == con_vec else "unknown"
    try:
        import importlib.util

        modelo_disponible = importlib.util.find_spec("fastembed") is not None
    except (ImportError, ValueError):
        modelo_disponible = False
    indexado = antiguedad = None
    if filas.get("indexado"):
        try:
            sello = float(filas["indexado"])
        except (TypeError, ValueError):
            sello = 0.0
        if sello:
            indexado = datetime.fromtimestamp(sello).astimezone().isoformat(timespec="seconds")
            antiguedad = max(0, int((time.time() - sello) / 60))
    estado = filas.get("estado") or "unknown"
    # Freshness is counted in Python: the dates are ISO UTC and comparing strings with
    # different offsets would give a false order.
    vencidas = sin_fecha = por_vencer = 0
    ahora = datetime.now(timezone.utc)
    for iso in vigencias:
        if not iso:
            sin_fecha += 1
            continue
        try:
            vence = datetime.fromisoformat(iso)
        except ValueError:
            sin_fecha += 1
            continue
        if ahora >= vence:
            vencidas += 1
        elif (vence - ahora).days < 7:
            por_vencer += 1
    datos = {
        "index": str(config.DB),
        "vault": str(config.VAULT),
        "notes": notas,
        "chunks": chunks,
        "embedded": con_vec,
        "without_chunks": sin_chunks,
        "stale": vencidas,
        "undated": sin_fecha,
        "expiring_7d": por_vencer,
        "model": config.MODELO,
        "model_available": modelo_disponible,
        "embedder": embedder,
        "vectors_missing": faltan_vec,
        "indexed_at": indexado,
        "age_min": antiguedad,
        "state": estado,
    }
    if faltan_vec:
        print(
            f"[warn] {faltan_vec} of {chunks} chunks have no embedding: semantic search is"
            " off (install fastembed and run `obsn reindex`)",
            file=sys.stderr, flush=True,
        )
    if args.json:
        _json(datos)
        return
    print(
        f"notes={datos['notes']} chunks={datos['chunks']} "
        f"embedded={datos['embedded']} without_chunks={datos['without_chunks']} "
        f"stale={vencidas} undated={sin_fecha} state={estado} embedder={embedder}\n"
        f"vault={datos['vault']}\nindex={datos['index']}"
    )
    if indexado:
        print(f"indexed_at={indexado} ({antiguedad} min ago)")
    if por_vencer:
        print(f"expiring in 7 days: {por_vencer}")


def cmd_stale(args: argparse.Namespace) -> None:
    """List the notes whose `stale_after` has passed. With no index it does not guess: it says so."""
    from datetime import datetime, timezone

    from . import config
    from .vigencia import fecha_local

    if not config.DB.exists():
        raise SystemExit(f"no index at {config.DB}: run `obsn reindex`")
    ahora = datetime.now(timezone.utc)
    filas: list[dict] = []
    try:
        conexion = sqlite3.connect(config.DB)
    except sqlite3.Error as exc:
        raise SystemExit(_diagnostico_indice(exc, config.DB)) from None
    with contextlib.closing(conexion) as c:
        try:
            pares = list(c.execute(
                "SELECT path, stale_after FROM notes WHERE stale_after IS NOT NULL"))
            total = c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        except sqlite3.Error as exc:
            # A missing column is the old index (reindex fixes it); anything else is access.
            if "no such column" in str(exc).lower() or "no such table" in str(exc).lower():
                raise SystemExit(
                    "the index has no freshness column: run `obsn reindex`") from None
            raise SystemExit(_diagnostico_indice(exc, config.DB)) from None
    for path, iso in pares:
        try:
            vence = datetime.fromisoformat(iso)
        except ValueError:
            continue
        if ahora >= vence:
            dias = (ahora - vence).days
            if args.days is None or dias >= args.days:
                filas.append({"path": path, "stale_after": fecha_local(iso), "days": dias})
    filas.sort(key=lambda r: -r["days"])
    if args.json:
        _json({"stale": filas, "total_notes": total})
        return
    if not filas:
        print(f"(no stale notes out of {total})")
        return
    for f in filas:
        print(f"{f['stale_after']}  {f['days']:>4} days ago  {f['path']}")
    print(f"\n{len(filas)} stale out of {total} notes")


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
            "replace needs the old text: obsn edit <path> replace <new> --old <old>"
        )
    contenido = _pedir_contenido(args)
    print(vault.edit_note(args.path, args.op, contenido, old=viejo))
    if not args.no_reindex:
        _encolar_reindex()


def cmd_delete(args: argparse.Namespace) -> None:
    if not _confirmar(f"Delete {args.path}?", args):
        print("cancelled")
        return
    print(vault.delete_note(args.path, hard=args.hard))
    if not args.no_reindex:
        _encolar_reindex()


def cmd_move(args: argparse.Namespace) -> None:
    print(vault.move_note(args.src, args.dst))
    if not args.no_reindex:
        _encolar_reindex()


def cmd_list(args: argparse.Namespace) -> None:
    if args.limit is not None and args.limit < 0:
        raise SystemExit("--limit cannot be negative (0 or no --limit = all)")
    notas = vault.list_notes(folder=args.folder, limit=args.limit, tag=args.tag)
    if args.json:
        _json(notas)
        return
    for n in notas:
        print(n["name"])


def cmd_folders(args: argparse.Namespace) -> None:
    if args.json:
        _json(vault.folder_counts())
        return
    print(vault.list_folders())


def cmd_tags(args: argparse.Namespace) -> None:
    if args.json:
        _json(vault.tag_counts())
        return
    print(vault.list_tags())


def cmd_meta(args: argparse.Namespace) -> None:
    print(vault.get_note_metadata(args.path))


# --------------------------------------------------------------------- parser


def construir_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="obsn",
        description="Obsidian vault: semantic RAG + file operations.",
    )
    try:
        from importlib.metadata import version as _v
        ap.add_argument("--version", action="version", version=f"obsn {_v('obsidian-rag')}")
    except Exception:
        ap.add_argument("--version", action="version", version="obsn (unknown version)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rag", help="semantic search")
    p.add_argument("query")
    p.add_argument("n", nargs="?", type=int, default=5)
    p.add_argument("--fresh-only", action="store_true",
                   help="only fresh notes (drop the ones past their stale_after)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rag)

    p = sub.add_parser("search", aliases=["search_notes"], help="lexical search by content")
    p.add_argument("terms", nargs="+")
    p.add_argument("--folder")
    p.add_argument("--tag")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("reindex", help="build/update the index")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("status", help="index state")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("stale", help="notes past their stale_after")
    p.add_argument("--days", type=int, default=None,
                   help="only the ones that expired N days ago or more")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stale)

    p = sub.add_parser("upgrade", help="update the package from its git repo")
    p.add_argument("--check", action="store_true",
                   help="report only: 0 up to date, 10 update available, 1 error")
    p.add_argument("--how", action="store_true", help="explain the procedure")
    p.add_argument("--no-reindex", action="store_true", help="do not reindex after updating")
    p.set_defaults(func=cmd_upgrade)

    p = sub.add_parser("graph", help="wikilink graph audit")
    p.add_argument("resto", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("link", help="propose/apply wikilinks by similarity")
    p.add_argument("resto", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("read", aliases=["read_note"], help="read a note")
    p.add_argument("path")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("write", aliases=["write_note"], help="create/overwrite a note")
    p.add_argument("path")
    p.add_argument("content", nargs="?")
    p.add_argument("--file")
    p.add_argument("--no-reindex", action="store_true",
                   help="do not reindex in the background after writing")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("edit", aliases=["edit_note"], help="append | prepend | replace")
    p.add_argument("path")
    p.add_argument("op", choices=["append", "prepend", "replace"])
    p.add_argument("content", nargs="?")
    p.add_argument("old", nargs="?", help="old text to replace (or use --old)")
    p.add_argument("--old", dest="old_flag",
                   help="old text to replace; meant to be combined with --file")
    p.add_argument("--file")
    p.add_argument("--no-reindex", action="store_true",
                   help="do not reindex in the background after editing")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("delete", aliases=["delete_note"], help="delete (to the trash)")
    p.add_argument("path")
    p.add_argument("--hard", action="store_true", help="delete for real (no trash)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--no-reindex", action="store_true",
                   help="do not reindex in the background after deleting")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("move", aliases=["move_note"], help="move or rename")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--no-reindex", action="store_true",
                   help="do not reindex in the background after moving")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("list", aliases=["list_notes"], help="list notes")
    p.add_argument("folder", nargs="?")
    p.add_argument("--limit", type=int)
    p.add_argument("--tag")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("folders", aliases=["list_folders"], help="folders with counts")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_folders)

    p = sub.add_parser("tags", aliases=["list_tags"], help="vault tags")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tags)

    p = sub.add_parser("meta", aliases=["get_note_metadata"], help="frontmatter, tags, backlinks")
    p.add_argument("path")
    p.set_defaults(func=cmd_meta)

    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = construir_parser()
    # `graph` and `link` have their own argparse: their flags are not declared here, and with
    # `REMAINDER` a leading `--stats` is rejected by the parser above. Leftovers are accepted
    # and passed to the module, which is the one that validates.
    args, sobrantes = ap.parse_known_args(argv)
    if sobrantes:
        if hasattr(args, "resto"):
            args.resto = [*sobrantes, *args.resto]
        else:
            ap.error(f"unrecognized arguments: {' '.join(sobrantes)}")
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
