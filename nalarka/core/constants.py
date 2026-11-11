"""Constants, default values, and model presets for Nalarka Motion Graphics Studio."""

CONFIG_FILE = "config_nalarka.json"
LOG_FILE = "nalarka_debug.log"
FFMPEG_LOG_FILE = "ffmpeg_render.log"
DEFAULT_REF_LIBRARY_DIR = "references_library"
FPS = 30
MAX_DURATION = 60            # "Standard" cap from the Nalarka spec (default)
MIN_DURATION_CAP = 15        # lowest cap the UI accepts
HARD_MAX_DURATION = 900      # absolute ceiling for Extended / long-video mode
DEFAULT_MAX_DURATION = MAX_DURATION
DURATION_PRESETS = ["60", "90", "120", "180", "300", "600"]

SPEECH_WORDS_PER_SEC = 2.5   # spoken pace used for all duration estimates
INTRO_OUTRO_SECONDS = 2.5    # ~1.0s lead-in + ~1.5s tail (spec: voiceover + 2.5s, rounded up)
MIN_BEAT_SECONDS = 1.2       # shortest beat the storyboard sanitiser will keep
PASS1_WINDOW_SECONDS = 90.0  # storyboard is planned in windows of this length
PASS2_BATCH_BEATS = 6        # beats of Python code requested per LLM call
PASS2_PARALLEL_CALLS = 3     # concurrent code-generation calls
EL_CHUNK_CHARS = 4500        # ElevenLabs text per request (keeps well under the 10k model limit)
TTS_OVERSHOOT_TOLERANCE = 1.12 # tolerance for word-to-audio duration ratio

# --- AI providers ---------------------------------------------------------
PROVIDER_CUSTOM = "custom"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_LABELS = {
    PROVIDER_CUSTOM: "Custom OpenAI-compatible (oao.ai.id)",
    PROVIDER_OPENROUTER: "OpenRouter",
    PROVIDER_DEEPSEEK: "DeepSeek (direct)",
}
PROVIDER_DEFAULT_BASE_URL = {
    PROVIDER_CUSTOM: "https://oao.ai.id/v1",
    PROVIDER_OPENROUTER: "https://openrouter.ai/api/v1",
    PROVIDER_DEEPSEEK: "https://api.deepseek.com",
}
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# DeepSeek's own API (OpenAI-compatible, no proxy in between). Model ids change between releases, so these are only
# starting points: "Fetch models" in the AI engine card reads the live list from GET /models.
DEEPSEEK_VISUAL_CODER_MODELS = ["deepseek-v4-pro", "deepseek-flash"]
DEEPSEEK_SCRIPT_MODELS = ["deepseek-flash", "deepseek-v4-pro"]
PROVIDER_HINTS = {
    PROVIDER_CUSTOM: "Any OpenAI-compatible endpoint (LM Studio, a local server, oao.ai.id).",
    PROVIDER_OPENROUTER: "One key for many models. Use Fetch models to list them.",
    PROVIDER_DEEPSEEK: "Direct to api.deepseek.com with your DeepSeek key and balance. Use Fetch models to see the ids your account can call.",
}

OPENROUTER_VISUAL_CODER_MODELS = [
    "anthropic/claude-opus-5.5",
    "anthropic/claude-sonnet-5.5",
    "deepseek/deepseek-chat",
    "moonshotai/kimi-k2",
    "qwen/qwen3-coder",
    "google/gemini-2.5-pro",
    "openai/gpt-5",
]

OPENROUTER_SCRIPT_MODELS = [
    "anthropic/claude-sonnet-5.5",
    "anthropic/claude-haiku-5.5",
    "deepseek/deepseek-chat",
    "google/gemini-2.5-flash",
    "openai/gpt-5-mini",
]

OAO_VISUAL_CODER_MODELS = [
    "claude-fable-5",
    "claude-opus-5.5",
    "deepseek-v4-pro",
    "kimi-k2.7-code",
    "gpt-6-sol",
    "gpt-6-astra",
    "glm-5.3",
    "grok-4.7",
    "qwen3.8-27b",
    "gemini-3.8-flash",
]

OAO_SCRIPT_MODELS = [
    "claude-fable-5",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "gemini-3.8-flash",
    "gpt-6-luna",
    "claude-haiku-5.5",
    "glm-5.2",
]

ELEVENLABS_VOICE_PRESETS = [
    "Brian - Deep Documentary [Free API] (nPczCjzI2devNBz1zQrb)",
    "Daniel - Steady Explainer [Free API] (onwK4e9ZLuTAKqWW03F9)",
    "George - Warm Storyteller [Free API] (JBFqnCBsd6RMkjVDRZzb)",
    "Bill - Smooth Narrator [Free API] (pqHfZKP75CvOlQylNhV4)",
    "Adam - Punchy Shorts [Free API] (pNInz6obpgDQGcFmaJgB)",
]

VALID_LAYOUTS = ("triptych_dots", "top_text_bottom_art", "top_art_bottom_text", "statement_pill")
VALID_BG_COLORS = ("cream", "orange_bg", "green_bg", "blue_bg")
VALID_TRANSITIONS = ("cut", "directional_wipe", "whip_pan", "circle_iris", "crossfade")
