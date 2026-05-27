"""Groq provider — cloud heavy reasoning (``llama-3.3-70b-versatile``).

Groq's base URL already includes the ``/openai/v1`` root, so the shared
transport posts ``chat/completions`` against it directly. Auth is a bearer token
read from settings (`GROQ_API_KEY`, never hard-coded).
"""

from __future__ import annotations

from brain.llm.providers.openai_compatible import OpenAICompatibleProvider
from foundation.config import Settings

GROQ_PROVIDER_NAME = "groq"


class GroqProvider(OpenAICompatibleProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            name=GROQ_PROVIDER_NAME,
            base_url=settings.groq_base_url,
            api_key=settings.groq_api_key or None,
            timeout=60.0,
        )
