"""Tests del chunker: bloques de tabla/lista, fusión de prosa y herencia de heading.

El criterio que se defiende acá es el que hace buscable al índice: un bloque de
tabla o de lista es UN chunk (no una fila por chunk), y ninguna nota chica queda
invisible por descartar sus bloques cortos.
"""

from __future__ import annotations

import unittest

from rag.index import chunk


class Chunker(unittest.TestCase):
    def test_tabla_entera_es_un_chunk(self) -> None:
        contenido = (
            "# Hosts\n\n"
            "| host | ip | rol |\n"
            "|---|---|---|\n"
            "| alpha | 192.0.2.11 | docker |\n"
            "| beta | 192.0.2.12 | desktop |\n"
            "| gamma | 192.0.2.13 | laptop |\n"
        )
        tablas = [c for c in chunk(contenido) if c["ctype"] == "table"]
        self.assertEqual(len(tablas), 1, f"esperaba 1 tabla, salieron {len(tablas)}")
        self.assertIn("| alpha |", tablas[0]["text"])
        self.assertIn("| gamma |", tablas[0]["text"])

    def test_lista_entera_es_un_chunk(self) -> None:
        contenido = "# Pasos\n\n- uno\n- dos\n- tres\n- cuatro\n"
        listas = [c for c in chunk(contenido) if c["ctype"] == "list"]
        self.assertEqual(len(listas), 1, f"esperaba 1 lista, salieron {len(listas)}")
        for item in ("uno", "dos", "tres", "cuatro"):
            self.assertIn(item, listas[0]["text"])

    def test_tabla_gigante_se_parte_por_tope(self) -> None:
        filas = "\n".join(f"| fila{ i } | valor {i} | comentario largo {i} |" for i in range(200))
        contenido = f"# Tabla\n\n| a | b | c |\n|---|---|---|\n{filas}\n"
        tablas = [c for c in chunk(contenido, max_chars=1200) if c["ctype"] == "table"]
        self.assertGreater(len(tablas), 1, "una tabla de 200 filas no puede ser un solo chunk")
        self.assertTrue(all(len(t["text"]) <= 1300 for t in tablas))

    def test_prosa_corta_se_fusiona(self) -> None:
        contenido = (
            "# Nota\n\n"
            "Un párrafo suficientemente largo como para ser un chunk propio, con más de ciento veinte caracteres de extensión total.\n\n"
            "Corto.\n"
        )
        chunks = chunk(contenido)
        self.assertTrue(
            any("Corto." in c["text"] and "párrafo" in c["text"] for c in chunks),
            "el bloque corto de prosa tiene que fusionarse, no perderse",
        )

    def test_fence_grande_se_parte(self) -> None:
        cuerpo = "\n".join(f"echo linea-{i}" for i in range(400))
        contenido = f"# Código\n\n```bash\n{cuerpo}\n```\n"
        chunks = chunk(contenido)
        self.assertGreater(len(chunks), 1, "un fence de ~5 KB no puede ser un solo chunk")

    def test_nota_minima_no_desaparece(self) -> None:
        contenido = "# Prueba RAG\n\nEl token zeta-9931 vive acá.\n"
        chunks = chunk(contenido)
        self.assertTrue(any("zeta-9931" in c["text"] for c in chunks))

    def test_heading_se_hereda_en_bloques_partidos(self) -> None:
        filas = "\n".join(f"| a{i} | b{i} |" for i in range(120))
        contenido = f"## Sección\n\n| a | b |\n|---|---|\n{filas}\n"
        tablas = [c for c in chunk(contenido, max_chars=600) if c["ctype"] == "table"]
        self.assertGreater(len(tablas), 1)
        self.assertTrue(all(c["heading"] == "Sección" for c in tablas))

    def test_vacio_y_muy_corto(self) -> None:
        self.assertEqual(chunk(""), [])
        self.assertEqual(chunk("   \n\n"), [])


if __name__ == "__main__":
    unittest.main()
