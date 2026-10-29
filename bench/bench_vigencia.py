#!/usr/bin/env python3
"""Banco de vigencia: con el mismo texto, la nota vigente tiene que ganarle a la vencida.

  4 pares de notas idénticas salvo `stale_after`. Se exige:
    - top-1 = la vigente en los 4 pares;
    - la vencida sigue apareciendo (penalizada, no escondida);
    - `fresh_only` deja sólo la vigente.

Uso: python bench/bench_vigencia.py [--json]
Sale 1 si falla cualquier par (así lo puede usar el gate).
"""

from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import tempfile
import time

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

PARES = [
    ("puerto del servicio",
     "# Puerto\n\nEl puerto del servicio es 8080 y se cambia en el compose del proyecto.\n"),
    ("renovar certificados",
     "# Certificados\n\nLos certificados se renuevan con certbot y el plugin de DNS cada 90 días.\n"),
    ("respaldo de la base",
     "# Respaldo\n\nEl respaldo de la base corre a las 3 con pg_dump y se guarda comprimido.\n"),
    ("rotar claves ssh",
     "# Claves\n\nLas claves ssh se rotan cada 90 días y se firman con la CA interna.\n"),
]

FRESCO = "2099-01-01"
VENCIDO = "2020-01-01"


def main() -> int:
    from rag import index as idx

    tmp = tempfile.TemporaryDirectory()
    vault = pathlib.Path(tmp.name) / "vault"
    vault.mkdir()
    for consulta, cuerpo in PARES:
        tema = consulta.split()[-1]
        (vault / f"{tema}-vigente.md").write_text(
            f"---\nstale_after: {FRESCO}\n---\n{cuerpo}", encoding="utf-8")
        (vault / f"{tema}-vencida.md").write_text(
            f"---\nstale_after: {VENCIDO}\n---\n{cuerpo}", encoding="utf-8")

    previo_vault, previo_db = idx.VAULT, idx.DB
    idx.VAULT, idx.DB = str(vault), str(pathlib.Path(tmp.name) / "index.db")
    try:
        t0 = time.perf_counter()
        idx.build(force=True)
        build_s = time.perf_counter() - t0

        ok_top1 = ok_visible = ok_fresh = 0
        tiempos = []
        detalle = []
        for consulta, _ in PARES:
            t0 = time.perf_counter()
            filas = idx.search(consulta, k=5)
            tiempos.append((time.perf_counter() - t0) * 1000)
            paths = [r["path"] for r in filas]
            top1 = bool(paths) and paths[0].endswith("-vigente.md")
            visible = any(p.endswith("-vencida.md") for p in paths)
            solo_frescas = [r["path"] for r in idx.search(consulta, k=5, fresh_only=True)]
            fresh_ok = all(not p.endswith("-vencida.md") for p in solo_frescas)
            ok_top1 += top1
            ok_visible += visible
            ok_fresh += fresh_ok
            detalle.append({"consulta": consulta, "top1": paths[0] if paths else None,
                            "top1_es_vigente": top1, "vencida_visible": visible,
                            "fresh_only_limpio": fresh_ok})
    finally:
        idx.VAULT, idx.DB = previo_vault, previo_db
        tmp.cleanup()

    n = len(PARES)
    resumen = {
        "pares": n,
        "top1_vigente": ok_top1,
        "vencida_visible": ok_visible,
        "fresh_only_limpio": ok_fresh,
        "build_s": round(build_s, 2),
        "busqueda_ms_mediana": round(statistics.median(tiempos), 1),
        "detalle": detalle,
    }
    if "--json" in sys.argv:
        print(json.dumps(resumen, ensure_ascii=False))
    else:
        print(f"pares={n}  top1_vigente={ok_top1}/{n}  vencida_visible={ok_visible}/{n}  "
              f"fresh_only_limpio={ok_fresh}/{n}")
        print(f"build={build_s:.1f}s  búsqueda mediana={resumen['busqueda_ms_mediana']}ms")
        for d in detalle:
            marca = "ok " if d["top1_es_vigente"] and d["vencida_visible"] else "MAL"
            print(f"  {marca} {d['consulta']:<24} top-1={d['top1']}")
    return 0 if ok_top1 == n and ok_visible == n and ok_fresh == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
