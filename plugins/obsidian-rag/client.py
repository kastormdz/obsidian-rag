"""Adapter over the ``obsn`` CLI: one subprocess per call.

Why a subprocess instead of importing ``rag`` into the gateway process:

- ``obsn rag`` end to end is ~73 ms, and ~24 ms of that is interpreter startup. Importing
  ``rag.index`` in-process saves those 24 ms and costs numpy + onnxruntime resident in the
  agent's process, plus a schema/version coupling between plugin and index.
- A crash in the search path cannot take the agent down with it.
- ``obsn`` stays the single API: the plugin adds no behaviour of its own, so anything the
  CLI can do (and anything added to it later) keeps working here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

DEFAULT_TIMEOUT = 30

_CFG: dict[str, Any] = {"obsn": None, "vault": None, "timeout": DEFAULT_TIMEOUT}


class ObsnError(RuntimeError):
    """Anything that stops ``obsn`` from answering: missing binary, non-zero exit, bad JSON."""


def configure(ctx) -> None:
    """Read the plugin's settings namespace. Tolerates a ctx without them (tests, doctor)."""
    _CFG["obsn"] = _setting(ctx, "obsn") or None
    _CFG["vault"] = _setting(ctx, "vault") or None
    raw = _setting(ctx, "timeout")
    try:
        _CFG["timeout"] = max(5, int(raw)) if raw else DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        _CFG["timeout"] = DEFAULT_TIMEOUT


def _setting(ctx, key: str) -> str:
    try:
        value = ctx.get_config(key, "")
    except Exception:
        return ""
    return str(value or "").strip()


def binary() -> str:
    path = _CFG["obsn"] or shutil.which("obsn")
    if not path:
        raise ObsnError(
            "obsn no está en PATH. Instalá obsidian-rag (pip install -e .) o apuntá "
            "plugins.entries.obsidian-rag.obsn a la ruta del binario."
        )
    return path


def run(*argv: str, want_json: bool = False, stdin: str | None = None) -> Any:
    """Run ``obsn <argv>`` and return parsed JSON (``want_json``) or stdout text."""
    cmd = [binary(), *argv]
    if want_json:
        cmd.append("--json")
    env = dict(os.environ)
    if _CFG["vault"]:
        env["OBS_RAG_VAULT"] = _CFG["vault"]
    try:
        proc = subprocess.run(
            cmd, input=stdin, capture_output=True, text=True,
            timeout=_CFG["timeout"], env=env,
        )
    except FileNotFoundError:
        raise ObsnError(f"no se pudo ejecutar {cmd[0]!r}") from None
    except subprocess.TimeoutExpired:
        raise ObsnError(
            f"obsn {argv[0]} pasó de {_CFG['timeout']} s; subí plugins.entries."
            "obsidian-rag.timeout si el vault es grande"
        ) from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ObsnError(detail[:800] or f"obsn {argv[0]} salió con {proc.returncode}")
    if not want_json:
        return proc.stdout
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise ObsnError(f"obsn devolvió algo que no es JSON: {proc.stdout[:200]!r}") from None


def with_content_file(content: str) -> str:
    """Write ``content`` to a temp file and return its path.

    ``obsn write`` takes the body as a positional; a multi-line or ``-``-leading body is
    safer through ``--file``, and argv has a ~2 MB ceiling that a long note can hit.
    """
    fd, path = tempfile.mkstemp(prefix="obsn-", suffix=".md", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path
