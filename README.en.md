# obsidian-rag

Search and edit an [Obsidian](https://obsidian.md) vault from the terminal with a single
binary: `obsn`. It reads the notes from disk and builds a local SQLite index with embeddings.
No server and no account: the index is a file. The vault stays as markdown on your disk.

[Versión en español](README.md)

## Install

```bash
git clone https://github.com/kastormdz/obsidian-rag.git
cd obsidian-rag
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

The first search downloads the model (241 MB) into `~/.cache/fastembed`. Loading it costs
1.5 s, and it only happens when the query cannot be resolved lexically.

## Point it at your vault

By default it looks for the vault in `~/Obsidian`. If yours is there, you are done.

If it lives somewhere else, point `OBS_RAG_VAULT` at the folder that contains `.obsidian/`,
which is the vault root. It is not a subfolder and not the vault name: it is the directory.

```bash
export OBS_RAG_VAULT="$HOME/Documents/my-vault"                     # this session
echo 'export OBS_RAG_VAULT="$HOME/Documents/my-vault"' >> ~/.bashrc # permanent
```

To check it took, `obsn status` prints the vault and the index it is using:

```
$ obsn status
notas=199 chunks=7651 con_embedding=7651 sin_chunks=3
vault=/home/you/Documents/my-vault
indice=/home/you/.local/share/obsidian-rag/index.db
```

Two things people forget, and each one costs an afternoon:

- **The index belongs to one vault.** Index paths are relative to the vault root. If you
  change `OBS_RAG_VAULT`, the old index still points at notes that no longer exist: run
  `obsn reindex`, which purges the leftovers and indexes the new ones. It will take as long as
  embedding a whole vault, because to the index every note is new.
- **One vault, one index.** For two vaults, give each one its own file:
  `OBS_RAG_VAULT=~/other OBS_RAG_INDEX=~/.local/share/obsidian-rag/other.db obsn reindex`.
- **A wrong path will not empty your index.** `reindex` refuses when the vault does not exist,
  or when it has no notes while the index does: it tells you to check `OBS_RAG_VAULT` instead
  of purging 200 notes over a typo. To empty it on purpose, `obsn reindex --force`.

If the reindex runs from systemd, the service does not inherit your shell: put the variable
in the unit.

```ini
[Service]
Environment=OBS_RAG_VAULT=/home/you/Documents/my-vault
```

## Commands

Search:

```bash
obsn rag "wildcard certificates"    # hybrid: lexical + semantic
obsn rag "backup" 10 --json         # result count and JSON output
obsn search certificates            # substring over the content, no model
obsn status                         # notes, chunks, vectors, model
obsn reindex                        # incremental: only what changed
obsn reindex --force                # everything again
```

Files:

```bash
obsn read  "02-docs/note.md"
obsn write "02-docs/new.md" --file ~/tmp/note.md
obsn edit  "02-docs/note.md" append --file ~/tmp/tail.md
obsn delete "02-docs/old.md"        # to .trash; --hard really deletes
obsn move  "02-docs/a.md" "02-docs/b.md"
obsn list  "02-docs" --limit 20
obsn folders
obsn tags
obsn meta  "02-docs/note.md"        # frontmatter, tags, backlinks
```

Graph:

```bash
obsn graph --stats                  # one-line summary
obsn graph --orphans                # notes with no incoming links
obsn graph --clusters               # islands and components
obsn graph --broken                 # wikilinks that resolve to nothing
obsn link --neighbors docker        # notes close to a topic
obsn link --propose                 # proposes new links, writes nothing
obsn link --apply                   # writes them into the notes
```

`link --apply` modifies your notes. Run `--propose` first.

The MCP standard names (`read_note`, `write_note`, `search_notes`, `list_tags` and the rest)
work as aliases, so an agent does not have to translate anything.

## How search works

`rag` blends two signals into a single score:

```
0.6 · tf·idf  +  0.4 · cosine  +  0.35 · coverage
```

Coverage is measured per note, not per chunk. If your query names things that live in
different sections of the same note, that note moves up as a whole. Terms that appear in the
path or the heading get a boost, because a chunk that only says "First deploy" says nothing
on its own.

Before touching a vector, the search tries to answer from the inverted index. Almost every
real query names something that is written in some note, and in that case there is no need to
pull 11 MB of vectors or import numpy: 70 ms end to end versus 1.5 s when the model has to
load.

When none of the query words appear in the fragment, the cosine has to reach 0.62 or the
result is dropped. An empty list beats an invented answer.

`search` is a different tool: substring over the text, no model. Use it when you know the
exact word and want speed.

## The index

It is built from a content hash. A `touch` does not fool it, and neither does an edit that
keeps the same length. `reindex` without `--force` only touches what changed, and takes 0.1 s
when nothing did.

Chunks follow blocks: a whole table or a whole list is one chunk. It used to be one row per
chunk, and with 21,592 micro-chunks of 8 tokens the ranking favored table rows over the
sections that explain the topic. The same vault came down to 7,550 chunks.

The index does not store the note text. It stores terms, a line range per chunk and the
vector. When a result is printed, those lines are re-read from the vault. That is 41 MB, and
it is not a readable copy of the vault.

Vectors are cached by a hash of the text plus the model. That is why `reindex --force` with a
warm cache takes 7 s instead of 198.

## Alongside ovaultd

This is not a sync tool. If you want the vault on several machines and a server that cannot
read it, that is a different project: [ovaultd](https://github.com/kastormdz/ovaultd), a
self-hosted sync server that stores encrypted blobs and knows neither the content nor the
paths.

They complement each other, and neither needs the other:

| | ovaultd | obsidian-rag |
|---|---|---|
| What it solves | getting the vault onto all your machines without the server reading it | finding and editing notes on the machine you are on |
| Where the data lives | encrypted blobs on your server | plaintext markdown on your disk |
| What the index sees | nothing, it does not read blobs | the local text |

That split is exactly why it works: ovaultd never holds the plaintext, so the index cannot be
fed from the server. It indexes the vault on the machine running `obsn`. When ovaultd writes a
change to disk, the next `reindex` picks it up; when you write through `obsn`, the background
worker handles it.

Neither one will ask you to install the other.

## Background reindex

`write`, `edit`, `delete` and `move` trigger a separate reindex: `fork+exec`, a 1.5 s wait to
group bursts, and a `flock` so two runs cannot step on each other. The command gives you the
prompt back in about 60 ms. For changes made by another process (your editor, a sync) run a
periodic `obsn reindex`: a systemd timer every 2 minutes costs 0.2% CPU.

Turn it off with `OBS_RAG_NO_ASYNC=1`.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `OBS_RAG_VAULT` | `~/Obsidian` | vault to read and index (see [Point it at your vault](#point-it-at-your-vault)) |
| `OBS_RAG_INDEX` | `$XDG_DATA_HOME/obsidian-rag/index.db` | index file |
| `OBS_RAG_CACHE` | `$XDG_CACHE_HOME/fastembed` | model cache |
| `OBS_RAG_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | embedding model |
| `OBS_RAG_NO_ASYNC` | unset | `1` disables the background reindex |

## Upgrade

```bash
obsn upgrade --check   # 0 up to date · 10 update available · 1 error
obsn upgrade           # fetch + fast-forward, reinstalls if pyproject.toml changed, reindexes
obsn upgrade --how     # what it does, step by step
```

It refuses to run on a dirty tree and never creates a merge commit. If the install is not
editable (no `.git` next to the package), it points you at `pip install -U`.

## Versions

The package follows [SemVer](https://semver.org) and the history lives in
[CHANGELOG.md](CHANGELOG.md), in Keep a Changelog format. While we are on 0.x, a change to the
index schema or the ranking bumps the minor, because it forces a rebuild; a fix bumps the
patch.

```bash
obsn --version          # 0.2.0
obsn upgrade --check    # 0 up to date · 10 update available · 1 error
```

`--check` looks at new commits and at the last published tag, so a loose commit does not count
as a version.

Releases are cut with `scripts/release.sh`:

```bash
scripts/release.sh --check      # validates clean main, the gate and what would ship
scripts/release.sh 0.3.0        # bump, changelog, tag, push and GitHub release
```

It runs the full gate before touching anything, moves whatever is under `[Unreleased]` into the
new version with today's date, creates the annotated tag and the release, and reinstalls the
editable package so `obsn --version` reflects it. If `[Unreleased]` is empty it refuses: there
is nothing to ship.

## For agents

`skills/obsidian-rag/SKILL.md` is a skill with YAML frontmatter and a protocol: when to use
`rag` and when `search`, what to do when the result is empty, how to cite the note, and the
argv pitfalls. Point the agent there instead of explaining the tool.

## Security

- Every path is resolved and checked to stay inside the vault. `../..`, absolute paths and
  symlinks pointing outside are rejected.
- Writes are atomic: temp file, `fsync`, `rename`.
- `delete` moves the note to `.trash/` with a timestamp and its original folder. Only `--hard`
  really deletes.
- The index does not store the text, but it does store the full vocabulary and the vectors.
  Treat it as sensitive data, with the same care as the vault.

## Tests

```bash
python -m unittest discover -s tests -t .
```

62 tests against temporary vaults. They never touch a real vault.

To measure retrieval and latency before changing the ranking, the chunker or the model:

```bash
python bench/bench_search.py --verbose
python bench/compare_models.py
```

Reference numbers with 196 notes and 7,550 chunks: paraphrases hit top-1 in 3 of 10 cases,
top-5 in 5 of 10, zero false positives across 8 out-of-domain queries, and 5/5 when the term
is exact. Pure semantic retrieval is the weak spot: a 384-dimension, 12-layer model on CPU
does not handle a paraphrase with no shared words well. If that matters more to you than
speed, `bench/compare_models.py` compares alternatives against the index you already have.

## License

MIT.
