from fastapi import APIRouter
from backend import config
from backend.models import SettingsResponse, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
async def get_settings():
    return SettingsResponse(
        ai_endpoint=config.AI_ENDPOINT,
        ai_model=config.AI_MODEL,
        tts_device=config.TTS_DEVICE,
        default_voice_1=config.TTS_DEFAULT_VOICE_1,
        default_voice_2=config.TTS_DEFAULT_VOICE_2,
        available_voices=config.AVAILABLE_VOICES,
    )


@router.put("", response_model=SettingsResponse)
async def update_settings(body: SettingsUpdate):
    if body.ai_endpoint is not None:
        config.AI_ENDPOINT = body.ai_endpoint
    if body.ai_api_key is not None:
        config.AI_API_KEY = body.ai_api_key
    if body.ai_model is not None:
        config.AI_MODEL = body.ai_model
    return await get_settings()
