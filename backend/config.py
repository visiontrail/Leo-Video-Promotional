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

AIWORK_ROOT = Path(os.getenv("AIWORK_ROOT", "/Volumes/TP-1TB/AIWork"))
TTS_ENV_SCRIPT = AIWORK_ROOT / "env_vibevoice_1.5b.sh"
TTS_PROJECT_DIR = AIWORK_ROOT / "VibeVoice-1.5B"
TTS_INFERENCE_SCRIPT = TTS_PROJECT_DIR / "demo" / "inference_from_file.py"
TTS_DEVICE = os.getenv("TTS_DEVICE", "mps")
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
OUTPUTS_DIR = PROJECT_ROOT / os.getenv("OUTPUTS_DIR", "outputs")
DB_PATH = PROJECT_ROOT / "tasks.db"
UPLOADS_DIR = PROJECT_ROOT / "uploads"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

OUTPUTS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)
