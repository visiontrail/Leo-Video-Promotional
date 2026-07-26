import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

AI_ENDPOINT = os.getenv("AI_ENDPOINT", "http://oneapi.yhroot.com/v1/chat/completions")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "glm-4.6-chat")
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "120"))
AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "2"))

# Which backend drives digestion/scriptwriting AI calls:
#   "agent_sdk" (default) — Claude Agent SDK, talking the Anthropic protocol to
#                           a provider gateway (the bundled/system `claude` CLI
#                           is spawned in-process by the SDK).
#   "http"                — the legacy direct OpenAI-compatible HTTP client.
# The provider registry (endpoint/api_key/model) still drives both; for the
# Agent SDK the endpoint is mapped to an Anthropic base URL (see ANTHROPIC_*).
AI_BACKEND = os.getenv("AI_BACKEND", "agent_sdk").strip().lower()

# Anthropic-protocol provider settings for the Claude Agent SDK. When left
# blank, ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN are derived from the resolved
# provider (its OpenAI-style endpoint host and api_key). Set them explicitly to
# point at a gateway whose Anthropic route lives on a non-root path (e.g.
# DeepSeek's https://api.deepseek.com/anthropic).
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "").strip()
ANTHROPIC_AUTH_TOKEN = os.getenv("ANTHROPIC_AUTH_TOKEN", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "").strip()
# Haiku-tier model the CLI uses for cheap background tasks (title/summary);
# point it at the same gateway model when the provider has no separate haiku.
ANTHROPIC_DEFAULT_HAIKU_MODEL = os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "").strip()
# Optional explicit path to the `claude` CLI. Blank = let the SDK locate it
# (bundled with the wheel, else the first `claude` on PATH).
CLAUDE_CLI_PATH = os.getenv("CLAUDE_CLI_PATH", "").strip()

AIWORK_ROOT = Path(os.getenv("AIWORK_ROOT", "/Volumes/TP-1TB/AIWork"))
TTS_DEVICE = os.getenv("TTS_DEVICE", "mps")

# Registry of installed VibeVoice TTS models. Each entry carries the full
# invocation contract for that model: which venv to source, which project
# directory to cd into, which inference script to run, and which speaker flag
# the script expects (1.5B uses plural --speaker_names, 0.5B uses singular
# --speaker_name). The 0.5B realtime model is single-speaker, so it only ever
# receives one voice source. Adding a future model is a data change here, not a
# code change in tts.py.
TTS_DEFAULT_MODEL = os.getenv("TTS_DEFAULT_MODEL", "vibevoice-1.5b")
TTS_MODELS: dict[str, dict] = {
    "vibevoice-1.5b": {
        "label": "1.5B (high quality)",
        "env_script": AIWORK_ROOT / "env_vibevoice_1.5b.sh",
        "project_dir": AIWORK_ROOT / "VibeVoice-1.5B",
        "inference_script": AIWORK_ROOT / "VibeVoice-1.5B" / "demo" / "inference_from_file.py",
        "speaker_flag": "--speaker_names",
        "single_speaker": False,
    },
    "vibevoice-0.5b": {
        "label": "0.5B (fast draft)",
        "env_script": AIWORK_ROOT / "env_vibevoice.sh",
        "project_dir": AIWORK_ROOT / "VibeVoice",
        "inference_script": AIWORK_ROOT / "VibeVoice" / "demo" / "realtime_model_inference_from_file.py",
        "speaker_flag": "--speaker_name",
        "single_speaker": True,
    },
}
TTS_DEFAULT_VOICE_1 = os.getenv("TTS_DEFAULT_VOICE_1", "Carter")
TTS_DEFAULT_VOICE_2 = os.getenv("TTS_DEFAULT_VOICE_2", "Alice")

AVAILABLE_VOICES = {
    "Carter": {"gender": "male", "lang": "en"},
    "Frank": {"gender": "male", "lang": "en"},
    "Alice": {"gender": "female", "lang": "en"},
    "Maya": {"gender": "female", "lang": "en"},
    "Mary": {"gender": "female", "lang": "en"},
    "Samuel": {"gender": "male", "lang": "in"},
}

HYPERFRAME_DIR = PROJECT_ROOT / os.getenv("HYPERFRAME_DIR", "hyperframe")

# HyperFrames render tuning. The CLI is pinned and installed locally under
# HYPERFRAME_DIR/node_modules so a render never triggers an on-demand `npx`
# install (the old unpinned `npx hyperframes` re-installed latest every run —
# minutes of silent stall on first/cold runs, and a moving version target).
# Frame-by-frame headless-Chrome capture of a multi-minute 1080p composition is
# inherently heavy (~frames = duration x fps), so the defaults trade frame rate
# and quality for wall-clock time. All are env-overridable.
HYPERFRAMES_VERSION = os.getenv("HYPERFRAMES_VERSION", "0.6.99")
RENDER_FPS = int(os.getenv("RENDER_FPS", "15"))
RENDER_QUALITY = os.getenv("RENDER_QUALITY", "draft")  # draft | standard | high
RENDER_WORKERS = os.getenv("RENDER_WORKERS", "2")      # integer or "auto"
RENDER_RESOLUTION = os.getenv("RENDER_RESOLUTION", "landscape")  # 1920x1080

def _resolve_dir(env_name: str, default_rel: str) -> Path:
    """Resolve a directory from env: absolute paths (e.g. Docker volume mounts)
    are used as-is; relative paths hang off the project root."""
    raw = os.getenv(env_name, default_rel)
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p


OUTPUTS_DIR = _resolve_dir("OUTPUTS_DIR", "outputs")
UPLOADS_DIR = _resolve_dir("UPLOADS_DIR", "uploads")
# DB_PATH may point at a mounted volume in Docker; defaults to the repo root.
_db_env = os.getenv("DB_PATH", "")
DB_PATH = Path(_db_env) if _db_env else PROJECT_ROOT / "tasks.db"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
ISLA_READER_PROMOTION_SCRIPT = Path(
    os.getenv(
        "ISLA_READER_PROMOTION_SCRIPT",
        "/Volumes/TP-1TB/WorspacePlus/IslaProject/Isla-Reader-Promotional/Promotion-Agent/generate-promotion.sh",
    )
)

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
