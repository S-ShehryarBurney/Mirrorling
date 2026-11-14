from types import SimpleNamespace
from typing import Any

import httpx

from provider_config import PROVIDER_CONFIG
from ollama_client import client as ollama_client


MODEL = (
    PROVIDER_CONFIG.gemini_model
    if PROVIDER_CONFIG.llm_provider == "gemini"
    else PROVIDER_CONFIG.ollama_model
)


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _gemini_response(model: str, messages: list[dict[str, Any]], **options):
    if not PROVIDER_CONFIG.gemini_api_key:
        raise ProviderError(
            "Gemini is selected but GEMINI_API_KEY is not configured.",
            503,
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise ProviderError(
            "Gemini support is unavailable; install the google-genai package.",
            503,
        ) from error

    system_instruction = ""
    contents = []
    for message in messages:
        role = message.get("role")
        text = message.get("content", "")
        if role == "system":
            if not system_instruction:
                system_instruction = text
            continue
        if role not in {"user", "assistant"}:
            continue
        contents.append({
            "role": "user" if role == "user" else "model",
            "parts": [{"text": text}],
        })

    config = types.GenerateContentConfig(
        system_instruction=system_instruction or None,
        temperature=options.get("temperature", 0.85),
        top_p=options.get("top_p", 0.9),
        max_output_tokens=options.get("max_tokens", 512),
    )

    try:
        with genai.Client(
            api_key=PROVIDER_CONFIG.gemini_api_key,
            http_options=types.HttpOptions(timeout=30_000),
        ) as client:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
    except Exception as error:
        status = getattr(error, "code", None) or getattr(error, "status_code", None)
        if status == 429:
            raise ProviderError("Gemini rate limit reached. Try again shortly.", 429) from error
        is_timeout = isinstance(error, (TimeoutError, httpx.TimeoutException))
        is_timeout = is_timeout or "timeout" in type(error).__name__.lower()
        if status in {408, 504} or is_timeout:
            raise ProviderError("Gemini request timed out. Try again.", 504) from error
        if status in {401, 403}:
            raise ProviderError("Gemini rejected the configured API credentials.", 502) from error
        raise ProviderError("Gemini could not generate a response.", 502) from error

    return SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content=response.text or ""))
    ])


class _Completions:
    def create(self, **kwargs):
        provider = PROVIDER_CONFIG.llm_provider
        if provider == "ollama":
            return ollama_client.chat.completions.create(**kwargs)
        if provider != "gemini":
            raise ProviderError(
                f"Unsupported LLM_PROVIDER '{provider}'. Choose 'gemini' or 'ollama'.",
                503,
            )
        return _gemini_response(**kwargs)


class _Chat:
    completions = _Completions()


class _ProviderClient:
    chat = _Chat()


client = _ProviderClient()
