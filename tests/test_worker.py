"""Tests del worker de reindexado: no se pisan dos workers sobre el mismo índice."""

from __future__ import annotations

import fcntl
import os
import tempfile
import unittest
from unittest import mock


class Worker(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = {k: os.environ.get(k) for k in ("XDG_STATE_HOME", "OBS_RAG_DEBOUNCE")}
        os.environ["XDG_STATE_HOME"] = self._tmp.name
        os.environ["OBS_RAG_DEBOUNCE"] = "0"

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_se_retira_si_otro_worker_tiene_el_lock(self) -> None:
        from rag import index, reindex_worker

        base = reindex_worker.estado_dir()
        with open(base / "reindex.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(index, "build",
                                   side_effect=AssertionError("no debía reindexar")):
                # El worker tiene que retirarse en silencio: el que corre va a ver el cambio.
                self.assertEqual(reindex_worker.main(), 0)

    def test_corre_si_el_lock_esta_libre(self) -> None:
        from rag import index, reindex_worker

        llamadas = []
        with mock.patch.object(index, "build", side_effect=lambda force=False: llamadas.append(1)):
            self.assertEqual(reindex_worker.main(), 0)
        self.assertEqual(llamadas, [1], "sin lock tiene que reindexar una vez")

    def test_escribe_log(self) -> None:
        from rag import index, reindex_worker

        with mock.patch.object(index, "build", return_value=None):
            reindex_worker.main()
        log = reindex_worker.estado_dir() / "reindex.log"
        self.assertTrue(log.exists())
        self.assertIn("reindex async", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
