import logging
import re
from collections.abc import Callable
from pathlib import Path
from backend import config
from backend.pipeline.process_logging import stream_subprocess

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

# Synthesis is watched by inactivity, not elapsed time: a long script legitimately
# runs for hours, but VibeVoice prints a decode-progress line several times a
# second, so silence is the only reliable hang signal. The values are read from
# config at call time so an Admin change applies to the next run.
SPEAKER_LABEL_RE = re.compile(r"^\s*Speaker\s*\d+\s*[:：\-—–]\s*", re.IGNORECASE)


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
    script_path_obj = Path(script_path).expanduser().resolve()
    output_dir_path = Path(output_dir).expanduser().resolve()
    output_dir_path.mkdir(parents=True, exist_ok=True)

    # TTS models can read speaker labels aloud, so always feed a label-stripped
    # copy while leaving canonical script.txt available for captions/editing.
    cleaned = _strip_speaker_labels(script_path_obj.read_text(encoding="utf-8"))
    tts_input = output_dir_path / "tts_input.txt"
    tts_input.write_text(cleaned, encoding="utf-8")
    tts_script_path = str(tts_input)
    emit(f"TTS input: stripped speaker labels -> {tts_input}")

    speaker_args = " ".join(f'"{v}"' for v in voices)

    cmd = f"""
source "{model['env_script']}"
cd "{model['project_dir']}"
python "{model['inference_script']}" \
    --txt_path "{tts_script_path}" \
    {model['speaker_flag']} {speaker_args} \
    --output_dir "{output_dir_path}" \
    --device {config.TTS_DEVICE}
"""

    emit(f"Running TTS: model={tts_model}, voices={voices}, script={script_path_obj}")
    returncode, output = await stream_subprocess(
        name="TTS",
        command=["bash", "-c", cmd],
        logger=logger,
        log=log,
        cwd=model["project_dir"],
        timeout=config.TTS_TIMEOUT,
        stall_timeout=config.TTS_STALL_TIMEOUT,
    )

    if returncode != 0:
        raise RuntimeError(f"TTS generation failed (exit {returncode}): {output[-500:]}")

    # The inference scripts name output "<input-stem>_generated.wav" from the
    # txt they actually read, which is tts_script_path (the cleaned copy).
    script_stem = Path(tts_script_path).stem
    expected = output_dir_path / f"{script_stem}_generated.wav"
    if expected.exists():
        emit(f"TTS output: {expected} ({expected.stat().st_size / 1024:.0f} KB)")
        return str(expected)

    wav_files = list(output_dir_path.glob("*.wav"))
    if wav_files:
        latest = max(wav_files, key=lambda p: p.stat().st_mtime)
        emit(f"TTS output (fallback): {latest}")
        return str(latest)

    raise RuntimeError("TTS produced no WAV output")
