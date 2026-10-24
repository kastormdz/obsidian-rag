"""Tests de las operaciones de vault, contra un vault temporal (sin tocar uno real)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rag import vault


class VaultTemporal(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.raiz = Path(self._dir.name).resolve()
        self._original = vault.VAULT
        vault.VAULT = self.raiz
        (self.raiz / "A").mkdir()
        (self.raiz / "B").mkdir()
        (self.raiz / "A" / "uno.md").write_text(
            "---\ntags: [alfa, beta]\n---\n# Nota Uno\n\nContenido sobre docker.\n", encoding="utf-8"
        )
        (self.raiz / "B" / "dos.md").write_text("# Nota Dos\n\nHabla de TLS.\n", encoding="utf-8")

    def tearDown(self) -> None:
        vault.VAULT = self._original
        self._dir.cleanup()

    # --- lectura

    def test_read_devuelve_el_contenido(self) -> None:
        self.assertIn("Contenido sobre docker", vault.read_note("A/uno.md"))

    def test_read_de_inexistente_falla(self) -> None:
        with self.assertRaises(FileNotFoundError):
            vault.read_note("A/no-existe.md")

    def test_list_filtra_por_carpeta(self) -> None:
        nombres = [n["name"] for n in vault.list_notes(folder="A")]
        self.assertEqual(nombres, ["A/uno.md"])

    def test_list_filtra_por_tag_del_frontmatter(self) -> None:
        nombres = [n["name"] for n in vault.list_notes(tag="beta")]
        self.assertEqual(nombres, ["A/uno.md"])

    def test_folders_cuenta_notas(self) -> None:
        salida = vault.list_folders()
        self.assertIn("A", salida)
        self.assertIn("B", salida)

    def test_tags_incluye_frontmatter(self) -> None:
        self.assertIn("#alfa", vault.list_tags())

    def test_tags_no_agarra_headings_ni_codigo(self) -> None:
        (self.raiz / "A" / "codigo.md").write_text(
            "# Heading\n\n```bash\n#!/bin/bash\n#comentario\n```\n\n#real\n", encoding="utf-8"
        )
        etiquetas = vault.list_tags()
        self.assertIn("#real", etiquetas)
        self.assertNotIn("#heading", etiquetas)
        self.assertNotIn("#comentario", etiquetas)

    def test_meta_devuelve_frontmatter_y_backlinks(self) -> None:
        (self.raiz / "B" / "enlace.md").write_text("ver [[Nota Uno]]", encoding="utf-8")
        salida = vault.get_note_metadata("A/uno.md")
        self.assertIn("tags: alfa, beta", salida)
        self.assertIn("backlinks: 1", salida)

    def test_search_encuentra_por_contenido(self) -> None:
        hits = vault.search_notes("docker")
        self.assertEqual([h["path"] for h in hits], ["A/uno.md"])

    def test_search_sin_resultados_devuelve_vacio(self) -> None:
        self.assertEqual(vault.search_notes("palabraquenoexiste"), [])

    # --- escritura

    def test_write_crea_y_agrega_extension(self) -> None:
        salida = vault.write_note("C/nueva", "cuerpo")
        self.assertIn("creada", salida)
        self.assertTrue((self.raiz / "C" / "nueva.md").is_file())

    def test_write_es_atomico(self) -> None:
        vault.write_note("A/uno.md", "nuevo")
        restos = [p for p in self.raiz.rglob("*.tmp-*")]
        self.assertEqual(restos, [])

    def test_edit_append_prepend_replace(self) -> None:
        vault.edit_note("B/dos.md", "append", "\ncola")
        self.assertTrue(vault.read_note("B/dos.md").rstrip().endswith("cola"))
        vault.edit_note("B/dos.md", "prepend", "arriba\n")
        self.assertTrue(vault.read_note("B/dos.md").startswith("arriba"))
        salida = vault.edit_note("B/dos.md", "replace-old-new", "cola\nCOLA")
        self.assertIn("editada", salida)
        self.assertIn("COLA", vault.read_note("B/dos.md"))

    def test_replace_con_texto_ausente_falla(self) -> None:
        with self.assertRaises(ValueError):
            vault.edit_note("B/dos.md", "replace-old-new", "no-esta\nx")

    def test_move_no_pisa(self) -> None:
        vault.move_note("A/uno.md", "A/renombrada.md")
        self.assertTrue((self.raiz / "A" / "renombrada.md").is_file())
        with self.assertRaises(FileExistsError):
            vault.move_note("B/dos.md", "A/renombrada.md")

    def test_delete_va_a_la_papelera(self) -> None:
        salida = vault.delete_note("A/uno.md")
        self.assertIn("papelera", salida)
        self.assertFalse((self.raiz / "A" / "uno.md").exists())
        self.assertEqual(len(list((self.raiz / ".trash").iterdir())), 1)

    def test_delete_hard_borra(self) -> None:
        vault.delete_note("A/uno.md", hard=True)
        self.assertFalse((self.raiz / "A" / "uno.md").exists())
        self.assertFalse((self.raiz / ".trash").exists())

    # --- seguridad

    def test_rechaza_punto_punto(self) -> None:
        with self.assertRaises(vault.FueraDelVault):
            vault.read_note("../../etc/passwd")

    def test_rechaza_path_absoluto(self) -> None:
        with self.assertRaises(vault.FueraDelVault):
            vault.read_note("/etc/passwd")

    def test_rechaza_symlink_que_apunta_afuera(self) -> None:
        afuera = Path(self._dir.name).parent / "afuera-test.md"
        afuera.write_text("secreto", encoding="utf-8")
        try:
            (self.raiz / "A" / "link.md").symlink_to(afuera)
            with self.assertRaises(vault.FueraDelVault):
                vault.read_note("A/link.md")
        finally:
            afuera.unlink(missing_ok=True)

    def test_frontmatter_lista_en_bloque(self) -> None:
        (self.raiz / "A" / "bloque.md").write_text(
            "---\ntags:\n  - uno\n  - dos\n---\n# x\n", encoding="utf-8"
        )
        etiquetas = vault.list_tags()
        self.assertIn("#uno", etiquetas)
        self.assertIn("#dos", etiquetas)


if __name__ == "__main__":
    unittest.main()
