from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from backend import config
from backend.models import VoiceOption

router = APIRouter(prefix="/api/voices", tags=["voices"])


@router.get("", response_model=list[VoiceOption])
async def list_voices(tts_model: str | None = Query(default=None)):
    """Selectable voices plus whether each one can be previewed.

    Preview availability is per-model: the 0.5B model substitutes some voices,
    and a substituted preset may not ship a reference WAV.
    """
    return [
        VoiceOption(
            name=name,
            gender=meta["gender"],
            lang=meta["lang"],
            resolved_name=config.resolve_voice(name, tts_model),
            preview_available=config.voice_sample_path(name, tts_model) is not None,
        )
        for name, meta in config.AVAILABLE_VOICES.items()
    ]


@router.get("/{voice}/preview")
async def preview_voice(voice: str, tts_model: str | None = Query(default=None)):
    if voice not in config.AVAILABLE_VOICES:
        raise HTTPException(status_code=404, detail=f"Unknown voice '{voice}'")

    sample = config.voice_sample_path(voice, tts_model)
    if sample is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No preview sample installed for '{voice}'. Expected a WAV in "
                f"{config.VOICE_SAMPLE_DIR}."
            ),
        )

    # Samples are static per install; let the browser reuse them across clicks.
    return FileResponse(
        sample,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=86400"},
    )
