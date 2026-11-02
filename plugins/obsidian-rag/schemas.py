"""Tool schemas — this is what the model reads. The description is the whole contract."""

from __future__ import annotations

_PATH_HELP = "Path relative to the vault root, with or without .md (e.g. '01-Technical/docker.md')."

SEARCH = {
    "name": "vault_search",
    "description": (
        "Search the user's Obsidian vault. Start here before answering anything that could "
        "be in their notes. `mode: hybrid` (default) understands paraphrases and returns "
        "passages with their heading; `mode: exact` is a literal substring match, for when "
        "you know the exact word and want speed. Returns paths: read the one you need with "
        "vault_read. Each hit carries `stale` (true/false/null) when the note declares an "
        "expiry date; null means no date was declared, which is not the same as fresh."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look for, in natural language or the literal term depending on mode."},
            "limit": {"type": "integer", "description": "Maximum results (default 8, cap 25)."},
            "mode": {"type": "string", "enum": ["hybrid", "exact"], "description": "hybrid = semantic + lexical; exact = substring."},
            "folder": {"type": "string", "description": "Restrict to a folder (e.g. '03-Personal'). Works in both modes."},
            "tag": {"type": "string", "description": "mode=exact only: the index does not store tags, and hybrid rejects it."},
            "fresh_only": {"type": "boolean", "description": "mode=hybrid only: drop notes past their stale_after. Each hit reports stale=true|false|null (null = the note declares no date)."},
        },
        "required": ["query"],
    },
}

READ = {
    "name": "vault_read",
    "description": ("Read a whole note from the vault. Use it after vault_search to see the full context before quoting or editing."),
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": _PATH_HELP}},
        "required": ["path"],
    },
}

WRITE = {
    "name": "vault_write",
    "description": (
        "Create or modify a note. `mode: create` (default) writes the full content and "
        "overwrites if it exists; `append` and `prepend` add to it; `replace` swaps `old` "
        "for the new content. Before creating a new note, look for the canonical one with "
        "vault_search: if a note on the same subject exists, it belongs as a section "
        "inside it, not as a parallel note. The index refreshes in the background."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _PATH_HELP},
            "content": {"type": "string", "description": "Markdown to write."},
            "mode": {"type": "string", "enum": ["create", "append", "prepend", "replace"], "description": "create overwrites the file; append/prepend add; replace needs `old`."},
            "old": {"type": "string", "description": "mode=replace only: the exact text to replace."},
        },
        "required": ["path", "content"],
    },
}

LIST = {
    "name": "vault_list",
    "description": (
        "Explore the vault structure without searching content. `mode: notes` lists paths "
        "(optional folder and/or tag), `mode: folders` lists folders with their note count, "
        "`mode: tags` lists tags ordered by usage. Useful for deciding where something belongs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["notes", "folders", "tags"], "description": "What to list (default notes)."},
            "folder": {"type": "string", "description": "mode=notes only: folder prefix."},
            "tag": {"type": "string", "description": "mode=notes only: exact tag, without #."},
            "limit": {"type": "integer", "description": "Maximum rows (default 40)."},
        },
    },
}

MOVE = {
    "name": "vault_move",
    "description": "Move or rename a note inside the vault. It never overwrites the destination: if it exists, the call fails.",
    "parameters": {
        "type": "object",
        "properties": {
            "from": {"type": "string", "description": _PATH_HELP},
            "to": {"type": "string", "description": "New path, relative to the vault root."},
        },
        "required": ["from", "to"],
    },
}

DELETE = {
    "name": "vault_delete",
    "description": (
        "Delete a note. By default it goes to the vault's trash (.trash), so it can be "
        "recovered; `permanent: true` deletes it for real and cannot be undone."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _PATH_HELP},
            "permanent": {"type": "boolean", "description": "Real delete, no trash. Default false."},
        },
        "required": ["path"],
    },
}

STATUS = {
    "name": "vault_status",
    "description": (
        "Index status: active vault, note and chunk counts, embedding model, notes with no "
        "indexable content, how long ago it was indexed (age_min) and how many notes "
        "are stale or undated. Use it when a search returns nothing that should exist, to "
        "rule out a stale index or one pointing at another vault."
    ),
    "parameters": {"type": "object", "properties": {}},
}

SCHEMAS = {s["name"]: s for s in (SEARCH, READ, WRITE, LIST, MOVE, DELETE, STATUS)}
