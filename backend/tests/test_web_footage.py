import unittest
from unittest.mock import patch

from backend import config
from backend.pipeline import web_footage


class WebFootageAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_bilibili_uses_explicit_low_confidence_fallback(self):
        candidate = {
            "platform": "bilibili",
            "source_page_url": "https://www.bilibili.com/video/BV1demo",
            "duration_seconds": 100,
        }

        analysis = await web_footage.analyze_candidate_link(candidate, "city narration")

        self.assertEqual(analysis["analyzer"], "deterministic-safe-offset")
        self.assertEqual(analysis["status"], "fallback")
        self.assertIn("Bilibili", analysis["reason"])

    async def test_disabled_gemini_is_recorded_as_fallback(self):
        candidate = {
            "platform": "youtube",
            "source_page_url": "https://www.youtube.com/watch?v=demo",
            "duration_seconds": 30,
        }

        with patch.object(config, "WEB_FOOTAGE_GEMINI_ENABLED", False):
            analysis = await web_footage.analyze_candidate_link(candidate, "narration")

        self.assertEqual(analysis["status"], "fallback")
        self.assertIn("disabled", analysis["reason"])

    def test_unknown_duration_safe_offset_is_fitted_after_probe(self):
        analysis = {
            "start_seconds": 0,
            "end_seconds": 8,
            "analyzer": "deterministic-safe-offset",
        }

        fitted = web_footage._fit_analysis_to_media(analysis, 100)

        self.assertEqual(fitted["start_seconds"], 10)
        self.assertEqual(fitted["end_seconds"], 18)

    def test_model_interval_is_clamped_to_configured_clip_length(self):
        candidate = {"duration_seconds": 100}
        with patch.object(config, "WEB_FOOTAGE_CLIP_SECONDS", 8):
            result = web_footage._normalise_analysis(
                {
                    "start_seconds": 12,
                    "end_seconds": 40,
                    "confidence": 1.8,
                    "reason": "wide skyline",
                },
                candidate,
            )

        self.assertEqual(result["start_seconds"], 12)
        self.assertEqual(result["end_seconds"], 20)
        self.assertEqual(result["confidence"], 1)


if __name__ == "__main__":
    unittest.main()
