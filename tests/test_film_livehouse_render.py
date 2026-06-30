from __future__ import annotations

import unittest

import numpy as np

from op_kernel import apply_livehouse_film
from services.film_render_service import _apply_variant


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


if __name__ == "__main__":
    unittest.main()
