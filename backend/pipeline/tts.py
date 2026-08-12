import asyncio
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from backend import config
from backend.pipeline.process_logging import stream_subprocess

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

# Synthesis is watched by inactivity, not elapsed time: a long script legitimately
# runs for hours, but VibeVoice prints a decode-progress line several times a
# second, so silence is the only reliable hang signal. The values are read from
# config at call time so an Admin change applies to the next run.
SPEAKER_LABEL_RE = re.compile(r"^\s*Speaker\s*\d+\s*[:：\-—–]\s*", re.IGNORECASE)
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？])\s+")


def _strip_speaker_labels(script: str) -> str:
    """Remove leading 'Speaker N:' markers from a script.

    VibeVoice can vocalize labels verbatim ("Speaker one, ...") depending on
    model/script format. Stripping them yields clean spoken input while line
    breaks preserve turn/beat pacing."""
    lines = []
    for line in script.splitlines():
        cleaned = SPEAKER_LABEL_RE.sub("", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _split_tts_text(text: str, max_words: int) -> list[str]:
    """Split long narration at sentence boundaries without dropping words."""
    if max_words <= 0 or len(text.split()) <= max_words:
        return [text]

    units: list[str] = []
    for line in text.splitlines():
        units.extend(
            sentence.strip()
            for sentence in SENTENCE_BOUNDARY_RE.split(line.strip())
            if sentence.strip()
        )

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    def flush() -> None:
        nonlocal current, current_words
        if current:
            chunks.append("\n".join(current))
            current = []
            current_words = 0

    for unit in units:
        words = unit.split()
        while len(words) > max_words:
            flush()
            chunks.append(" ".join(words[:max_words]))
            words = words[max_words:]
        if not words:
            continue
        if current and current_words + len(words) > max_words:
            flush()
        current.append(" ".join(words))
        current_words += len(words)
    flush()
    return chunks


def _prepare_tts_inputs(script_path: str, output_dir: str) -> tuple[Path, Path, list[Path]]:
    """Create the canonical, label-free TTS input and any bounded chunks."""
    script_path_obj = Path(script_path).expanduser().resolve()
    output_dir_path = Path(output_dir).expanduser().resolve()
    output_dir_path.mkdir(parents=True, exist_ok=True)
    cleaned = _strip_speaker_labels(script_path_obj.read_text(encoding="utf-8"))
    if not cleaned:
        raise ValueError("TTS input is empty after removing speaker labels")
    tts_input = output_dir_path / "tts_input.txt"
    tts_input.write_text(cleaned, encoding="utf-8")
    chunks = _split_tts_text(cleaned, config.TTS_CHUNK_WORDS)
    if len(chunks) == 1:
        return script_path_obj, output_dir_path, [tts_input]
    input_paths = [
        output_dir_path / f"tts_input_part_{index:03d}.txt"
        for index in range(1, len(chunks) + 1)
    ]
    for input_path, chunk in zip(input_paths, chunks):
        input_path.write_text(chunk, encoding="utf-8")
    return script_path_obj, output_dir_path, input_paths


async def _concat_wav_parts(
    wav_parts: list[Path], output_dir: Path, *, log: LogCallback | None
) -> Path:
    if len(wav_parts) == 1:
        return wav_parts[0]
    expected = output_dir / "tts_input_generated.wav"
    concat_list = output_dir / "tts_concat.txt"
    lines = []
    for part in wav_parts:
        escaped = str(part).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
    expected.unlink(missing_ok=True)
    returncode, output = await stream_subprocess(
        name="TTS concat",
        command=[
            "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-c", "copy", expected,
        ],
        logger=logger,
        log=log,
        cwd=output_dir,
        timeout=300,
        stall_timeout=120,
    )
    if returncode != 0 or not expected.exists():
        raise RuntimeError(
            f"TTS WAV concatenation failed (exit {returncode}): {output[-500:]}"
        )
    return expected


async def _generate_orpheus(
    script_path: str,
    output_dir: str,
    voice: str,
    language: str,
    *,
    log: LogCallback | None,
    emit: LogCallback,
    max_tokens: int | None = None,
) -> str:
    if not config.ORPHEUS_TTS_API_KEY:
        raise RuntimeError(
            "Orpheus TTS API key is not configured. Set ORPHEUS_TTS_API_KEY in "
            "Admin -> System -> Voice & TTS."
        )
    script_path_obj, output_dir_path, input_paths = _prepare_tts_inputs(
        script_path, output_dir
    )
    emit(f"TTS input: stripped speaker labels -> {output_dir_path / 'tts_input.txt'}")
    if len(input_paths) > 1:
        emit(
            f"TTS input: split into {len(input_paths)} chunks "
            f"(limit {config.TTS_CHUNK_WORDS} words each)"
        )
    base_url = config.ORPHEUS_TTS_URL.rstrip("/")
    headers = {"X-API-Key": config.ORPHEUS_TTS_API_KEY}
    timeout = httpx.Timeout(config.ORPHEUS_TTS_REQUEST_TIMEOUT)
    wav_parts: list[Path] = []
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for index, input_path in enumerate(input_paths, start=1):
            name = "TTS" if len(input_paths) == 1 else f"TTS part {index}/{len(input_paths)}"
            payload = {
                "input": input_path.read_text(encoding="utf-8"),
                "language": language,
                "voice_id": voice,
                "max_tokens": max_tokens or config.ORPHEUS_TTS_MAX_TOKENS,
                "temperature": 0.8,
                "top_p": 0.95,
                "top_k": 40,
                "min_p": 0.05,
                "pre_buffer_size": 1.5,
                "n_threads": config.ORPHEUS_TTS_N_THREADS,
                "speed": config.ORPHEUS_TTS_SPEED_PERCENT / 100,
                "response_format": "wav",
            }
            try:
                response = await client.post(f"{base_url}/v1/audio/jobs", json=payload)
                response.raise_for_status()
                job = response.json()
                job_id = str(job["id"])
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise RuntimeError(f"{name} could not submit an Orpheus job: {exc}") from exc
            emit(f"{name}: Orpheus job {job_id} queued (voice={voice})")
            deadline = time.monotonic() + config.TTS_TIMEOUT
            last_status = ""
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"{name} Orpheus job {job_id} exceeded {config.TTS_TIMEOUT}s"
                    )
                try:
                    response = await client.get(f"{base_url}/v1/audio/jobs/{job_id}")
                    response.raise_for_status()
                    job = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise RuntimeError(f"{name} could not poll Orpheus job {job_id}: {exc}") from exc
                status = str(job.get("status", "")).lower()
                if status != last_status:
                    emit(f"{name}: Orpheus job {job_id} is {status or 'unknown'}")
                    last_status = status
                if status in {"completed", "complete", "succeeded", "done"}:
                    break
                if status in {"failed", "cancelled", "canceled", "error"}:
                    detail = job.get("error") or job.get("detail") or "no error detail"
                    raise RuntimeError(f"{name} Orpheus job {job_id} failed: {detail}")
                await asyncio.sleep(max(1, config.ORPHEUS_TTS_POLL_SECONDS))
            try:
                response = await client.get(f"{base_url}/v1/audio/jobs/{job_id}/audio")
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(f"{name} could not download Orpheus job {job_id}: {exc}") from exc
            expected_part = output_dir_path / f"{input_path.stem}_generated.wav"
            expected_part.write_bytes(response.content)
            if expected_part.stat().st_size < 44:
                raise RuntimeError(f"{name} downloaded an invalid or empty WAV")
            wav_parts.append(expected_part)

    expected = await _concat_wav_parts(wav_parts, output_dir_path, log=log)
    emit(
        f"TTS output: {expected} ({expected.stat().st_size / 1024:.0f} KB; "
        f"source={script_path_obj})"
    )
    return str(expected)


async def generate_tts(
    script_path: str,
    output_dir: str,
    voices: list[str] | None = None,
    tts_model: str | None = None,
    log: LogCallback | None = None,
) -> str:
    voices = voices or [config.TTS_DEFAULT_VOICE_1, config.TTS_DEFAULT_VOICE_2]
    tts_model = tts_model or config.TTS_DEFAULT_MODEL

    # Mirror to the task log (pipeline.log + LogPanel) when available, else the
    # module logger (start.sh log). Prefer the callback to avoid double-logging.
    emit = lambda message: log(message) if log else logger.info(message)

    model = config.TTS_MODELS.get(tts_model)
    if model is None:
        valid = ", ".join(sorted(config.TTS_MODELS))
        raise ValueError(f"Unknown TTS model '{tts_model}'. Valid models: {valid}")

    available_voices = config.voices_for_model(tts_model)
    unknown_voices = [voice for voice in voices if voice not in available_voices]
    if unknown_voices:
        raise ValueError(
            f"Voice(s) {unknown_voices} are unavailable for '{tts_model}'. Valid voices: "
            f"{', '.join(available_voices)}"
        )
    if model.get("kind") == "orpheus_http":
        if len(voices) > 1:
            emit(f"Model '{tts_model}' is single-speaker; using only '{voices[0]}'")
        return await _generate_orpheus(
            script_path,
            output_dir,
            voices[0],
            str(model.get("language") or "en"),
            log=log,
            emit=emit,
        )

    required_runtime_paths = {
        "environment script": Path(model["env_script"]),
        "project directory": Path(model["project_dir"]),
        "inference script": Path(model["inference_script"]),
    }
    missing = [
        f"{label}: {path}"
        for label, path in required_runtime_paths.items()
        if not path.exists()
    ]
    if missing:
        details = "; ".join(missing)
        raise RuntimeError(
            "VibeVoice TTS runtime is unavailable in this process "
            f"({details}). Run the local app with ./scripts/start.sh and "
            "verify AIWORK_ROOT points to the installed VibeVoice runtime."
        )

    # Single-speaker models (e.g. 0.5B realtime) only accept one voice source,
    # so drop any extras regardless of the task's configured speaker count.
    if model.get("single_speaker") and len(voices) > 1:
        emit(
            f"Model '{tts_model}' is single-speaker; using only first voice "
            f"'{voices[0]}' (ignoring {voices[1:]})"
        )
        voices = voices[:1]

    voice_aliases = model.get("voice_aliases", {})
    resolved_voices = [voice_aliases.get(voice, voice) for voice in voices]
    substitutions = [
        f"{requested} -> {resolved}"
        for requested, resolved in zip(voices, resolved_voices)
        if requested != resolved
    ]
    if substitutions:
        emit(
            f"Model '{tts_model}' voice substitution: "
            f"{', '.join(substitutions)}"
        )
    voices = resolved_voices

    # The subprocess runs from VibeVoice's project directory. Resolve every
    # application-owned path before changing cwd, otherwise relative paths are
    # interpreted under VibeVoice and valid inputs appear to be missing.
    script_path_obj, output_dir_path, input_paths = _prepare_tts_inputs(
        script_path, output_dir
    )
    emit(f"TTS input: stripped speaker labels -> {output_dir_path / 'tts_input.txt'}")
    if len(input_paths) > 1:
        emit(
            f"TTS input: split into {len(input_paths)} "
            f"chunks (limit {config.TTS_CHUNK_WORDS} words each)"
        )

    speaker_args = " ".join(f'"{v}"' for v in voices)

    emit(f"Running TTS: model={tts_model}, voices={voices}, script={script_path_obj}")
    wav_parts: list[Path] = []
    for index, input_path in enumerate(input_paths, start=1):
        expected_part = output_dir_path / f"{input_path.stem}_generated.wav"
        expected_part.unlink(missing_ok=True)
        cmd = f"""
source "{model['env_script']}"
cd "{model['project_dir']}"
python "{model['inference_script']}" \
    --txt_path "{input_path}" \
    {model['speaker_flag']} {speaker_args} \
    --output_dir "{output_dir_path}" \
    --device {config.TTS_DEVICE}
"""
        process_name = (
            "TTS"
            if len(input_paths) == 1
            else f"TTS part {index}/{len(input_paths)}"
        )
        returncode, output = await stream_subprocess(
            name=process_name,
            command=["bash", "-c", cmd],
            logger=logger,
            log=log,
            cwd=model["project_dir"],
            timeout=config.TTS_TIMEOUT,
            stall_timeout=config.TTS_STALL_TIMEOUT,
        )
        if returncode != 0:
            raise RuntimeError(
                f"{process_name} generation failed (exit {returncode}): {output[-500:]}"
            )
        if not expected_part.exists():
            raise RuntimeError(
                f"{process_name} produced no WAV output at {expected_part}"
            )
        wav_parts.append(expected_part)

    expected = await _concat_wav_parts(wav_parts, output_dir_path, log=log)

    emit(f"TTS output: {expected} ({expected.stat().st_size / 1024:.0f} KB)")
    return str(expected)
