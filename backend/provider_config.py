import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"), override=False)


@dataclass(frozen=True)
class ProviderConfig:
    llm_provider: str = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2:latest")
    tts_provider: str = os.getenv("TTS_PROVIDER", "elevenlabs").strip().lower()
    tts_fallback_provider: str = os.getenv(
        "TTS_FALLBACK_PROVIDER", "piper"
    ).strip().lower()
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    elevenlabs_model_id: str = os.getenv(
        "ELEVENLABS_MODEL_ID", "eleven_v4_turbo"
    )
    elevenlabs_voice_ids: dict[str, str] = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "elevenlabs_voice_ids", {
            "moss": os.getenv("ELEVENLABS_VOICE_ID_MOSS", "").strip(),
            "nova": os.getenv("ELEVENLABS_VOICE_ID_NOVA", "").strip(),
            "echo": os.getenv("ELEVENLABS_VOICE_ID_ECHO", "").strip(),
            "zen": os.getenv("ELEVENLABS_VOICE_ID_ZEN", "").strip(),
        })


PROVIDER_CONFIG = ProviderConfig()
