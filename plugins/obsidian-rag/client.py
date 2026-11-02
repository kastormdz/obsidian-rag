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
from typing import Any, Sequence

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
            "obsn is not in PATH. Install obsidian-rag (pip install -e .) or point "
            "plugins.entries.obsidian-rag.obsn at the binary."
        )
    return path


# Only what obsn needs to find its binary, its vault and its caches. The full Hermes
# environment carries provider keys and gateway tokens, and a child process that does not
# need them should not be able to read them.
_ENV_PASSTHROUGH = (
    "PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
)
_ENV_PREFIXES = ("OBS_RAG_", "XDG_")


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k in _ENV_PASSTHROUGH or k.startswith(_ENV_PREFIXES)}
    if _CFG["vault"]:
        env["OBS_RAG_VAULT"] = _CFG["vault"]
    return env


def run(sub: str, *args: str, flags: Sequence[str] = (), want_json: bool = False,
        stdin: str | None = None) -> Any:
    """Run ``obsn <sub> [flags] [--] <args>`` and return parsed JSON (``want_json``) or stdout.

    Positional arguments always go after ``--``, so a query like ``--force`` or a path
    starting with ``-`` is not read as a CLI option.
    """
    cmd = [binary(), sub]
    if want_json:
        cmd.append("--json")
    cmd.extend(flags)
    if args:
        cmd.append("--")
        cmd.extend(args)
    try:
        proc = subprocess.run(
            cmd, input=stdin, capture_output=True, text=True,
            timeout=_CFG["timeout"], env=_env(),
        )
    except FileNotFoundError:
        raise ObsnError(f"could not execute {cmd[0]!r}") from None
    except subprocess.TimeoutExpired:
        raise ObsnError(
            f"obsn {sub} took longer than {_CFG['timeout']} s; raise plugins.entries."
            "obsidian-rag.timeout if the vault is large"
        ) from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ObsnError(detail[:800] or f"obsn {sub} exited with {proc.returncode}")
    if not want_json:
        return proc.stdout
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise ObsnError(f"obsn returned something that is not JSON: {proc.stdout[:200]!r}") from None


def with_content_file(content: str) -> str:
    """Write ``content`` to a temp file and return its path.

    ``obsn write`` takes the body as a positional; a multi-line or ``-``-leading body is
    safer through ``--file``, and argv has a ~2 MB ceiling that a long note can hit.
    """
    fd, path = tempfile.mkstemp(prefix="obsn-", suffix=".md", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path
