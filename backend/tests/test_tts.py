import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import config
from backend.pipeline import tts


class GenerateTtsTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_application_paths_before_changing_cwd(self):
        captured = {}

        async def fake_stream_subprocess(**kwargs):
            captured.update(kwargs)
            command = kwargs["command"][2]
            output_dir = Path(command.split('--output_dir "', 1)[1].split('"', 1)[0])
            input_path = Path(command.split('--txt_path "', 1)[1].split('"', 1)[0])
            (output_dir / f"{input_path.stem}_generated.wav").write_bytes(b"wav")
            return 0, ""

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            root = Path(temp_dir)
            script_path = root / "script.txt"
            script_path.write_text("Speaker 1: Hello")
            output_dir = root / "audio"

            runtime = {
                "env_script": root / "env.sh",
                "project_dir": root / "vibevoice",
                "inference_script": root / "vibevoice" / "infer.py",
                "speaker_flag": "--speaker_name",
                "single_speaker": True,
            }
            runtime["env_script"].write_text("")
            runtime["project_dir"].mkdir()
            runtime["inference_script"].write_text("")

            with (
                patch.dict(config.TTS_MODELS, {"test-model": runtime}, clear=True),
                patch.object(tts, "stream_subprocess", fake_stream_subprocess),
            ):
                result = await tts.generate_tts(
                    str(script_path.relative_to(Path.cwd())),
                    str(output_dir.relative_to(Path.cwd())),
                    voices=["Alice"],
                    tts_model="test-model",
                )

        command = captured["command"][2]
        self.assertIn(f'--txt_path "{script_path.resolve().parent / "audio" / "tts_input.txt"}"', command)
        self.assertIn(f'--output_dir "{output_dir.resolve()}"', command)
        self.assertEqual(captured["cwd"], runtime["project_dir"])
        self.assertTrue(Path(result).is_absolute())

    async def test_reports_missing_runtime_before_spawning(self):
        runtime = {
            "env_script": Path("/missing/env.sh"),
            "project_dir": Path("/missing/vibevoice"),
            "inference_script": Path("/missing/vibevoice/infer.py"),
            "speaker_flag": "--speaker_name",
            "single_speaker": True,
        }
        with patch.dict(config.TTS_MODELS, {"missing": runtime}, clear=True):
            with self.assertRaisesRegex(RuntimeError, r"Run the local app"):
                await tts.generate_tts(
                    "script.txt",
                    "audio",
                    voices=["Alice"],
                    tts_model="missing",
                )

    async def test_applies_model_specific_voice_alias(self):
        captured = {}

        async def fake_stream_subprocess(**kwargs):
            captured.update(kwargs)
            command = kwargs["command"][2]
            output_dir = Path(command.split('--output_dir "', 1)[1].split('"', 1)[0])
            input_path = Path(command.split('--txt_path "', 1)[1].split('"', 1)[0])
            (output_dir / f"{input_path.stem}_generated.wav").write_bytes(b"wav")
            return 0, ""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script_path = root / "script.txt"
            script_path.write_text("Hello")
            runtime = {
                "env_script": root / "env.sh",
                "project_dir": root / "vibevoice",
                "inference_script": root / "vibevoice" / "infer.py",
                "speaker_flag": "--speaker_name",
                "single_speaker": True,
                "voice_aliases": {"Alice": "Emma"},
            }
            runtime["env_script"].write_text("")
            runtime["project_dir"].mkdir()
            runtime["inference_script"].write_text("")

            with (
                patch.dict(config.TTS_MODELS, {"test-model": runtime}, clear=True),
                patch.object(tts, "stream_subprocess", fake_stream_subprocess),
            ):
                await tts.generate_tts(
                    str(script_path),
                    str(root / "audio"),
                    voices=["Alice"],
                    tts_model="test-model",
                )

        self.assertIn('--speaker_name "Emma"', captured["command"][2])


if __name__ == "__main__":
    unittest.main()
