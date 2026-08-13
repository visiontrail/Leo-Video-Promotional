import asyncio
import hashlib
import json
import logging
import os
import re
import time
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass
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
SPEAKER_LINE_RE = re.compile(
    r"^\s*(Speaker\s*\d+\s*[:：\-—–])\s*(.*)$", re.IGNORECASE
)
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？])\s+")

# Orpheus emits seven LM tokens for every 2,048 PCM samples at 24 kHz. This is
# the model's actual codec geometry, not a heuristic. It lets us turn the
# remote service's token ceiling into a safe text-chunk budget and detect a
# response that stopped because it hit max_tokens rather than end-of-speech.
ORPHEUS_AUDIO_TOKENS_PER_SECOND = 7 * 24_000 / 2_048
ORPHEUS_CHUNK_MIN_WPM = 90
ORPHEUS_CHUNK_SAFETY = 0.80
ORPHEUS_TOKEN_LIMIT_RATIO = 0.97
MAX_PLAUSIBLE_SPEECH_WPM = 320


class TtsIntegrityError(RuntimeError):
    """A provider returned audio that cannot contain the requested narration."""


@dataclass(frozen=True)
class WavInfo:
    channels: int
    sample_width: int
    sample_rate: int
    frame_count: int
    duration_seconds: float


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


def _spoken_word_count(text: str) -> int:
    return len(_strip_speaker_labels(text).split())


def _split_tts_text(
    text: str,
    max_words: int,
    *,
    preserve_speaker_labels: bool = False,
) -> list[str]:
    """Split at sentence boundaries while preserving every spoken word.

    Dialogue-capable VibeVoice requires a ``Speaker N:`` marker on every input
    segment. When a long speaker turn crosses a chunk boundary, the marker is
    repeated as metadata; the spoken text itself is neither repeated nor
    dropped.
    """
    if max_words <= 0 or _spoken_word_count(text) <= max_words:
        return [text]

    units: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        speaker_label = ""
        content = stripped
        if preserve_speaker_labels:
            match = SPEAKER_LINE_RE.match(stripped)
            if match:
                speaker_label = match.group(1).strip()
                content = match.group(2).strip()
        for sentence in SENTENCE_BOUNDARY_RE.split(content):
            words = sentence.strip().split()
            while words:
                piece = " ".join(words[:max_words])
                words = words[max_words:]
                units.append(f"{speaker_label} {piece}".strip())

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
        word_count = _spoken_word_count(unit)
        if current and current_words + word_count > max_words:
            flush()
        current.append(unit)
        current_words += word_count
    flush()
    return chunks


def _prepare_tts_input(
    script_path: str,
    output_dir: str,
    *,
    strip_speaker_labels: bool,
) -> tuple[Path, Path, Path, str]:
    """Create the canonical provider-specific TTS input."""
    script_path_obj = Path(script_path).expanduser().resolve()
    output_dir_path = Path(output_dir).expanduser().resolve()
    output_dir_path.mkdir(parents=True, exist_ok=True)
    source = script_path_obj.read_text(encoding="utf-8")
    cleaned = _strip_speaker_labels(source) if strip_speaker_labels else source.strip()
    if not cleaned:
        raise ValueError("TTS input is empty after removing speaker labels")
    tts_input = output_dir_path / "tts_input.txt"
    tts_input.write_text(cleaned, encoding="utf-8")
    return script_path_obj, output_dir_path, tts_input, cleaned


def _write_chunk_inputs(
    text: str,
    output_dir: Path,
    *,
    max_words: int,
    preserve_speaker_labels: bool = False,
) -> tuple[list[Path], list[str]]:
    chunks = _split_tts_text(
        text,
        max_words,
        preserve_speaker_labels=preserve_speaker_labels,
    )
    if len(chunks) == 1:
        return [output_dir / "tts_input.txt"], chunks
    for stale in output_dir.glob("tts_input_part_*.txt"):
        stale.unlink(missing_ok=True)
    input_paths = [
        output_dir / f"tts_input_part_{index:03d}.txt"
        for index in range(1, len(chunks) + 1)
    ]
    for input_path, chunk in zip(input_paths, chunks):
        input_path.write_text(chunk, encoding="utf-8")
    return input_paths, chunks


def _read_pcm_wav(path: Path) -> WavInfo:
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getcomptype() != "NONE":
                raise TtsIntegrityError(
                    f"TTS output must be uncompressed PCM WAV, got {handle.getcomptype()}"
                )
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            info = WavInfo(
                channels=handle.getnchannels(),
                sample_width=handle.getsampwidth(),
                sample_rate=sample_rate,
                frame_count=frame_count,
                duration_seconds=frame_count / max(1, sample_rate),
            )
    except (wave.Error, EOFError, OSError) as exc:
        raise TtsIntegrityError(f"TTS produced an unreadable WAV at {path}: {exc}") from exc
    if info.frame_count <= 0:
        raise TtsIntegrityError(f"TTS produced an empty WAV at {path}")
    return info


def _validate_wav_part(
    path: Path,
    text: str,
    *,
    token_limit_seconds: float | None = None,
    speed: float = 1.0,
) -> WavInfo:
    info = _read_pcm_wav(path)
    words = _spoken_word_count(text)
    minimum_seconds = words * 60 / (MAX_PLAUSIBLE_SPEECH_WPM * speed)
    if words >= 8 and info.duration_seconds < minimum_seconds:
        raise TtsIntegrityError(
            f"TTS audio is too short for its input: {words} words produced only "
            f"{info.duration_seconds:.1f}s (minimum sanity bound {minimum_seconds:.1f}s)"
        )
    if (
        token_limit_seconds is not None
        and info.duration_seconds >= token_limit_seconds * ORPHEUS_TOKEN_LIMIT_RATIO
    ):
        raise TtsIntegrityError(
            f"Orpheus audio reached {info.duration_seconds:.1f}s, the configured "
            f"max-token ceiling ({token_limit_seconds:.1f}s); refusing a likely "
            "truncated narration"
        )
    return info


async def _concat_wav_parts(
    wav_parts: list[Path], output_dir: Path, *, log: LogCallback | None
) -> Path:
    if len(wav_parts) == 1:
        return wav_parts[0]
    expected = output_dir / "tts_input_generated.wav"
    infos = [_read_pcm_wav(part) for part in wav_parts]
    reference = infos[0]
    for part, info in zip(wav_parts[1:], infos[1:]):
        if (
            info.channels,
            info.sample_width,
            info.sample_rate,
        ) != (
            reference.channels,
            reference.sample_width,
            reference.sample_rate,
        ):
            raise TtsIntegrityError(
                f"TTS WAV format changed between chunks at {part}: "
                f"expected {reference.channels}ch/{reference.sample_width * 8}bit/"
                f"{reference.sample_rate}Hz, got {info.channels}ch/"
                f"{info.sample_width * 8}bit/{info.sample_rate}Hz"
            )

    staged = output_dir / "tts_input_generated.tmp.wav"
    staged.unlink(missing_ok=True)
    with wave.open(str(staged), "wb") as destination:
        destination.setnchannels(reference.channels)
        destination.setsampwidth(reference.sample_width)
        destination.setframerate(reference.sample_rate)
        for part in wav_parts:
            with wave.open(str(part), "rb") as source:
                destination.writeframesraw(source.readframes(source.getnframes()))
    os.replace(staged, expected)
    joined = _read_pcm_wav(expected)
    expected_frames = sum(info.frame_count for info in infos)
    if joined.frame_count != expected_frames:
        raise TtsIntegrityError(
            f"Lossless WAV join wrote {joined.frame_count} frames; expected {expected_frames}"
        )
    return expected


def _write_tts_manifest(
    output_dir: Path,
    *,
    model: str,
    source_text: str,
    chunks: list[str],
    wav_parts: list[Path],
    output: Path,
    deterministic: bool,
) -> None:
    parts = []
    for text, path in zip(chunks, wav_parts):
        info = _read_pcm_wav(path)
        parts.append(
            {
                "input": path.with_suffix(".txt").name.replace("_generated", ""),
                "output": path.name,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "word_count": _spoken_word_count(text),
                **asdict(info),
            }
        )
    payload = {
        "model": model,
        "deterministic_decoding": deterministic,
        "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "source_word_count": _spoken_word_count(source_text),
        "chunk_count": len(parts),
        "parts": parts,
        "output": output.name,
        "output_wav": asdict(_read_pcm_wav(output)),
    }
    (output_dir / "tts_manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _orpheus_chunk_word_limit(max_tokens: int) -> int:
    audio_seconds = max_tokens / ORPHEUS_AUDIO_TOKENS_PER_SECOND
    return max(
        1,
        int(audio_seconds * ORPHEUS_CHUNK_MIN_WPM / 60 * ORPHEUS_CHUNK_SAFETY),
    )


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
    token_budget = max_tokens or config.ORPHEUS_TTS_MAX_TOKENS
    script_path_obj, output_dir_path, _tts_input, cleaned = _prepare_tts_input(
        script_path,
        output_dir,
        strip_speaker_labels=True,
    )
    chunk_words = _orpheus_chunk_word_limit(token_budget)
    input_paths, chunks = _write_chunk_inputs(
        cleaned,
        output_dir_path,
        max_words=chunk_words,
    )
    emit(f"TTS input: stripped speaker labels -> {output_dir_path / 'tts_input.txt'}")
    if len(input_paths) > 1:
        emit(
            f"TTS input: Orpheus-safe split into {len(input_paths)} chunks "
            f"(up to {chunk_words} words for {token_budget} audio tokens each)"
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
                "max_tokens": token_budget,
                # Greedy decoding removes per-request sampling drift. The
                # selected built-in voice remains identical across chunks.
                "temperature": 0.0,
                "top_p": 1.0,
                "top_k": 0,
                "min_p": 0.0,
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
            staged_part = expected_part.with_suffix(".tmp.wav")
            staged_part.write_bytes(response.content)
            os.replace(staged_part, expected_part)
            _validate_wav_part(
                expected_part,
                chunks[index - 1],
                token_limit_seconds=(
                    token_budget
                    / ORPHEUS_AUDIO_TOKENS_PER_SECOND
                    / (config.ORPHEUS_TTS_SPEED_PERCENT / 100)
                ),
                speed=config.ORPHEUS_TTS_SPEED_PERCENT / 100,
            )
            wav_parts.append(expected_part)

    expected = await _concat_wav_parts(wav_parts, output_dir_path, log=log)
    _write_tts_manifest(
        output_dir_path,
        model="orpheus-en",
        source_text=cleaned,
        chunks=chunks,
        wav_parts=wav_parts,
        output=expected,
        deterministic=True,
    )
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
    preserve_speaker_labels = bool(model.get("requires_speaker_labels"))
    script_path_obj, output_dir_path, _tts_input, prepared_text = _prepare_tts_input(
        script_path,
        output_dir,
        strip_speaker_labels=not preserve_speaker_labels,
    )
    input_paths, chunks = _write_chunk_inputs(
        prepared_text,
        output_dir_path,
        max_words=config.VIBEVOICE_TTS_CHUNK_WORDS,
        preserve_speaker_labels=preserve_speaker_labels,
    )
    input_contract = (
        "preserved speaker labels"
        if preserve_speaker_labels
        else "stripped speaker labels"
    )
    emit(f"TTS input: {input_contract} -> {output_dir_path / 'tts_input.txt'}")
    if len(input_paths) > 1:
        emit(
            f"TTS input: VibeVoice-safe split into {len(input_paths)} "
            f"chunks (limit {config.VIBEVOICE_TTS_CHUNK_WORDS} words each)"
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
python "{config.PROJECT_ROOT / 'backend' / 'pipeline' / 'tts_seeded_runner.py'}" \
    {config.TTS_RANDOM_SEED} \
    "{model['inference_script']}" \
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
        _validate_wav_part(expected_part, chunks[index - 1])
        wav_parts.append(expected_part)

    expected = await _concat_wav_parts(wav_parts, output_dir_path, log=log)
    _write_tts_manifest(
        output_dir_path,
        model=tts_model,
        source_text=prepared_text,
        chunks=chunks,
        wav_parts=wav_parts,
        output=expected,
        deterministic=True,
    )

    emit(f"TTS output: {expected} ({expected.stat().st_size / 1024:.0f} KB)")
    return str(expected)
