"""
obsidian-link — proposes and applies wikilinks between notes using the local index.

WHY IT EXISTS
─────────────
Measured 2026-09-29: 152 notes, 82% with no outgoing link, 76% islands, 120 components
for 152 notes. The RAG retrieves by similarity but does NOT accumulate relationships:
every new note is a loose file and nobody links it again.

This script inverts that: it uses the embeddings already in obsidian-index.db to work
out, note by note, which are its real neighbors. It does not invent relationships —
the proposal comes from the same scores the RAG uses.

DESIGN
──────
- It only proposes links when the relationship is STRONG (high score) and the note is
  not already linked to the other one. A graph of 300 weak links is noise; the value
  is in 30 links someone reads and thinks "yes, that makes sense".
- It distinguishes "neighbor" (shared topic) from "correction" (the other note is more
  canonical about the same subject) — the second category is the most useful.
- It NEVER touches `Secrets/` or `06-Todo/`: those are credentials and to-dos.
- Propose first, apply later. It never writes without an explicit `--apply`.

USAGE
─────
  python3 obsidian-link.py                       # general report
  python3 obsidian-link.py --neighbors keycloak  # neighbors of a topic
  python3 obsidian-link.py --propose             # suggested links, touches nothing
  python3 obsidian-link.py --apply               # writes the links into the vault
  python3 obsidian-link.py --apply --max 30      # up to 30 (default 50)
  python3 obsidian-link.py --canonical           # detects duplicate subjects
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

# Carpetas fuera de alcance: credenciales y lista de cosas por hacer.
EXCLUIR_DIRS = ("03-Personal/Secrets", "06-Todo", "05-Archive", ".obsidian")
# No proponer links hacia/desde estas: son índices, no contenido.
NO_ENLACAR = ("00-INDEX.md",)

WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def cargando_indice():
    """The index engine, as a module of the package (loads the model once)."""
    from . import index as motor

    return motor


def notas_filtradas() -> list[str]:
    import sqlite3
    c = sqlite3.connect(DB)
    rows = [r[0] for r in c.execute("SELECT path FROM notes ORDER BY path")]
    c.close()
    return [p for p in rows
            if not p.startswith(EXCLUIR_DIRS) and p not in NO_ENLACAR]


def vec_por_nota() -> dict[str, "object"]:
    """Mean vector per note (average of its chunks), normalized."""
    import numpy as np
    import sqlite3
    c = sqlite3.connect(DB)
    acc: dict[str, list] = defaultdict(list)
    for path, vec in c.execute("SELECT path, vec FROM chunks WHERE vec IS NOT NULL"):
        acc[path].append(np.frombuffer(vec, dtype="float32"))
    c.close()
    out = {}
    for p, vs in acc.items():
        v = np.mean(vs, axis=0)
        n = np.linalg.norm(v)
        if n:
            out[p] = v / n
    return out


def enlaces_actuales() -> dict[str, set[str]]:
    """Wikilinks that already exist, read from the vault (the index stores no text)."""
    from .vault import notas_del_vault

    # Mismo camino que el grafo: confina los symlinks que apuntan afuera del vault.
    notes = notas_del_vault()
    cur: dict[str, set[str]] = defaultdict(set)
    names = {p: os.path.splitext(os.path.basename(p))[0].lower() for p in notes}
    # basename → paths, para resolver cada wikilink en O(1) en vez de recorrer el vault
    por_nombre: dict[str, list[str]] = defaultdict(list)
    for p, n in names.items():
        por_nombre[n].append(p)
    for p, joined in notes.items():
        for raw in WIKILINK.findall(joined):
            t = raw.strip().lower().removesuffix(".md")
            for q in por_nombre.get(t, ()):
                if q != p:
                    cur[p].add(q)
    return cur


def _dueno(p: str, q: str, cur: dict) -> str:
    """From a pair, which note "owns" the link: the most canonical one, breaking ties by name."""
    cp, cq = score_de_canonicidad(p, cur), score_de_canonicidad(q, cur)
    if cp != cq:
        return p if cp > cq else q
    if len(p) != len(q):
        return p if len(p) < len(q) else q
    return min(p, q)


def vecinos(vecs: dict, cur: dict, min_score: float, top_k: int) -> dict[str, list]:
    """For each note, its unlinked neighbors with score >= min_score.

    Reciprocal dedup: if A proposes B and B proposes A, it is ONE link, not two. The
    direction of the more canonical note wins.
    """
    import numpy as np
    paths = list(vecs)
    if not paths:
        # Sin vectores `np.vstack([])` explota con ValueError; un índice sin embeddings
        # (o con todo excluido) no tiene vecinos, no es un error.
        return {}
    M = np.vstack([vecs[p] for p in paths])
    sim = M @ M.T
    idx = {p: i for i, p in enumerate(paths)}
    crudos: dict[str, list] = {}
    for p in paths:
        row = sim[idx[p]]
        cands = []
        for j, q in enumerate(paths):
            if p == q or q in cur.get(p, ()):
                continue
            s = float(row[j])
            if s >= min_score:
                cands.append((s, q))
        cands.sort(key=lambda x: -x[0])
        if cands:
            crudos[p] = cands[:top_k]

    # Un link por par, en UNA dirección. Antes, al ver el par por segunda vez se borraban
    # las DOS direcciones: los pares más parecidos del vault —justo los que importan— no
    # se proponían nunca.
    pares: dict[frozenset, tuple[float, str, str]] = {}
    for p, cands in crudos.items():
        for s, q in cands:
            par = frozenset((p, q))
            previo = pares.get(par)
            if previo is None or s > previo[0]:
                pares[par] = (s, p, q)

    out: dict[str, list] = {}
    for s, p, q in pares.values():
        dueno = _dueno(p, q, cur)
        out.setdefault(dueno, []).append((s, q if dueno == p else p))
    for lista in out.values():
        lista.sort(key=lambda x: -x[0])
    return out


def detecta_canonicos(vecs: dict, cur: dict, umbral: float) -> list[tuple]:
    """Almost-duplicate pairs: same subject, two notes. The most canonical one wins.

    Canonicity heuristic: the note whose name is shorter and more specific
    (fewer suffixes like '-research', '-setup', '-report') and that has more
    incoming links. It is not infallible — that is why it ONLY proposes, moves nothing.
    """
    import numpy as np
    paths = list(vecs)
    if not paths:
        return []
    M = np.vstack([vecs[p] for p in paths])
    sim = M @ M.T
    idx = {p: i for i, p in enumerate(paths)}
    pares = []
    for i, p in enumerate(paths):
        for j in range(i + 1, len(paths)):
            q = paths[j]
            s = float(sim[i][j])
            if s >= umbral and p not in cur.get(q, ()) and q not in cur.get(p, ()):
                pares.append((s, p, q))
    pares.sort(key=lambda x: -x[0])
    return pares


def score_de_canonicidad(p: str, cur: dict) -> float:
    """Higher = more likely to be the canonical note for the subject."""
    base = os.path.splitext(os.path.basename(p))[0]
    # ENTRANTES de verdad: `cur[p]` son los links que p EMITE. Contarlos como entrantes
    # hacía que la nota más enlazada del vault pareciera la menos canónica.
    entrantes = sum(1 for destinos in cur.values() if p in destinos)
    # sufijos que sugieren "nota derivada" y no canónica
    derivado = sum(1 for s in ("investigacion", "setup", "informe", "research",
                               "test", "notas", "draft", "wip", "deep-dive")
                   if s in base.lower())
    largo = max(0, len(base) - 20) / 40
    return entrantes - derivado * 1.5 - largo


def main() -> None:
    ap = argparse.ArgumentParser(description="Propose and apply wikilinks by similarity (RAG)")
    ap.add_argument("--neighbors", metavar="TOPIC", help="neighbors of a topic")
    ap.add_argument("--propose", action="store_true", help="show only, do not write")
    ap.add_argument("--apply", action="store_true", help="WRITE the links into the vault")
    ap.add_argument("--min-score", type=float, default=0.78,
                    help="minimum similarity to propose (default 0.78)")
    ap.add_argument("--max", type=int, default=50, help="maximum notes to touch")
    ap.add_argument("--canonical", action="store_true", help="detect duplicate subjects")
    ap.add_argument("--canonical-threshold", type=float, default=0.80)
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("obsidian-link: no index. Run: obsn reindex")
    oi = cargando_indice()
    notas = set(notas_filtradas())
    vecs_all = vec_por_nota()
    vecs = {p: v for p, v in vecs_all.items() if p in notas}
    cur = enlaces_actuales()
    cur = {p: {q for q in s if q in vecs} for p, s in cur.items()}

    # ── mode: neighbors of a topic ──
    if a.neighbors:
        q = oi.search(a.neighbors, 8)
        print(f"\n─── NEIGHBORS OF {a.neighbors!r} (RAG search) ───")
        for h in q:
            print(f"  {h['score']:.3f}  {h['path']}"
                  f"{'   [already linked]' if h['path'] in cur.get('00-INDEX.md', ()) else ''}")
        return

    # ── mode: canonical notes (duplicates) ──
    if a.canonical:
        pares = [(s, p, q) for s, p, q in detecta_canonicos(vecs, cur, a.canonical_threshold)
                 if p in vecs and q in vecs]
        print(f"\n─── POSSIBLE DUPLICATES (similarity >= {a.canonical_threshold}) ───")
        print(f"  {len(pares)} pairs. Proposes the canonical one, moves NOTHING.\n")
        for s, p, q in pares[:30]:
            cp, cq = score_de_canonicidad(p, cur), score_de_canonicidad(q, cur)
            canon = p if cp >= cq else q
            otro = q if canon == p else p
            print(f"  {s:.3f}  canonical: {canon}")
            print(f"          duplicate: {otro}")
        return

    # ── mode: propose / apply ──
    vec = vecinos(vecs, cur, a.min_score, top_k=4)
    total_links = sum(len(v) for v in vec.values())
    print(f"\n─── LINK PROPOSALS (similarity >= {a.min_score}) ───")
    print(f"  {len(vec)} notes would gain links · {total_links} suggested links\n")
    if not vec:
        print("  (nothing to propose at that threshold — try --min-score 0.55)")
        return

    ranking = sorted(vec.items(), key=lambda x: -x[1][0][0])
    for p, cands in ranking[:25]:
        print(f"  {p}")
        for s, q in cands:
            print(f"      ← {q}   ({s:.3f})")
    if len(ranking) > 25:
        print(f"  … and {len(ranking)-25} more notes")

    if not a.apply:
        print("\n  (mirror only. Use --apply to write)")
        return

    # ── apply ── goes straight to the filesystem, with the same guarantees as the rest:
    # confined path and atomic write.
    from .vault import edit_note, read_note

    tocadas = 0
    for p, cands in ranking:
        if tocadas >= a.max:
            break
        try:
            body = read_note(p)
        except Exception as e:
            print(f"  ! could not read {p}: {e}")
            continue
        # If it already has the section, adding it again duplicates it: skip the note,
        # do not keep going.
        if not isinstance(body, str):
            print(f"  ! unexpected response in {p}")
            continue
        # "Notas relacionadas" is the heading older runs wrote: both markers are checked so
        # existing notes do not get a second section in the new language.
        if "Related notes" in body or "Notas relacionadas" in body:
            continue
        nuevos = [q for _, q in cands
                  if f"[[{os.path.splitext(os.path.basename(q))[0]}]]" not in body
                  and f"[[{q}]]" not in body]
        if not nuevos:
            continue
        # wikilinks by basename (how Obsidian resolves them)
        line = "\n\n## 🔗 Related notes\n\n" + "\n".join(
            f"- [[{os.path.splitext(os.path.basename(q))[0]}]]" for q in nuevos)
        try:
            edit_note(p, "append", line)
            print(f"  ✓ {p}  ←  {len(nuevos)} links")
            tocadas += 1
        except Exception as e:
            print(f"  ! failed {p}: {e}")
    print(f"\n  {tocadas} notes updated. Reindex with: obsn reindex")


if __name__ == "__main__":
    main()
