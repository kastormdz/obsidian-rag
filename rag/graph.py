"""
obs-graph — audit of the vault's wikilink graph.

WHY IT EXISTS
─────────────
Measured 2026-09-29 over 152 notes: 82% link to nothing, 76% are islands
(no link in or out), 120 disconnected components for 152 notes.
The only real graph was 00-INDEX.md. The RAG retrieves by similarity but does
NOT accumulate relationships: every new note is a loose file.

This script measures that. And `obsidian-link.py` proposes the missing links using
the embeddings that are already in the local index.

USAGE
─────
  python3 obsidian-graph.py                 # full report
  python3 obsidian-graph.py --stats         # summary only
  python3 obsidian-graph.py --orphans       # list of orphans
  python3 obsidian-graph.py --clusters      # connected components
  python3 obsidian-graph.py --broken        # wikilinks that do not resolve
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from .config import DB as _CONFIG_DB

DB = str(_CONFIG_DB)

WIKILINK = re.compile(r"\[\[([^\]|#]+)")
# Cosas que PARECEN wikilinks y no lo son: sintaxis YAML (`[[name: '*/main']]`) o clases
# POSIX dentro de comandos (`[[:space:]]`). Sin este filtro el reporte de links rotos
# miente sobre la salud del grafo.
FALSE_POSITIVE = re.compile(
    r"^(https?|ftp)://|^name:\s|^url:\s|^note$|^link$|^\*|^//|^\["
    r"|^\[:?\w+:\]|^\d+$|^-$"
)


def load_notes(db: str = DB) -> dict[str, str]:
    """Read the vault's notes. The index no longer stores the text in clear."""
    from .config import VAULT
    from .vault import notas_del_vault

    if not Path(VAULT).is_dir():
        sys.exit(f"obs-graph: no vault at {VAULT}")
    # `notas_del_vault` es el único que camina el vault: filtra symlinks que apunten afuera.
    # Con `rglob` propio, un `nota.md -> ~/.ssh/id_ed25519` se leía y terminaba en el grafo.
    return notas_del_vault()


def basename(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0].lower()


def build_graph(notes: dict[str, str]) -> dict:
    """Out/in edges, orphans, islands, components and broken links."""
    names = {p: basename(p) for p in notes}
    # índice inverso basename → path (si hay colisiones, gana la primera;
    # se reportan aparte porque un basename duplicado es un problema real)
    by_name: dict[str, list[str]] = defaultdict(list)
    for p, n in names.items():
        by_name[n].append(p)
    # ruta relativa sin `.md` → path, para `[[carpeta/nota]]`
    by_relpath: dict[str, str] = {p.lower().removesuffix(".md"): p for p in notes}

    out: dict[str, set[str]] = defaultdict(set)
    inb: dict[str, set[str]] = defaultdict(set)
    broken: list[tuple[str, str]] = []
    ambiguous: list[tuple[str, str]] = []

    for path, body in notes.items():
        for raw in WIKILINK.findall(body):
            target = raw.strip()
            if not target or FALSE_POSITIVE.match(target):
                continue
            low = target.lower().removesuffix(".md")
            # O(1) por wikilink: `by_name` ya existía pero nadie lo usaba, y el fallback
            # recorría TODAS las notas por cada link (O(W*N) en un vault grande).
            cands = by_name.get(low) or ([by_relpath[low]] if low in by_relpath else [])
            if not cands:
                broken.append((path, target))
                continue
            if len(cands) > 1:
                ambiguous.append((path, f"{target} → {len(cands)} notes"))
            hit = cands[0]
            if hit != path:
                out[path].add(hit)
                inb[hit].add(path)

    orphans = [p for p in notes if not out[p]]
    islands = [p for p in notes if not inb[p] and not out[p]]
    leaves = [p for p in notes if not inb[p]]

    # Componentes conexas (BFS sobre el grafo no dirigido)
    seen: set[str] = set()
    clusters: list[list[str]] = []
    for p in notes:
        if p in seen:
            continue
        stack, comp = [p], []
        seen.add(p)
        while stack:
            n = stack.pop()
            comp.append(n)
            for m in out[n] | inb[n]:
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        clusters.append(comp)
    clusters.sort(key=len, reverse=True)

    return {
        "notes": notes, "out": out, "inb": inb,
        "orphans": orphans, "islands": islands, "leaves": leaves,
        "clusters": clusters, "broken": broken, "ambiguous": ambiguous,
        "edges": sum(len(v) for v in out.values()),
    }


def by_folder(notes: dict, out: dict) -> list[tuple[str, int, int]]:
    data: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for p in notes:
        d = os.path.dirname(p) or "(root)"
        data[d][0] += 1
        if out[p]:
            data[d][1] += 1
    return sorted(data.items(), key=lambda x: -x[1][0])


def report(g: dict, show: str) -> None:
    notes, out, inb = g["notes"], g["out"], g["inb"]
    tot = len(notes)
    if tot == 0:
        print("obs-graph: the index is empty")
        return
    linked_out = tot - len(g["orphans"])
    linked_in = len([p for p in notes if inb[p]])

    if show == "stats":
        print(f"notes={tot}  outgoing={linked_out} ({100*linked_out//tot}%)  "
              f"incoming={linked_in} ({100*linked_in//tot}%)  "
              f"orphans={len(g['orphans'])}  islands={len(g['islands'])}  "
              f"components={len(g['clusters'])}  edges={g['edges']}  "
              f"broken={len(g['broken'])}")
        return

    print("═" * 66)
    print(f"WIKILINK GRAPH — {tot} notes")
    print("═" * 66)
    print(f"  with outgoing links : {linked_out:4d}  ({100*linked_out//tot:3d}%)")
    print(f"  with incoming links : {linked_in:4d}  ({100*linked_in//tot:3d}%)")
    print(f"  orphans             : {len(g['orphans']):4d}  ({100*len(g['orphans'])//tot:3d}%)")
    print(f"  islands             : {len(g['islands']):4d}  ({100*len(g['islands'])//tot:3d}%)")
    print(f"  components          : {len(g['clusters']):4d}")
    print(f"  edges               : {g['edges']:4d}")
    print(f"  broken links        : {len(g['broken']):4d}")

    print(f"\n─── DENSITY BY FOLDER {'─'*44}")
    for d, (n, l) in by_folder(notes, out):
        pct = 100 * l // n
        bar = "█" * (pct // 5)
        flag = "  ←" if pct < 20 and n >= 2 else ""
        print(f"  {d:42s} {n:3d}  {pct:3d}%  {bar}{flag}")

    if show in ("orphans", "all"):
        print(f"\n─── ORPHANS (linking to nothing) {'─'*34}")
        for p in sorted(g["orphans"]):
            print(f"  {p}")

    if show in ("clusters", "all"):
        print(f"\n─── CONNECTED COMPONENTS {'─'*42}")
        multi = [c for c in g["clusters"] if len(c) > 1]
        print(f"  {len(g['clusters'])} components · {len(multi)} with >1 note")
        for comp in g["clusters"][:15]:
            if len(comp) == 1:
                print(f"      loose   {comp[0]}")
            else:
                print(f"  CLUSTER({len(comp):3d})  {comp[0]}" + (f"  +{len(comp)-1} more" if len(comp) > 1 else ""))
        if len(multi) > 15:
            print(f"  … and {len(g['clusters'])-15} more components")

    if show in ("broken", "all") and g["broken"]:
        print(f"\n─── BROKEN LINKS {'─'*50}")
        for p, t in g["broken"]:
            print(f"  {p}\n      → [[{t}]]")
    if g["ambiguous"]:
        print(f"\n─── AMBIGUOUS BASENAMES {'─'*45}")
        for p, t in g["ambiguous"]:
            print(f"  {p}  →  {t}")

    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit the vault's wikilink graph")
    ap.add_argument("--stats", action="store_true", help="summary only, one line")
    ap.add_argument("--orphans", action="store_true")
    ap.add_argument("--clusters", action="store_true")
    ap.add_argument("--broken", action="store_true")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()
    show = ("orphans" if a.orphans else "clusters" if a.clusters
            else "broken" if a.broken else "stats" if a.stats else "all")
    report(build_graph(load_notes(a.db)), show)


if __name__ == "__main__":
    main()
