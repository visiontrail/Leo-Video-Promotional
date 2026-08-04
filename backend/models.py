from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
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
    SOURCING = "sourcing"
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
    # Captions historically rendered for every task. Keep that behaviour for
    # older clients while allowing new tasks to opt out explicitly.
    captions_enabled: bool = True
    tts_model: str = "vibevoice-0.5b"
    video_template: str = "podcast"
    processing_mode: str = "full_text"
    ai_endpoint: Optional[str] = None
    ai_model: Optional[str] = None
    provider_id: Optional[int] = None
    # Optional B-roll scout. New tasks enable this from the UI; the model
    # default remains off so tasks created by older clients keep their original
    # network and storage behavior.
    footage_enabled: bool = False
    footage_provider: str = "wikimedia"  # wikimedia | hybrid | opencli_web
    footage_license_policy: str = "open_only"
    footage_clip_count: int = Field(default=3, ge=1, le=6)
    footage_orientation: str = "landscape"
    footage_multimodal_analyzer: str = "gemini_web"
    # Generate a script-driven cover through the signed-in ChatGPT web app.
    # This runs before TTS and can therefore be tested independently.
    thumbnail_enabled: bool = True
    # Skip the audio review pause and go straight from TTS into compose by
    # default. Clients can still opt into a manual review explicitly.
    auto_render: bool = True


class TaskCreate(BaseModel):
    source_type: SourceType
    source_url: Optional[str] = None
    config: TaskConfig = Field(default_factory=TaskConfig)
    # Hold the task in the queue until this moment (UTC ISO-8601). None starts
    # it as soon as the worker is free.
    scheduled_at: Optional[str] = None


class TaskSchedule(BaseModel):
    """Move a still-queued task's start time, or clear it to start now."""
    scheduled_at: Optional[str] = None


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
    scheduled_at: Optional[str] = None
    output_dir: Optional[str] = None
    script_path: Optional[str] = None
    audio_path: Optional[str] = None
    video_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    duration_seconds: Optional[float] = None


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]


class ScriptUpdate(BaseModel):
    content: str


class FootageAcquireRequest(BaseModel):
    queries: list[str] = Field(default_factory=list, max_length=6)


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


class VoiceOption(BaseModel):
    name: str
    gender: str
    lang: str
    # The preset the selected TTS model actually uses (0.5B substitutes some).
    resolved_name: str
    preview_available: bool


class SettingsUpdate(BaseModel):
    ai_endpoint: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_model: Optional[str] = None


class SettingField(BaseModel):
    """One runtime setting, as rendered by the Admin console."""
    key: str                      # the .env variable name / config attribute
    label: str
    type: str                     # string | secret | int | bool | choice | path
    description: str = ""
    placeholder: str = ""
    unit: str = ""
    options: list[str] = Field(default_factory=list)
    value: Any = None
    default: Any = None
    is_overridden: bool = False
    restart_required: bool = False
    allow_blank: bool = True
    # Secrets only: the value is never sent back, just its shape.
    masked: Optional[str] = None
    default_masked: Optional[str] = None
    is_set: Optional[bool] = None


class SettingGroup(BaseModel):
    id: str
    label: str
    description: str = ""
    fields: list[SettingField] = Field(default_factory=list)


class SettingsSchemaResponse(BaseModel):
    groups: list[SettingGroup]
    # Keys whose last change needs a restart before it fully takes effect.
    restart_required: list[str] = Field(default_factory=list)


class SettingsValuesUpdate(BaseModel):
    """Partial update: only the submitted keys are touched."""
    values: dict[str, Any] = Field(default_factory=dict)


class SettingsResetRequest(BaseModel):
    keys: list[str] = Field(default_factory=list)


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


def normalize_schedule(value: str | None) -> str | None:
    """Normalise a client-supplied start time to a UTC ISO-8601 string.

    Start times are compared as text in SQL (``scheduled_at <= now``), so every
    stored value has to carry the same UTC offset — a local-offset string like
    ``2026-07-28T01:00+08:00`` would otherwise compare wrong. Naive input is
    read as UTC. Empty input means "no schedule".

    Raises ValueError on input that is not a valid ISO-8601 datetime.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid start time: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
