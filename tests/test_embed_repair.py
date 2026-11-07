"""Un índice construido sin fastembed tiene que repararse solo cuando el modelo aparece.

El hash de la nota no cambia al instalar el modelo, así que un `reindex` incremental la
saltea: sin reparación explícita, los chunks quedan con `vec = NULL` para siempre y la
búsqueda semántica no vuelve nunca. El índice no guarda texto, así que la reparación
re-trocea la nota por su `ord`.
"""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

NOTAS = {
    "a.md": "# A\n\nContenido sobre docker y certificados.\n",
    "b.md": "# B\n\nOtra nota sobre respaldos y postgres.\n",
}


class ReparacionDeVectores(unittest.TestCase):
    def setUp(self) -> None:
        from rag import index as idx

        self.idx = idx
        self._dir = tempfile.TemporaryDirectory()
        raiz = Path(self._dir.name)
        self.vault = raiz / "vault"
        self.vault.mkdir()
        for nombre, cuerpo in NOTAS.items():
            (self.vault / nombre).write_text(cuerpo, encoding="utf-8")

        self._previo = (idx.VAULT, idx.DB, idx.embed_all, idx._MODEL_HOLDER[0])
        idx.VAULT, idx.DB = str(self.vault), str(raiz / "index.db")
        idx._MODEL_HOLDER[0] = None
        # Primera mitad del escenario: fastembed no está (o falla) y no hay vectores.
        idx.embed_all = lambda textos, c, model=None: [None] * len(textos)
        idx.build(force=True)

    def tearDown(self) -> None:
        idx = self.idx
        (idx.VAULT, idx.DB, idx.embed_all, idx._MODEL_HOLDER[0]) = self._previo
        self._dir.cleanup()

    def _estado(self) -> tuple[int, int, str | None]:
        with contextlib.closing(sqlite3.connect(self.idx.DB)) as c:
            faltan = c.execute("SELECT COUNT(*) FROM chunks WHERE vec IS NULL").fetchone()[0]
            total = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            embedder = c.execute("SELECT v FROM meta WHERE k='embedder'").fetchone()
        return faltan, total, embedder[0] if embedder else None

    def test_marca_el_indice_como_degradado_sin_modelo(self) -> None:
        faltan, total, embedder = self._estado()
        self.assertGreater(total, 0)
        self.assertEqual(faltan, total, "sin modelo ningún chunk puede tener vector")
        self.assertEqual(embedder, "missing")

    def test_el_build_incremental_repara_los_que_faltan(self) -> None:
        import numpy as np

        llamadas: list[int] = []

        def con_modelo(textos, c, model=None):
            llamadas.append(len(textos))
            return [np.ones(4, dtype="float32") for _ in textos]

        self.idx.embed_all = con_modelo
        # `_modelo_disponible` mira si fastembed está importable: en el intérprete que
        # corre esta suite puede no estarlo. El escenario que se prueba es "el modelo
        # apareció", así que se declara disponible.
        with mock.patch.object(self.idx, "_modelo_disponible", return_value=True):
            self.idx.build(force=False)   # las notas NO cambiaron: sólo faltan vectores

        faltan, total, embedder = self._estado()
        self.assertEqual(faltan, 0, "la reparación no rellenó los vectores")
        self.assertEqual(embedder, "ok")
        self.assertEqual(sum(llamadas), total, "tiene que embeber exactamente los chunks que faltan")

    def test_sin_fastembed_no_re_trocea_el_vault_en_cada_build(self) -> None:
        # Sin modelo la reparación no puede llenar nada: re-trocear en cada tick del timer
        # sería costo sin salida. El build incremental tiene que saltearla.
        llamadas: list[int] = []

        def nunca(textos, c, model=None):
            llamadas.append(len(textos))
            return [None] * len(textos)

        self.idx.embed_all = nunca
        with mock.patch.object(self.idx, "_modelo_disponible", return_value=False):
            self.idx.build(force=False)
        self.assertEqual(llamadas, [], "no debe intentar reparar sin modelo")

    def test_sin_faltantes_no_toca_el_modelo(self) -> None:
        import numpy as np

        self.idx.embed_all = lambda textos, c, model=None: [
            np.ones(4, dtype="float32") for _ in textos
        ]
        self.idx.build(force=True)
        llamadas: list[int] = []
        self.idx.embed_all = lambda textos, c, model=None: (
            llamadas.append(len(textos)) or [np.ones(4, dtype="float32") for _ in textos]
        )
        self.idx.build(force=False)
        self.assertEqual(llamadas, [], "un build sin cambios no debe cargar el modelo")


if __name__ == "__main__":
    unittest.main()
