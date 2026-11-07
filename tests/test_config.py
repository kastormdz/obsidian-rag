"""Config robusta: valores inválidos no matan el proceso y los paths quedan absolutos.

Cada caso corre en un subproceso: `rag.config` lee el entorno una sola vez, al importarse,
así que recargarlo dentro del mismo intérprete mediría otra cosa.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def correr(codigo: str, cwd: Path, **env: str) -> subprocess.CompletedProcess:
    entorno = {
        **os.environ,
        "PYTHONPATH": f"{RAIZ}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        **env,
    }
    return subprocess.run(
        [sys.executable, "-c", codigo], cwd=cwd, capture_output=True, text=True,
        env=entorno, timeout=60,
    )


class Config(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_penalty_invalido_usa_el_default_y_avisa(self) -> None:
        p = correr(
            "import rag.config as c; print(c.STALE_PENALTY)",
            self.tmp, OBS_RAG_STALE_PENALTY="0,75",
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "0.75")
        self.assertIn("OBS_RAG_STALE_PENALTY", p.stderr)
        self.assertIn("not a number", p.stderr)

    def test_penalty_fuera_de_rango_se_acota(self) -> None:
        p = correr(
            "import rag.config as c; print(c.STALE_PENALTY)",
            self.tmp, OBS_RAG_STALE_PENALTY="9",
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "1.0")
        self.assertIn("outside", p.stderr)

    def test_modelo_vacio_usa_el_default(self) -> None:
        p = correr("import rag.config as c; print(c.MODELO)", self.tmp, OBS_RAG_MODEL="  ")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("paraphrase-multilingual-MiniLM", p.stdout)

    def test_index_relativo_es_absoluto_y_no_explota(self) -> None:
        # `os.path.dirname("index.db")` es "": `os.makedirs("")` tiraba FileNotFoundError.
        p = correr(
            "from rag import index; c = index.conn(); c.close(); print(index.DB)",
            self.tmp, OBS_RAG_INDEX="index.db",
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(Path(p.stdout.strip()), (self.tmp / "index.db").resolve())
        self.assertTrue((self.tmp / "index.db").exists())

    def test_index_en_directorio_inexistente_lo_crea(self) -> None:
        destino = self.tmp / "a" / "b" / "i.db"
        p = correr(
            "from rag import index; c = index.conn(); c.close()",
            self.tmp, OBS_RAG_INDEX=str(destino),
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(destino.exists())


if __name__ == "__main__":
    unittest.main()
