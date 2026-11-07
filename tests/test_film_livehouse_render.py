from __future__ import annotations

import unittest

import numpy as np

from op_kernel import apply_film_default, apply_livehouse_film
from services.film_render_service import FILM_VARIANT_IDS, _apply_variant


class FilmLivehouseRenderTests(unittest.TestCase):
    def test_apply_livehouse_film_small_rgb(self) -> None:
        rgb = np.random.default_rng(0).integers(0, 255, (320, 480, 3), dtype=np.uint8)
        out = apply_livehouse_film(rgb)
        self.assertEqual(out.shape, rgb.shape)
        self.assertEqual(out.dtype, np.uint8)

    def test_apply_variant_film_livehouse(self) -> None:
        rgb = np.random.default_rng(1).integers(0, 255, (256, 384, 3), dtype=np.uint8)
        out = _apply_variant(rgb, "film_livehouse")
        self.assertEqual(out.shape, rgb.shape)

    def test_default_film_is_adaptive_warm_bright_and_grainy(self) -> None:
        dark = np.full((180, 260, 3), 40, dtype=np.uint8)
        out = apply_film_default(dark)

        self.assertIn("film_default", FILM_VARIANT_IDS)
        self.assertEqual(out.shape, dark.shape)
        self.assertEqual(out.dtype, np.uint8)
        self.assertGreater(float(out.mean()), float(dark.mean()) + 20.0)
        self.assertGreater(float(out[:, :, 0].mean()), float(out[:, :, 2].mean()) + 8.0)
        self.assertGreater(float(out.astype(np.float32).var()), 1.0)

    def test_default_film_keeps_bright_frame_highlight_detail(self) -> None:
        ramp = np.linspace(80, 245, 260, dtype=np.uint8)
        rgb = np.broadcast_to(ramp[None, :, None], (180, 260, 3)).copy()
        out = _apply_variant(rgb, "film_default")

        clipped = np.mean(np.all(out == 255, axis=2))
        self.assertLess(float(clipped), 0.15)
        self.assertGreater(float(np.percentile(out, 90)), 210.0)
        self.assertLess(float(np.percentile(out, 90)), float(np.percentile(rgb, 90)))

    def test_default_film_reduces_dominant_magenta_cast(self) -> None:
        rgb = np.full((180, 260, 3), [160, 2, 220], dtype=np.uint8)
        out = _apply_variant(rgb, "film_default").astype(np.float32)

        input_cast = (float(rgb[0, 0, 0]) + float(rgb[0, 0, 2])) * 0.5 - float(rgb[0, 0, 1])
        output_mean = out.mean(axis=(0, 1))
        output_cast = float((output_mean[0] + output_mean[2]) * 0.5 - output_mean[1])
        self.assertLess(output_cast, input_cast * 0.75)
        self.assertGreater(float(output_mean[1]), 20.0)


if __name__ == "__main__":
    unittest.main()
