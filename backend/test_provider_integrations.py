from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google import genai

import llm_provider
import speech_output


def test_gemini_translates_system_instruction_and_history(monkeypatch):
    monkeypatch.setattr(
        llm_provider,
        "PROVIDER_CONFIG",
        SimpleNamespace(gemini_api_key="configured", llm_provider="gemini"),
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.models.generate_content.return_value = SimpleNamespace(text="Gemini reply")
    monkeypatch.setattr(genai, "Client", MagicMock(return_value=client))

    response = llm_provider._gemini_response(
        model="gemini-test",
        messages=[
            {"role": "system", "content": "Companion instructions"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "Continue"},
        ],
        max_tokens=123,
        temperature=0.5,
        top_p=0.8,
    )

    request = client.models.generate_content.call_args.kwargs
    assert request["model"] == "gemini-test"
    assert request["contents"] == [
        {"role": "user", "parts": [{"text": "Hello"}]},
        {"role": "model", "parts": [{"text": "Hi there"}]},
        {"role": "user", "parts": [{"text": "Continue"}]},
    ]
    assert request["config"].system_instruction == "Companion instructions"
    assert request["config"].max_output_tokens == 123
    assert response.choices[0].message.content == "Gemini reply"


def test_gemini_requires_api_key(monkeypatch):
    monkeypatch.setattr(
        llm_provider,
        "PROVIDER_CONFIG",
        SimpleNamespace(gemini_api_key=""),
    )

    with pytest.raises(llm_provider.ProviderError, match="GEMINI_API_KEY") as error:
        llm_provider._gemini_response(model="gemini-test", messages=[])

    assert error.value.status_code == 503


def test_ollama_remains_selectable(monkeypatch):
    ollama = MagicMock()
    ollama.chat.completions.create.return_value = "ollama response"
    monkeypatch.setattr(llm_provider, "ollama_client", ollama)
    monkeypatch.setattr(
        llm_provider,
        "PROVIDER_CONFIG",
        SimpleNamespace(llm_provider="ollama"),
    )

    result = llm_provider.client.chat.completions.create(
        model="llama3.2:latest",
        messages=[{"role": "user", "content": "Hello"}],
    )

    assert result == "ollama response"
    ollama.chat.completions.create.assert_called_once()


def test_elevenlabs_uses_companion_voice_and_returns_playable_audio(monkeypatch):
    monkeypatch.setattr(
        speech_output,
        "PROVIDER_CONFIG",
        SimpleNamespace(
            tts_provider="elevenlabs",
            tts_fallback_provider="piper",
            elevenlabs_api_key="configured",
            elevenlabs_model_id="eleven_v4_turbo",
            elevenlabs_voice_ids={"moss": "account-voice-id"},
        ),
    )
    client = MagicMock()
    client.text_to_speech.convert.return_value = iter([b"audio", b"bytes"])
    monkeypatch.setattr(speech_output, "_elevenlabs_client", lambda: client)

    audio, media_type = speech_output.generate_speech(
        "Hello",
        companion_id="moss",
    )

    assert audio == b"audiobytes"
    assert media_type == "audio/mpeg"
    client.text_to_speech.convert.assert_called_once_with(
        voice_id="account-voice-id",
        text="Hello",
        model_id="eleven_v4_turbo",
        output_format="mp3_44100_128",
    )


def test_explicit_piper_selection_overrides_configured_voice(monkeypatch):
    monkeypatch.setattr(
        speech_output,
        "PROVIDER_CONFIG",
        SimpleNamespace(
            tts_provider="elevenlabs",
            tts_fallback_provider="piper",
            elevenlabs_api_key="configured",
            elevenlabs_model_id="eleven_v4_turbo",
            elevenlabs_voice_ids={"moss": "account-voice-id"},
        ),
    )
    piper = MagicMock(return_value=b"local wav")
    monkeypatch.setattr(speech_output, "_generate_piper", piper)
    elevenlabs_client = MagicMock()
    monkeypatch.setattr(
        speech_output,
        "_elevenlabs_client",
        lambda: elevenlabs_client,
    )

    audio, media_type = speech_output.generate_speech(
        "Hello",
        companion_id="moss",
        voice_id="",
    )

    assert (audio, media_type) == (b"local wav", "audio/wav")
    elevenlabs_client.text_to_speech.convert.assert_not_called()


def test_missing_elevenlabs_key_uses_piper(monkeypatch):
    monkeypatch.setattr(
        speech_output,
        "PROVIDER_CONFIG",
        SimpleNamespace(
            tts_provider="elevenlabs",
            tts_fallback_provider="piper",
            elevenlabs_api_key="",
            elevenlabs_model_id="eleven_v4_turbo",
            elevenlabs_voice_ids={"moss": "account-voice-id"},
        ),
    )
    monkeypatch.setattr(
        speech_output,
        "_generate_piper",
        MagicMock(return_value=b"local wav"),
    )

    assert speech_output.generate_speech("Hello", companion_id="moss") == (
        b"local wav",
        "audio/wav",
    )


def test_empty_speech_text_is_rejected():
    with pytest.raises(speech_output.SpeechProviderError) as error:
        speech_output.generate_speech("  ")

    assert error.value.status_code == 400


def test_invalid_elevenlabs_voice_id_returns_clear_error(monkeypatch):
    monkeypatch.setattr(
        speech_output,
        "PROVIDER_CONFIG",
        SimpleNamespace(
            tts_provider="elevenlabs",
            tts_fallback_provider="piper",
            elevenlabs_api_key="configured",
            elevenlabs_model_id="eleven_v4_turbo",
            elevenlabs_voice_ids={},
        ),
    )

    class VoiceNotFound(Exception):
        status_code = 404

    client = MagicMock()
    client.text_to_speech.convert.side_effect = VoiceNotFound()
    monkeypatch.setattr(speech_output, "_elevenlabs_client", lambda: client)

    with pytest.raises(speech_output.SpeechProviderError, match="voice ID") as error:
        speech_output.generate_speech("Hello", voice_id="not-available")

    assert error.value.status_code == 400


def test_voice_list_returns_account_metadata(monkeypatch):
    monkeypatch.setattr(
        speech_output,
        "PROVIDER_CONFIG",
        SimpleNamespace(elevenlabs_api_key="configured"),
    )
    client = MagicMock()
    client.voices.get_all.return_value.voices = [SimpleNamespace(
        voice_id="account-voice-id",
        name="Voice Name",
        category="premade",
        description="A calm voice",
        labels={"accent": "neutral"},
    )]
    monkeypatch.setattr(speech_output, "_elevenlabs_client", lambda: client)

    assert speech_output.list_elevenlabs_voices() == [{
        "voice_id": "account-voice-id",
        "name": "Voice Name",
        "category": "premade",
        "description": "A calm voice",
        "labels": {"accent": "neutral"},
    }]
