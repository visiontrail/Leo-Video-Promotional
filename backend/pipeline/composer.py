import json
import logging
import re
import shutil
import wave
from collections.abc import Callable
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from backend import config
from backend.pipeline.process_logging import run_capture_logged, stream_subprocess

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

TEMPLATE_FILES = {
    "podcast": "podcast.html",
    "kinetic": "podcast_kinetic.html",
    "swiss": "podcast_swiss.html",
    "minimal": "podcast_minimal.html",
}

TITLE_DURATION = 5.0
OUTRO_DURATION = 5.0
TRANSITION_DURATION = 0.4

# FFmpeg silence analysis is a quick pass; the HyperFrames render is the long
# stage and is streamed live, so this ceiling only guards a genuine hang.
SILENCE_TIMEOUT = 120

# Per-frame wall-clock ceiling used to derive the render timeout from the frame
# count (duration x fps). Generous on purpose: it must not kill a slow-but-live
# render, only a genuinely wedged one. ~2 fps capture was measured on this Mac
# at 1 worker; 1.5 s/frame leaves headroom for multi-worker and warmup.
RENDER_SECONDS_PER_FRAME = 1.5
RENDER_TIMEOUT_FLOOR = 900  # never below 15 min, regardless of how short the clip is


def _get_audio_duration(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as w:
        return w.getnframes() / w.getframerate()


def _detect_silence_boundaries(wav_path: str, log: LogCallback | None = None) -> list[float]:
    command = [
        "ffmpeg", "-i", wav_path,
        # VibeVoice inserts sub-second pauses inside sentences and longer pauses
        # between script paragraphs/turns. Treat only the latter as caption
        # boundaries; otherwise early sentence pauses consume segment slots and
        # leave the final caption on-screen for most of the episode.
        "-af", "silencedetect=noise=-30dB:d=1.2",
        "-f", "null", "-",
    ]
    # NB: do NOT pass the task `log` callback here. silencedetect emits hundreds
    # of stderr lines; fanning each one out per-line to pipeline.log (reopened
    # every line) + the SSE stream + the root logger floods the log pipe and is
    # what produced the "--- Logging error ---" spam. The full output still goes
    # to the module logger as a single record (start.sh log) for debugging, and
    # compose_video emits a concise one-line boundary summary to the task log.
    result = run_capture_logged(
        name="FFmpeg silence detect",
        command=command,
        logger=logger,
        log=None,
        timeout=SILENCE_TIMEOUT,
    )
    boundaries = []
    for line in result.stderr.splitlines():
        match = re.search(r"silence_end: ([\d.]+)", line)
        if match:
            boundaries.append(float(match.group(1)))
    return boundaries


SPEAKER_LABEL_RE = re.compile(r"^Speaker\s*(\d+)\s*[:：\-—–]\s*(.+)$", re.IGNORECASE)


def _parse_script_segments(script_path: str, is_monologue: bool = False) -> list[dict]:
    segments = []
    with open(script_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            match = SPEAKER_LABEL_RE.match(line)
            if match:
                text = match.group(2)
                segments.append({
                    "speaker": int(match.group(1)),
                    "text": text,
                    "word_count": len(text.split()),
                })
            else:
                speaker = 1 if is_monologue else (len(segments) % 2) + 1
                segments.append({
                    "speaker": speaker,
                    "text": line,
                    "word_count": len(line.split()),
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


def _project_relative(path: str | Path, project_dir: Path) -> str:
    """Path of ``path`` relative to the render project dir, POSIX-style.

    HyperFrames sandboxes asset access to the project directory, so every
    referenced file must live inside it and be referenced relatively. Falls back
    to an absolute path for assets outside the project (which HyperFrames will
    flag as missing) rather than raising.
    """
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _build_render_command(project_dir: Path, video_path: Path) -> list[str]:
    """Build the render command for the current HyperFrames CLI (v0.6.x).

    The old ``--input/--width/--height`` flags no longer exist: the render entry
    is the project directory (which must contain ``index.html``) and dimensions
    come from ``--resolution``. Prefer the locally-installed, version-pinned
    binary so a render never triggers an on-demand ``npx`` install (the old
    unpinned ``npx hyperframes`` re-installed latest every run); fall back to a
    *pinned* npx invocation only if the local install is missing.
    """
    local_bin = config.HYPERFRAME_DIR / "node_modules" / ".bin" / "hyperframes"
    if local_bin.exists():
        base = [str(local_bin)]
    else:
        base = ["npx", "--yes", f"hyperframes@{config.HYPERFRAMES_VERSION}"]
    return base + [
        "render", str(project_dir),
        "--output", str(video_path),
        "--resolution", config.RENDER_RESOLUTION,
        "--fps", str(config.RENDER_FPS),
        "--quality", config.RENDER_QUALITY,
        "-w", str(config.RENDER_WORKERS),
    ]


async def compose_video(
    script_path: str,
    audio_path: str,
    output_dir: str,
    title: str = "Podcast Episode",
    include_character: bool = False,
    video_template: str = "podcast",
    is_monologue: bool = False,
    log: LogCallback | None = None,
) -> str:
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    # Mirror to the task log (pipeline.log + LogPanel) when available, else the
    # module logger (start.sh log). Prefer the callback to avoid double-logging.
    emit = lambda message: log(message) if log else logger.info(message)

    audio_duration = _get_audio_duration(audio_path)
    composition_duration = audio_duration + TITLE_DURATION + OUTRO_DURATION
    emit(f"Audio duration: {audio_duration:.1f}s; composition {composition_duration:.1f}s")

    segments = _parse_script_segments(script_path, is_monologue=is_monologue)
    emit(f"Parsed {len(segments)} script segments")
    boundaries = _detect_silence_boundaries(audio_path, log)
    timing_mode = "silence boundaries" if boundaries and len(boundaries) >= len(segments) - 1 else "proportional (word count)"
    emit(f"Detected {len(boundaries)} silence boundaries; caption timing via {timing_mode}")
    segments = _assign_timing(segments, audio_duration, boundaries)

    display_segments = []
    for seg in segments:
        display_segments.append({
            "start": round(seg["start"] + TITLE_DURATION, 2),
            "duration": round(seg["duration"], 2),
            "speaker": seg["speaker"],
            "text": _truncate_text(seg["text"]),
        })

    env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR)))
    template_file = TEMPLATE_FILES.get(video_template)
    if not template_file:
        raise ValueError(f"Unknown video template: {video_template}")
    template = env.get_template(template_file)
    emit(f"Rendering template '{video_template}' ({template_file})")

    # HyperFrames renders output_dir as the project root and sandboxes asset
    # access to it, so every referenced file must live inside output_dir and be
    # referenced by a path relative to it. An absolute path (or a file outside
    # the project) is reported as audio_src_not_found and the video comes out
    # SILENT — which is why earlier renders had no sound.
    audio_src = _project_relative(audio_path, output_dir_path)

    character_src = None
    if include_character:
        lottie_source = config.PROJECT_ROOT / "assets" / "lottie" / "podcast_host.json"
        if lottie_source.exists():
            char_dest = output_dir_path / "assets" / "podcast_host.json"
            char_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(lottie_source, char_dest)
            character_src = _project_relative(char_dest, output_dir_path)
    has_character = character_src is not None

    html = template.render(
        title=title,
        total_duration=round(composition_duration, 2),
        audio_duration=round(audio_duration, 2),
        content_start=round(TITLE_DURATION, 2),
        outro_start=round(TITLE_DURATION + audio_duration, 2),
        outro_duration=round(OUTRO_DURATION, 2),
        transition_duration=round(TRANSITION_DURATION, 2),
        segments=display_segments,
        audio_path=audio_src,
        include_character=has_character,
        character_path=character_src,
        is_monologue=is_monologue,
    )

    # HyperFrames discovers the entry composition as <project>/index.html, so the
    # task output dir doubles as a single-composition project. Write index.html
    # (not composition.html) and drop any stale entry file that would trip the
    # multiple_root_compositions check and cause duplicate audio.
    composition_path = output_dir_path / "index.html"
    composition_path.write_text(html)
    stale = output_dir_path / "composition.html"
    if stale.exists():
        stale.unlink()
    emit(f"Composition written to {composition_path}")

    video_path = output_dir_path / "video.mp4"

    total_frames = max(1, round(composition_duration * config.RENDER_FPS))
    render_timeout = max(RENDER_TIMEOUT_FLOOR, int(300 + total_frames * RENDER_SECONDS_PER_FRAME))
    emit(
        f"Rendering ~{total_frames} frames "
        f"({composition_duration:.0f}s @ {config.RENDER_FPS}fps, {config.RENDER_QUALITY}, "
        f"{config.RENDER_WORKERS} worker(s)); render timeout {render_timeout}s"
    )

    render_command = _build_render_command(output_dir_path, video_path)
    returncode, output = await stream_subprocess(
        name="HyperFrames render",
        command=render_command,
        logger=logger,
        log=log,
        cwd=config.HYPERFRAME_DIR,
        timeout=render_timeout,
    )

    if returncode != 0:
        raise RuntimeError(f"Video render failed (exit {returncode}): {output[-500:]}")

    if not video_path.exists():
        raise RuntimeError("Video render produced no output file")

    emit(f"Video rendered: {video_path} ({video_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return str(video_path)
