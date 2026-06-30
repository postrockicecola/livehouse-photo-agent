from __future__ import annotations

import unittest

import numpy as np

from op_kernel import apply_optical_p1_enhancements
from services.film_render_service import _apply_variant
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


if __name__ == "__main__":
    unittest.main()
