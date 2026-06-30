from __future__ import annotations

import unittest
from unittest.mock import patch

from services.film_render_service import FILM_VARIANT_IDS
from services.vibe_film_policy import (
    FILM_VARIANT_SESSION_VIBE,
    effective_film_variant_for_export,
    resolve_vibe_from_prompt,
    session_vibe_is_matched,
    session_vibe_payload_from_decision,
    FilmVibeDecision,
)


class VibeFilmPolicyTests(unittest.TestCase):
    def test_romantic_retro_maps_warm(self):
        d = resolve_vibe_from_prompt("浪漫复古的暖调")
        self.assertEqual(d.film_variant, "film_cold_v2")
        self.assertTrue(d.matched)
        self.assertIn(d.film_variant, FILM_VARIANT_IDS)

    def test_ricoh_keywords(self):
        d = resolve_vibe_from_prompt("理光街拍纪实")
        self.assertEqual(d.film_variant, "film_ricoh_gr")
        self.assertTrue(d.matched)

    def test_empty_prompt_unmatched(self):
        d = resolve_vibe_from_prompt("")
        self.assertEqual(d.film_variant, "film_livehouse")
        self.assertFalse(d.matched)

    # NOTE: prompt must avoid every keyword in vibe_film_policy rule tables
    # (e.g. 粉紫/紫 now map to neon presets), so pick unrelated domain words.
    @patch("services.vibe_llm_resolver.llm_on_miss_enabled", return_value=False)
    def test_unknown_prompt_unmatched_without_llm(self, _mock_llm_off):
        d = resolve_vibe_from_prompt("季度审计报表汇总xyz")
        self.assertFalse(d.matched)
        self.assertEqual(d.matched_by, "llm:failed")

    @patch("services.vibe_llm_resolver.try_resolve_vibe_via_llm")
    def test_unknown_prompt_llm_fallback(self, mock_llm):
        mock_llm.return_value = FilmVibeDecision(
            film_variant="film_cinestill_800t",
            label_zh="Cinestill 800T",
            reason_zh="霓虹夜景（AI 解析）",
            matched_by="llm:ollama",
            prompt="季度审计报表汇总xyz",
            matched=True,
        )
        d = resolve_vibe_from_prompt("季度审计报表汇总xyz")
        self.assertTrue(d.matched)
        self.assertEqual(d.film_variant, "film_cinestill_800t")
        mock_llm.assert_called_once()

    def test_payload_includes_matched(self):
        d = resolve_vibe_from_prompt("电影感")
        p = session_vibe_payload_from_decision(d)
        self.assertIn("matched", p)
        self.assertTrue(p["matched"])

    def test_effective_variant_explicit_wins(self):
        session = {"film_variant": "film_cold_v2", "matched": True}
        v = effective_film_variant_for_export(
            spec_film_variant="film_ricoh_gr",
            session_vibe=session,
            use_session_vibe=True,
        )
        self.assertEqual(v, "film_ricoh_gr")

    def test_effective_variant_session_requires_matched(self):
        session = {"film_variant": "film_cold_v4", "matched": False}
        v = effective_film_variant_for_export(
            spec_film_variant=None,
            session_vibe=session,
            use_session_vibe=True,
        )
        self.assertIsNone(v)

    def test_session_vibe_is_matched_legacy_matched_by(self):
        self.assertTrue(session_vibe_is_matched({"matched_by": "rules:cool"}))
        self.assertFalse(session_vibe_is_matched({"matched_by": "rules:fallback"}))

    def test_session_vibe_sentinel(self):
        session = {"film_variant": "film_black_mist", "matched": True}
        v = effective_film_variant_for_export(
            spec_film_variant=FILM_VARIANT_SESSION_VIBE,
            session_vibe=session,
            use_session_vibe=True,
        )
        self.assertEqual(v, "film_black_mist")


if __name__ == "__main__":
    unittest.main()
