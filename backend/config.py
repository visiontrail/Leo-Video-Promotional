"""Process configuration.

Every value here is a *live* module attribute, not a constant: `.env` (and the
built-in fallbacks) only seed the process, and the Admin console layers its
saved settings on top at import time — see :mod:`backend.settings_store`. That
is why consumers must read ``config.NAME`` at call time rather than binding
``from backend.config import NAME`` at import: a rebound module attribute is
what makes an Admin save reach the next pipeline stage without a restart.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no")


def resolve_project_path(raw: str | os.PathLike) -> Path:
    """Resolve a configured path: absolute as-is, relative under the repo root."""
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


AI_ENDPOINT = os.getenv("AI_ENDPOINT", "http://oneapi.yhroot.com/v1/chat/completions")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "glm-4.6-chat")
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "120"))
AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "2"))

# Public-footage scouting. Wikimedia Commons needs no key, but asks API clients
# to identify themselves. The byte ceiling prevents an autonomous scout from
# pulling down unexpectedly large archival masters.
FOOTAGE_USER_AGENT = os.getenv(
    "FOOTAGE_USER_AGENT",
    "VideoPromotional/1.0 (local AI media scout)",
)
FOOTAGE_TIMEOUT = int(os.getenv("FOOTAGE_TIMEOUT", "45"))
FOOTAGE_MAX_BYTES = int(os.getenv("FOOTAGE_MAX_BYTES", str(50 * 1024 * 1024)))

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

# yt-dlp source extraction. start.sh exports sensible defaults for the first
# two before launching (a node runtime it can find, and the browser to borrow
# cookies from), which is why these are read from the environment at all.
YTDLP_JS_RUNTIME = os.getenv("YTDLP_JS_RUNTIME", "").strip()
YTDLP_REMOTE_COMPONENTS = os.getenv("YTDLP_REMOTE_COMPONENTS", "ejs:github").strip()
YTDLP_COOKIES = os.getenv("YTDLP_COOKIES", "").strip()
YTDLP_COOKIES_FROM_BROWSER = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()

AIWORK_ROOT = resolve_project_path(os.getenv("AIWORK_ROOT", "/Volumes/TP-1TB/AIWork"))
TTS_DEVICE = os.getenv("TTS_DEVICE", "mps")

# VibeVoice synthesis is the slowest stage and its cost scales with script
# length: a full-length monologue is 8192 decode steps, and the step rate decays
# as the KV cache grows, so an hour of wall clock is not by itself evidence of a
# hang. The real watchdog is TTS_STALL_TIMEOUT — the model streams a progress
# line several times a second, so going quiet for minutes means it is wedged,
# whatever the elapsed time. TTS_TIMEOUT is only a coarse backstop.
TTS_TIMEOUT = int(os.getenv("TTS_TIMEOUT", str(6 * 3600)))
TTS_STALL_TIMEOUT = int(os.getenv("TTS_STALL_TIMEOUT", "600"))

TTS_DEFAULT_MODEL = os.getenv("TTS_DEFAULT_MODEL", "vibevoice-1.5b")
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


def _build_tts_models(root: Path) -> dict[str, dict]:
    """Registry of installed VibeVoice TTS models, rooted at ``root``.

    Each entry carries the full invocation contract for that model: which venv
    to source, which project directory to cd into, which inference script to
    run, and which speaker flag the script expects (1.5B uses plural
    --speaker_names, 0.5B uses singular --speaker_name). The 0.5B realtime model
    is single-speaker, so it only ever receives one voice source. Adding a
    future model is a data change here, not a code change in tts.py.

    It is a function because AIWORK_ROOT is settable from the Admin console:
    every path below has to be rebuilt when the root moves.
    """
    return {
        "vibevoice-1.5b": {
            "label": "1.5B (high quality)",
            "env_script": root / "env_vibevoice_1.5b.sh",
            "project_dir": root / "VibeVoice-1.5B",
            "inference_script": root / "VibeVoice-1.5B" / "demo" / "inference_from_file.py",
            "speaker_flag": "--speaker_names",
            "single_speaker": False,
        },
        "vibevoice-0.5b": {
            "label": "0.5B (fast draft)",
            "env_script": root / "env_vibevoice.sh",
            "project_dir": root / "VibeVoice",
            "inference_script": root / "VibeVoice" / "demo" / "realtime_model_inference_from_file.py",
            "speaker_flag": "--speaker_name",
            "single_speaker": True,
            # Realtime 0.5B ships a smaller preset set than 1.5B. Keep existing
            # tasks portable by substituting the closest English voice rather
            # than letting VibeVoice silently fall back to its first preset
            # (German).
            "voice_aliases": {
                "Alice": "Emma",
                "Maya": "Grace",
                "Mary": "Emma",
            },
        },
    }


TTS_MODELS: dict[str, dict] = _build_tts_models(AIWORK_ROOT)

# Voice preview samples. VibeVoice defines each preset by a short reference WAV
# that the model clones, so playing that file previews the timbre a task will
# get without paying for a synthesis run. Only the 1.5B project ships the raw
# WAVs (demo/voices/<lang>-<Name>_<gender>[_bgm].wav); the 0.5B realtime model
# ships pre-encoded .pt embeddings, so its previews resolve through the same
# voice_aliases map used at synthesis time and fall back to the 1.5B WAV.
VOICE_SAMPLE_DIR = AIWORK_ROOT / "VibeVoice-1.5B" / "demo" / "voices"


def resolve_voice(voice: str, tts_model: str | None = None) -> str:
    """Map a requested voice to the preset the given model actually uses."""
    model = TTS_MODELS.get(tts_model or TTS_DEFAULT_MODEL, {})
    return model.get("voice_aliases", {}).get(voice, voice)


def voice_sample_path(voice: str, tts_model: str | None = None):
    """Return the reference WAV for a voice, or None if none is installed.

    `voice` must already be a known preset name — callers validate it against
    the model registry so an arbitrary string never reaches the glob.
    """
    resolved = resolve_voice(voice, tts_model)
    matches = sorted(VOICE_SAMPLE_DIR.glob(f"*-{resolved}_*.wav"))
    return matches[0] if matches else None


HYPERFRAME_DIR = resolve_project_path(os.getenv("HYPERFRAME_DIR", "hyperframe"))

# HyperFrames render tuning. The CLI is pinned and installed locally under
# HYPERFRAME_DIR/node_modules so a render never triggers an on-demand `npx`
# install (the old unpinned `npx hyperframes` re-installed latest every run —
# minutes of silent stall on first/cold runs, and a moving version target).
# Frame-by-frame headless-Chrome capture of a multi-minute 1080p composition is
# inherently heavy (~frames = duration x fps), so the defaults trade frame rate
# and quality for wall-clock time.
# Claude Agent SDK video-production crews. When enabled, agents author the
# per-scene HyperFrames compositions on top of the deterministic drafts; every
# file they write is gated against the runtime contract and reverted to its
# draft if it fails, so turning this off only costs visual variety.
DIRECTOR_ENABLED = _env_bool("DIRECTOR_ENABLED", "1")
# Cap how many scenes are handed to agents (0 = no cap). Each crew is a `claude`
# CLI process and a provider round-trip, so this is the cost/latency dial.
DIRECTOR_MAX_SCENES = int(os.getenv("DIRECTOR_MAX_SCENES", "0"))
# Run `hyperframes inspect` before the render and let the agents repair the
# layout failures it reports. It drives headless Chrome over the timeline, so it
# costs a couple of minutes on a long episode; turning it off only skips the
# layout polish pass.
INSPECT_ENABLED = _env_bool("INSPECT_ENABLED", "1")

HYPERFRAMES_VERSION = os.getenv("HYPERFRAMES_VERSION", "0.6.99")
RENDER_FPS = int(os.getenv("RENDER_FPS", "15"))
RENDER_QUALITY = os.getenv("RENDER_QUALITY", "draft")  # draft | standard | high
RENDER_WORKERS = os.getenv("RENDER_WORKERS", "2")      # integer or "auto"
RENDER_RESOLUTION = os.getenv("RENDER_RESOLUTION", "landscape")  # 1920x1080

OUTPUTS_DIR = resolve_project_path(os.getenv("OUTPUTS_DIR", "outputs"))
UPLOADS_DIR = resolve_project_path(os.getenv("UPLOADS_DIR", "uploads"))
# DB_PATH may point at a custom location; defaults to the repo root.
_db_env = os.getenv("DB_PATH", "")
DB_PATH = resolve_project_path(_db_env) if _db_env else PROJECT_ROOT / "tasks.db"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
ISLA_READER_PROMOTION_SCRIPT = resolve_project_path(
    os.getenv(
        "ISLA_READER_PROMOTION_SCRIPT",
        "/Volumes/TP-1TB/WorspacePlus/IslaProject/Isla-Reader-Promotional/Promotion-Agent/generate-promotion.sh",
    )
)


def rebuild_derived() -> None:
    """Recompute values that hang off other settings, and ensure directories.

    Called after any settings change so that e.g. moving AIWORK_ROOT relocates
    the whole VibeVoice contract (model scripts and voice samples) with it.
    """
    global TTS_MODELS, VOICE_SAMPLE_DIR
    TTS_MODELS = _build_tts_models(AIWORK_ROOT)
    VOICE_SAMPLE_DIR = AIWORK_ROOT / "VibeVoice-1.5B" / "demo" / "voices"
    for directory in (OUTPUTS_DIR, UPLOADS_DIR, DB_PATH.parent):
        directory.mkdir(parents=True, exist_ok=True)


def apply_values(values: dict) -> None:
    """Rebind config attributes from an already-coerced settings mapping.

    :mod:`backend.settings_store` owns validation and coercion; this only lands
    the values on the module and refreshes whatever derives from them.
    """
    module = sys.modules[__name__]
    for key, value in values.items():
        setattr(module, key, value)
    rebuild_derived()


rebuild_derived()

# Layer the Admin console's saved settings over the .env seed. Imported here (at
# the bottom, deferred) because settings_store reads this module for its
# defaults: by now every attribute above exists.
from backend import settings_store as _settings_store  # noqa: E402

_settings_store.apply_saved()
