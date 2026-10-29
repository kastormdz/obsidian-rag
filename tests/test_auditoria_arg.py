"""Regresión de los bugs de la auditoría 2026-10-28 (Auditoria-Argon.md).

Un test por hallazgo confirmado, sobre vaults temporales. Si alguno vuelve, esto lo caza.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from rag import config, graph, link, vault
from rag.index import chunk


class Base(unittest.TestCase):
    """Vault temporal con las DOS referencias parcheadas (config y vault)."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.raiz = Path(self._dir.name) / "vault"
        self.raiz.mkdir()
        self._prev = (config.VAULT, vault.VAULT)
        config.VAULT = vault.VAULT = self.raiz

    def tearDown(self) -> None:
        config.VAULT, vault.VAULT = self._prev
        self._dir.cleanup()

    def nota(self, rel: str, cuerpo: str) -> Path:
        dest = self.raiz / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(cuerpo, encoding="utf-8")
        return dest


class Chunker(Base):
    """ARG-01: los encabezados no se funden con la sección anterior."""

    MD = (
        "# Intro\n\n"
        "Este es un párrafo suficientemente largo en la primera sección para superar los "
        "ciento veinte caracteres requeridos por el chunker.\n\n"
        "## Backup\n\nSe guarda en /srv/backup cada noche.\n\n"
        "## Puerto\n\nEl servicio escucha en 8443.\n"
    )

    def test_cada_seccion_conserva_su_heading(self) -> None:
        headings = [c["heading"] for c in chunk(self.MD)]
        self.assertEqual(headings, ["Intro", "Backup", "Puerto"])

    def test_no_emite_chunk_huerfano_de_una_linea(self) -> None:
        primero = chunk(self.MD)[0]
        self.assertIn("\n", primero["text"], "el título quedó solo en su chunk")

    def test_seccion_sin_cuerpo_no_desaparece(self) -> None:
        headings = [c["heading"] for c in chunk("## A\n\n## B\n\ncuerpo de B, con texto.\n")]
        self.assertEqual(headings, ["A", "B"])

    def test_texto_y_offsets_reconstruyen_lo_mismo(self) -> None:
        # Invariante del índice sin texto: lo guardado en `text` tiene que ser exactamente
        # lo que se relee por off_start/off_end.
        lineas = self.MD.split("\n")
        for c in chunk(self.MD):
            releido = "\n".join(lineas[c["off_start"]:c["off_end"] + 1]).strip()
            self.assertEqual(c["text"], releido)


class Edicion(Base):
    def test_replace_multilinea_no_corrompe(self) -> None:
        """ARG-06: `old` va como parámetro, no serializado con `\\n`."""
        self.nota("x.md", "# X\n\nlinea uno\nlinea dos\ncola\n")
        vault.edit_note("x.md", "replace", "NUEVO", old="linea uno\nlinea dos")
        cuerpo = (self.raiz / "x.md").read_text(encoding="utf-8")
        self.assertEqual(cuerpo, "# X\n\nNUEVO\ncola\n")

    def test_replace_sin_old_falla(self) -> None:
        self.nota("x.md", "# X\n\ncuerpo\n")
        with self.assertRaises(ValueError):
            vault.edit_note("x.md", "replace", "nuevo")

    def test_replace_old_new_ya_no_existe(self) -> None:
        self.nota("x.md", "# X\n\ncuerpo\n")
        with self.assertRaises(ValueError):
            vault.edit_note("x.md", "replace-old-new", "viejo\nnuevo")


class Extensiones(Base):
    """ARG-11: `.md` se normaliza en TODAS las operaciones, no sólo en write."""

    def test_move_sin_extension_no_pierde_la_nota(self) -> None:
        self.nota("a.md", "# A\n\ncuerpo\n")
        vault.move_note("a.md", "b")
        self.assertTrue((self.raiz / "b.md").is_file())
        self.assertFalse((self.raiz / "b").exists())
        self.assertIn("cuerpo", vault.read_note("b"))

    def test_read_y_delete_sin_extension(self) -> None:
        self.nota("a.md", "# A\n\ncuerpo\n")
        self.assertIn("cuerpo", vault.read_note("a"))
        vault.delete_note("a")
        self.assertFalse((self.raiz / "a.md").exists())

    def test_alias_interno_conserva_su_nombre(self) -> None:
        self.nota("real.md", "# Real\n\ncuerpo\n")
        (self.raiz / "alias.md").symlink_to(self.raiz / "real.md")
        nombres = {n["name"] for n in vault.list_notes()}
        self.assertIn("alias.md", nombres)
        self.assertIn("real.md", nombres)


class Confinamiento(Base):
    """ARG-02: `_recorrer` no puede leer fuera del vault ni ver ocultos."""

    def test_symlink_externo_no_se_lee_ni_rompe_el_comando(self) -> None:
        self.nota("nota.md", "# Nota\n\ncuerpo\n")
        secreto = Path(tempfile.mkdtemp(prefix="fuera-")) / "afuera.txt"
        secreto.write_text("SECRETO-FUERA token=abc\n", encoding="utf-8")
        try:
            (self.raiz / "evil.md").symlink_to(secreto)
            self.assertEqual(vault.search_notes(["SECRETO-FUERA"]), [])
            nombres = {n["name"] for n in vault.list_notes()}
            self.assertNotIn("evil.md", nombres)
        finally:
            secreto.unlink()
            secreto.parent.rmdir()

    def test_carpetas_ocultas_no_se_listan(self) -> None:
        self.nota("nota.md", "# Nota\n\ncuerpo\n")
        self.nota(".stversions/vieja.md", "# Vieja\n\ntag #borrada\n")
        self.assertEqual([n["name"] for n in vault.list_notes()], ["nota.md"])


class Metadatos(Base):
    """ARG-12: tags escalar y backlinks por prefijo."""

    def test_tags_escalar_no_se_itera_por_caracter(self) -> None:
        self.nota("m.md", "---\ntags: infra\n---\n\n# M\n\ncuerpo\n")
        salida = vault.get_note_metadata("m.md")
        self.assertIn("tags: infra", salida)
        self.assertNotIn("tags: a, f", salida)

    def test_backlink_por_prefijo_no_cuenta(self) -> None:
        self.nota("uno.md", "# Uno\n\ncuerpo\n")
        self.nota("uno-dos.md", "# Uno Dos\n\nVer [[uno-dos]].\n")
        salida = vault.get_note_metadata("uno.md")
        self.assertIn("backlinks: 0", salida)

    def test_backlink_exacto_si_cuenta(self) -> None:
        self.nota("uno.md", "# Uno\n\ncuerpo\n")
        self.nota("otra.md", "# Otra\n\nVer [[uno]] y [[uno|alias]].\n")
        self.assertIn("backlinks: 1", vault.get_note_metadata("uno.md"))


class Grafo(Base):
    """ARG-05: `.rstrip(".md")` mutilaba nombres terminados en m/d."""

    def test_wikilink_a_nota_terminada_en_d_no_es_roto(self) -> None:
        g = graph.build_graph({
            "systemd.md": "# systemd\n\nservicio\n",
            "otra.md": "# Otra\n\nVer [[systemd]].\n",
            "ovaultd.md": "# ovaultd\n\nsync\n",
            "tercera.md": "# Tercera\n\nVer [[ovaultd]].\n",
        })
        self.assertEqual(g["broken"], [])
        self.assertEqual(len(g["out"]["otra.md"]), 1)


class Vecinos(unittest.TestCase):
    """ARG-04 / ARG-15: pares recíprocos y vectores vacíos."""

    def test_par_reciproco_propone_una_direccion(self) -> None:
        import numpy as np
        v = np.ones(4, dtype=np.float32) / 2.0
        prop = link.vecinos({"nota-a.md": v, "nota-b.md": v}, {}, 0.78, 4)
        self.assertEqual(sum(len(x) for x in prop.values()), 1, prop)
        self.assertEqual(len(prop), 1, "el link se propuso desde las dos notas")

    def test_sin_vectores_no_explota(self) -> None:
        self.assertEqual(link.vecinos({}, {}, 0.5, 4), {})
        self.assertEqual(link.detecta_canonicos({}, {}, 0.9), [])

    def test_canonicidad_cuenta_entrantes(self) -> None:
        cur = {"a.md": {"hub.md"}, "b.md": {"hub.md"}}
        self.assertGreater(link.score_de_canonicidad("hub.md", cur),
                           link.score_de_canonicidad("a.md", cur))


if __name__ == "__main__":
    unittest.main()
