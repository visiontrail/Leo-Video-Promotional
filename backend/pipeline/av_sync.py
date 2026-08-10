"""Acoustic timing and audio/visual quality gates.

The script is the semantic source of truth, but it is not a clock. TTS delivery
changes with punctuation, numbers, names and pauses, so word-count timing and a
raw silence list both drift on long narration. This module transcribes the
rendered WAV to word timestamps, caches the result next to the task, and lets
``storyboard`` force-align the canonical script to that acoustic clock.

MLX Whisper is preferred on Apple Silicon because it is fast and can reuse the
operator's Hugging Face cache. HyperFrames' whisper.cpp command is the portable
fallback. A render configured to require alignment fails closed if neither
backend can produce a trustworthy transcript.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from backend import config
from backend.pipeline.process_logging import stream_subprocess

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

TRANSCRIPT_NAME = "transcript.json"
TRANSCRIPT_META_NAME = "transcript.meta.json"
TRANSCRIBE_TIMEOUT = 1800
TRANSCRIBE_STALL_TIMEOUT = 900
NON_SPEECH_TOKENS = frozenset({"♪", "♫", "♬", "�"})


def _emit(log: LogCallback | None, message: str) -> None:
    if log:
        log(message)
    else:
        logger.info(message)


def _audio_signature(audio_path: Path) -> dict[str, int]:
    stat = audio_path.stat()
    return {"audio_size": stat.st_size, "audio_mtime_ns": stat.st_mtime_ns}


def _load_words(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []

    words: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or entry.get("word") or "").strip()
        try:
            start = float(entry["start"])
            end = float(entry["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or end <= start:
            continue
        words.append({"text": text, "start": round(max(0.0, start), 3), "end": round(end, 3)})
    words.sort(key=lambda word: (word["start"], word["end"]))
    return words


def _transcript_quality(words: list[dict]) -> tuple[bool, str]:
    if len(words) < 10:
        return False, f"only {len(words)} timestamped words"
    non_speech = sum(1 for word in words if word["text"].strip() in NON_SPEECH_TOKENS)
    ratio = non_speech / len(words)
    if ratio > 0.20:
        return False, f"{ratio:.1%} non-speech/music tokens"
    return True, ""


def _cache_is_current(task_dir: Path, audio_path: Path) -> bool:
    transcript_path = task_dir / TRANSCRIPT_NAME
    meta_path = task_dir / TRANSCRIPT_META_NAME
    if not transcript_path.is_file():
        return False
    signature = _audio_signature(audio_path)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Backwards-compatible reuse of a transcript made after the WAV.
        return transcript_path.stat().st_mtime_ns >= audio_path.stat().st_mtime_ns
    return all(meta.get(key) == value for key, value in signature.items())


def _python_can_import(python: Path, module: str) -> bool:
    if not python.is_file():
        return False
    try:
        return (
            subprocess.run(
                [str(python), "-c", f"import {module}"],
                capture_output=True,
                timeout=20,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _mlx_command(audio_path: Path, task_dir: Path) -> list[str] | None:
    python = Path(sys.executable)
    if importlib.util.find_spec("mlx_whisper") is None:
        # The TTS installation already carries torch/numpy/scipy, so setup may
        # install the small MLX-specific layer there to avoid duplicating
        # several gigabytes in the application venv.
        candidates = [
            config.AIWORK_ROOT / "venvs" / "vibevoice" / "bin" / "python",
            config.AIWORK_ROOT / "venvs" / "vibevoice-1.5b" / "bin" / "python",
        ]
        python = next(
            (candidate for candidate in candidates if _python_can_import(candidate, "mlx_whisper")),
            Path(),
        )
    if not python.is_file():
        return None
    return [
        str(python),
        "-m",
        "backend.pipeline.av_sync",
        "transcribe-mlx",
        str(audio_path),
        str(task_dir / TRANSCRIPT_NAME),
        "--model",
        config.AV_SYNC_MLX_MODEL,
        "--language",
        config.AV_SYNC_LANGUAGE,
    ]


def _hyperframes_command(audio_path: Path, task_dir: Path) -> list[str] | None:
    binary = config.HYPERFRAME_DIR / "node_modules" / ".bin" / "hyperframes"
    if not binary.is_file():
        return None
    return [
        str(binary),
        "transcribe",
        str(audio_path),
        "--dir",
        str(task_dir),
        "--model",
        config.AV_SYNC_WHISPER_MODEL,
        "--language",
        config.AV_SYNC_LANGUAGE,
        "--json",
    ]


async def ensure_word_transcript(
    audio_path: str | Path,
    task_dir: str | Path,
    *,
    required: bool = True,
    log: LogCallback | None = None,
) -> tuple[list[dict], dict]:
    """Return a current, quality-checked word transcript for ``audio_path``."""
    audio = Path(audio_path).resolve()
    directory = Path(task_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    transcript_path = directory / TRANSCRIPT_NAME

    if _cache_is_current(directory, audio):
        words = _load_words(transcript_path)
        good, reason = _transcript_quality(words)
        if good:
            _emit(log, f"A/V sync: reusing {len(words)} cached word timestamps")
            return words, {"backend": "cache", "word_count": len(words), "passed": True}
        _emit(log, f"A/V sync: cached transcript rejected ({reason}); regenerating")

    attempts: list[tuple[str, list[str] | None]] = [
        ("mlx-whisper", _mlx_command(audio, directory)),
        ("hyperframes-whisper.cpp", _hyperframes_command(audio, directory)),
    ]
    failures: list[str] = []
    backend_used = ""
    for backend, command in attempts:
        if command is None:
            failures.append(f"{backend}: runtime unavailable")
            continue
        _emit(log, f"A/V sync: transcribing narration with {backend}")
        try:
            returncode, output = await stream_subprocess(
                name=f"A/V transcription ({backend})",
                command=command,
                logger=logger,
                log=log,
                cwd=config.PROJECT_ROOT,
                timeout=TRANSCRIBE_TIMEOUT,
                stall_timeout=TRANSCRIBE_STALL_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - try the portable backend next
            failures.append(f"{backend}: {exc}")
            continue
        if returncode != 0:
            failures.append(f"{backend}: exit {returncode}: {output[-300:]}")
            continue
        words = _load_words(transcript_path)
        good, reason = _transcript_quality(words)
        if not good:
            failures.append(f"{backend}: {reason}")
            continue
        backend_used = backend
        break

    if backend_used:
        meta = {
            **_audio_signature(audio),
            "backend": backend_used,
            "word_count": len(words),
            "model": (
                config.AV_SYNC_MLX_MODEL
                if backend_used == "mlx-whisper"
                else config.AV_SYNC_WHISPER_MODEL
            ),
        }
        (directory / TRANSCRIPT_META_NAME).write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _emit(log, f"A/V sync: {len(words)} word timestamps ready via {backend_used}")
        return words, {**meta, "passed": True}

    detail = "; ".join(failures)
    if required:
        raise RuntimeError(
            "A/V sync quality gate: no reliable word-level transcript was produced. " + detail
        )
    _emit(log, "A/V sync unavailable; falling back to estimated timing: " + detail)
    return [], {"passed": False, "failure_reasons": failures}


def _write_mlx_transcript(audio_path: Path, output_path: Path, model: str, language: str) -> None:
    """Subprocess entry point so model memory is released before rendering."""
    import mlx_whisper

    print(f"Loading MLX Whisper model {model}", flush=True)
    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model,
        word_timestamps=True,
        language=language,
        verbose=False,
        condition_on_previous_text=False,
    )
    words: list[dict] = []
    for segment in result.get("segments") or []:
        for word in segment.get("words") or []:
            text = str(word.get("word") or word.get("text") or "").strip()
            if not text:
                continue
            words.append(
                {
                    "text": text,
                    "start": round(float(word["start"]), 3),
                    "end": round(float(word["end"]), 3),
                }
            )
    temporary = output_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, output_path)
    print(f"Wrote {len(words)} word timestamps to {output_path}", flush=True)


def _main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    mlx = subparsers.add_parser("transcribe-mlx")
    mlx.add_argument("audio")
    mlx.add_argument("output")
    mlx.add_argument("--model", required=True)
    mlx.add_argument("--language", default="en")
    args = parser.parse_args()
    if args.command == "transcribe-mlx":
        _write_mlx_transcript(
            Path(args.audio).resolve(),
            Path(args.output).resolve(),
            args.model,
            args.language,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
