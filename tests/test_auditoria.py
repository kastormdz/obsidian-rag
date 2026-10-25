"""Regresiones de la auditoría: que no vuelvan los restos del stack viejo.

El modo `obsn link --apply` importaba `obsc`, el cliente MCP que se borró con el stack
anterior: el comando moría con ModuleNotFoundError. Este test lee el código fuente para
que nadie lo reintroduzca sin darse cuenta.
"""

from __future__ import annotations

import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent / "rag"


class SinRestosDelStackViejo(unittest.TestCase):
    def test_ningun_modulo_importa_el_cliente_viejo(self) -> None:
        import re

        # Se miran las LÍNEAS DE CÓDIGO, no los comentarios: documentar el bug histórico
        # es correcto, volver a importar el cliente borrado no.
        patron = re.compile(r"^\s*(import\s+obsc\b|from\s+obsc\b)|obsc\.(call|rag|main)\(")
        hallazgos = []
        for archivo in RAIZ.glob("*.py"):
            for n, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
                if linea.lstrip().startswith("#"):
                    continue
                if patron.search(linea):
                    hallazgos.append(f"{archivo.name}:{n}: {linea.strip()}")
        self.assertEqual(hallazgos, [], f"restos del stack viejo: {hallazgos}")

    def test_link_apply_usa_el_filesystem(self) -> None:
        from rag import link

        self.assertFalse(hasattr(link, "obsc"), "link.py no debe depender del cliente viejo")


if __name__ == "__main__":
    unittest.main()
