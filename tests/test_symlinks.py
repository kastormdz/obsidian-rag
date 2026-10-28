"""Un symlink dentro del vault no puede hacer que el RAG lea (e indexe) archivos de afuera.

El CRUD de `vault.py` ya confinaba, pero el indexado, el grafo y los enlaces caminan el
vault por su cuenta. Un `nota.md -> ~/.ssh/id_ed25519` se leía, se indexaba y después
`obsn rag` lo devolvía: el secreto quedaba persistido en el índice.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag import config, graph, index, link, vault


class SymlinkFueraDelVault(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.raiz = Path(self._dir.name).resolve()
        self.vault = self.raiz / "vault"
        self.vault.mkdir()
        (self.vault / "real.md").write_text("# real\n\ncontenido\n", encoding="utf-8")
        self.secreto = self.raiz / "secreto.txt"
        self.secreto.write_text("SECRETO-FUERA-DEL-VAULT\n", encoding="utf-8")
        (self.vault / "inocente.md").symlink_to(self.secreto)
        self._parches = [
            mock.patch.object(config, "VAULT", self.vault),
            mock.patch.object(index, "VAULT", self.vault),
            mock.patch.object(vault, "VAULT", self.vault),
        ]
        for p in self._parches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._parches):
            p.stop()
        self._dir.cleanup()

    def test_el_indexado_no_lista_ni_lee_el_symlink(self) -> None:
        nombres = [n["name"] for n in index.listar_notas()]
        self.assertIn("real.md", nombres)
        self.assertNotIn("inocente.md", nombres, "el symlink no entra al índice")
        # Y aunque le pasen el path a mano, `leer_nota` no cruza el borde.
        with self.assertRaises(Exception):
            index.leer_nota(str(self.vault / "inocente.md"))

    def test_el_grafo_no_lee_el_symlink(self) -> None:
        notas = graph.load_notes(str(self.raiz / "idx.db"))
        self.assertIn("real.md", notas)
        self.assertNotIn("inocente.md", notas)
        self.assertNotIn("SECRETO-FUERA-DEL-VAULT", "".join(notas.values()))

    def test_los_enlaces_no_leen_el_symlink(self) -> None:
        # `enlaces_actuales` sólo devuelve notas que ENLAZAN a otra: el fixture necesita un
        # wikilink real para que el camino se ejercite.
        (self.vault / "real.md").write_text("# real\n\n[[otra]]\n", encoding="utf-8")
        (self.vault / "otra.md").write_text("# otra\n", encoding="utf-8")
        enlaces = link.enlaces_actuales()
        self.assertIn("real.md", enlaces)
        self.assertNotIn("inocente.md", enlaces)

    def test_un_symlink_dentro_del_vault_si_se_permite(self) -> None:
        (self.vault / "alias.md").symlink_to(self.vault / "real.md")
        nombres = [n["name"] for n in index.listar_notas()]
        self.assertIn("alias.md", nombres, "un symlink interno es legítimo")


if __name__ == "__main__":
    unittest.main()
