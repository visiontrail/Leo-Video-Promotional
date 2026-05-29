import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from backend.pipeline.extractors.base import ExtractedContent

logger = logging.getLogger(__name__)


def _parse_vtt(vtt_text: str) -> str:
    lines = []
    seen = set()
    for line in vtt_text.splitlines():
        line = line.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if "-->" in line:
            continue
        if re.match(r"^\d+$", line):
            continue
        clean = re.sub(r"<[^>]+>", "", line)
        clean = re.sub(r"\[.*?\]", "", clean).strip()
        if clean and clean not in seen:
            seen.add(clean)
            lines.append(clean)
    return " ".join(lines)


async def extract_youtube(url: str) -> ExtractedContent:
    logger.info(f"Extracting YouTube subtitles from {url}")

    meta_result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-download", url],
        capture_output=True, text=True, timeout=60,
    )
    if meta_result.returncode != 0:
        raise RuntimeError(f"yt-dlp metadata failed: {meta_result.stderr}")

    meta = json.loads(meta_result.stdout)
    title = meta.get("title", "Unknown")
    channel = meta.get("channel", "")
    duration = meta.get("duration", 0)

    with tempfile.TemporaryDirectory() as tmpdir:
        sub_result = subprocess.run(
            [
                "yt-dlp",
                "--write-auto-sub", "--write-sub",
                "--sub-lang", "en",
                "--sub-format", "vtt",
                "--skip-download",
                "-o", f"{tmpdir}/%(id)s.%(ext)s",
                url,
            ],
            capture_output=True, text=True, timeout=120,
        )

        vtt_files = list(Path(tmpdir).glob("*.vtt"))
        if not vtt_files:
            raise RuntimeError(f"No subtitles found. yt-dlp output: {sub_result.stderr}")

        vtt_text = vtt_files[0].read_text(encoding="utf-8")
        text = _parse_vtt(vtt_text)

    if not text.strip():
        raise RuntimeError("Extracted subtitle text is empty")

    logger.info(f"Extracted {len(text.split())} words from '{title}'")
    return ExtractedContent(
        source_type="youtube",
        title=title,
        text=text,
        metadata={"channel": channel, "duration": duration, "url": url},
    )
