"""obsidian-rag for Hermes: seven ``vault_*`` tools over the ``obsn`` CLI.

The plugin holds no index and no model. Every call is one ``obsn`` subprocess (see
``client.py`` for why), so the gateway stays free of numpy/onnxruntime and a broken search
path cannot take the agent down with it. ``obsn`` remains the single API.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import client, schemas, tools

log = logging.getLogger("obsidian-rag.hermes")

TOOLSET = "obsidian_vault"
SKILL = Path(__file__).parent / "skills" / "vault" / "SKILL.md"
RULE_KEY = "vault_delete_permanent"


def _on_pre_tool_call(tool_name: str = "", args: dict | None = None, **kwargs):
    """Ask before the only irreversible action: ``vault_delete`` with ``permanent: true``.

    Every other tool writes atomically or moves the note to the trash, so they need no
    approval. This one deletes the file for real and cannot be undone: the model should not
    decide that on its own. ``rule_key`` makes a "always" cover the following calls.
    """
    if tool_name != "vault_delete":
        return None
    params = args or {}
    if not params.get("permanent"):
        return None
    path = str(params.get("path") or "").strip() or "(no path)"
    return {
        "action": "approve",
        "message": (
            f"vault_delete PERMANENT on {path}\n"
            "Deletes the file without the trash and cannot be undone. "
            "With `permanent: false` it goes to .trash and can be recovered."
        ),
        "rule_key": RULE_KEY,
    }


def register(ctx) -> None:
    """Wire the schemas to the handlers. Called once, at startup."""
    client.configure(ctx)

    for spec in tools.TOOLS:
        name = spec["name"]
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schemas.SCHEMAS[name],
            handler=spec["handler"],
        )

    ctx.register_hook("pre_tool_call", _on_pre_tool_call)

    if SKILL.exists():
        try:
            ctx.register_skill(
                "vault", SKILL,
                description="When to use each vault tool, writing conventions, and what to do when a search finds nothing.",
            )
        except Exception as exc:  # neither a missing nor a clashing skill should disable the tools
            log.warning("obsidian-rag: could not register the skill: %s", exc)
