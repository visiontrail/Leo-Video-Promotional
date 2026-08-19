import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from backend import config
from backend.pipeline import web_footage
from backend.pipeline.opencli import OpenCLIResult


class WebFootageAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_youtube_candidate_is_rejected(self):
        candidate = {
            "platform": "bilibili",
            "source_page_url": "https://www.bilibili.com/video/BV1demo",
            "duration_seconds": 100,
        }

        with self.assertRaisesRegex(web_footage.WebFootageError, "only accepts YouTube"):
            await web_footage.analyze_candidate_link(candidate, "city narration")

    async def test_late_gemini_response_is_recovered_before_fallback(self):
        source_url = "https://www.youtube.com/watch?v=demo"
        candidate = {
            "platform": "youtube",
            "source_page_url": source_url,
            "duration_seconds": 100,
        }
        no_response = OpenCLIResult((), 0, "[NO RESPONSE] timed out", "")
        recovered = OpenCLIResult(
            (),
            0,
            (
                '[{"Role":"User","Text":"Analyze '
                + source_url
                + '"},{"Role":"Assistant","Text":"JSON{\\"start_seconds\\":66,'
                '\\"end_seconds\\":73,\\"confidence\\":0.95,\\"reason\\":'
                '\\"Italian states\\"}"}]'
            ),
            "",
        )

        with (
            patch.object(config, "WEB_FOOTAGE_CLIP_SECONDS", 8),
            patch.object(config, "WEB_FOOTAGE_CLIP_MIN_SECONDS", 5),
            patch.object(
                web_footage, "run_opencli", AsyncMock(side_effect=[no_response, recovered])
            ),
        ):
            analysis = await web_footage.analyze_candidate_link(candidate, "narration")

        self.assertEqual(analysis["start_seconds"], 66)
        self.assertEqual(analysis["end_seconds"], 73)
        self.assertEqual(analysis["analyzer"], "gemini-web-via-opencli")
        self.assertEqual(analysis["status"], "analyzed_after_timeout")

    def test_recovery_does_not_reuse_answer_after_another_video_request(self):
        source_url = "https://www.youtube.com/watch?v=first"
        turns = (
            '[{"Role":"User","Text":"'
            + source_url
            + '"},{"Role":"User","Text":"https://www.youtube.com/watch?v=second"},'
            '{"Role":"Assistant","Text":"{\\"start_seconds\\":10,\\"end_seconds\\":18}"}]'
        )

        self.assertIsNone(web_footage._analysis_from_gemini_turns(turns, source_url))

    def test_recovery_accepts_explicit_rejection_without_timestamps(self):
        source_url = "https://www.youtube.com/watch?v=unsuitable"
        turns = (
            '[{"Role":"User","Text":"Analyze '
            + source_url
            + '"},{"Role":"Assistant","Text":"{\\"suitable\\":false,'
            '\\"confidence\\":0.96,\\"reason\\":\\"Only a talking head\\"}"}]'
        )

        analysis = web_footage._analysis_from_gemini_turns(turns, source_url)

        self.assertIsNotNone(analysis)
        self.assertFalse(analysis["suitable"])

    async def test_gemini_can_reject_candidate_without_timestamps(self):
        candidate = {
            "platform": "youtube",
            "source_page_url": "https://www.youtube.com/watch?v=unsuitable",
            "duration_seconds": 90,
        }
        runner = AsyncMock(
            return_value=OpenCLIResult(
                (),
                0,
                '{"suitable":false,"confidence":0.91,'
                '"reason":"The video contains no matching visuals"}',
                "",
            )
        )

        with patch.object(web_footage, "run_opencli", runner):
            analysis = await web_footage.analyze_candidate_link(
                candidate, "narration about a city skyline"
            )

        self.assertEqual(analysis["status"], "rejected")
        self.assertFalse(analysis["suitable"])
        self.assertNotIn("start_seconds", analysis)
        prompt = runner.await_args.args[0][2]
        self.assertIn('"suitable":false', prompt)
        self.assertIn("local footage agent will discard it and search again", prompt)

    async def test_late_gemini_rejection_remains_rejected_after_recovery(self):
        source_url = "https://www.youtube.com/watch?v=late-unsuitable"
        candidate = {
            "platform": "youtube",
            "source_page_url": source_url,
            "duration_seconds": 90,
        }
        no_response = OpenCLIResult((), 0, "[NO RESPONSE] timed out", "")
        recovered = OpenCLIResult(
            (),
            0,
            (
                '[{"Role":"User","Text":"Analyze '
                + source_url
                + '"},{"Role":"Assistant","Text":"{\\"suitable\\":false,'
                '\\"confidence\\":0.88,\\"reason\\":\\"No matching visuals\\"}"}]'
            ),
            "",
        )

        with patch.object(
            web_footage,
            "run_opencli",
            AsyncMock(side_effect=[no_response, recovered]),
        ):
            analysis = await web_footage.analyze_candidate_link(
                candidate, "narration"
            )

        self.assertFalse(analysis["suitable"])
        self.assertEqual(analysis["status"], "rejected_after_timeout")
        self.assertNotIn("start_seconds", analysis)

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

        with (
            patch.object(config, "WEB_FOOTAGE_CLIP_SECONDS", 8),
            patch.object(config, "WEB_FOOTAGE_CLIP_MIN_SECONDS", 5),
        ):
            fitted = web_footage._fit_analysis_to_media(analysis, 100)

        self.assertEqual(fitted["start_seconds"], 10)
        self.assertEqual(fitted["end_seconds"], 18)

    def test_model_interval_is_clamped_to_configured_clip_length(self):
        candidate = {"duration_seconds": 100}
        with (
            patch.object(config, "WEB_FOOTAGE_CLIP_SECONDS", 8),
            patch.object(config, "WEB_FOOTAGE_CLIP_MIN_SECONDS", 5),
        ):
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

    def test_short_gemini_interval_is_extended_to_minimum(self):
        """Gemini frequently returns ~6-second fragments; the normaliser must
        extend them to at least WEB_FOOTAGE_CLIP_MIN_SECONDS."""
        candidate = {"duration_seconds": 120}
        with (
            patch.object(config, "WEB_FOOTAGE_CLIP_SECONDS", 15),
            patch.object(config, "WEB_FOOTAGE_CLIP_MIN_SECONDS", 10),
        ):
            result = web_footage._normalise_analysis(
                {
                    "start_seconds": 20,
                    "end_seconds": 26,
                    "confidence": 0.8,
                    "reason": "city panorama",
                },
                candidate,
            )

        self.assertEqual(result["start_seconds"], 20)
        self.assertEqual(result["end_seconds"], 30)
        self.assertGreaterEqual(
            result["end_seconds"] - result["start_seconds"], 10
        )

    def test_fit_analysis_extends_short_interval_to_minimum(self):
        analysis = {
            "start_seconds": 5,
            "end_seconds": 8,
            "analyzer": "gemini-web-via-opencli",
        }
        with (
            patch.object(config, "WEB_FOOTAGE_CLIP_SECONDS", 15),
            patch.object(config, "WEB_FOOTAGE_CLIP_MIN_SECONDS", 10),
        ):
            fitted = web_footage._fit_analysis_to_media(analysis, 100)

        self.assertEqual(fitted["start_seconds"], 5)
        self.assertEqual(fitted["end_seconds"], 15)

    async def test_trim_passes_recovered_source_interval_to_ffmpeg(self):
        runner = AsyncMock(return_value=(0, "", ""))
        analysis = {"start_seconds": 66.0, "end_seconds": 73.0}

        with patch.object(web_footage, "_run_command", runner):
            await web_footage._trim(
                Path("raw.mp4"), Path("clip.mp4"), analysis, "landscape"
            )

        command = runner.await_args.args[0]
        self.assertEqual(command[command.index("-ss") + 1], "66.000")
        self.assertEqual(command[command.index("-t") + 1], "7.000")

    async def test_youtube_download_fetches_only_the_analyzed_video_section(self):
        candidate = {
            "source_page_url": "https://www.youtube.com/watch?v=demo",
            "duration_seconds": 100,
        }
        analysis = {"start_seconds": 66.0, "end_seconds": 73.0}

        with TemporaryDirectory() as directory:
            raw_dir = Path(directory)

            async def fake_run(command, **_kwargs):
                (raw_dir / "demo.mp4").write_bytes(b"video")
                return 0, "", ""

            with (
                patch.object(web_footage, "_yt_dlp_bin", return_value="yt-dlp"),
                patch.object(web_footage, "_run_command", AsyncMock(side_effect=fake_run)) as runner,
            ):
                _path, sectioned = await web_footage._download_youtube(
                    candidate, raw_dir, analysis
                )

        command = runner.await_args.args[0]
        self.assertTrue(sectioned)
        self.assertEqual(
            command[command.index("--download-sections") + 1], "*66.000-73.000"
        )
        self.assertNotIn("bestaudio", command[command.index("-f") + 1])

    async def test_rejected_candidate_makes_scout_try_next_search_result(self):
        rejected = {
            "platform": "youtube",
            "provider": "YouTube",
            "provider_id": "youtube-first",
            "title": "Talking head",
            "creator": "First creator",
            "source_page_url": "https://www.youtube.com/watch?v=first",
            "duration_seconds": 60,
        }
        accepted = {
            "platform": "youtube",
            "provider": "YouTube",
            "provider_id": "youtube-second",
            "title": "Matching city view",
            "creator": "Second creator",
            "source_page_url": "https://www.youtube.com/watch?v=second",
            "duration_seconds": 60,
        }
        rejection = {
            "suitable": False,
            "confidence": 0.95,
            "reason": "No matching city visuals",
            "analyzer": "gemini-web-via-opencli",
            "status": "rejected",
        }
        selection = {
            "suitable": True,
            "start_seconds": 12.0,
            "end_seconds": 27.0,
            "confidence": 0.9,
            "reason": "Wide city skyline",
            "analyzer": "gemini-web-via-opencli",
            "status": "analyzed",
        }
        logs: list[str] = []

        with TemporaryDirectory() as directory:
            task_dir = Path(directory)
            manifest = {
                "task_id": "demo",
                "provider": "YouTube",
                "provider_id": "opencli-web",
                "clips": [],
                "errors": [],
            }

            async def fake_download(candidate, raw_dir, _analysis):
                raw_path = raw_dir / f"{candidate['provider_id']}.mp4"
                raw_path.write_bytes(b"raw-video")
                return raw_path, True

            async def fake_trim(_raw_path, destination, _analysis, _orientation):
                destination.write_bytes(b"trimmed-video")

            with (
                patch.object(
                    web_footage,
                    "search_youtube",
                    AsyncMock(return_value=[rejected, accepted]),
                ),
                patch.object(
                    web_footage,
                    "analyze_candidate_link",
                    AsyncMock(side_effect=[rejection, selection]),
                ) as analyzer,
                patch.object(
                    web_footage,
                    "_download_youtube",
                    AsyncMock(side_effect=fake_download),
                ) as downloader,
                patch.object(
                    web_footage,
                    "_probe",
                    AsyncMock(
                        return_value={
                            "duration_seconds": 15.0,
                            "width": 1280,
                            "height": 720,
                        }
                    ),
                ),
                patch.object(web_footage, "_trim", AsyncMock(side_effect=fake_trim)),
                patch.object(
                    web_footage,
                    "_evidence_frames",
                    AsyncMock(return_value=["frame-01.jpg"]),
                ),
            ):
                result = await web_footage.supplement_web_footage(
                    task_dir=task_dir,
                    manifest=manifest,
                    query_plan=[{"query": "city skyline", "purpose": "Show the city"}],
                    target_total=1,
                    orientation="landscape",
                    script="The city grew across the horizon.",
                    log=logs.append,
                )

            saved = json.loads(
                (task_dir / "footage" / "manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(analyzer.await_count, 2)
        downloader.assert_awaited_once()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["clips"][0]["source_page_url"], accepted["source_page_url"])
        self.assertEqual(
            saved["rejected_candidates"][0]["source_page_url"],
            rejected["source_page_url"],
        )
        self.assertTrue(
            any("local footage agent continuing search" in message for message in logs)
        )


if __name__ == "__main__":
    unittest.main()
