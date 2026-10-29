"""Parser y veredicto de vigencia: fechas del frontmatter, sin dependencias."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from rag.vigencia import parse_fecha, resumen, vigencia


class Parser(unittest.TestCase):
    def test_fecha_sin_hora_vence_al_final_del_dia_local(self):
        # 2026-12-31 23:59:59 en -03:00 => 2027-01-01T02:59:59Z
        self.assertEqual(
            parse_fecha("2026-12-31", tz="America/Argentina/Buenos_Aires"),
            datetime(2027, 1, 1, 2, 59, 59, tzinfo=timezone.utc),
        )

    def test_datetime_con_z(self):
        self.assertEqual(
            parse_fecha("2026-12-31T10:00:00Z"),
            datetime(2026, 12, 31, 10, 0, tzinfo=timezone.utc),
        )

    def test_datetime_con_offset(self):
        self.assertEqual(
            parse_fecha("2026-12-31T10:00:00-03:00"),
            datetime(2026, 12, 31, 13, 0, tzinfo=timezone.utc),
        )

    def test_datetime_sin_offset_usa_la_tz_del_vault(self):
        self.assertEqual(
            parse_fecha("2026-12-31T10:00:00", tz="America/Argentina/Buenos_Aires"),
            datetime(2026, 12, 31, 13, 0, tzinfo=timezone.utc),
        )

    def test_basura_devuelve_none(self):
        for malo in ("ayer", "31/12/2026", "", None, "2026-13-45", 20261231):
            self.assertIsNone(parse_fecha(malo), malo)


class Veredicto(unittest.TestCase):
    ahora = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)

    def test_vencida(self):
        # vence el 30 a las 23:59:59 y "ahora" es el 1 a las 12:00 => 1 día y 12 h
        v = vigencia({"stale_after": "2026-10-30"}, ahora=self.ahora, tz="UTC")
        self.assertTrue(v["vencida"])
        self.assertEqual(v["dias_vencida"], 1)
        self.assertEqual(v["stale_after"], "2026-10-30T23:59:59+00:00")

    def test_vencida_hace_horas_es_cero_dias(self):
        v = vigencia({"stale_after": "2026-10-31"}, ahora=self.ahora, tz="UTC")
        self.assertTrue(v["vencida"])
        self.assertEqual(v["dias_vencida"], 0)

    def test_vigente(self):
        v = vigencia({"stale_after": "2026-12-31"}, ahora=self.ahora, tz="UTC")
        self.assertFalse(v["vencida"])
        self.assertIsNone(v["dias_vencida"])

    def test_sin_fecha_no_opina(self):
        v = vigencia({}, ahora=self.ahora)
        self.assertIsNone(v["vencida"])
        self.assertIsNone(v["stale_after"])
        self.assertIsNone(v["updated"])

    def test_fecha_invalida_es_sin_fecha(self):
        v = vigencia({"stale_after": "no es una fecha"}, ahora=self.ahora)
        self.assertIsNone(v["vencida"])

    def test_updated_se_guarda_pero_no_decide(self):
        v = vigencia({"updated": "2026-01-01", "stale_after": "2027-01-01"},
                     ahora=self.ahora, tz="UTC")
        self.assertEqual(v["updated"], "2026-01-01T23:59:59+00:00")
        self.assertFalse(v["vencida"])

    def test_resumen_dice_el_total(self):
        self.assertIn("203", resumen(12, 5, 203))
