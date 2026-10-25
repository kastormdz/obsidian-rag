#!/usr/bin/env python3
"""Banco de evaluación del RAG: mide acierto y latencia, sin opiniones.

Uso:
    ~/.hermes/hermes-agent/venv/bin/python bench/bench_search.py [--verbose]

Tres grupos:
  POS    paráfrasis: la consulta no repite las palabras de la nota, pero la nota
         correcta existe. Se mide top-1 y top-5.
  NEG    consultas fuera de dominio: lo correcto es devolver VACÍO. Un resultado
         acá es un falso positivo (el consumidor lo lee como acierto).
  EXACT  término exacto (nombre propio, host, sigla): tiene que salir en top-1.

Este banco es el gate de cualquier cambio de ranking, chunker o modelo: si un
cambio sube la latencia pero baja los aciertos, no entra.
"""
from __future__ import annotations

import json
import statistics
import sys
import time

sys.path.insert(0, __file__.rsplit("/bench/", 1)[0])

import rag.index as idx  # noqa: E402

# (consulta, fragmento del path esperado)
POS = [
    ("cuando se me llena el disco duro", "appctl-manual-uso"),
    ("renovar un certificado vencido", "certificados-wildcard"),
    ("ataques automatizados contra el login", "keycloak"),
    ("como hago el respaldo de la base de datos", "postgres"),
    ("separar los servicios en contenedores", "docker"),
    ("que pasa si se corta la luz de golpe", "post-outage"),
    ("monitoreo de seguridad con agentes", "wazuh"),
    ("unificar todas las notas en un solo lugar", "obsidian"),
    ("poner el terminal lindo y rapido", "kitty"),
    ("cuentas y claves del correo interno", "keycloak"),
]
NEG = [
    ("zqxjkv-9931-no-existe", None),
    ("receta de pizza sin gluten", None),
    ("historia del imperio bizantino", None),
    ("como entrenar un perro pastor aleman", None),
    ("precio del litio en bolivia", None),
    ("fisica cuantica de particulas", None),
    ("resultado del partido del domingo", None),
    ("como tejer un sweater de lana", None),
]
EXACT = [
    ("obsn reindex", "obsidian-rag"),
    ("paraphrase-multilingual-MiniLM", "obsidian-rag"),
    ("keycloak", "keycloak"),
    ("wazuh", "wazuh"),
    ("seafile", "seafile"),
]


def medir(grupo, casos, esperar_vacio=False, verbose=False):
    top1 = top5 = vacios = 0
    latencias = []
    detalle = []
    for consulta, esperado in casos:
        t0 = time.perf_counter()
        res = idx.search(consulta, 5)
        latencias.append((time.perf_counter() - t0) * 1000)
        if not res:
            vacios += 1
            detalle.append((consulta, "VACIO", "", ""))
            continue
        if esperar_vacio:
            # Cualquier resultado en una consulta fuera de dominio es un falso positivo.
            detalle.append((consulta, "FP  ", res[0]["path"], f"{res[0]['score']:.3f}"))
            continue
        acierto1 = esperado.lower() in res[0]["path"].lower()
        acierto5 = any(esperado.lower() in r["path"].lower() for r in res)
        top1 += acierto1
        top5 += acierto5
        detalle.append((consulta, "OK " if acierto1 else ("T5 " if acierto5 else "X  "),
                        res[0]["path"], f"{res[0]['score']:.3f}"))
    if verbose:
        print(f"\n--- {grupo} ---")
        for consulta, marca, path, score in detalle:
            print(f"  {marca} {consulta[:44]:46s} -> {path}  {score}")
    return {
        "n": len(casos),
        "top1": top1,
        "top5": top5,
        "vacios": vacios,
        "ms_mediana": statistics.median(latencias),
        "ms_p95": sorted(latencias)[max(0, int(len(latencias) * 0.95) - 1)],
    }


def main() -> int:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    idx._MODEL_HOLDER[0] = idx.embedder()  # calentar el modelo una vez
    idx.search("calentamiento", 1)          # y la primera consulta

    grupos = [
        ("POS  (paráfrasis)", medir("POS", POS, verbose=verbose)),
        ("NEG  (fuera de dominio)", medir("NEG", NEG, esperar_vacio=True, verbose=verbose)),
        ("EXACT (término exacto)", medir("EXACT", EXACT, verbose=verbose)),
    ]
    # Salida para máquinas: el gate no puede parsear la tabla humana (los nombres de
    # grupo tienen espacios y las columnas se corren).
    if "--json" in sys.argv:
        print(json.dumps({"pos": grupos[0][1], "neg": grupos[1][1], "exact": grupos[2][1]}))
        return 0
    print(f"\n{'grupo':24s} {'n':>3s} {'top-1':>6s} {'top-5':>6s} {'vacíos':>7s} {'mediana':>9s} {'p95':>8s}")
    print("-" * 76)
    for nombre, m in grupos:
        print(f"{nombre:24s} {m['n']:3d} {m['top1']:6d} {m['top5']:6d} {m['vacios']:7d}"
              f" {m['ms_mediana']:8.0f}ms {m['ms_p95']:7.0f}ms")
    pos, neg = grupos[0][1], grupos[1][1]
    print(f"\nfalsos positivos en NEG: {neg['n'] - neg['vacios']}/{neg['n']}"
          f"   (lo deseable es 0)")
    print(f"acierto top-1 en POS:    {pos['top1']}/{pos['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
