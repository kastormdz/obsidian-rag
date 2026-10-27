"""Build transaccional: una nota que falla no puede llevarse el índice puesto.

Sin SAVEPOINT por nota, el `DELETE` de una nota que después explotaba quedaba firme en
el commit de la nota 10 y sus chunks desaparecían del índice hasta el próximo build.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

NOTAS = {
    "buena.md": "# Buena\n\nServicio de docker compose con volúmenes persistentes.\n",
    "mala.md": "# Mala\n\nNota que después se rompe a propósito.\n",
}


class BuildTransaccional(unittest.TestCase):
    def setUp(self) -> None:
        from rag import index as idx

        self.idx = idx
        self._dir = tempfile.TemporaryDirectory()
        raiz = Path(self._dir.name)
        self.vault = raiz / "vault"
        self.vault.mkdir()
        for nombre, cuerpo in NOTAS.items():
            (self.vault / nombre).write_text(cuerpo, encoding="utf-8")

        self._vault_previo, self._db_previo = idx.VAULT, idx.DB
        self._embed_previo = idx.embed_all
        idx.VAULT, idx.DB = str(self.vault), str(raiz / "index.db")
        # Sin modelo: el test es de transacciones, no de embeddings.
        idx.embed_all = lambda textos, c, model=None: [None] * len(textos)
        idx.build(force=True)
        # Marcar el índice invertido como al día: si no, el segundo build corre la
        # migración de postings y el fallo inyectado abajo saltaría fuera del savepoint.
        with sqlite3.connect(idx.DB) as c:
            idx._set_meta(c, "postings", idx.POSTINGS_VERSION)
            c.commit()

    def tearDown(self) -> None:
        idx = self.idx
        idx.VAULT, idx.DB = self._vault_previo, self._db_previo
        idx.embed_all = self._embed_previo
        self._dir.cleanup()

    def _chunks(self, path: str) -> list[str]:
        """Reconstruye el texto de cada chunk desde el vault (el índice guarda offsets)."""
        with sqlite3.connect(self.idx.DB) as c:
            rangos = list(c.execute(
                "SELECT off_start, off_end FROM chunks WHERE path=? ORDER BY ord", (path,)
            ))
        lineas = (self.vault / path).read_text(encoding="utf-8").split("\n")
        return ["\n".join(lineas[a:b + 1]).strip() for a, b in rangos]

    def test_una_nota_que_falla_conserva_sus_chunks_y_no_frena_al_resto(self) -> None:
        (self.vault / "buena.md").write_text(
            "# Buena\n\nContenido NUEVO con zanahoria.\n", encoding="utf-8"
        )
        (self.vault / "mala.md").write_text(
            "# Mala\n\nAhora tiene EXPLOTA adentro.\n", encoding="utf-8"
        )
        original = self.idx._fila_postings

        def rompe(texto: str, metadatos: str):
            if "EXPLOTA" in texto:
                raise RuntimeError("boom")
            return original(texto, metadatos)

        self.idx._fila_postings = rompe
        try:
            # build() corta con exit 1 para que nadie crea que el índice quedó entero
            with self.assertRaises(SystemExit):
                self.idx.build(force=True)
        finally:
            self.idx._fila_postings = original

        # La que falló conserva su fila y sus chunks: el rollback deshace el DELETE.
        with sqlite3.connect(self.idx.DB) as c:
            hash_mala = c.execute("SELECT hash FROM notes WHERE path='mala.md'").fetchone()[0]
            n_mala = c.execute("SELECT COUNT(*) FROM chunks WHERE path='mala.md'").fetchone()[0]
        nuevo_mala = hashlib.sha1(
            (self.vault / "mala.md").read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        self.assertEqual(n_mala, 1, "la nota que falló se quedó sin chunks")
        self.assertNotEqual(hash_mala, nuevo_mala,
                            "el build fallido dejó la nota marcada como indexada")
        # La otra se indexó igual: un fallo no frena al resto.
        self.assertTrue(any("zanahoria" in t for t in self._chunks("buena.md")))

    def _n_chunks(self, path: str) -> int:
        """Cuenta chunks en la DB (sirve aunque la nota ya no exista en disco)."""
        with sqlite3.connect(self.idx.DB) as c:
            return c.execute("SELECT COUNT(*) FROM chunks WHERE path=?", (path,)).fetchone()[0]

    def test_no_vacia_el_indice_si_el_vault_no_existe(self) -> None:
        # Un OBS_RAG_VAULT mal apuntado devolvía cero notas y el purgado borraba el índice
        # entero, dejándolo "ready". Ahora se niega antes de tocar nada.
        antes = self._chunks("buena.md")
        previo = self.idx.VAULT
        self.idx.VAULT = "/ruta/que/no/existe"
        try:
            with self.assertRaises(SystemExit) as ctx:
                self.idx.build(force=True)
        finally:
            self.idx.VAULT = previo
        self.assertIn("OBS_RAG_VAULT", str(ctx.exception))
        self.assertEqual(self._chunks("buena.md"), antes, "el índice quedó vaciado")

    def test_no_vacia_el_indice_si_el_vault_queda_sin_notas(self) -> None:
        for nota in self.vault.glob("*.md"):
            nota.unlink()
        with self.assertRaises(SystemExit) as ctx:
            self.idx.build()
        self.assertIn("apunta al lugar equivocado", str(ctx.exception))
        self.assertEqual(self._n_chunks("buena.md"), 1, "se purgó el índice sin permiso")
        # Con --force sí se puede vaciar a propósito
        self.idx.build(force=True)
        self.assertEqual(self._n_chunks("buena.md"), 0)


class ErroresDeCli(unittest.TestCase):
    def test_status_con_indice_sin_tabla_notes_avisa_que_hacer(self) -> None:
        from rag import config, obsn

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "viejo.db"
            sqlite3.connect(db).close()  # DB vacía: sin tabla `notes`
            previo = config.DB
            config.DB = db
            try:
                with self.assertRaises(SystemExit) as ctx:
                    obsn.cmd_status(argparse.Namespace(json=False))
            finally:
                config.DB = previo
        self.assertIn("reindex", str(ctx.exception))

    def test_list_limit_negativo_se_rechaza(self) -> None:
        from rag import obsn

        args = argparse.Namespace(limit=-1, folder=None, tag=None, json=False)
        with self.assertRaises(SystemExit):
            obsn.cmd_list(args)

    def test_graph_y_link_aceptan_sus_propios_flags(self) -> None:
        # `--stats` no está declarado en el parser de obsn: lo valida graph.main(). Antes,
        # `obsn graph --stats` moría con "unrecognized arguments".
        import contextlib
        import io

        from rag import config, obsn

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            (vault / "uno.md").write_text("# Uno\n\nVease [[dos]].\n", encoding="utf-8")
            (vault / "dos.md").write_text("# Dos\n\nNada que enlazar.\n", encoding="utf-8")
            previo = config.VAULT
            config.VAULT = vault
            try:
                salida = io.StringIO()
                with contextlib.redirect_stdout(salida):
                    rc = obsn.main(["graph", "--stats"])
            finally:
                config.VAULT = previo
        self.assertEqual(rc, 0)
        self.assertIn("notas=2", salida.getvalue())


if __name__ == "__main__":
    unittest.main()
