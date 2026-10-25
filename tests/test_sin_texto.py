"""El índice no guarda el vault en claro: sólo términos, offsets y vectores.

El texto de cada chunk se relee del vault por su rango de líneas. Estos tests fijan las
dos mitades del contrato: que el texto NO esté en la DB y que se reconstruya idéntico.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

FRASE = "el codigo secreto de la bodega es zanahoria morada 9931"
NOTAS = {
    "bodega.md": f"# Bodega\n\n{FRASE}.\n",
    "otra.md": "# Otra\n\nDocker compose y volúmenes persistentes para los servicios.\n",
}


class SinTextoEnClaro(unittest.TestCase):
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
        # Sin modelo: acá se prueba el almacenamiento, no la calidad del embedding.
        idx.embed_all = lambda textos, c, model=None: [None] * len(textos)
        idx.build(force=True)

    def tearDown(self) -> None:
        idx = self.idx
        idx.VAULT, idx.DB = self._vault_previo, self._db_previo
        idx.embed_all = self._embed_previo
        self._dir.cleanup()

    def test_la_tabla_chunks_no_tiene_columna_text(self) -> None:
        with sqlite3.connect(self.idx.DB) as c:
            columnas = {r[1] for r in c.execute("PRAGMA table_info(chunks)")}
        self.assertNotIn("text", columnas)
        self.assertIn("off_start", columnas)
        self.assertIn("off_end", columnas)

    def test_el_contenido_no_esta_en_la_db_ni_en_el_wal(self) -> None:
        # Checkpoint para que el WAL no esconda datos recién escritos.
        with sqlite3.connect(self.idx.DB) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        crudo = b""
        for sufijo in ("", "-wal"):
            p = Path(str(self.idx.DB) + sufijo)
            if p.exists():
                crudo += p.read_bytes()
        self.assertNotIn(FRASE.encode(), crudo, "la frase quedó guardada en claro")
        # Los términos sueltos SÍ están (es un índice invertido): eso se acepta y se
        # documenta. Lo que no está es el texto legible con su orden y puntuación.
        self.assertIn(b"zanahoria", crudo)

    def test_el_texto_se_reconstruye_igual_que_el_chunker(self) -> None:
        cuerpo = (self.vault / "bodega.md").read_text(encoding="utf-8")
        original = [ch["text"] for ch in self.idx.chunk(cuerpo)]
        with sqlite3.connect(self.idx.DB) as c:
            rangos = list(c.execute(
                "SELECT off_start, off_end FROM chunks WHERE path='bodega.md' ORDER BY ord"))
        reconstruido = [self.idx._texto_del_chunk("bodega.md", a, b) for a, b in rangos]
        self.assertEqual(reconstruido, original)

    def test_search_devuelve_texto_leido_del_vault(self) -> None:
        res = self.idx.search("codigo secreto bodega", 3)
        self.assertTrue(res)
        self.assertIn("bodega.md", res[0]["path"])
        self.assertIn("zanahoria morada", res[0]["text"])

    def test_la_migracion_descarta_el_indice_con_texto(self) -> None:
        viejo = Path(self._dir.name) / "viejo.db"
        c = sqlite3.connect(viejo)
        c.executescript(
            "CREATE TABLE notes(path TEXT PRIMARY KEY, hash TEXT);"
            "CREATE TABLE chunks(id INTEGER PRIMARY KEY, path TEXT, ord INTEGER, text TEXT);"
            "INSERT INTO notes VALUES('a.md','x');"
            "INSERT INTO chunks VALUES(1,'a.md',0,'hola mundo');"
        )
        c.commit()
        c.close()
        previo = self.idx.DB
        self.idx.DB = str(viejo)
        try:
            nuevo = self.idx.conn()
            columnas = {r[1] for r in nuevo.execute("PRAGMA table_info(chunks)")}
            self.assertNotIn("text", columnas, "la migración no recreó la tabla")
            self.assertEqual(nuevo.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 0)
            nuevo.close()
        finally:
            self.idx.DB = previo


if __name__ == "__main__":
    unittest.main()
