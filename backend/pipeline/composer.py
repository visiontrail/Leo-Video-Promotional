import json
import logging
import re
import subprocess
import wave
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from backend import config

logger = logging.getLogger(__name__)


def _get_audio_duration(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as w:
        return w.getnframes() / w.getframerate()


def _detect_silence_boundaries(wav_path: str) -> list[float]:
    result = subprocess.run(
        [
            "ffmpeg", "-i", wav_path,
            "-af", "silencedetect=noise=-30dB:d=0.4",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=120,
    )
    boundaries = []
    for line in result.stderr.splitlines():
        match = re.search(r"silence_end: ([\d.]+)", line)
        if match:
            boundaries.append(float(match.group(1)))
    return boundaries


def _parse_script_segments(script_path: str) -> list[dict]:
    segments = []
    with open(script_path, "r") as f:
        for line in f:
            line = line.strip()
            match = re.match(r"^Speaker\s+(\d+):\s*(.+)$", line, re.IGNORECASE)
            if match:
                segments.append({
                    "speaker": int(match.group(1)),
                    "text": match.group(2),
                    "word_count": len(match.group(2).split()),
                })
    return segments


def _assign_timing(segments: list[dict], total_duration: float, silence_boundaries: list[float]) -> list[dict]:
    total_words = sum(s["word_count"] for s in segments)
    if not total_words:
        return segments

    if silence_boundaries and len(silence_boundaries) >= len(segments) - 1:
        boundaries = [0.0] + silence_boundaries[:len(segments) - 1]
        for i, seg in enumerate(segments):
            seg["start"] = boundaries[i]
            seg["duration"] = (boundaries[i + 1] if i + 1 < len(boundaries) else total_duration) - boundaries[i]
    else:
        elapsed = 0.0
        for seg in segments:
            proportion = seg["word_count"] / total_words
            seg["start"] = elapsed
            seg["duration"] = proportion * total_duration
            elapsed += seg["duration"]

    return segments


def _truncate_text(text: str, max_words: int = 25) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


async def compose_video(
    script_path: str,
    audio_path: str,
    output_dir: str,
    title: str = "Podcast Episode",
    include_character: bool = False,
) -> str:
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    duration = _get_audio_duration(audio_path)
    logger.info(f"Audio duration: {duration:.1f}s")

    segments = _parse_script_segments(script_path)
    boundaries = _detect_silence_boundaries(audio_path)
    segments = _assign_timing(segments, duration, boundaries)

    display_segments = []
    for seg in segments:
        display_segments.append({
            "start": round(seg["start"], 2),
            "duration": round(seg["duration"], 2),
            "speaker": seg["speaker"],
            "text": _truncate_text(seg["text"]),
        })

    env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR)))
    template = env.get_template("podcast.html")

    audio_abs = str(Path(audio_path).resolve())
    character_path = str((config.PROJECT_ROOT / "assets" / "lottie" / "podcast_host.json").resolve())
    has_character = include_character and Path(character_path).exists()

    html = template.render(
        title=title,
        total_duration=round(duration, 2),
        segments=display_segments,
        audio_path=audio_abs,
        include_character=has_character,
        character_path=character_path,
    )

    composition_path = output_dir_path / "composition.html"
    composition_path.write_text(html)
    logger.info(f"Composition written to {composition_path}")

    video_path = output_dir_path / "video.mp4"

    render_result = subprocess.run(
        [
            "npx", "hyperframes", "render",
            "--input", str(composition_path),
            "--output", str(video_path),
            "--width", "1920",
            "--height", "1080",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(config.HYPERFRAME_DIR),
    )

    if render_result.returncode != 0:
        logger.error(f"HyperFrame render failed: {render_result.stderr}")
        logger.info(f"HyperFrame stdout: {render_result.stdout}")
        raise RuntimeError(f"Video render failed: {render_result.stderr[-500:]}")

    if not video_path.exists():
        raise RuntimeError("Video render produced no output file")

    logger.info(f"Video rendered: {video_path} ({video_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return str(video_path)
