#!/usr/bin/env python3
"""Compara modelos de embeddings sobre los chunks YA indexados.

Sirve para decidir un cambio de `OBS_RAG_MODEL` con números en vez de opinión:

    ~/.hermes/hermes-agent/venv/bin/python bench/compare_models.py \
        sentence-transformers/paraphrase-multilingual-mpnet-base-v2

Qué mide, sobre los mismos textos ("path :: heading :: texto") y el mismo corpus:

  - top-1 y top-5 en las paráfrasis del banco (la nota esperada existe en el vault);
  - coseno máximo de las consultas fuera de dominio, para elegir el piso semántico
    (si una consulta inventada saca coseno alto, el piso no va a filtrarla).

OJO: descarga el modelo (mpnet ~1 GB, e5-large ~2,2 GB) y embebe todo el corpus en CPU
(~5-15 min). No modifica el índice: es una medición en memoria.

Para ADOPTAR un modelo: `OBS_RAG_MODEL=<modelo> obsn reindex --force` (el índice guarda
el modelo y fuerza el rebuild solo) y después volver a correr `bench/bench_search.py`.
"""
from __future__ import annotations

import sys
import time

import numpy as np

sys.path.insert(0, __file__.rsplit("/bench/", 1)[0])

import rag.index as idx  # noqa: E402

from bench_search import NEG, POS  # noqa: E402


def main() -> int:
    models = sys.argv[1:] or ["sentence-transformers/paraphrase-multilingual-mpnet-base-v2"]
    from fastembed import TextEmbedding

    c = idx.conn()
    # El índice ya no guarda el texto (P-03): se reconstruye por el rango de líneas.
    filas = c.execute("SELECT path, heading, off_start, off_end FROM chunks").fetchall()
    c.close()
    textos = [f"{p} :: {h or ''} :: {idx._texto_del_chunk(p, ini, fin)}"
              for p, h, ini, fin in filas]
    paths = [f[0] for f in filas]
    print(f"corpus: {len(textos)} chunks\n", flush=True)

    for nombre in models:
        print(f"=== {nombre}", flush=True)
        t0 = time.perf_counter()
        m = TextEmbedding(model_name=nombre, cache_dir=str(idx.EMBEDDINGS_CACHE))
        mat = np.stack(list(m.embed(textos)))
        mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
        print(f"  embeddings {mat.shape} en {time.perf_counter() - t0:.0f}s", flush=True)

        def cos(q: str):
            qv = np.asarray(next(iter(m.embed([q]))), dtype=np.float32)
            return mat @ (qv / (np.linalg.norm(qv) or 1.0))

        t1 = t5 = 0
        for consulta, esperado in POS:
            orden = np.argsort(-cos(consulta))[:5]
            h1 = esperado.lower() in paths[orden[0]].lower()
            h5 = any(esperado.lower() in paths[i].lower() for i in orden)
            t1 += h1
            t5 += h5
            print(f"  {'OK ' if h1 else ('T5 ' if h5 else 'X  ')} {consulta[:40]:42s}"
                  f" -> {paths[orden[0]]}", flush=True)

        maximos = [float(cos(q).max()) for q, _ in NEG]
        print(f"\n  top-1 {t1}/{len(POS)}   top-5 {t5}/{len(POS)}", flush=True)
        print(f"  NEG coseno max: min={min(maximos):.3f} max={max(maximos):.3f}"
              f"   (>=0.62: {sum(1 for x in maximos if x >= 0.62)}/{len(maximos)})", flush=True)
        print(flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
