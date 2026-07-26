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
    AWAITING_REVIEW = "awaiting_review"
    COMPOSING = "composing"
    COMPLETE = "complete"
    FAILED = "failed"


class ScriptFormat(str, Enum):
    MONOLOGUE = "monologue"  # solo talk-show host (the primary, default style)
    DIALOGUE = "dialogue"    # two-host back-and-forth conversation


class TaskConfig(BaseModel):
    target_duration_minutes: int = 10
    # Solo talk-show is the primary product direction; dialogue is the secondary
    # option. speaker_count is derived from script_format and kept in sync for
    # back-compat (monologue -> 1 voice, dialogue -> 2 voices).
    script_format: ScriptFormat = ScriptFormat.MONOLOGUE
    speaker_count: int = 1
    voice_1: str = "Carter"
    voice_2: str = "Alice"
    include_character: bool = False
    tts_model: str = "vibevoice-1.5b"
    video_template: str = "podcast"
    processing_mode: str = "full_text"
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


class ScriptUpdate(BaseModel):
    content: str


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


class ProviderTestRequest(BaseModel):
    provider_id: Optional[int] = None
    endpoint: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None


class ProviderTestResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: Optional[int] = None


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


class PromptSummary(BaseModel):
    key: str
    file: str
    label: str
    stage: str
    description: str
    variables: list[str]
    has_default: bool


class PromptDetail(PromptSummary):
    content: str
    is_modified: bool
    missing_variables: list[str]


class PromptListResponse(BaseModel):
    prompts: list[PromptDetail]


class PromptUpdate(BaseModel):
    content: str


class SkillSummary(BaseModel):
    name: str
    slug: str
    description: str
    path: str
    is_symlink: bool
    enabled: bool


class SkillDetail(SkillSummary):
    body: str


class SkillListResponse(BaseModel):
    skills: list[SkillSummary]


class SkillUpdate(BaseModel):
    enabled: Optional[bool] = None
    body: Optional[str] = None


def new_task_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
