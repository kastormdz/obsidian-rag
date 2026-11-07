"""El texto del resultado no puede salir de offsets viejos.

El índice guarda un rango de líneas, no el texto. Si la nota cambió sin reindexar, ese rango
apunta a otra parte del archivo: el resultado mostraría líneas que no son el chunk que se
puntuó. El hash de la nota lo detecta, la nota se re-corta y el hit se marca `index_stale`.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

NOTA = "# A\n\nprimera linea\n\nsegunda linea con zanahoria morada\n"


class TextoFresco(unittest.TestCase):
    def setUp(self) -> None:
        from rag import index as idx

        self.idx = idx
        self._dir = tempfile.TemporaryDirectory()
        raiz = Path(self._dir.name)
        self.vault = raiz / "vault"
        self.vault.mkdir()
        self.nota = self.vault / "a.md"
        self.nota.write_text(NOTA, encoding="utf-8")

        self._previo = (idx.VAULT, idx.DB, idx.embed_all)
        idx.VAULT, idx.DB = str(self.vault), str(raiz / "index.db")
        # Sin modelo: este test es de offsets y texto, no de ranking semántico.
        idx.embed_all = lambda textos, c, model=None: [None] * len(textos)
        idx.build(force=True)

    def tearDown(self) -> None:
        idx = self.idx
        (idx.VAULT, idx.DB, idx.embed_all) = self._previo
        self._dir.cleanup()

    def test_nota_cambiada_sin_reindexar_devuelve_el_chunk_correcto(self) -> None:
        # Cinco líneas al principio desplazan TODOS los offsets: sin la validación, el
        # rango guardado corta los comentarios nuevos y el texto pierde "zanahoria".
        self.nota.write_text(
            "<!-- 1 -->\n<!-- 2 -->\n<!-- 3 -->\n<!-- 4 -->\n<!-- 5 -->\n" + NOTA,
            encoding="utf-8",
        )
        res = self.idx.search("zanahoria", 3)
        self.assertTrue(res)
        self.assertIn("zanahoria", res[0]["text"])
        self.assertTrue(res[0].get("index_stale"), "el hit tiene que avisar que el texto se re-cortó")

    def test_nota_sin_cambios_no_marca_stale(self) -> None:
        res = self.idx.search("zanahoria", 3)
        self.assertTrue(res)
        self.assertIn("zanahoria", res[0]["text"])
        self.assertNotIn("index_stale", res[0])


if __name__ == "__main__":
    unittest.main()
