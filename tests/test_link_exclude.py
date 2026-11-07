"""Las carpetas que `link --apply` no toca son configurables, no nombres del vault del autor.

Los defaults siguen protegiendo el vault donde nació la herramienta; el entorno y el flag
suman, nunca reemplazan. Un vault con `Privado/Claves` tiene que poder protegerlo sin editar
el código.
"""

from __future__ import annotations

import os
import unittest

from rag import link


class Exclusiones(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.get("OBS_RAG_LINK_EXCLUDE")
        os.environ.pop("OBS_RAG_LINK_EXCLUDE", None)

    def tearDown(self) -> None:
        if self._env is None:
            os.environ.pop("OBS_RAG_LINK_EXCLUDE", None)
        else:
            os.environ["OBS_RAG_LINK_EXCLUDE"] = self._env

    def test_default_entorno_y_flag_se_suman(self) -> None:
        os.environ["OBS_RAG_LINK_EXCLUDE"] = "Privado/Claves:Otra"
        fuera = link._excluidos(["/Tercera/"])
        self.assertEqual(fuera[:4], link.EXCLUIR_DIRS)
        self.assertIn("Privado/Claves", fuera)
        self.assertIn("Otra", fuera)
        self.assertIn("Tercera", fuera)

    def test_sin_duplicados_ni_entradas_vacias(self) -> None:
        os.environ["OBS_RAG_LINK_EXCLUDE"] = "::03-Personal/Secrets:"
        fuera = link._excluidos(["06-Todo", ""])
        self.assertEqual(fuera.count("03-Personal/Secrets"), 1)
        self.assertEqual(fuera.count("06-Todo"), 1)
        self.assertNotIn("", fuera)

    def test_defaults_sin_entorno(self) -> None:
        self.assertEqual(link._excluidos(), link.EXCLUIR_DIRS)


if __name__ == "__main__":
    unittest.main()
