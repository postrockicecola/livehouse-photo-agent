from __future__ import annotations

import unittest

import cv2
import numpy as np

from op_kernel import apply_optical_console_enhancements, apply_optical_p1_enhancements
from services.film_render_service import _apply_variant, normalize_grade_luminance
from services.optical_params import OpticalP1Params, parse_optical_p1


class OpticalP1Tests(unittest.TestCase):
    def test_parse_optical_p1_json(self) -> None:
        p = parse_optical_p1('{"flow": 22, "wear": 12, "flow_angle": -20, "air": 40}')
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.flow, 22.0)
        self.assertEqual(p.wear, 12.0)
        self.assertEqual(p.air, 40.0)
        self.assertEqual(p.flow_angle, -20.0)
        self.assertIn("f22", p.cache_token())

    def test_parse_optical_p1_empty_inactive(self) -> None:
        self.assertIsNone(parse_optical_p1('{"flow": 0, "wear": 0}'))
        self.assertIsNone(parse_optical_p1(None))
        self.assertIsNone(parse_optical_p1(""))

    def test_parse_exposure_only_is_active_and_clamped(self) -> None:
        p = parse_optical_p1('{"exposure": 4}')
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.exposure, 1.5)
        self.assertIn("ev+1.50", p.cache_token())

    def test_apply_optical_p1_identity(self) -> None:
        rgb = np.random.default_rng(0).integers(0, 255, (240, 360, 3), dtype=np.uint8)
        out = apply_optical_p1_enhancements(rgb, flow=0, wear=0)
        np.testing.assert_array_equal(out, rgb)

    def test_apply_optical_p1_flow_changes_image(self) -> None:
        rgb = np.zeros((180, 280, 3), dtype=np.uint8)
        rgb[80:100, 40:240] = [255, 220, 180]
        base = apply_optical_p1_enhancements(rgb, flow=0, wear=0)
        smear = apply_optical_p1_enhancements(rgb, flow=75, wear=0, flow_angle=-15)
        self.assertEqual(smear.shape, rgb.shape)
        region = smear[75:105, 35:245].astype(np.int16) - base[75:105, 35:245].astype(np.int16)
        self.assertGreater(float(np.max(np.abs(region))), 2.0)

    def test_apply_optical_p1_wear_changes_image(self) -> None:
        rgb = np.random.default_rng(2).integers(20, 220, (200, 300, 3), dtype=np.uint8)
        base = apply_optical_p1_enhancements(rgb, flow=0, wear=0)
        worn = apply_optical_p1_enhancements(rgb, flow=0, wear=40)
        self.assertFalse(np.array_equal(base, worn))

    def test_dream_effect_preserves_subject_edge_detail(self) -> None:
        rgb = np.full((240, 360, 3), 25, dtype=np.uint8)
        for y in range(50, 190, 12):
            for x in range(90, 270, 12):
                rgb[y : y + 6, x : x + 6] = 220
        dreamy = apply_optical_console_enhancements(rgb, dream=75)
        before = cv2.Laplacian(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), cv2.CV_32F).var()
        after = cv2.Laplacian(cv2.cvtColor(dreamy, cv2.COLOR_RGB2GRAY), cv2.CV_32F).var()
        self.assertGreater(after / before, 0.45)

    def test_apply_variant_with_optical_p1(self) -> None:
        rgb = np.random.default_rng(3).integers(0, 255, (256, 384, 3), dtype=np.uint8)
        plain = _apply_variant(rgb, "film_livehouse")
        from services.optical_params import OpticalConsoleParams

        stacked = _apply_variant(
            rgb,
            "film_livehouse",
            optical=OpticalConsoleParams(flow=18, wear=10),
        )
        self.assertEqual(plain.shape, stacked.shape)
        self.assertFalse(np.array_equal(plain, stacked))

    def test_adaptive_luminance_lifts_dark_grade(self) -> None:
        src = np.full((120, 180, 3), 42, dtype=np.uint8)
        graded = np.full_like(src, 18)
        out = normalize_grade_luminance(src, graded, "film_black_mist")
        self.assertGreater(float(np.median(out)), float(np.median(graded)))
        self.assertLessEqual(int(out.max()), 255)

    def test_manual_exposure_applies_without_adaptive_correction(self) -> None:
        src = np.full((80, 120, 3), 80, dtype=np.uint8)
        graded = np.full_like(src, 80)
        out = normalize_grade_luminance(
            src,
            graded,
            "film_automated",
            manual_exposure_ev=0.5,
            adaptive=False,
        )
        self.assertGreater(float(np.median(out)), 80.0)


if __name__ == "__main__":
    unittest.main()
