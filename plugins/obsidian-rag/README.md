# Hermes plugin for obsidian-rag

Seven tools so a Hermes agent can search, read and write in the vault, with `obsn` as the
engine. No Python dependencies: the plugin is an adapter over the binary.

```
plugins/obsidian-rag/
├── plugin.yaml      # manifest (tools, config, version)
├── __init__.py      # register(ctx)
├── schemas.py       # what the model reads
├── tools.py         # handlers
├── client.py        # subprocess → obsn
└── skills/vault/SKILL.md   # when to use each tool (loaded as obsidian-rag:vault)
```

## The tools

| Tool | What it does | Engine |
|---|---|---|
| `vault_search` | Hybrid search (`hybrid`) or substring (`exact`), with folder/tag; each hit carries `stale` and accepts `fresh_only` | `obsn rag` · `obsn search` |
| `vault_read` | Reads a whole note | `obsn read` |
| `vault_write` | Creates, appends, prepends or replaces text | `obsn write` · `obsn edit` |
| `vault_list` | Notes, folders with counts, or tags by usage | `obsn list` · `folders` · `tags` |
| `vault_move` | Moves or renames; never overwrites the destination | `obsn move` |
| `vault_delete` | To the trash, or permanent with `permanent: true` (**asks for approval**) | `obsn delete` |
| `vault_status` | Active vault, notes, chunks, model, stale count | `obsn status` |

The only irreversible tool is `vault_delete` with `permanent: true`. A `pre_tool_call` hook
returns `{"action": "approve", ...}` with `rule_key: vault_delete_permanent`, so the user sees
the path before something is deleted for good. Normal deletes go to `.trash` and do not ask.

## Install

```bash
# from the repo (recommended: pinned and updatable)
hermes plugins install kastormdz/obsidian-rag#plugins/obsidian-rag --no-enable
hermes plugins enable obsidian-rag
```

For development, without installing:

```bash
ln -s "$PWD/plugins/obsidian-rag" ~/.hermes/plugins/obsidian-rag
hermes plugins doctor plugins/obsidian-rag --ci   # validates without installing
```

The tools show up in the next session (`/new`), not in the one currently running.

## Configure

```yaml
plugins:
  entries:
    obsidian-rag:
      obsn: ""            # empty = look it up in PATH
      vault: ""           # folder containing .obsidian/; empty = OBS_RAG_VAULT
      timeout: 30         # seconds per call
```

## Why a subprocess instead of importing `rag`

Measured on this machine:

| | |
|---|---|
| `obsn rag` end to end | **73 ms** |
| of which, interpreter startup | 24 ms |
| `import rag.index` inside the gateway process | 33 ms **+ numpy/onnxruntime resident** |

Saving 24 ms does not pay for putting numpy and a 241 MB ONNX model inside the agent's process,
nor for coupling the plugin version to the index schema. It also means a broken search path
cannot take the agent down, and `obsn` stays the single API: anything added to the CLI works
here without touching the plugin.

The child gets a **minimal environment** (PATH, HOME, TMPDIR, locale, TZ and the `OBS_RAG_*` /
`XDG_*` variables): provider keys and gateway tokens do not travel to a process that does not
need them.

## Pitfalls that cost blood

- **`obsn edit <path> replace "" <old> --file <tmp>`**: the body comes from `--file`, but
  `content` has to exist as a positional (even empty) because `old` is the next positional.
  With `--file` first, argparse eats the old text as content and the replacement never happens.
  It is in `tools.py::write` with the comment.
- **`OBS_RAG_VAULT` exported in the shell** points every tool at another vault, even when you
  want the default one. Same thing `bench/gate.sh` documents.
- **In a 3-note vault** a paraphrase with no word in common falls below the semantic floor and
  returns nothing. That is the engine, not the plugin: in the real vault the same query returns
  5. If you write tests, use a query with a term that exists.
- **`vault_status` right after a write** may show the previous state: the reindex is async
  (~1.5 s debounce). Wait a couple of seconds before claiming it did not index.

## Verify

```bash
hermes plugins doctor plugins/obsidian-rag --ci   # manifest + registration + drift
python3 tests/e2e_plugin.py                       # 38 checks against a temporary vault
```
