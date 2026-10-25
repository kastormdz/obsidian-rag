"""Tests de `obsn upgrade`: detección de versión nueva y negativa con árbol sucio.

Usan dos repos git locales (un bare como "origin" y un clon como "instalación"):
sin red, sin tocar el repo real y sin pip.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from rag import upgrade

GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]


def git(*args: str, cwd: Path) -> None:
    subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True)


class Upgrade(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        raiz = Path(self._dir.name)
        self.origen = raiz / "origen.git"
        self.trabajo = raiz / "trabajo"
        self.otro = raiz / "otro"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.origen)],
                       check=True, capture_output=True, text=True)
        subprocess.run(["git", "clone", str(self.origen), str(self.trabajo)],
                       check=True, capture_output=True, text=True)
        git("checkout", "-b", "main", cwd=self.trabajo)
        (self.trabajo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        git("add", "-A", cwd=self.trabajo)
        git("commit", "-m", "inicial", cwd=self.trabajo)
        git("push", "-u", "origin", "main", cwd=self.trabajo)
        # Un segundo clon hace de "otra máquina" que publica una versión nueva.
        subprocess.run(["git", "clone", str(self.origen), str(self.otro)],
                       check=True, capture_output=True, text=True)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _publicar(self, mensaje: str) -> None:
        (self.otro / "nuevo.txt").write_text(mensaje, encoding="utf-8")
        git("add", "-A", cwd=self.otro)
        git("commit", "-m", mensaje, cwd=self.otro)
        git("push", "origin", "HEAD:main", cwd=self.otro)

    def test_al_dia(self) -> None:
        est = upgrade.estado(self.trabajo)
        self.assertEqual(est["atras"], 0)
        self.assertEqual(est["adelante"], 0)
        self.assertFalse(est["sucio"])
        self.assertEqual(est["rama"], "main")
        self.assertEqual(est["remoto"], est["head"])

    def test_detecta_version_nueva(self) -> None:
        self._publicar("mejora")
        est = upgrade.estado(self.trabajo)
        self.assertEqual(est["atras"], 1, "tiene que ver el commit nuevo del remoto")

    def test_detecta_arbol_sucio(self) -> None:
        (self.trabajo / "local.txt").write_text("sin commitear", encoding="utf-8")
        est = upgrade.estado(self.trabajo)
        self.assertTrue(est["sucio"])
        self.assertIn("local.txt", est["archivos_sucios"])

    def test_check_no_modifica_y_devuelve_codigo(self) -> None:
        self.assertEqual(upgrade.main(check=True, repo=self.trabajo, reindexar=False),
                         upgrade.EXIT_AL_DIA)
        self._publicar("otra mejora")
        self.assertEqual(upgrade.main(check=True, repo=self.trabajo, reindexar=False),
                         upgrade.EXIT_HAY_UPDATE)
        # --check no puede haber movido el HEAD
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.trabajo,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(upgrade.estado(self.trabajo, fetch=False)["head"], head)

    def test_no_actualiza_con_arbol_sucio(self) -> None:
        self._publicar("mejora")
        (self.trabajo / "local.txt").write_text("sin commitear", encoding="utf-8")
        self.assertEqual(upgrade.main(repo=self.trabajo, reindexar=False), upgrade.EXIT_ERROR)
        self.assertEqual(upgrade.estado(self.trabajo, fetch=False)["atras"], 1,
                         "con el árbol sucio no puede haber avanzado")

    def test_upgrade_hace_fast_forward(self) -> None:
        self._publicar("mejora")
        antes = upgrade.estado(self.trabajo, fetch=False)["head"]
        self.assertEqual(upgrade.main(repo=self.trabajo, reindexar=False), upgrade.EXIT_AL_DIA)
        despues = upgrade.estado(self.trabajo, fetch=False)
        self.assertEqual(despues["atras"], 0)
        self.assertNotEqual(antes, despues["head"], "el HEAD tiene que haberse movido")

    # --- versiones (tags)

    def _tag(self, version: str) -> None:
        git("tag", "-a", f"v{version}", "-m", f"v{version}", cwd=self.otro)
        git("push", "origin", f"v{version}", cwd=self.otro)

    def test_lee_el_tag_mas_alto(self) -> None:
        self._tag("0.1.0")
        self._publicar("cambios para la 0.2.0")
        self._tag("0.2.0")
        # `estado` es quien hace el fetch con --tags; después se lee del clon local.
        self.assertEqual(upgrade.estado(self.trabajo)["ultima_version"], "0.2.0")
        self.assertEqual(upgrade.ultima_version(self.trabajo), "0.2.0")

    def test_un_commit_sin_tag_no_es_version_publicada(self) -> None:
        self._publicar("commit que nadie tageó")
        self.assertIsNone(upgrade.estado(self.trabajo)["ultima_version"])

    def test_check_avisa_por_version_y_no_solo_por_commits(self) -> None:
        from unittest import mock

        self._tag("0.1.0")
        with mock.patch.object(upgrade, "version_instalada", return_value="0.1.0"):
            self.assertEqual(upgrade.main(check=True, repo=self.trabajo, reindexar=False),
                             upgrade.EXIT_AL_DIA)
        self._publicar("cambios para la 0.2.0")
        self._tag("0.2.0")
        with mock.patch.object(upgrade, "version_instalada", return_value="0.1.0"):
            self.assertEqual(upgrade.main(check=True, repo=self.trabajo, reindexar=False),
                             upgrade.EXIT_HAY_UPDATE)

    def test_version_instalada_mas_nueva_que_el_tag_no_alerta(self) -> None:
        from unittest import mock

        self._tag("0.1.0")
        with mock.patch.object(upgrade, "version_instalada", return_value="0.2.0"):
            self.assertEqual(upgrade.main(check=True, repo=self.trabajo, reindexar=False),
                             upgrade.EXIT_AL_DIA)

    def test_como_funciona_no_falla(self) -> None:
        self.assertEqual(upgrade.main(how=True), 0)

    def test_repo_dir_es_el_repo_editable(self) -> None:
        repo = upgrade.repo_dir()
        self.assertIsNotNone(repo, "el paquete se instala editable: tiene que ver el repo")
        assert repo is not None
        self.assertTrue((repo / ".git").exists())


if __name__ == "__main__":
    unittest.main()
