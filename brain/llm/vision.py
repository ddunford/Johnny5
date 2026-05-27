"""Vision captioning — image understanding via the router (gemma4:e4b).

Vision percepts go through the router (FC-4), not a direct provider call: the
``vision`` role maps to the local multimodal workhorse ``gemma4:e4b``. The image
is sent as an OpenAI ``image_url`` data-URL content part (verified against
inference.lan).
"""

from __future__ import annotations

import base64
from typing import Any

from brain.llm.base import Message
from brain.llm.router import LLMRouter

VISION_ROLE = "vision"


class Vision:
    def __init__(self, router: LLMRouter, *, role: str = VISION_ROLE) -> None:
        self._router = router
        self._role = role

    async def describe(
        self,
        image: bytes,
        prompt: str,
        *,
        media_type: str = "image/png",
        temperature: float = 0.2,
        max_tokens: int | None = 512,
    ) -> str:
        """Return a textual description/answer for an image given a prompt."""
        data_url = f"data:{media_type};base64,{base64.b64encode(image).decode()}"
        parts: list[dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        completion = await self._router.complete(
            self._role,
            [Message(role="user", content=parts)],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return completion.content
