"""Tool handlers. Contract: ``handler(args: dict, **kwargs) -> str``, always JSON, never raise."""

from __future__ import annotations

import json
import os
import re

from . import client

MAX_SEARCH = 25
SNIPPET = 400
MAX_CONTENT = 40_000


def _ok(**fields) -> str:
    return json.dumps({"ok": True, **fields}, ensure_ascii=False)


def _fail(exc: Exception) -> str:
    return json.dumps({"ok": False, "error": str(exc)[:800]}, ensure_ascii=False)


def _int(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def _snippet(text: str) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    return flat[:SNIPPET] + ("…" if len(flat) > SNIPPET else "")


def _clean(path: object) -> str:
    return str(path or "").strip().lstrip("/")


# --- search -------------------------------------------------------------------------

def search(args: dict, **kwargs) -> str:
    try:
        query = (args.get("query") or "").strip()
        if not query:
            return _fail(ValueError("query vacía"))
        limit = _int(args.get("limit"), 8, 1, MAX_SEARCH)
        mode = args.get("mode") or "hybrid"
        folder = _clean(args.get("folder")).rstrip("/")
        tag = str(args.get("tag") or "").lstrip("#")

        if mode == "exact":
            flags = ["--limit", str(limit)]
            if folder:
                flags += ["--folder", folder]
            if tag:
                flags += ["--tag", tag]
            rows = client.run("search", query, flags=flags, want_json=True)
            hits = [
                {"path": r.get("path"), "matches": r.get("matches"),
                 "in_path": bool(r.get("path_hit")), "context": _snippet(r.get("context", ""))}
                for r in rows
            ]
        else:
            # En híbrido el filtro por tag no existe: el índice no guarda tags, y fingir
            # que se aplicó es peor que decir que no. El de carpeta sí: es prefijo de path.
            if tag:
                return _fail(ValueError(
                    "mode=hybrid no filtra por tag (el índice no guarda tags). Usá "
                    "mode=exact con tag, o vault_list para ubicar la carpeta y filtrá por folder."
                ))
            # Se piden MÁS chunks que notas queridas: `rag` devuelve chunks y acá se
            # deduplica por path. Pidiendo `limit` pelado, una nota con varios chunks
            # ganadores dejaba la respuesta en 1 o 2 notas aunque se pidieran 5. Con folder
            # se pide más margen, porque el filtro se aplica después de deduplicar.
            pedir = min(limit * (5 if folder else 3), 60)
            rows = client.run("rag", query, str(pedir), want_json=True)
            hits, seen = [], set()
            for r in rows:
                path = r.get("path") or ""
                if path in seen:  # one row per chunk; the model only needs the best one per note
                    continue
                if folder and not path.startswith(folder + "/"):
                    continue
                seen.add(path)
                hits.append({
                    "path": path,
                    "heading": r.get("heading") or "",
                    "score": round(float(r.get("score") or 0), 3),
                    "snippet": _snippet(r.get("text", "")),
                })
                if len(hits) >= limit:
                    break

        payload = {"mode": mode, "query": query, "count": len(hits), "hits": hits}
        if folder:
            payload["folder"] = folder
        if not hits:
            payload["note"] = (
                "Sin resultados. Probá mode=exact con un término literal que aparezca en el "
                "vault, o vault_status si sospechás que el índice está viejo."
            )
        return _ok(**payload)
    except Exception as exc:  # noqa: BLE001 - a tool must never raise
        return _fail(exc)


# --- read ---------------------------------------------------------------------------

def read(args: dict, **kwargs) -> str:
    try:
        path = _clean(args.get("path"))
        if not path:
            return _fail(ValueError("path vacío"))
        text = client.run("read", path)
        truncated = len(text) > MAX_CONTENT
        return _ok(path=path, chars=len(text), truncated=truncated,
                   content=text[:MAX_CONTENT] + ("\n\n[… nota truncada]" if truncated else ""))
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


# --- write --------------------------------------------------------------------------

def write(args: dict, **kwargs) -> str:
    tmp = None
    try:
        path = _clean(args.get("path"))
        content = args.get("content")
        if not path:
            return _fail(ValueError("path vacío"))
        if content is None:
            return _fail(ValueError("falta content"))
        mode = args.get("mode") or "create"

        if mode == "create":
            tmp = client.with_content_file(content)
            out = client.run("write", path, flags=("--file", tmp))
        else:
            if mode == "replace" and not args.get("old"):
                return _fail(ValueError("mode=replace necesita `old`"))
            tmp = client.with_content_file(content)
            if mode == "replace":
                # `--old` va como flag: el cuerpo viene de --file y el texto viejo no puede
                # ser un positional libre (argparse se lo comía como contenido).
                out = client.run("edit", path, "replace",
                                 flags=("--file", tmp, "--old", str(args["old"])))
            else:
                out = client.run("edit", path, mode, flags=("--file", tmp))

        return _ok(path=path, mode=mode, output=(out or "").strip()[:400],
                   reindex="en segundo plano; el índice se actualiza solo en ~2 s")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


# --- list ---------------------------------------------------------------------------

_COUNTED = re.compile(r"^\s*(\d+)\s+(.*)$")


def _counted_rows(text: str, limit: int, key: str) -> list[dict]:
    rows = []
    for line in (text or "").splitlines():
        m = _COUNTED.match(line)
        if m:
            rows.append({key: m.group(2).strip(), "count": int(m.group(1))})
    return rows[:limit]


def list_(args: dict, **kwargs) -> str:
    try:
        mode = args.get("mode") or "notes"
        limit = _int(args.get("limit"), 40, 1, 200)

        if mode == "folders":
            return _ok(mode=mode, folders=_counted_rows(client.run("folders"), limit, "folder"))
        if mode == "tags":
            return _ok(mode=mode, tags=_counted_rows(client.run("tags"), limit, "tag"))

        argv = ["list"]
        if args.get("folder"):
            argv.append(_clean(args["folder"]).rstrip("/"))
        flags = ["--limit", str(limit)]
        if args.get("tag"):
            flags += ["--tag", str(args["tag"]).lstrip("#")]
        rows = client.run(*argv, flags=flags, want_json=True)
        notes = [r.get("name") for r in rows if r.get("name")]
        return _ok(mode="notes", count=len(notes), notes=notes)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


# --- move / delete ------------------------------------------------------------------

def move(args: dict, **kwargs) -> str:
    try:
        src, dst = _clean(args.get("from")), _clean(args.get("to"))
        if not src or not dst:
            return _fail(ValueError("hacen falta `from` y `to`"))
        out = client.run("move", src, dst)
        return _ok(**{"from": src, "to": dst}, output=(out or "").strip()[:400])
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


def delete(args: dict, **kwargs) -> str:
    try:
        path = _clean(args.get("path"))
        if not path:
            return _fail(ValueError("path vacío"))
        permanent = bool(args.get("permanent"))
        flags = ["--yes"] + (["--hard"] if permanent else [])
        out = client.run("delete", path, flags=flags)
        return _ok(path=path, permanent=permanent, output=(out or "").strip()[:400],
                   recoverable=not permanent)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


# --- status -------------------------------------------------------------------------

def status(args: dict, **kwargs) -> str:
    try:
        data = client.run("status", want_json=True)
        data["obsn"] = client.binary()
        data["obsn_version"] = (client.run("--version") or "").strip()
        return json.dumps({"ok": True, **data}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


TOOLS = (
    {"name": "vault_search", "handler": search},
    {"name": "vault_read", "handler": read},
    {"name": "vault_write", "handler": write},
    {"name": "vault_list", "handler": list_},
    {"name": "vault_move", "handler": move},
    {"name": "vault_delete", "handler": delete},
    {"name": "vault_status", "handler": status},
)
