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

    if SKILL.exists():
        try:
            ctx.register_skill(
                "vault", SKILL,
                description="Cuándo usar cada tool del vault, convenciones de escritura y qué hacer si la búsqueda no encuentra.",
            )
        except Exception as exc:  # neither a missing nor a clashing skill should disable the tools
            log.warning("obsidian-rag: no se pudo registrar la skill: %s", exc)
