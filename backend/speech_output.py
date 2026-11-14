from io import BytesIO
from pathlib import Path
import wave

from piper import PiperVoice

from provider_config import PROVIDER_CONFIG


VOICE_PATH = Path(__file__).parent / "voices" / "en_US-kusal-medium.onnx"
piper_voice = PiperVoice.load(str(VOICE_PATH))


class SpeechProviderError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _generate_piper(text: str) -> bytes:
    audio_buffer = BytesIO()
    with wave.open(audio_buffer, "wb") as wav_file:
        piper_voice.synthesize_wav(text, wav_file)
    return audio_buffer.getvalue()


def _elevenlabs_client():
    try:
        from elevenlabs.client import ElevenLabs
    except ImportError as error:
        raise SpeechProviderError(
            "ElevenLabs support is unavailable; install the elevenlabs package.",
            503,
        ) from error

    return ElevenLabs(api_key=PROVIDER_CONFIG.elevenlabs_api_key, timeout=30.0)


def _configured_voice(companion_id: str | None) -> str:
    if not companion_id or not PROVIDER_CONFIG.elevenlabs_voice_ids:
        return ""
    return PROVIDER_CONFIG.elevenlabs_voice_ids.get(companion_id, "")


def _fallback_or_raise(text: str) -> bytes:
    if PROVIDER_CONFIG.tts_fallback_provider == "piper":
        return _generate_piper(text)
    raise SpeechProviderError(
        "ElevenLabs is unavailable and no speech fallback is configured.",
        503,
    )


def generate_speech(
    text: str,
    companion_id: str | None = None,
    voice_id: str | None = None,
) -> tuple[bytes, str]:
    if not text or not text.strip():
        raise SpeechProviderError("Speech text must not be empty.", 400)

    provider = PROVIDER_CONFIG.tts_provider
    if provider == "piper":
        return _generate_piper(text), "audio/wav"
    if provider != "elevenlabs":
        raise SpeechProviderError(
            f"Unsupported TTS_PROVIDER '{provider}'. Choose 'elevenlabs' or 'piper'.",
            503,
        )

    selected_voice_id = (
        voice_id if voice_id is not None else _configured_voice(companion_id)
    ).strip()
    if not PROVIDER_CONFIG.elevenlabs_api_key:
        return _fallback_or_raise(text), "audio/wav"
    if not selected_voice_id:
        return _fallback_or_raise(text), "audio/wav"

    try:
        client = _elevenlabs_client()
        audio = client.text_to_speech.convert(
            voice_id=selected_voice_id,
            text=text,
            model_id=PROVIDER_CONFIG.elevenlabs_model_id,
            output_format="mp3_44100_128",
        )
        return b"".join(audio), "audio/mpeg"
    except SpeechProviderError as error:
        if error.status_code >= 500:
            return _fallback_or_raise(text), "audio/wav"
        raise
    except Exception as error:
        status = getattr(error, "status_code", None) or getattr(error, "status", None)
        if status == 404:
            raise SpeechProviderError(
                "The selected ElevenLabs voice ID is unavailable to this account.",
                400,
            ) from error
        if status == 429:
            return _fallback_or_raise(text), "audio/wav"
        if status in {408, 504} or isinstance(error, TimeoutError):
            return _fallback_or_raise(text), "audio/wav"
        return _fallback_or_raise(text), "audio/wav"


def list_elevenlabs_voices() -> list[dict]:
    if not PROVIDER_CONFIG.elevenlabs_api_key:
        return []

    try:
        response = _elevenlabs_client().voices.get_all()
    except Exception as error:
        status = getattr(error, "status_code", None) or getattr(error, "status", None)
        if status == 401 or status == 403:
            raise SpeechProviderError(
                "ElevenLabs rejected the configured API credentials.",
                502,
            ) from error
        if status == 429:
            raise SpeechProviderError(
                "ElevenLabs voice listing is rate limited. Try again shortly.",
                429,
            ) from error
        raise SpeechProviderError(
            "Available ElevenLabs voices could not be loaded.",
            503,
        ) from error

    voices = []
    for voice in getattr(response, "voices", []) or []:
        labels = getattr(voice, "labels", None) or {}
        voices.append({
            "voice_id": getattr(voice, "voice_id", ""),
            "name": getattr(voice, "name", "Unnamed voice"),
            "category": getattr(voice, "category", None),
            "description": getattr(voice, "description", None),
            "labels": labels,
        })
    return voices
