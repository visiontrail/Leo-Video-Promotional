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

OUTPUTS_DIR = PROJECT_ROOT / os.getenv("OUTPUTS_DIR", "outputs")
DB_PATH = PROJECT_ROOT / "tasks.db"
UPLOADS_DIR = PROJECT_ROOT / "uploads"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
ISLA_READER_PROMOTION_SCRIPT = Path(
    os.getenv(
        "ISLA_READER_PROMOTION_SCRIPT",
        "/Volumes/TP-1TB/WorspacePlus/IslaProject/Isla-Reader-Promotional/Promotion-Agent/generate-promotion.sh",
    )
)

OUTPUTS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)
