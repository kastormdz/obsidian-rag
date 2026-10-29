"""Tests de la búsqueda: evidencia léxica, vacío en vez de ruido, y contrato de salida.

Necesitan fastembed (si no está, se saltean): construyen un índice temporal con un
vault de 4 notas y nunca tocan el vault real.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

HAY_FASTEMBED = importlib.util.find_spec("fastembed") is not None

NOTAS = {
    "docker.md": (
        "# Docker\n\n"
        "Servicios en contenedores con docker compose y volúmenes persistentes para los datos.\n"
        "El arranque se hace con compose up -d y el estado se mira con docker ps.\n"
    ),
    "certificados.md": (
        "# Certificados\n\n"
        "Renovación automática de certificados wildcard con certbot y el plugin de DNS.\n"
        "Los certificados vencen a los 90 días y el timer los renueva antes.\n"
    ),
    "ram.md": (
        "# Memoria\n\n"
        "zram comprime la memoria en RAM y zswap hace swap comprimido en un dispositivo de bloque.\n"
        "En un equipo con poca RAM conviene zram antes que una partición de swap grande.\n"
    ),
    "raro.md": (
        "# Informe\n\n"
        "El identificador interno del incidente fue buchovial-8891 y quedó documentado acá.\n"
        "Ninguna otra nota menciona ese identificador en todo el vault.\n"
    ),
}


@unittest.skipUnless(HAY_FASTEMBED, "fastembed no está instalado en este intérprete")
class Busqueda(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from rag import index as idx

        cls.idx = idx
        cls._dir = tempfile.TemporaryDirectory()
        raiz = Path(cls._dir.name)
        vault = raiz / "vault"
        vault.mkdir()
        for nombre, cuerpo in NOTAS.items():
            (vault / nombre).write_text(cuerpo, encoding="utf-8")

        cls._vault_previo, cls._db_previo = idx.VAULT, idx.DB
        idx.VAULT = str(vault)
        idx.DB = str(raiz / "index.db")
        idx._MODEL_HOLDER[0] = None
        idx.build(force=True)

    @classmethod
    def tearDownClass(cls) -> None:
        idx = cls.idx
        idx.VAULT, idx.DB = cls._vault_previo, cls._db_previo
        idx._MODEL_HOLDER[0] = None
        cls._dir.cleanup()

    def test_termino_exacto_va_primero(self) -> None:
        res = self.idx.search("docker compose", 3)
        self.assertTrue(res, "una consulta con dos términos presentes no puede volver vacía")
        self.assertIn("docker.md", res[0]["path"])

    def test_identificador_raro_va_primero(self) -> None:
        res = self.idx.search("buchovial-8891", 3)
        self.assertTrue(res)
        self.assertIn("raro.md", res[0]["path"])

    def test_termino_inexistente_devuelve_vacio(self) -> None:
        # El peor modo de falla es devolver ruido con score: un consumidor lo lee
        # como acierto. Sin evidencia léxica y con coseno bajo, la respuesta es [].
        self.assertEqual(self.idx.search("zqxjkv-9931-no-existe", 3), [])

    def test_no_rompe_con_stopwords(self) -> None:
        self.assertEqual(self.idx.search("de la el los", 3), [])

    def test_resultados_traen_texto_y_van_ordenados(self) -> None:
        res = self.idx.search("renovación de certificados", 3)
        self.assertTrue(res)
        for r in res:
            self.assertTrue(r["text"], "cada resultado tiene que traer el texto del chunk")
            self.assertIn("path", r)
            self.assertIn("score", r)
        puntajes = [r["score"] for r in res]
        self.assertEqual(puntajes, sorted(puntajes, reverse=True))

    def test_indice_vacio_no_explota(self) -> None:
        idx = self.idx
        db_previo = idx.DB
        with tempfile.TemporaryDirectory() as tmp:
            idx.DB = str(Path(tmp) / "vacio.db")
            try:
                self.assertEqual(idx.search("docker", 3), [])
            finally:
                idx.DB = db_previo

    def test_chunker_version_fuerza_rebuild(self) -> None:
        # Un índice construido con otro criterio de troceado no puede sobrevivir a
        # un `reindex` incremental: el hash de la nota no cambió, el índice sí.
        idx = self.idx
        c = idx.conn()
        idx._set_meta(c, "chunker", 999)
        idx._set_meta(c, "modelo", "modelo-viejo")
        c.commit()
        idx.build(force=False)
        c = idx.conn()
        self.assertEqual(idx._meta(c, "chunker"), str(idx.CHUNKER_VERSION))
        self.assertEqual(idx._meta(c, "modelo"), idx.MODELO)


NOTAS_VIGENCIA = {
    # mismo texto en las dos: la única diferencia es la fecha de vencimiento
    "vigente.md": (
        "---\nstale_after: 2099-01-01\n---\n"
        "# Puerto\n\nEl puerto del servicio es 8080 y se cambia en el compose.\n"
    ),
    "vencida.md": (
        "---\nstale_after: 2020-01-01\n---\n"
        "# Puerto\n\nEl puerto del servicio es 8080 y se cambia en el compose.\n"
    ),
}


@unittest.skipUnless(HAY_FASTEMBED, "fastembed no está instalado en este intérprete")
class VigenciaEnRanking(unittest.TestCase):
    """Una nota vencida baja en el ranking; no desaparece salvo que se pida."""

    @classmethod
    def setUpClass(cls) -> None:
        from rag import index as idx

        cls.idx = idx
        cls._dir = tempfile.TemporaryDirectory()
        raiz = Path(cls._dir.name)
        vault = raiz / "vault"
        vault.mkdir()
        for nombre, cuerpo in NOTAS_VIGENCIA.items():
            (vault / nombre).write_text(cuerpo, encoding="utf-8")

        cls._vault_previo, cls._db_previo = idx.VAULT, idx.DB
        idx.VAULT = str(vault)
        idx.DB = str(raiz / "index.db")
        idx._MODEL_HOLDER[0] = None
        idx.build(force=True)

    @classmethod
    def tearDownClass(cls) -> None:
        idx = cls.idx
        idx.VAULT, idx.DB = cls._vault_previo, cls._db_previo
        idx._MODEL_HOLDER[0] = None
        cls._dir.cleanup()

    def test_vigente_gana_el_primer_puesto(self) -> None:
        r = self.idx.search("puerto del servicio", k=5)
        self.assertEqual(r[0]["path"], "vigente.md", [x["path"] for x in r])

    def test_vencida_sigue_apareciendo_y_se_marca(self) -> None:
        r = {x["path"]: x for x in self.idx.search("puerto del servicio", k=5)}
        self.assertIn("vencida.md", r)
        self.assertTrue(r["vencida.md"]["vencida"])
        self.assertGreater(r["vencida.md"]["score"], 0)
        self.assertFalse(r["vigente.md"]["vencida"])

    def test_fresh_only_excluye_vencidas(self) -> None:
        paths = [x["path"] for x in self.idx.search("puerto del servicio", k=5, fresh_only=True)]
        self.assertIn("vigente.md", paths)
        self.assertNotIn("vencida.md", paths)


@unittest.skipUnless(HAY_FASTEMBED, "fastembed no está instalado en este intérprete")
class VigenciaSinFecha(unittest.TestCase):
    """Una nota sin `stale_after` no opina: `vencida` es None, no False."""

    @classmethod
    def setUpClass(cls) -> None:
        from rag import index as idx

        cls.idx = idx
        cls._dir = tempfile.TemporaryDirectory()
        raiz = Path(cls._dir.name)
        vault = raiz / "vault"
        vault.mkdir()
        (vault / "sin-fecha.md").write_text(
            "# Puerto\n\nEl puerto del servicio es 8080 y se cambia en el compose.\n",
            encoding="utf-8")
        cls._vault_previo, cls._db_previo = idx.VAULT, idx.DB
        idx.VAULT = str(vault)
        idx.DB = str(raiz / "index.db")
        idx._MODEL_HOLDER[0] = None
        idx.build(force=True)

    @classmethod
    def tearDownClass(cls) -> None:
        idx = cls.idx
        idx.VAULT, idx.DB = cls._vault_previo, cls._db_previo
        idx._MODEL_HOLDER[0] = None
        cls._dir.cleanup()

    def test_vencida_es_none_cuando_no_hay_fecha(self) -> None:
        r = self.idx.search("puerto del servicio", k=5)
        self.assertTrue(r, "la nota sin fecha tiene que seguir apareciendo")
        self.assertIsNone(r[0]["vencida"])
        self.assertIsNone(r[0]["stale_after"])


if __name__ == "__main__":
    unittest.main()
