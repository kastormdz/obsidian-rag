---
name: vault
description: Search, read and write in the user's Obsidian vault through the vault_* tools.
---

# The user's vault

An Obsidian vault indexed with obsidian-rag. The tools wrap the `obsn` binary: they run the
hybrid search, read and write the `.md` files, and keep the index fresh.

## The order that works

1. **`vault_search`** before answering anything that could be in their notes. It is a primary
   source, not a last resort: the vault holds infra, finances, config, security and personal
   life.
2. **`vault_read`** on the hit you care about. The search snippet is a passage, not the note:
   to quote or edit, read the whole note.
3. Only then answer or write.

## Searching well

- Start with `hybrid`. It understands paraphrases and returns each passage's heading.
- If `hybrid` returns nothing, switch to `mode: exact` with a word you know is in the text (a
  service name, a command, an acronym). Substring matching does not forgive.
- If that also fails, **`vault_status`**: confirm which vault and which index it is pointing
  at, and how many notes it sees. An index from another vault explains every empty result.
- `folder` and `tag` in `hybrid`: `folder` filters by prefix; `tag` is rejected because the
  index does not store tags.

## Freshness: do not cite a stale note as if it were current

Each hit may carry `stale`: `false` (still valid), `true` (past its `stale_after`) or `null`
(the note declares no date). When it is `true`:

- say **when it expired** when citing it ("this is from August, worth verifying"), and never
  present it as current state;
- if the answer has to be true today, run the search again with `fresh_only: true`;
- if the fact no longer holds, offer to fix the note instead of repeating it.

`null` is not "fresh": it is "nobody declared". Do not present it as a guarantee.

## Writing without dirtying the vault

- **Search before creating.** If a note on the same subject already exists, it belongs inside
  it with `mode: append`, not as a parallel note with a new name.
- Paths are relative to the vault root and include the folder: `01-Technical/docker.md`. No
  leading slash.
- `mode: create` overwrites the whole file. To add without losing anything: `append` or
  `prepend`. To change one sentence: `replace` with the exact text in `old`.
- Do not invent new folders without checking `vault_list` with `mode: folders`: the vault
  already has a structure, and misplaced notes do not show up in the user's indexes.

## Deleting and moving

- `vault_delete` goes to `.trash` by default: it is recoverable, but still tell the user when
  you delete something they did not ask to delete.
- `permanent: true` cannot be undone and **requires the user's approval**: the plugin stops it
  with a `pre_tool_call` hook because it is the only irreversible tool. If the user approves,
  continue; if not, leave it in the trash. Do not look for a way around it (delete and
  recreate, or `--hard` from a terminal): the guard is there for a reason.
- `vault_move` never overwrites: if the destination exists, it fails. Rename on purpose.

## After writing

The index refreshes on its own, in the background, within a couple of seconds. No manual
reindex, and no need to tell the user about it.
