"""Retículas deformadas y regresión opcional sobre la fotografía real del usuario."""
import io
import os
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app import create_app
from omr25 import read_sheet, sheet_geometry, _read_text, SX, SY
from omr_regions import register_geometry
from tests.test_omr25_engine import render_sheet, desk

REAL_PHOTO = Path(os.environ.get("OMR25_REAL_PHOTO", str(Path(__file__).parent / "fixtures" / "real_photo.jpg")))


class RegionalTrackingTests(unittest.TestCase):
    def test_independent_local_deformation_without_answer_key(self):
        source = read_sheet(desk(render_sheet(name="MUÑOZ ANA", school="SAN JOSE", dpi=180)))["warped_image"]
        gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
        yy, xx = np.indices(gray.shape, dtype=np.float32)
        mx, my = xx.copy(), yy.copy()
        # Distorsiones diferentes en nombres y colegio; las esquinas no cambian.
        for x1, x2, dx, dy in ((17, 171, 8., 9.), (180, 232, 11., -8.)):
            mask = (xx > x1*SX) & (xx < x2*SX) & (yy > 50*SY) & (yy < 192*SY)
            u = (xx-x1*SX)/((x2-x1)*SX)
            v = (yy-50*SY)/(142*SY)
            mx[mask] -= (dx*(.4+.5*u+.1*v))[mask]
            my[mask] -= (dy*(2*v-1))[mask]
        deformed = cv2.remap(gray, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        adjusted, quality = register_geometry(deformed, sheet_geometry(), SX, SY)
        for field, expected in (("name", "MUÑOZ ANA"), ("school", "SAN JOSE")):
            issues = []
            actual = _read_text(deformed, adjusted[field+"_columns"], field, issues)[0]
            self.assertEqual(actual, expected)
            self.assertFalse(issues)
            self.assertTrue(quality[field]["reliable"])
        self.assertNotEqual(quality["name"]["max_offset_mm"], quality["school"]["max_offset_mm"])

    def test_missing_rings_are_not_silently_trusted(self):
        gray = np.full((2480,3508), 255, np.uint8)
        adjusted, quality = register_geometry(gray, sheet_geometry(), SX, SY)
        self.assertTrue(all(not v["reliable"] for v in quality.values()))
        self.assertEqual(adjusted["name_columns"], sheet_geometry()["name_columns"])

    @unittest.skipUnless(REAL_PHOTO.is_file(), "Foto real local no disponible")
    def test_real_photo_no_false_trailing_letters_and_no_forced_key(self):
        result = read_sheet(REAL_PHOTO)
        self.assertEqual(result["grade"], 2)
        self.assertEqual(result["answers"], list("ABCDEXDCBAB-EAB"))
        self.assertTrue(all(s in {"blank","after_end"} for s in result["name_states"][31:]))
        self.assertTrue(all(s in {"blank","after_end"} for s in result["school_states"][8:]))
        self.assertEqual(result["school"], "MARISCAL")
        self.assertEqual(result["name"], "ALBERTO BRAYAN MOROTA SODRIGUEZ")
        self.assertLessEqual(len(result["issues"]), 7)
        self.assertTrue(result["needs_review"])
        self.assertTrue(all(v["reliable"] for v in result["quality"]["region_tracking"].values()))

    @unittest.skipUnless(REAL_PHOTO.is_file(), "Foto real local no disponible")
    def test_reprocess_keeps_original_record_and_manual_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(tmp, testing=True).test_client()
            with REAL_PHOTO.open("rb") as image:
                response = client.post("/api/scan", data={"course":"Matematica", "files":(image, REAL_PHOTO.name)}, content_type="multipart/form-data")
            self.assertEqual(response.status_code, 200)
            record = response.json["records"][0]
            key = list("ABCDEEDCBABCEAB")
            client.post("/api/keys", json={"course":"Matematica", "grade":2, "answers":key})
            confirmed = client.post(f"/api/records/{record['id']}/confirm", json={"reviewed":True, "name":"NOMBRE REVISADO", "school":"COLEGIO REVISADO", "grade":2, "answers":key, "order":"002", "time":"10:10"})
            self.assertEqual(confirmed.status_code, 200)
            url = f"/api/records/{record['id']}/reprocess"
            self.assertEqual(client.post(url, json={}).status_code, 400)
            from unittest.mock import patch
            with patch("omr25.read_sheet", side_effect=ValueError("Imagen de prueba no alineable")):
                failed = client.post(url, json={"reprocess":True})
            self.assertEqual(failed.status_code, 400)
            previous = client.get("/api/records").json["records"][0]
            self.assertTrue(previous["confirmed"])
            self.assertEqual(previous["name"], "NOMBRE REVISADO")
            reread = client.post(url, json={"reprocess":True})
            self.assertEqual(reread.status_code, 200)
            self.assertEqual(reread.json["id"], record["id"])
            self.assertFalse(reread.json["confirmed"])
            self.assertEqual((reread.json["order"],reread.json["time"]), ("002","10:10"))
            self.assertEqual(reread.json["answers"][5], "X")
            self.assertEqual(len(client.get("/api/records").json["records"]), 1)
            self.assertEqual(client.get("/api/export/excel").status_code, 409)


if __name__ == "__main__":
    unittest.main()
