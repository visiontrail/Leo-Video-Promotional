from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class SourceType(str, Enum):
    YOUTUBE = "youtube"
    EPUB = "epub"
    PDF = "pdf"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    DIGESTING = "digesting"
    TTS = "tts"
    COMPOSING = "composing"
    COMPLETE = "complete"
    FAILED = "failed"


class TaskConfig(BaseModel):
    target_duration_minutes: int = 10
    speaker_count: int = 2
    voice_1: str = "Carter"
    voice_2: str = "Alice"
    include_character: bool = False
    ai_endpoint: Optional[str] = None
    ai_model: Optional[str] = None
    provider_id: Optional[int] = None


class TaskCreate(BaseModel):
    source_type: SourceType
    source_url: Optional[str] = None
    config: TaskConfig = Field(default_factory=TaskConfig)


class TaskResponse(BaseModel):
    id: str
    created_at: str
    updated_at: str
    source_type: SourceType
    source_url: Optional[str] = None
    source_title: Optional[str] = None
    status: TaskStatus
    error_message: Optional[str] = None
    config: TaskConfig
    output_dir: Optional[str] = None
    script_path: Optional[str] = None
    audio_path: Optional[str] = None
    video_path: Optional[str] = None
    duration_seconds: Optional[float] = None


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]


class ProviderCreate(BaseModel):
    name: str
    endpoint: str
    api_key: Optional[str] = None
    model: str
    is_default: bool = False


class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    endpoint: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    is_default: Optional[bool] = None


class ProviderResponse(BaseModel):
    id: int
    name: str
    endpoint: str
    api_key_masked: str
    model: str
    is_default: bool
    created_at: str


class ProviderListResponse(BaseModel):
    providers: list[ProviderResponse]


class SettingsResponse(BaseModel):
    ai_endpoint: str
    ai_model: str
    tts_device: str
    default_voice_1: str
    default_voice_2: str
    available_voices: dict


class SettingsUpdate(BaseModel):
    ai_endpoint: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_model: Optional[str] = None


def new_task_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
