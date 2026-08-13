import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
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
ORPHEUS_MIN_EXACT_ASR_COVERAGE = 1.0
ORPHEUS_MIN_ASR_WORD_RATIO = 1.0
ORPHEUS_MAX_ASR_WORD_RATIO = 1.0
ORPHEUS_EDGE_ANCHOR_WORDS = 3
ORPHEUS_MAX_INTEGRITY_ATTEMPTS = 3
ORPHEUS_MIN_REQUEST_TOKENS = 512
MAX_PLAUSIBLE_SPEECH_WPM = 320
LEXICAL_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?|[\u3400-\u9fff]")
NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90",
}
ORDINAL_DIGITS = {
    "1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth",
    "5th": "fifth", "6th": "sixth", "7th": "seventh", "8th": "eighth",
    "9th": "ninth", "10th": "tenth", "20th": "twentieth", "30th": "thirtieth",
}
# Acoustic verification cannot distinguish exact homophones. Keep this list
# deliberately narrow; near-homophones such as ``feed``/``feet`` must still fail.
ACOUSTIC_EQUIVALENTS = {"feat": "feet"}
NUMBER_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}
DANGLING_CHUNK_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
    "into", "nor", "of", "on", "or", "the", "to", "with",
}
TERMINAL_SPEECH_PUNCTUATION_RE = re.compile(r"[.!?。！？][\"'’”)]*\s*$")


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


def _lexical_tokens(text: str) -> list[str]:
    normalized: list[str] = []
    for token in LEXICAL_TOKEN_RE.findall(_strip_speaker_labels(text)):
        value = token.replace("’", "'").casefold()
        # Whisper commonly renders spoken "percent" as the punctuation symbol
        # "%", which is not a lexical token. Ignore the unit on both sides;
        # the adjacent normalized number remains the acoustic anchor.
        if value == "percent":
            continue
        value = ACOUSTIC_EQUIVALENTS.get(value, value)
        normalized.append(ORDINAL_DIGITS.get(value, NUMBER_WORDS.get(value, value)))
    return _canonicalize_number_tokens(normalized)


def _canonicalize_number_tokens(tokens: list[str]) -> list[str]:
    """Collapse acoustically identical written/spoken English number forms."""
    result: list[str] = []
    index = 0
    while index < len(tokens):
        if (
            tokens[index].isdigit()
            and index + 1 < len(tokens)
            and len(tokens[index + 1]) == 3
            and tokens[index + 1].isdigit()
        ):
            result.append(tokens[index] + tokens[index + 1])
            index += 2
            continue
        if (
            tokens[index] == "a"
            and index + 1 < len(tokens)
            and tokens[index + 1] in NUMBER_SCALES
        ):
            start = index
            current = 1
        elif tokens[index] in NUMBER_SCALES:
            start = index
            current = 1
        else:
            result.append(tokens[index])
            index += 1
            continue
        index = start + (2 if tokens[start] == "a" else 1)
        first_scale = tokens[index - 1]
        current *= NUMBER_SCALES[first_scale]
        while index < len(tokens) and tokens[index] in NUMBER_SCALES:
            scale = NUMBER_SCALES[tokens[index]]
            current = current * scale if scale >= 1_000 else current + scale
            index += 1
        result.append(str(current))
    return result


def _transcript_tokens(words: list[dict]) -> tuple[list[str], list[int]]:
    tokens: list[str] = []
    word_indexes: list[int] = []
    for index, word in enumerate(words):
        for token in _lexical_tokens(str(word.get("text") or "")):
            tokens.append(token)
            word_indexes.append(index)
    canonical = _canonicalize_number_tokens(tokens)
    if len(canonical) == len(tokens):
        return canonical, word_indexes
    # Canonical number collapsing is used only for lexical comparison. Timing
    # indexes remain conservative at the first contributing Whisper word.
    canonical_indexes: list[int] = []
    cursor = 0
    for token in canonical:
        canonical_indexes.append(word_indexes[min(cursor, len(word_indexes) - 1)])
        cursor += 2 if token.isdigit() and cursor + 1 < len(tokens) else 1
    return canonical, canonical_indexes


def _subsequence_starts(haystack: list[str], needle: list[str]) -> list[int]:
    if not needle or len(needle) > len(haystack):
        return []
    return [
        index
        for index in range(len(haystack) - len(needle) + 1)
        if haystack[index:index + len(needle)] == needle
    ]


def _repetition_start(haystack: list[str], needle: list[str]) -> int | None:
    """Return the second utterance onset, including a truncated repetition.

    Orpheus can spend the remainder of its token budget starting the requested
    sentence again.  Waiting for a second *complete* copy misses that partial
    duplicate, so after locating one complete utterance also look for its
    three-word opening anchor in the trailing transcript.
    """
    complete = _subsequence_starts(haystack, needle)
    if len(complete) > 1:
        return complete[1]
    if len(complete) != 1:
        return None
    anchor_size = min(ORPHEUS_EDGE_ANCHOR_WORDS, len(needle))
    if anchor_size < 2:
        return None
    trailing_start = complete[0] + len(needle)
    opening = needle[:anchor_size]
    for index in range(trailing_start, len(haystack) - anchor_size + 1):
        if haystack[index:index + anchor_size] == opening:
            return index
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
                take = min(max_words, len(words))
                while (
                    take > 1
                    and take < len(words)
                    and words[take - 1].strip(".,!?;:\"'’”()[]{}").casefold()
                    in DANGLING_CHUNK_WORDS
                ):
                    take -= 1
                piece = " ".join(words[:take])
                words = words[take:]
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
    integrity: dict | None = None,
) -> None:
    parts = []
    for text, path in zip(chunks, wav_parts):
        info = _read_pcm_wav(path)
        parts.append(
            {
                "input": path.with_suffix(".txt").name.replace("_generated", ""),
                "output": path.name,
                "audio_sha256": _file_sha256(path),
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
        "output_audio_sha256": _file_sha256(output),
        "output_wav": asdict(_read_pcm_wav(output)),
    }
    if integrity is not None:
        payload["integrity"] = integrity
    (output_dir / "tts_manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _orpheus_chunk_word_limit(max_tokens: int) -> int:
    audio_seconds = max_tokens / ORPHEUS_AUDIO_TOKENS_PER_SECOND
    return max(
        1,
        int(audio_seconds * ORPHEUS_CHUNK_MIN_WPM / 60 * ORPHEUS_CHUNK_SAFETY),
    )


def _orpheus_request_token_budget(text: str, maximum: int) -> int:
    """Bound one short utterance without starving slow natural delivery."""
    expected_seconds = _spoken_word_count(text) * 60 / ORPHEUS_CHUNK_MIN_WPM
    estimated = math.ceil(
        expected_seconds * ORPHEUS_AUDIO_TOKENS_PER_SECOND / ORPHEUS_CHUNK_SAFETY
    )
    return min(maximum, max(ORPHEUS_MIN_REQUEST_TOKENS, estimated))


def _orpheus_prompt_text(text: str) -> str:
    """Give every short LM request an explicit speech termination boundary."""
    stripped = text.rstrip()
    return stripped if TERMINAL_SPEECH_PUNCTUATION_RE.search(stripped) else stripped + "."


def _orpheus_transcript_report(text: str, words: list[dict]) -> dict:
    """Measure whether a short WAV contains its complete requested utterance.

    Whisper is an independent acoustic observer, so exact-token coverage is not
    expected to be 100% for numbers, names, or contractions. Completeness is
    instead fail-closed at the utterance level: high exact coverage, a plausible
    word count, and acoustic anchors at both ends must all pass. When every
    source utterance passes, the final manifest records 100% verified source
    coverage while preserving the raw ASR measurements for audit.
    """
    expected = _lexical_tokens(text)
    observed, observed_word_indexes = _transcript_tokens(words)
    matcher = SequenceMatcher(a=expected, b=observed, autojunk=False)
    pairs: list[tuple[int, int]] = []
    for block in matcher.get_matching_blocks():
        pairs.extend((block.a + offset, block.b + offset) for offset in range(block.size))

    matched_expected = {left for left, _ in pairs}
    exact_coverage = len(matched_expected) / max(1, len(expected))
    word_ratio = len(observed) / max(1, len(expected))
    edge = min(ORPHEUS_EDGE_ANCHOR_WORDS, len(expected))
    leading_anchor = any(index < edge for index in matched_expected)
    trailing_anchor = any(index >= len(expected) - edge for index in matched_expected)
    speech_end = max((float(word.get("end") or 0) for word in words), default=0.0)
    repetition_start = _repetition_start(observed, expected)
    repeat_start_seconds = None
    if repetition_start is not None:
        repeat_word_index = observed_word_indexes[repetition_start]
        repeat_start_seconds = max(0.0, float(words[repeat_word_index].get("start") or 0))

    failures: list[str] = []
    if exact_coverage < ORPHEUS_MIN_EXACT_ASR_COVERAGE:
        failures.append(
            f"exact ASR word coverage {exact_coverage:.1%} is below "
            f"{ORPHEUS_MIN_EXACT_ASR_COVERAGE:.1%}"
        )
    if word_ratio < ORPHEUS_MIN_ASR_WORD_RATIO:
        failures.append(
            f"ASR returned only {len(observed)}/{len(expected)} expected-scale words"
        )
    if word_ratio > ORPHEUS_MAX_ASR_WORD_RATIO:
        failures.append(
            f"ASR returned {len(observed)}/{len(expected)} expected-scale words; "
            "the utterance was likely repeated"
        )
    if not leading_anchor:
        failures.append("opening words have no acoustic transcript anchor")
    if not trailing_anchor:
        failures.append("closing words have no acoustic transcript anchor")

    return {
        "verified": not failures,
        "expected_words": len(expected),
        "transcript_words": len(observed),
        "matched_exact_words": len(matched_expected),
        "exact_asr_word_coverage": round(exact_coverage, 4),
        "transcript_word_ratio": round(word_ratio, 4),
        "leading_anchor": leading_anchor,
        "trailing_anchor": trailing_anchor,
        "speech_end_seconds": round(speech_end, 3),
        "repeat_start_seconds": (
            round(repeat_start_seconds, 3) if repeat_start_seconds is not None else None
        ),
        "failure_reasons": failures,
    }


def _trim_pcm_wav(path: Path, end_seconds: float) -> None:
    info = _read_pcm_wav(path)
    end_frame = min(info.frame_count, max(1, round(end_seconds * info.sample_rate)))
    staged = path.with_suffix(".trim.tmp.wav")
    staged.unlink(missing_ok=True)
    with wave.open(str(path), "rb") as source, wave.open(str(staged), "wb") as destination:
        destination.setparams(source.getparams())
        destination.writeframes(source.readframes(end_frame))
    os.replace(staged, path)


async def _verify_orpheus_part(
    path: Path,
    text: str,
    verification_dir: Path,
    *,
    emit: LogCallback,
) -> dict:
    from backend.pipeline import av_sync

    words, transcription = await av_sync.ensure_word_transcript(
        path,
        verification_dir,
        log=None,
        minimum_words=1,
    )
    if not words:
        failures = "; ".join(transcription.get("failure_reasons") or [])
        raise TtsIntegrityError(
            "Orpheus narration cannot be integrity-verified because acoustic "
            f"transcription is unavailable{': ' + failures if failures else ''}"
        )
    report = _orpheus_transcript_report(text, words)
    repeat_start = report.get("repeat_start_seconds")
    if repeat_start is not None and float(repeat_start) > 0.2:
        emit(
            "Orpheus integrity: trimming repeated utterance at "
            f"{float(repeat_start):.2f}s and re-transcribing"
        )
        _trim_pcm_wav(path, float(repeat_start))
        words, transcription = await av_sync.ensure_word_transcript(
            path,
            verification_dir,
            log=None,
            minimum_words=1,
        )
        if not words:
            raise TtsIntegrityError(
                "Orpheus narration could not be transcribed after repetition trimming"
            )
        report = _orpheus_transcript_report(text, words)
    if not report["verified"]:
        raise TtsIntegrityError(
            "Orpheus narration does not match its input utterance: "
            + "; ".join(report["failure_reasons"])
        )
    emit(
        "Orpheus integrity: utterance verified "
        f"({report['matched_exact_words']}/{report['expected_words']} exact ASR words; "
        "opening and closing anchors present)"
    )
    return report


def _part_metadata_path(path: Path) -> Path:
    return path.with_suffix(".json")


def _load_cached_orpheus_part(path: Path, text: str) -> dict | None:
    metadata_path = _part_metadata_path(path)
    if not path.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if metadata.get("text_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
        return None
    if metadata.get("speed_percent") != config.ORPHEUS_TTS_SPEED_PERCENT:
        return None
    if not (metadata.get("integrity") or {}).get("verified"):
        return None
    try:
        info = _read_pcm_wav(path)
    except TtsIntegrityError:
        return None
    if asdict(info) != metadata.get("wav"):
        return None
    return metadata


def _write_orpheus_part_metadata(
    path: Path,
    text: str,
    *,
    job_id: str,
    request_token_budget: int,
    integrity: dict,
) -> dict:
    payload = {
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "word_count": _spoken_word_count(text),
        "job_id": job_id,
        "request_token_budget": request_token_budget,
        "speed_percent": config.ORPHEUS_TTS_SPEED_PERCENT,
        "wav": asdict(_read_pcm_wav(path)),
        "integrity": integrity,
    }
    destination = _part_metadata_path(path)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, destination)
    return payload


async def _generate_orpheus(
    script_path: str,
    output_dir: str,
    voice: str,
    language: str,
    *,
    log: LogCallback | None,
    emit: LogCallback,
    max_tokens: int | None = None,
    verify_text: bool = False,
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
    chunk_words = min(
        config.ORPHEUS_TTS_CHUNK_WORDS,
        _orpheus_chunk_word_limit(token_budget),
    )
    input_paths, chunks = _write_chunk_inputs(
        cleaned,
        output_dir_path,
        max_words=chunk_words,
    )
    emit(f"TTS input: stripped speaker labels -> {output_dir_path / 'tts_input.txt'}")
    if len(input_paths) > 1:
        emit(
            f"TTS input: Orpheus-safe split into {len(input_paths)} chunks "
            f"(up to {chunk_words} words per acoustically verified utterance)"
        )
    base_url = config.ORPHEUS_TTS_URL.rstrip("/")
    headers = {"X-API-Key": config.ORPHEUS_TTS_API_KEY}
    timeout = httpx.Timeout(config.ORPHEUS_TTS_REQUEST_TIMEOUT)
    wav_parts: list[Path] = []
    part_metadata: list[dict] = []
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for index, input_path in enumerate(input_paths, start=1):
            name = "TTS" if len(input_paths) == 1 else f"TTS part {index}/{len(input_paths)}"
            chunk = chunks[index - 1]
            expected_part = output_dir_path / f"{input_path.stem}_generated.wav"
            if verify_text:
                cached = _load_cached_orpheus_part(expected_part, chunk)
                if cached is not None:
                    emit(
                        f"{name}: reusing acoustically verified Orpheus audio "
                        f"({cached['word_count']} source words)"
                    )
                    wav_parts.append(expected_part)
                    part_metadata.append(cached)
                    continue

            request_token_budget = _orpheus_request_token_budget(chunk, token_budget)
            payload = {
                "input": _orpheus_prompt_text(input_path.read_text(encoding="utf-8")),
                "language": language,
                "voice_id": voice,
                "max_tokens": request_token_budget,
                # Orpheus is an autoregressive audio LM. Greedy decoding
                # collapses real prompts to unrelated phrases (observed as
                # repeated "Thank you"), so use the service/model defaults and
                # rely on the acoustic integrity gate instead of determinism.
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
            staged_part = expected_part.with_suffix(".tmp.wav")
            staged_part.write_bytes(response.content)
            os.replace(staged_part, expected_part)
            _validate_wav_part(
                expected_part,
                chunk,
                token_limit_seconds=(
                    None
                    if verify_text
                    else request_token_budget
                    / ORPHEUS_AUDIO_TOKENS_PER_SECOND
                    / (config.ORPHEUS_TTS_SPEED_PERCENT / 100)
                ),
                speed=config.ORPHEUS_TTS_SPEED_PERCENT / 100,
            )
            if verify_text:
                integrity = await _verify_orpheus_part(
                    expected_part,
                    chunk,
                    output_dir_path / "verification" / input_path.stem,
                    emit=emit,
                )
            else:
                integrity = {
                    "verified": True,
                    "method": "duration_only_preview",
                    "expected_words": _spoken_word_count(chunk),
                }
            metadata = _write_orpheus_part_metadata(
                expected_part,
                chunk,
                job_id=job_id,
                request_token_budget=request_token_budget,
                integrity=integrity,
            )
            wav_parts.append(expected_part)
            part_metadata.append(metadata)

    expected = await _concat_wav_parts(wav_parts, output_dir_path, log=log)
    source_words = _spoken_word_count(cleaned)
    verified_words = sum(
        int(metadata.get("word_count") or 0)
        for metadata in part_metadata
        if (metadata.get("integrity") or {}).get("verified")
    )
    integrity = {
        "method": "per_utterance_mlx_whisper",
        "required": verify_text,
        "passed": verified_words == source_words,
        "source_words": source_words,
        "verified_source_words": verified_words,
        "verified_source_coverage": round(verified_words / max(1, source_words), 4),
        "part_reports": [metadata.get("integrity") or {} for metadata in part_metadata],
    }
    if verify_text and not integrity["passed"]:
        raise TtsIntegrityError(
            f"Orpheus verified only {verified_words}/{source_words} source words; "
            "refusing to join incomplete narration"
        )
    _write_tts_manifest(
        output_dir_path,
        model="orpheus-en",
        source_text=cleaned,
        chunks=chunks,
        wav_parts=wav_parts,
        output=expected,
        deterministic=False,
        integrity=integrity,
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
        for attempt in range(1, ORPHEUS_MAX_INTEGRITY_ATTEMPTS + 1):
            try:
                return await _generate_orpheus(
                    script_path,
                    output_dir,
                    voices[0],
                    str(model.get("language") or "en"),
                    log=log,
                    emit=emit,
                    verify_text=True,
                )
            except TtsIntegrityError as exc:
                if attempt >= ORPHEUS_MAX_INTEGRITY_ATTEMPTS:
                    raise
                emit(
                    "Orpheus integrity retry "
                    f"{attempt}/{ORPHEUS_MAX_INTEGRITY_ATTEMPTS - 1}: {exc}. "
                    "Verified earlier utterances will be reused."
                )
        raise AssertionError("unreachable Orpheus integrity retry state")

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
