import logging
import subprocess
from pathlib import Path
from backend import config

logger = logging.getLogger(__name__)


async def generate_tts(script_path: str, output_dir: str, voices: list[str] | None = None) -> str:
    voices = voices or [config.TTS_DEFAULT_VOICE_1, config.TTS_DEFAULT_VOICE_2]
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    speaker_args = " ".join(f'"{v}"' for v in voices)

    cmd = f"""
source "{config.TTS_ENV_SCRIPT}"
cd "{config.TTS_PROJECT_DIR}"
python "{config.TTS_INFERENCE_SCRIPT}" \
    --txt_path "{script_path}" \
    --speaker_names {speaker_args} \
    --output_dir "{output_dir}" \
    --device {config.TTS_DEVICE}
"""

    logger.info(f"Running TTS: voices={voices}, script={script_path}")
    result = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=3600,
    )

    if result.returncode != 0:
        logger.error(f"TTS stderr: {result.stderr}")
        raise RuntimeError(f"TTS generation failed: {result.stderr[-500:]}")

    logger.info(f"TTS stdout (last 500 chars): {result.stdout[-500:]}")

    script_stem = Path(script_path).stem
    expected = output_dir_path / f"{script_stem}_generated.wav"
    if expected.exists():
        logger.info(f"TTS output: {expected} ({expected.stat().st_size / 1024:.0f} KB)")
        return str(expected)

    wav_files = list(output_dir_path.glob("*.wav"))
    if wav_files:
        latest = max(wav_files, key=lambda p: p.stat().st_mtime)
        logger.info(f"TTS output (fallback): {latest}")
        return str(latest)

    raise RuntimeError("TTS produced no WAV output")
