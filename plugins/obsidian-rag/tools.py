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
            return _fail(ValueError("empty query"))
        limit = _int(args.get("limit"), 8, 1, MAX_SEARCH)
        mode = args.get("mode") or "hybrid"
        folder = _clean(args.get("folder")).rstrip("/")
        tag = str(args.get("tag") or "").lstrip("#")
        fresh_only = bool(args.get("fresh_only"))

        if mode == "exact":
            flags = ["--limit", str(limit)]
            if folder:
                flags += ["--folder", folder]
            if tag:
                flags += ["--tag", tag]
            rows = client.run("search", query, flags=flags, want_json=True)
            hits = [
                {"path": r.get("path"), "matches": r.get("matches"),
                 "in_path": bool(r.get("path_hit")), "context": _snippet(r.get("context", "")),
                 # `exact` does not go through the index: it knows nothing about freshness,
                 # and null says exactly that.
                 "stale": None, "stale_after": None}
                for r in rows
            ]
        else:
            # In hybrid mode the tag filter does not exist: the index stores no tags, and
            # pretending it applied is worse than saying it cannot. The folder one does:
            # it is a path prefix.
            if tag:
                return _fail(ValueError(
                    "mode=hybrid does not filter by tag (the index stores no tags). Use "
                    "mode=exact with tag, or vault_list to locate the folder and filter by folder."
                ))
            # Ask for MORE chunks than wanted notes: `rag` returns chunks and dedup happens
            # here by path. Asking for a bare `limit` left the answer at 1 or 2 notes even
            # when 5 were requested, because one note can win with several chunks. With
            # `folder` more margin is needed, since the filter runs after dedup.
            pedir = min(limit * (5 if folder else 3), 60)
            flags = ["--fresh-only"] if fresh_only else []
            rows = client.run("rag", query, str(pedir), flags=flags, want_json=True)
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
                    # None = the note declares no date; that is not the same as "fresh".
                    "stale": r.get("stale"),
                    "stale_after": (r.get("stale_after") or "")[:10] or None,
                })
                if len(hits) >= limit:
                    break

        payload = {"mode": mode, "query": query, "count": len(hits), "hits": hits}
        if folder:
            payload["folder"] = folder
        if fresh_only:
            payload["fresh_only"] = True
        vencidas = sum(1 for h in hits if h.get("stale"))
        if vencidas:
            payload["stale_count"] = vencidas
            payload["notice"] = (
                f"{vencidas} of {len(hits)} results are STALE according to their stale_after. "
                "State the date when citing them, or search again with fresh_only=true."
            )
        if not hits:
            payload["note"] = (
                "No results. Try mode=exact with a literal term that appears in the vault, "
                "or vault_status if you suspect the index is stale."
            )
        return _ok(**payload)
    except Exception as exc:  # noqa: BLE001 - a tool must never raise
        return _fail(exc)


# --- read ---------------------------------------------------------------------------

def read(args: dict, **kwargs) -> str:
    try:
        path = _clean(args.get("path"))
        if not path:
            return _fail(ValueError("empty path"))
        text = client.run("read", path)
        truncated = len(text) > MAX_CONTENT
        return _ok(path=path, chars=len(text), truncated=truncated,
                   content=text[:MAX_CONTENT] + ("\n\n[… note truncated]" if truncated else ""))
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


# --- write --------------------------------------------------------------------------

def write(args: dict, **kwargs) -> str:
    tmp = None
    try:
        path = _clean(args.get("path"))
        content = args.get("content")
        if not path:
            return _fail(ValueError("empty path"))
        if content is None:
            return _fail(ValueError("missing content"))
        mode = args.get("mode") or "create"

        if mode == "create":
            tmp = client.with_content_file(content)
            out = client.run("write", path, flags=("--file", tmp))
        else:
            if mode == "replace" and not args.get("old"):
                return _fail(ValueError("mode=replace needs `old`"))
            tmp = client.with_content_file(content)
            if mode == "replace":
                # `--old` is a flag: the body comes from --file and the old text cannot be a
                # free positional (argparse would read it as the content).
                out = client.run("edit", path, "replace",
                                 flags=("--file", tmp, "--old", str(args["old"])))
            else:
                out = client.run("edit", path, mode, flags=("--file", tmp))

        return _ok(path=path, mode=mode, output=(out or "").strip()[:400],
                   reindex="in the background; the index catches up in ~2 s")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


# --- list ---------------------------------------------------------------------------

def list_(args: dict, **kwargs) -> str:
    try:
        mode = args.get("mode") or "notes"
        limit = _int(args.get("limit"), 40, 1, 200)

        # folders/tags have --json: parsing the human table with a regex broke silently the
        # day the columns changed, and that is exactly what the skill forbids.
        if mode == "folders":
            rows = client.run("folders", want_json=True)
            return _ok(mode=mode, folders=rows[:limit])
        if mode == "tags":
            rows = client.run("tags", want_json=True)
            return _ok(mode=mode, tags=rows[:limit])

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
            return _fail(ValueError("both `from` and `to` are required"))
        out = client.run("move", src, dst)
        return _ok(**{"from": src, "to": dst}, output=(out or "").strip()[:400])
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


def delete(args: dict, **kwargs) -> str:
    try:
        path = _clean(args.get("path"))
        if not path:
            return _fail(ValueError("empty path"))
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
